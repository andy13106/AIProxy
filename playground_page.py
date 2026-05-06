import base64
import datetime
import html
import io
import json
import mimetypes
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

import requests
import streamlit as st

from db import (
    APIKey,
    PlaygroundChatAttachment,
    PlaygroundChatMessage,
    PlaygroundChatSession,
    Provider,
    SessionLocal,
)
from utils import classify_model_type, fetch_models, log_custom_usage


_WORKER_LOCK = threading.Lock()
_WORKER_THREADS: dict[str, threading.Thread] = {}
_WORKER_STOP_FLAGS: dict[str, bool] = {}


def cleanup_threads() -> None:
    """清理所有工作线程"""
    with _WORKER_LOCK:
        # 为所有线程设置停止标志
        for session_id in list(_WORKER_THREADS.keys()):
            _WORKER_STOP_FLAGS[session_id] = True
        
        # 等待线程结束（最多等待2秒）
        for session_id, thread in list(_WORKER_THREADS.items()):
            if thread.is_alive():
                try:
                    thread.join(timeout=2.0)
                except Exception:
                    pass
        
        # 清空线程和停止标志
        _WORKER_THREADS.clear()
        _WORKER_STOP_FLAGS.clear()


_DATA_DIR = Path(__file__).parent / "data" / "attachments"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


SUPPORTED_ATTACHMENT_TYPES = {
    "image": {
        "extensions": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"},
        "mime_types": {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"},
    },
    "document": {
        "extensions": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"},
        "mime_types": {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/plain",
            "text/csv",
            "text/markdown",
        },
    },
}


def _save_attachment(file_data: bytes, filename: str, mime_type: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    attachment_id = str(uuid.uuid4())
    saved_filename = f"{attachment_id}{ext}"
    file_path = _DATA_DIR / saved_filename

    with open(file_path, "wb") as f:
        f.write(file_data)

    attachment_type = "unknown"
    if mime_type.startswith("image/") or ext in SUPPORTED_ATTACHMENT_TYPES["image"]["extensions"]:
        attachment_type = "image"
    elif ext in SUPPORTED_ATTACHMENT_TYPES["document"]["extensions"]:
        attachment_type = "document"

    with SessionLocal() as session:
        db_attachment = PlaygroundChatAttachment(
            attachment_uid=attachment_id,
            filename=filename,
            file_path=str(file_path),
            file_size=len(file_data),
            mime_type=mime_type,
            attachment_type=attachment_type,
        )
        session.add(db_attachment)
        session.commit()

    return attachment_id


def _get_attachment_data(attachment_id: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        db_attachment = session.query(PlaygroundChatAttachment).filter(
            PlaygroundChatAttachment.attachment_uid == attachment_id
        ).first()
        if db_attachment is None:
            return None
        return {
            "id": db_attachment.attachment_uid,
            "filename": db_attachment.filename,
            "file_path": db_attachment.file_path,
            "file_size": db_attachment.file_size,
            "mime_type": db_attachment.mime_type,
            "attachment_type": db_attachment.attachment_type,
        }


def _encode_image_to_base64(file_path: str) -> str:
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _stop_stream_worker(session_id: str) -> None:
    with _WORKER_LOCK:
        _WORKER_STOP_FLAGS[session_id] = True


def _clear_stop_flag(session_id: str) -> None:
    with _WORKER_LOCK:
        _WORKER_STOP_FLAGS.pop(session_id, None)


def _should_stop(session_id: str) -> bool:
    with _WORKER_LOCK:
        return _WORKER_STOP_FLAGS.get(session_id, False)


THINKING_PATTERN = re.compile(r"<thinking>([\s\S]*?)</thinking>", re.IGNORECASE)
CODE_BLOCK_PATTERN = re.compile(r"```(\w+)?\n([\s\S]*?)```")


def _ensure_state(provider_names: list[str]) -> None:
    if "playground_state" not in st.session_state:
        st.session_state.playground_state = {
            "sessions": {},
            "current_session_id": None,
            "input_history": [],
            "models_cache": {},
            "loaded_from_db": False,
            "pending_attachments": [],
            "file_uploader_counters": {},
        }

    state = st.session_state.playground_state
    if not state.get("loaded_from_db"):
        loaded_sessions, current_id = _load_sessions_from_db(provider_names)
        state["sessions"] = loaded_sessions
        state["current_session_id"] = current_id
        state["loaded_from_db"] = True

    if not state["sessions"]:
        _create_new_session(provider_names)


def _create_new_session(provider_names: list[str], persist: bool = True) -> str:
    state = st.session_state.playground_state
    session_id = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    default_provider = provider_names[0] if provider_names else None
    state["sessions"][session_id] = {
        "id": session_id,
        "title": "新对话",
        "created_at": datetime.datetime.now().isoformat(),
        "messages": [],
        "provider": default_provider,
        "model": None,
        "is_streaming": False,
        "streaming_index": None,
        "last_error": None,
    }
    state["current_session_id"] = session_id
    if persist:
        _upsert_session_to_db(state["sessions"][session_id])
    return session_id


def _upsert_session_to_db(session_data: dict[str, Any]) -> None:
    now = datetime.datetime.utcnow()
    with SessionLocal() as session:
        db_obj = (
            session.query(PlaygroundChatSession)
            .filter(PlaygroundChatSession.session_uid == session_data["id"])
            .first()
        )
        if db_obj is None:
            db_obj = PlaygroundChatSession(
                session_uid=session_data["id"],
                title=session_data.get("title") or "新对话",
                provider_name=session_data.get("provider"),
                model_name=session_data.get("model"),
                created_at=now,
                updated_at=now,
            )
            session.add(db_obj)
        else:
            db_obj.title = session_data.get("title") or "新对话"
            db_obj.provider_name = session_data.get("provider")
            db_obj.model_name = session_data.get("model")
            db_obj.updated_at = now
        session.commit()


def _upsert_message_to_db(session_uid: str, seq: int, role: str, content: str) -> int:
    with SessionLocal() as session:
        db_obj = (
            session.query(PlaygroundChatMessage)
            .filter(
                PlaygroundChatMessage.session_uid == session_uid,
                PlaygroundChatMessage.seq == seq,
            )
            .first()
        )
        if db_obj is None:
            db_obj = PlaygroundChatMessage(
                session_uid=session_uid,
                seq=seq,
                role=role,
                content=content or "",
            )
            session.add(db_obj)
            session.flush()
        else:
            db_obj.role = role
            db_obj.content = content or ""
        session.commit()
        return db_obj.id


def _delete_session_from_db(session_uid: str) -> None:
    with SessionLocal() as session:
        session.query(PlaygroundChatMessage).filter(PlaygroundChatMessage.session_uid == session_uid).delete()
        session.query(PlaygroundChatSession).filter(PlaygroundChatSession.session_uid == session_uid).delete()
        session.commit()


def _load_sessions_from_db(provider_names: list[str]) -> tuple[dict[str, dict[str, Any]], str | None]:
    sessions: dict[str, dict[str, Any]] = {}
    current_id: str | None = None
    with SessionLocal() as session:
        db_sessions = (
            session.query(PlaygroundChatSession)
            .order_by(PlaygroundChatSession.updated_at.asc(), PlaygroundChatSession.id.asc())
            .all()
        )
        for db_item in db_sessions:
            sid = db_item.session_uid
            provider = (
                db_item.provider_name
                if db_item.provider_name in provider_names
                else (provider_names[0] if provider_names else None)
            )
            sess = {
                "id": sid,
                "title": db_item.title or "新对话",
                "created_at": (db_item.created_at or datetime.datetime.utcnow()).isoformat(),
                "messages": [],
                "provider": provider,
                "model": db_item.model_name,
                "is_streaming": False,
                "streaming_index": None,
                "last_error": None,
            }
            db_messages = (
                session.query(PlaygroundChatMessage)
                .filter(PlaygroundChatMessage.session_uid == sid)
                .order_by(PlaygroundChatMessage.seq.asc(), PlaygroundChatMessage.id.asc())
                .all()
            )
            for msg in db_messages:
                sess["messages"].append({"role": msg.role, "content": msg.content or ""})
            sessions[sid] = sess
            current_id = sid
    return sessions, current_id


def _get_provider_to_key() -> dict[str, tuple[Provider, APIKey]]:
    with SessionLocal() as session:
        providers = session.query(Provider).all()
        active_keys = session.query(APIKey).filter(APIKey.is_active.is_(True)).all()

    provider_to_key: dict[str, tuple[Provider, APIKey]] = {}
    for provider in providers:
        key = next((k for k in active_keys if k.provider_id == provider.id), None)
        if key:
            provider_to_key[provider.name] = (provider, key)
    return provider_to_key


def _get_text_models(provider: Provider, key: APIKey) -> list[str]:
    state = st.session_state.playground_state
    cache_key = f"provider_models_{provider.id}"
    if cache_key not in state["models_cache"]:
        state["models_cache"][cache_key] = fetch_models(provider.api_base, key.key)
    models = state["models_cache"][cache_key]
    return [m for m in models if classify_model_type(m) == "text"]


def _format_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            text = msg["content"].strip()
            if text:
                return text[:30] + ("..." if len(text) > 30 else "")
    return "新对话"


def _build_api_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload_messages: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role not in {"user", "assistant", "system"}:
            continue

        attachments = msg.get("attachments", [])
        text_content = str(msg.get("content") or "")

        if not attachments:
            payload_messages.append({"role": role, "content": text_content})
            continue

        content_parts = []
        if text_content.strip():
            content_parts.append({"type": "text", "text": text_content})

        for att in attachments:
            att_data = _get_attachment_data(att.get("id"))
            if not att_data:
                continue

            att_type = att_data.get("attachment_type")
            if att_type == "image":
                base64_data = _encode_image_to_base64(att_data["file_path"])
                mime_type = att_data.get("mime_type") or "image/jpeg"
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_data}"
                    }
                })
            else:
                if text_content.strip():
                    text_content += f"\n\n[附件: {att_data.get('filename')}]"
                else:
                    text_content = f"[附件: {att_data.get('filename')}]"

        if content_parts:
            payload_messages.append({"role": role, "content": content_parts})
        else:
            payload_messages.append({"role": role, "content": text_content or ""})

    return payload_messages


def _start_stream_worker(
    state: dict[str, Any],
    session_id: str,
    provider: Provider,
    key: APIKey,
    model_name: str,
    payload_messages: list[dict[str, Any]],
) -> None:
    def run_stream() -> None:
        usage_data: dict[str, Any] = {}
        stopped = False
        resp = None
        try:
            _clear_stop_flag(session_id)
            url = f"{provider.api_base.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {key.key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model_name,
                "messages": payload_messages,
                "max_tokens": 4096,
                "temperature": 0.7,
                "stream": True,
            }

            if _should_stop(session_id):
                stopped = True
                return

            resp = requests.post(url, headers=headers, json=payload, timeout=180, stream=True)
            if resp.status_code != 200:
                err = f"请求失败：HTTP {resp.status_code}，{resp.text[:300]}"
                with _WORKER_LOCK:
                    session = state["sessions"].get(session_id)
                    if session is None:
                        return
                    stream_idx = session.get("streaming_index")
                    if stream_idx is not None and 0 <= stream_idx < len(session["messages"]):
                        session["messages"][stream_idx] = {"role": "error", "content": err}
                    else:
                        session["messages"].append({"role": "error", "content": err})
                    session["is_streaming"] = False
                    session["streaming_index"] = None
                    session["last_error"] = err
                    msg_idx = len(session["messages"]) - 1
                    if msg_idx >= 0:
                        msg = session["messages"][msg_idx]
                        _upsert_message_to_db(
                            session_uid=session_id,
                            seq=msg_idx,
                            role=msg.get("role") or "error",
                            content=msg.get("content") or "",
                        )
                    _upsert_session_to_db(session)
                return

            for line in resp.iter_lines(decode_unicode=True):
                if _should_stop(session_id):
                    stopped = True
                    break

                if not line or not line.startswith("data: "):
                    continue
                chunk_data = line[6:]
                if chunk_data.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(chunk_data)
                except json.JSONDecodeError:
                    continue

                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                piece = delta.get("content") or ""
                if chunk.get("usage"):
                    usage_data = chunk["usage"]

                if piece:
                    with _WORKER_LOCK:
                        session = state["sessions"].get(session_id)
                        if session is None:
                            return
                        stream_idx = session.get("streaming_index")
                        if stream_idx is None or stream_idx >= len(session["messages"]):
                            return
                        session["messages"][stream_idx]["content"] += piece

            with _WORKER_LOCK:
                session = state["sessions"].get(session_id)
                if session is None:
                    return
                stream_idx = session.get("streaming_index")
                if stopped:
                    if stream_idx is not None and 0 <= stream_idx < len(session["messages"]):
                        existing_content = session["messages"][stream_idx].get("content") or ""
                        if existing_content:
                            session["messages"][stream_idx]["content"] = existing_content + "\n\n[用户已停止生成]"
                        else:
                            session["messages"][stream_idx]["content"] = "[用户已停止生成]"
                else:
                    if stream_idx is not None and 0 <= stream_idx < len(session["messages"]):
                        if not session["messages"][stream_idx].get("content"):
                            session["messages"][stream_idx]["content"] = "(空响应)"
                session["is_streaming"] = False
                session["streaming_index"] = None

            if not stopped:
                prompt_tokens = usage_data.get("prompt_tokens") or 0
                completion_tokens = usage_data.get("completion_tokens") or 0
                total_tokens = usage_data.get("total_tokens") or (prompt_tokens + completion_tokens)
                log_custom_usage(
                    key_id=key.id,
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            with _WORKER_LOCK:
                session = state["sessions"].get(session_id)
                if session is not None:
                    msg_idx = len(session["messages"]) - 1
                    if msg_idx >= 0:
                        msg = session["messages"][msg_idx]
                        _upsert_message_to_db(
                            session_uid=session_id,
                            seq=msg_idx,
                            role=msg.get("role") or "assistant",
                            content=msg.get("content") or "",
                        )
                    _upsert_session_to_db(session)

        except Exception as exc:
            err = f"请求异常：{exc}"
            with _WORKER_LOCK:
                session = state["sessions"].get(session_id)
                if session is None:
                    return
                stream_idx = session.get("streaming_index")
                if stream_idx is not None and 0 <= stream_idx < len(session["messages"]):
                    if session["messages"][stream_idx].get("content"):
                        session["messages"].append({"role": "error", "content": err})
                    else:
                        session["messages"][stream_idx] = {"role": "error", "content": err}
                else:
                    session["messages"].append({"role": "error", "content": err})
                session["is_streaming"] = False
                session["streaming_index"] = None
                session["last_error"] = err
                msg_idx = len(session["messages"]) - 1
                if msg_idx >= 0:
                    msg = session["messages"][msg_idx]
                    _upsert_message_to_db(
                        session_uid=session_id,
                        seq=msg_idx,
                        role=msg.get("role") or "error",
                        content=msg.get("content") or "",
                    )
                _upsert_session_to_db(session)
        finally:
            # 确保关闭响应连接
            if resp:
                try:
                    resp.close()
                except Exception:
                    pass
            with _WORKER_LOCK:
                _WORKER_THREADS.pop(session_id, None)
                _WORKER_STOP_FLAGS.pop(session_id, None)

    with _WORKER_LOCK:
        existing = _WORKER_THREADS.get(session_id)
        if existing and existing.is_alive():
            return
        worker = threading.Thread(target=run_stream, daemon=True, name=f"playground_stream_{session_id}")
        _WORKER_THREADS[session_id] = worker
        worker.start()


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _render_user_message(content: str, attachments: list[dict[str, Any]] | None = None) -> None:
    safe_text = html.escape(content or "")
    file_badges = []

    if attachments:
        for att in attachments:
            att_data = _get_attachment_data(att.get("id"))
            if not att_data:
                continue
            filename = att_data.get("filename", "?")
            size_str = _format_file_size(att_data.get("file_size", 0))
            att_type = att_data.get("attachment_type", "unknown")
            file_path = att_data.get("file_path")
            icon = "\U0001f5bc\ufe0f" if att_type == "image" else "\U0001f4ce"

            if att_type == "image" and file_path and os.path.exists(file_path):
                try:
                    st.image(file_path, caption=filename, width=200)
                    continue
                except Exception:
                    pass

            file_badges.append(
                f'<div class="msg-file-badge">{icon} {html.escape(filename)[:20]} ({size_str})</div>'
            )

    parts = []
    if file_badges:
        parts.append(f'<div class="msg-files">{"".join(file_badges)}</div>')
    if safe_text:
        parts.append(
            '<div class="chat-user-wrap">'
            '<div class="chat-user-avatar">U</div>'
            f'<div class="chat-bubble chat-bubble-user"><div class="message-text">{safe_text}</div></div>'
            '</div>'
        )

    if parts:
        st.markdown(
            f'<div class="chat-row chat-row-user">{"".join(parts)}</div>',
            unsafe_allow_html=True,
        )


def _render_assistant_message(content: str, message_idx: int, is_streaming: bool = False) -> None:
    with st.container(key=f"pg_asst_{message_idx}"):
        st.markdown('<div class="chat-meta"><div class="role-icon assistant">AI</div> <span>Assistant</span></div>', unsafe_allow_html=True)
    text = content or ""
    thinking_parts = THINKING_PATTERN.findall(text)
    content_without_thinking = THINKING_PATTERN.sub("", text).strip()

    for idx, think in enumerate(thinking_parts):
        with st.expander(f"思考过程 #{idx + 1}", expanded=False):
            st.markdown(think)

        if not content_without_thinking and is_streaming:
            st.markdown('<div class="chat-stream-cursor">▌</div>', unsafe_allow_html=True)
            return

        tail_cursor = " ▌" if is_streaming else ""
        st.markdown(content_without_thinking + tail_cursor)

def _render_error_message(content: str) -> None:
    st.error(content or "未知错误")


def _render_messages(messages: list[dict[str, Any]], current_session_streaming: bool) -> None:
    if not messages:
        st.markdown(
            """
            <div class="chat-empty-state">
                <h3>开始一个新对话</h3>
                <p>在下方输入框发送消息，支持流式回复、代码块和思考折叠。</p>
                <p>支持添加图片、文档等附件，可拖拽文件到界面添加。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content") or ""
        attachments = msg.get("attachments", [])
        if role == "user":
            _render_user_message(content, attachments)
        elif role == "assistant":
            is_streaming = current_session_streaming and idx == len(messages) - 1
            _render_assistant_message(content, message_idx=idx, is_streaming=is_streaming)
        elif role == "system":
            st.info(content)
        elif role == "error":
            _render_error_message(content)


def _apply_page_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --pg-bg: #0b1220;
            --pg-surface: #111a2e;
            --pg-surface-soft: #18243c;
            --pg-border: rgba(148, 163, 184, 0.25);
            --pg-text: #dbe5f5;
            --pg-muted: #9fb0c8;
            --pg-accent: #4f8cff;
            --pg-accent-strong: #2e6fff;
            --pg-user-gradient: linear-gradient(135deg, #2f7bff 0%, #1f5de2 100%);
        }
        html, body {
            overflow: hidden !important;
        }
        [data-testid="stAppViewContainer"] {
            overflow: hidden !important;
            background: radial-gradient(circle at top, #1b2b4a 0%, var(--pg-bg) 38%) !important;
        }
        [data-testid="stMain"] {
            overflow: hidden !important;
        }
        .block-container {
            padding-top: 0.4rem;
            padding-bottom: 10rem;
            max-width: 1380px;
            height: calc(100vh - 1rem);
            overflow: hidden;
        }
        .block-container h3, .block-container p, .block-container span, .block-container label {
            color: var(--pg-text);
        }
        .block-container h3 {
            margin-top: 0.08rem;
            margin-bottom: 0.16rem;
            letter-spacing: -0.01em;
        }
        [data-testid="stCaptionContainer"] {
            margin-top: 0;
            margin-bottom: 0.28rem;
            color: var(--pg-muted) !important;
        }
        .st-key-pg_chat_scroll {
            border: 1px solid var(--pg-border);
            background: rgba(7, 12, 24, 0.55);
            border-radius: 16px;
            padding: 0.55rem 0.8rem;
        }
        .chat-row {
            display: flex;
            width: 100%;
            margin-bottom: 0.95rem;
        }
        .chat-row-user {
            justify-content: flex-end;
        }
        .chat-row-assistant {
            justify-content: flex-start;
            max-width: 960px;
        }
        .chat-bubble {
            border-radius: 14px;
            padding: 0.82rem 0.98rem;
            word-break: break-word;
            line-height: 1.5;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2);
        }
        .chat-bubble-user {
            max-width: 72%;
            background: var(--pg-user-gradient);
            color: #fff;
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-bottom-right-radius: 6px;
        }
        .chat-bubble-assistant {
            width: 100%;
            background: linear-gradient(180deg, rgba(22, 33, 57, 0.95) 0%, rgba(15, 24, 43, 0.95) 100%);
            border: 1px solid var(--pg-border);
            border-bottom-left-radius: 6px;
            color: var(--pg-text);
        }
        .chat-user-wrap {
            display: flex;
            align-items: flex-end;
            gap: 0.5rem;
            justify-content: flex-end;
            width: 100%;
        }
        .chat-user-avatar {
            width: 1.5rem;
            height: 1.5rem;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 0.7rem;
            font-weight: 700;
            color: #d8e6ff;
            background: rgba(79, 140, 255, 0.22);
            border: 1px solid rgba(79, 140, 255, 0.4);
            flex: 0 0 auto;
        }
        [class*="st-key-pg_asst_"] {
            width: min(960px, 100%);
            border: 1px solid var(--pg-border);
            background: rgba(11, 19, 34, 0.45);
            border-radius: 14px;
            padding: 0.58rem 0.72rem 0.52rem 0.72rem;
            box-shadow: 0 10px 26px rgba(0, 0, 0, 0.22);
            margin-bottom: 0.9rem;
        }
        [class*="st-key-pg_asst_"] [data-testid="stMarkdownContainer"] p,
        [class*="st-key-pg_asst_"] [data-testid="stMarkdownContainer"] li {
            color: var(--pg-text) !important;
            line-height: 1.58;
        }
        [class*="st-key-pg_asst_"] [data-testid="stCodeBlock"] {
            border-radius: 10px;
            border: 1px solid rgba(148, 163, 184, 0.2);
        }
        .chat-empty-state {
            text-align: center;
            margin-top: 3.5rem;
            color: var(--pg-muted);
        }
        .chat-stream-cursor {
            color: #f8fafc;
            font-size: 1.2rem;
            line-height: 1;
            padding: 0.1rem 0.3rem;
        }
        .chat-meta {
            font-size: 0.85rem;
            color: var(--pg-muted);
            margin-bottom: 0.42rem;
        }
        .chat-meta {
        font-size: 12px;
        font-weight: 500;
        color: var(--pg-muted);
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .role-icon {
        width: 22px;
        height: 22px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 10px;
        font-weight: 700;
    }
    .role-icon.assistant {
        background: rgba(79,140,255,0.18);
        color: #9ec1ff;
        border: 1px solid rgba(79,140,255,0.26);
    }
    .msg-files {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 6px;
    }
    .msg-file-badge {
        display: flex;
        align-items: center;
        gap: 5px;
        background: rgba(79,140,255,0.18);
        border: 1px solid rgba(79,140,255,0.28);
        border-radius: 6px;
        padding: 3px 8px;
        font-size: 12px;
        color: #c9ddff;
    }
    .st-key-pg_dock {
            position: sticky;
            bottom: 3.8rem;
            z-index: 20;
            padding: 0.45rem 0 0.15rem 0;
            transform: translateY(-14px);
            backdrop-filter: blur(8px);
            background: transparent;
            border-top: none;
        }
        .st-key-pg_dock > div {
            background: linear-gradient(180deg, rgba(18, 28, 48, 0.95) 0%, rgba(11, 19, 35, 0.96) 100%);
            border: 1px solid var(--pg-border);
            border-radius: 16px;
            padding: 0.55rem 0.7rem 0.6rem 0.7rem;
            box-shadow: 0 16px 35px rgba(0, 0, 0, 0.35);
        }
        .st-key-pg_dock [data-testid="stHorizontalBlock"] {
            align-items: center;
        }
        .st-key-pg_dock [data-testid="stForm"] {
            background: rgba(11, 18, 33, 0.55);
            border: 1px solid rgba(148, 163, 184, 0.28);
            border-radius: 14px;
            padding: 0.4rem 0.45rem 0.3rem 0.45rem;
        }
        .st-key-pg_dock [data-baseweb="select"] {
            margin-bottom: 0;
        }
        .st-key-pg_dock [data-baseweb="select"] > div {
            border-radius: 999px !important;
            border-color: rgba(148, 163, 184, 0.35) !important;
            background: rgba(13, 22, 39, 0.92) !important;
            min-height: 2.25rem;
            max-height: 2.25rem;
            box-shadow: none !important;
        }
        .st-key-pg_dock [data-baseweb="select"] span {
            font-size: 0.84rem;
        }
        .status-pill {
            width: 100%;
            height: 2.25rem;
            border-radius: 999px;
            display: flex;
            align-items: center;
            padding: 0 0.9rem;
            font-weight: 600;
            border: 1px solid rgba(148, 163, 184, 0.28);
            box-sizing: border-box;
            margin-top: 0.15rem;
        }
        .status-pill.idle {
            color: #8ff3b4;
            background: rgba(16, 122, 72, 0.24);
        }
        .status-pill.streaming {
            color: #93c5fd;
            background: rgba(29, 78, 216, 0.28);
        }
        .attachments-list {
            margin-top: 0.5rem;
            padding-top: 0.5rem;
            border-top: 1px solid rgba(255, 255, 255, 0.2);
        }
        .attachment-item {
            display: flex;
            align-items: center;
            padding: 0.4rem 0.6rem;
            margin-bottom: 0.3rem;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            font-size: 0.9rem;
        }
        .attachment-name {
            margin-left: 0.5rem;
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .attachment-size {
            margin-left: 0.5rem;
            opacity: 0.8;
            font-size: 0.8rem;
        }
        .message-text {
            margin-bottom: 0.3rem;
        }
        .attachment-preview-container {
            margin-top: 0.5rem;
            padding: 0.5rem;
            background: rgba(0, 0, 0, 0.1);
            border-radius: 8px;
        }
        .attachment-badge {
            display: inline-flex;
            align-items: center;
            padding: 0.2rem 0.5rem;
            margin-right: 0.3rem;
            margin-bottom: 0.3rem;
            background: rgba(59, 130, 246, 0.2);
            border: 1px solid rgba(59, 130, 246, 0.3);
            border-radius: 4px;
            font-size: 0.8rem;
        }
        .attachment-badge .remove-btn {
            margin-left: 0.4rem;
            cursor: pointer;
            opacity: 0.7;
        }
        .attachment-badge .remove-btn:hover {
            opacity: 1;
        }
        /* ── Compact file uploader in dock ── */
    .st-key-pg_dock [data-testid="stFileUploader"] [data-testid="stFileUploaderFile"],
    .st-key-pg_dock [data-testid="stFileUploader"] [data-testid="stFileInfo"] {
        display: none !important;
    }
    .st-key-pg_dock [data-testid="stFileUploader"] {
        min-height: 2.5rem;
        max-height: 2.5rem;
        display: flex;
        align-items: center;
        margin: 0;
        padding: 0;
    }
    .st-key-pg_dock [data-testid="stFileUploader"] > div {
        margin: 0;
        padding: 0;
    }
    .st-key-pg_dock [data-testid="stFileUploaderDropzone"] {
        min-height: 2.5rem;
        max-height: 2.5rem;
        padding: 0;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .st-key-pg_dock [data-testid="stFileUploaderDropzone"] > section {
        padding: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 2.5rem;
        max-height: 2.5rem;
    }
    .st-key-pg_dock [data-testid="stFileUploaderDropzoneInput"] {
        font-size: 0.75rem;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #bdd1f3;
    }
    .st-key-pg_dock input[type="text"] {
        min-height: 2.7rem;
        border-radius: 999px !important;
        border: 1px solid rgba(148, 163, 184, 0.35) !important;
        background: rgba(13, 22, 39, 0.92) !important;
        color: #dbe5f5 !important;
        padding-left: 1rem !important;
    }
    .st-key-pg_dock button[kind="secondaryFormSubmit"],
    .st-key-pg_dock button[kind="primaryFormSubmit"],
    .st-key-pg_dock button[kind="primary"] {
        min-height: 2.7rem;
        border-radius: 999px !important;
        font-weight: 600 !important;
    }
    .st-key-pg_dock [data-testid="stForm"] {
        margin-top: 0.3rem;
    }
    .st-key-pg_dock [data-testid="stFileUploaderDropzone"] [data-testid="stFileUploaderDropzoneInstructions"] {
        display: none !important;
    }
    </style>
        """,
        unsafe_allow_html=True,
    )


def _render_session_panel(
    state: dict[str, Any],
    provider_names: list[str],
    provider_to_key: dict[str, tuple[Provider, APIKey]],
) -> None:
    current_session_id = state["current_session_id"]
    current_session = state["sessions"][current_session_id]

    if current_session.get("provider") not in provider_names:
        current_session["provider"] = provider_names[0]

    selected_provider_name = current_session["provider"]
    selected_provider, selected_key = provider_to_key[selected_provider_name]
    text_models = _get_text_models(selected_provider, selected_key)

    if current_session.get("model") not in text_models:
        current_session["model"] = text_models[0] if text_models else None

    with _WORKER_LOCK:
        message_snapshot = [dict(item) for item in current_session["messages"]]
        current_streaming = bool(current_session.get("is_streaming", False))


    chat_scroll = st.container(height=560, key="pg_chat_scroll", autoscroll=True)
    with chat_scroll:
        _render_messages(message_snapshot, current_streaming)

    with st.container(key="pg_dock"):
        # ── Row 1: provider | model | upload | stop/idle ──
        cfg1, cfg2, cfg3, cfg4 = st.columns([1.2, 1.6, 0.8, 1.0])
        with cfg1:
            provider_choice = st.selectbox(
                "供应商",
                options=provider_names,
                index=provider_names.index(current_session["provider"]),
                key=f"provider_picker_{current_session_id}",
                label_visibility="collapsed",
                placeholder="选择供应商",
            )
            if provider_choice != current_session["provider"]:
                current_session["provider"] = provider_choice
                current_session["model"] = None
                _upsert_session_to_db(current_session)
                st.rerun()

            selected_provider, selected_key = provider_to_key[current_session["provider"]]
            text_models = _get_text_models(selected_provider, selected_key)

        with cfg2:
            if not text_models:
                st.warning("当前供应商未获取到文本模型。")
                selected_model = None
            else:
                selected_model = st.selectbox(
                    "模型",
                    options=text_models,
                    index=text_models.index(current_session["model"]) if current_session["model"] in text_models else 0,
                    key=f"model_picker_{current_session_id}",
                    label_visibility="collapsed",
                    placeholder="选择模型",
                )
                if current_session["model"] != selected_model:
                    current_session["model"] = selected_model
                    _upsert_session_to_db(current_session)

        with cfg3:
            uploader_key = f"file_uploader_{current_session_id}"
            uploader_counter = state.get("file_uploader_counters", {}).get(uploader_key, 0)
            uploaded_files = st.file_uploader(
                "📎",
                type=["jpg", "jpeg", "png", "gif", "webp", "bmp", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "md"],
                key=f"{uploader_key}_{uploader_counter}",
                accept_multiple_files=True,
                label_visibility="collapsed",
            )

        with cfg4:
            if current_streaming:
                if st.button("⏹ 停止", key=f"stop_btn_{current_session_id}", use_container_width=True, type="primary"):
                    _stop_stream_worker(current_session_id)
                    st.rerun()
            else:
                st.markdown('<div class="status-pill idle">空闲</div>', unsafe_allow_html=True)

        # ── Process uploaded files and reset uploader ──
        if uploaded_files:
            for uploaded_file in uploaded_files:
                filename = uploaded_file.name
                try:
                    file_size = uploaded_file.size
                except AttributeError:
                    file_bytes = uploaded_file.read()
                    file_size = len(file_bytes)
                    uploaded_file.seek(0)

                already_exists = any(
                    _get_attachment_data(e.get("id")) is not None
                    and _get_attachment_data(e.get("id")).get("filename") == filename
                    and _get_attachment_data(e.get("id")).get("file_size") == file_size
                    for e in state.get("pending_attachments", [])
                )
                if not already_exists:
                    try:
                        uploaded_file.seek(0)
                    except Exception:
                        pass
                    file_bytes = uploaded_file.read()
                    mime_type = uploaded_file.type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    att_id = _save_attachment(file_bytes, filename, mime_type)
                    if "pending_attachments" not in state:
                        state["pending_attachments"] = []
                    state["pending_attachments"].append({"id": att_id})

            # Reset file uploader by incrementing counter
            if "file_uploader_counters" not in state:
                state["file_uploader_counters"] = {}
            uploader_key = f"file_uploader_{current_session_id}"
            state["file_uploader_counters"][uploader_key] = state.get("file_uploader_counters", {}).get(uploader_key, 0) + 1
            st.rerun()

        # ── Row 2: attachment list with clear button ──
        pending_attachments = state.get("pending_attachments", [])
        if pending_attachments:
            chip_parts = []
            for att in pending_attachments:
                att_data = _get_attachment_data(att.get("id"))
                if att_data:
                    fname = att_data.get("filename", "?")
                    sz = _format_file_size(att_data.get("file_size", 0))
                    icon = "\U0001f5bc\ufe0f" if att_data.get("attachment_type") == "image" else "\U0001f4ce"
                    chip_parts.append(
                        f'<span class="msg-file-badge">{icon} {html.escape(fname)[:20]} ({sz})</span>'
                    )

            if chip_parts:
                # Create a single row with attachments on left, clear button on right
                att_col1, att_col2 = st.columns([8, 2])
                with att_col1:
                    st.markdown(
                        f'<div class="msg-files" style="display: flex; flex-wrap: nowrap; overflow-x: auto; gap: 6px; margin-bottom: 0;">{" ".join(chip_parts)}</div>',
                        unsafe_allow_html=True,
                    )
                with att_col2:
                    if st.button("清空附件", key=f"clear_attachments_{current_session_id}", use_container_width=True):
                        state["pending_attachments"] = []
                        # Reset file uploader counter as well
                        if "file_uploader_counters" not in state:
                            state["file_uploader_counters"] = {}
                        uploader_key = f"file_uploader_{current_session_id}"
                        state["file_uploader_counters"][uploader_key] = state.get("file_uploader_counters", {}).get(uploader_key, 0) + 1
                        st.rerun()

        # ── Row 3: input + send (inside form) ──
        with st.form(key=f"chat_form_{current_session_id}", clear_on_submit=True, border=False):
            input_col, send_col = st.columns([12, 1.6])
            with input_col:
                prompt = st.text_input(
                    "输入消息",
                    key=f"chat_input_{current_session_id}",
                    label_visibility="collapsed",
                    placeholder="输入消息…",
                    disabled=current_streaming or not bool(current_session.get("model")),
                )
            with send_col:
                submitted = st.form_submit_button(
                    "▶ 发送",
                    use_container_width=True,
                    disabled=current_streaming or not bool(current_session.get("model")),
                )

        # ── Handle form submission ──
        if submitted:
            pending_attachments = state.get("pending_attachments", [])
            has_content = bool(prompt and prompt.strip()) or bool(pending_attachments)

            if not has_content:
                st.warning("请输入消息或添加附件。")
            elif not current_session.get("model"):
                st.warning("请先选择模型。")
            else:
                text = prompt.strip() if prompt else ""
                if text:
                    state["input_history"].append(text)

                user_message = {
                    "role": "user",
                    "content": text,
                }

                current_session["messages"].append(user_message)
                current_session["messages"].append({"role": "assistant", "content": ""})
                current_session["is_streaming"] = True
                current_session["streaming_index"] = len(current_session["messages"]) - 1
                current_session["title"] = _format_title(current_session["messages"])
                user_idx = len(current_session["messages"]) - 2
                assistant_idx = len(current_session["messages"]) - 1
                _upsert_session_to_db(current_session)
                user_message_id = _upsert_message_to_db(
                    session_uid=current_session_id,
                    seq=user_idx,
                    role="user",
                    content=text,
                )

                if pending_attachments:
                    user_message["attachments"] = [dict(a) for a in pending_attachments]
                    for att in pending_attachments:
                        with SessionLocal() as session:
                            db_attachment = session.query(PlaygroundChatAttachment).filter(
                                PlaygroundChatAttachment.attachment_uid == att.get("id")
                            ).first()
                            if db_attachment:
                                db_attachment.session_uid = current_session_id
                                db_attachment.message_id = user_message_id
                                session.commit()

                _upsert_message_to_db(
                    session_uid=current_session_id,
                    seq=assistant_idx,
                    role="assistant",
                    content="",
                )

                state["pending_attachments"] = []
                # Reset file uploader counter so Streamlit creates a fresh uploader
                if "file_uploader_counters" not in state:
                    state["file_uploader_counters"] = {}
                uploader_key = f"file_uploader_{current_session_id}"
                state["file_uploader_counters"][uploader_key] = state.get("file_uploader_counters", {}).get(uploader_key, 0) + 1

                payload_messages = _build_api_messages(current_session["messages"][:-1])
                _start_stream_worker(
                    state=state,
                    session_id=current_session_id,
                    provider=selected_provider,
                    key=selected_key,
                    model_name=current_session["model"],
                    payload_messages=payload_messages,
                )
                st.rerun()

def render_playground_page() -> None:
    import os

    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    if proxy_host == "0.0.0.0":
        proxy_host = "127.0.0.1"
    proxy_port = os.getenv("PROXY_PORT", "8000")
    static_dir = os.path.join(os.path.dirname(__file__), "playground_web_static")
    version_files = [
        __file__,
        os.path.join(static_dir, "index.html"),
        os.path.join(static_dir, "style.css"),
        os.path.join(static_dir, "app.js"),
    ]
    ui_ver = int(max(os.path.getmtime(p) for p in version_files if os.path.exists(p)))
    def _normalize_theme(value: Any) -> str | None:
        if value is None:
            return None
        v = str(value).strip().lower()
        if "light" in v:
            return "light"
        if "dark" in v:
            return "dark"
        return None

    theme_type: str | None = None
    try:
        ctx_theme = getattr(st.context, "theme", None)
        theme_type = _normalize_theme(getattr(ctx_theme, "type", None))
        if theme_type is None:
            theme_type = _normalize_theme(getattr(ctx_theme, "base", None))
    except Exception:
        theme_type = None

    if theme_type is None:
        theme_type = _normalize_theme(st.get_option("theme.base")) or "dark"
    ui_url = f"http://{proxy_host}:{proxy_port}/playground-ui?v={ui_ver}&theme={theme_type}"

    if "playground_last_theme_type" not in st.session_state:
        st.session_state.playground_last_theme_type = theme_type

    if hasattr(st, "fragment"):
        @st.fragment(run_every="1s")
        def _theme_watcher() -> None:
            current_theme: str | None = None
            try:
                ctx_theme = getattr(st.context, "theme", None)
                current_theme = _normalize_theme(getattr(ctx_theme, "type", None))
                if current_theme is None:
                    current_theme = _normalize_theme(getattr(ctx_theme, "base", None))
            except Exception:
                current_theme = None
            if current_theme is None:
                current_theme = _normalize_theme(st.get_option("theme.base")) or "dark"

            if current_theme != st.session_state.get("playground_last_theme_type"):
                st.session_state.playground_last_theme_type = current_theme
                st.rerun()

        _theme_watcher()

    st.markdown(
        """
        <style>
        .block-container {
            max-width: 100% !important;
            padding: 0 !important;
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
        }
        section[data-testid="stSidebar"] {
            z-index: 1000 !important;
        }
        header[data-testid="stHeader"] {
            z-index: 1001 !important;
        }
        /* Make st.iframe stretch to fill available height */
        div[data-testid="stIFrame"] {
            flex: 1;
            min-height: 0;
        }
        div[data-testid="stIFrame"] > div {
            height: 100% !important;
        }
        div[data-testid="stIFrame"] iframe {
            height: 100% !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.iframe(ui_url, width="stretch", height="stretch")
    return

    provider_to_key = _get_provider_to_key()
    if not provider_to_key:
        st.warning("暂无配置了有效 API Key 的供应商，请先在供应商/API Key 页面完成配置。")
        return

    provider_names = list(provider_to_key.keys())
    _ensure_state(provider_names)
    state = st.session_state.playground_state

    if (
        state["current_session_id"] not in state["sessions"]
        and state["sessions"]
    ):
        state["current_session_id"] = next(iter(state["sessions"].keys()))

    with st.sidebar:
        st.markdown("### 对话历史")
        if st.button("➕ 新建对话", use_container_width=True):
            _create_new_session(provider_names)
            st.rerun()

        st.markdown("---")
        for session_id in list(reversed(list(state["sessions"].keys()))):
            session = state["sessions"][session_id]
            title = session.get("title") or "新对话"
            status = " ⏳" if session.get("is_streaming") else ""
            is_active = session_id == state["current_session_id"]

            col_a, col_b = st.columns([5, 1])
            with col_a:
                if st.button(
                    f"💬 {title}{status}",
                    key=f"switch_{session_id}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    state["current_session_id"] = session_id
                    st.rerun()
            with col_b:
                if st.button("🗑", key=f"drop_{session_id}"):
                    if session.get("is_streaming"):
                        st.warning("该对话正在生成中，暂不支持删除。")
                    else:
                        _delete_session_from_db(session_id)
                        del state["sessions"][session_id]
                        if not state["sessions"]:
                            _create_new_session(provider_names)
                        elif state["current_session_id"] == session_id:
                            state["current_session_id"] = next(iter(state["sessions"].keys()))
                        st.rerun()

    has_streaming_session = any(s.get("is_streaming") for s in state["sessions"].values())

    if hasattr(st, "fragment"):
        run_every = "800ms" if has_streaming_session else None

        @st.fragment(run_every=run_every)
        def _live_panel() -> None:
            _render_session_panel(
                state=state,
                provider_names=provider_names,
                provider_to_key=provider_to_key,
            )

        _live_panel()
    else:
        _render_session_panel(
            state=state,
            provider_names=provider_names,
            provider_to_key=provider_to_key,
        )
