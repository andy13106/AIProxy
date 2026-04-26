import datetime
import html
import json
import re
import threading
from typing import Any

import requests
import streamlit as st

from db import (
    APIKey,
    PlaygroundChatMessage,
    PlaygroundChatSession,
    Provider,
    SessionLocal,
)
from utils import classify_model_type, fetch_models, log_custom_usage


_WORKER_LOCK = threading.Lock()
_WORKER_THREADS: dict[str, threading.Thread] = {}


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


def _upsert_message_to_db(session_uid: str, seq: int, role: str, content: str) -> None:
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
        else:
            db_obj.role = role
            db_obj.content = content or ""
        session.commit()


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


def _build_api_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload_messages: list[dict[str, str]] = []
    for msg in messages:
        role = msg.get("role")
        if role not in {"user", "assistant", "system"}:
            continue
        content = str(msg.get("content") or "")
        payload_messages.append({"role": role, "content": content})
    return payload_messages


def _start_stream_worker(
    state: dict[str, Any],
    session_id: str,
    provider: Provider,
    key: APIKey,
    model_name: str,
    payload_messages: list[dict[str, str]],
) -> None:
    def run_stream() -> None:
        usage_data: dict[str, Any] = {}
        try:
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
                if stream_idx is not None and 0 <= stream_idx < len(session["messages"]):
                    if not session["messages"][stream_idx].get("content"):
                        session["messages"][stream_idx]["content"] = "(空响应)"
                session["is_streaming"] = False
                session["streaming_index"] = None

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
            with _WORKER_LOCK:
                _WORKER_THREADS.pop(session_id, None)

    with _WORKER_LOCK:
        existing = _WORKER_THREADS.get(session_id)
        if existing and existing.is_alive():
            return
        worker = threading.Thread(target=run_stream, daemon=True, name=f"playground_stream_{session_id}")
        _WORKER_THREADS[session_id] = worker
        worker.start()


def _render_user_message(content: str) -> None:
    safe_text = html.escape(content or "")
    st.markdown(
        f"""
        <div class="chat-row chat-row-user">
            <div class="chat-bubble chat-bubble-user">{safe_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_assistant_message(content: str, is_streaming: bool = False) -> None:
    text = content or ""
    thinking_parts = THINKING_PATTERN.findall(text)
    content_without_thinking = THINKING_PATTERN.sub("", text).strip()

    for idx, think in enumerate(thinking_parts):
        with st.expander(f"思考过程 #{idx + 1}", expanded=False):
            st.markdown(think)

    if not content_without_thinking and is_streaming:
        st.markdown("▌")
        return

    cursor = 0
    for match in CODE_BLOCK_PATTERN.finditer(content_without_thinking):
        start, end = match.span()
        if start > cursor:
            plain_text = content_without_thinking[cursor:start]
            if plain_text.strip():
                st.markdown(plain_text)

        lang = (match.group(1) or "text").strip()
        code = match.group(2) or ""
        st.code(code, language=lang)
        cursor = end

    if cursor < len(content_without_thinking):
        tail = content_without_thinking[cursor:]
        if tail.strip() or is_streaming:
            st.markdown(tail + ("\n\n▌" if is_streaming else ""))


def _render_error_message(content: str) -> None:
    st.error(content or "未知错误")


def _render_messages(messages: list[dict[str, Any]], current_session_streaming: bool) -> None:
    if not messages:
        st.markdown(
            """
            <div class="chat-empty-state">
                <h3>开始一个新对话</h3>
                <p>在下方输入框发送消息，支持流式回复、代码块和思考折叠。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "user":
            _render_user_message(content)
        elif role == "assistant":
            is_streaming = current_session_streaming and idx == len(messages) - 1
            if is_streaming and not str(content).strip():
                st.markdown(
                    '<div class="chat-row chat-row-assistant"><div class="chat-stream-cursor">▌</div></div>',
                    unsafe_allow_html=True,
                )
            else:
                _render_assistant_message(content, is_streaming=is_streaming)
        elif role == "system":
            st.info(content)
        elif role == "error":
            _render_error_message(content)


def _apply_page_style() -> None:
    st.markdown(
        """
        <style>
        html, body {
            overflow: hidden !important;
        }
        [data-testid="stAppViewContainer"] {
            overflow: hidden !important;
        }
        [data-testid="stMain"] {
            overflow: hidden !important;
        }
        .block-container {
            padding-top: 0.55rem;
            padding-bottom: 9.5rem;
            max-width: 1380px;
            height: calc(100vh - 1rem);
            overflow: hidden;
        }
        .block-container h3 {
            margin-top: 0.1rem;
            margin-bottom: 0.25rem;
        }
        [data-testid="stCaptionContainer"] {
            margin-top: 0;
            margin-bottom: 0.35rem;
        }
        .chat-row {
            display: flex;
            width: 100%;
            margin-bottom: 0.85rem;
        }
        .chat-row-user {
            justify-content: flex-end;
        }
        .chat-row-assistant {
            justify-content: flex-start;
        }
        .chat-bubble {
            border-radius: 16px;
            padding: 0.75rem 0.95rem;
            word-break: break-word;
            line-height: 1.5;
        }
        .chat-bubble-user {
            max-width: 75%;
            background: #1f6feb;
            color: #fff;
            border-bottom-right-radius: 4px;
        }
        .chat-bubble-assistant {
            width: 100%;
            background: #f6f8fb;
            border: 1px solid #e6ebf2;
            border-bottom-left-radius: 4px;
        }
        .chat-empty-state {
            text-align: center;
            margin-top: 3.5rem;
            color: #6b7280;
        }
        .chat-stream-cursor {
            color: #f8fafc;
            font-size: 1.2rem;
            line-height: 1;
            padding: 0.1rem 0.3rem;
        }
        .chat-meta {
            font-size: 0.85rem;
            color: #6b7280;
            margin-bottom: 0.35rem;
        }
        .st-key-pg_dock {
            position: sticky;
            bottom: 4.0rem;
            z-index: 20;
            padding: 0.55rem 0 0.2rem 0;
            transform: translateY(-14px);
            backdrop-filter: blur(6px);
            background: rgba(10, 14, 30, 0.72);
            border-top: 1px solid rgba(255, 255, 255, 0.08);
        }
        .st-key-pg_dock [data-baseweb="select"] {
            margin-bottom: 0;
        }
        .status-pill {
            width: 100%;
            height: 2.5rem;
            border-radius: 8px;
            display: flex;
            align-items: center;
            padding: 0 0.9rem;
            font-weight: 600;
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-sizing: border-box;
            margin-top: 0.15rem;
        }
        .status-pill.idle {
            color: #86efac;
            background: rgba(21, 128, 61, 0.28);
        }
        .status-pill.streaming {
            color: #93c5fd;
            background: rgba(29, 78, 216, 0.28);
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

    st.markdown("### 模型体验")
    st.caption("底部输入，支持流式回复。切换历史对话时，后台生成会持续进行。")

    with _WORKER_LOCK:
        message_snapshot = [dict(item) for item in current_session["messages"]]
        current_streaming = bool(current_session.get("is_streaming", False))


    chat_scroll = st.container(height=610, key="pg_chat_scroll", autoscroll=True)
    with chat_scroll:
        _render_messages(message_snapshot, current_streaming)

    with st.container(key="pg_dock"):
        config_col1, config_col2, config_col3 = st.columns([1.2, 1.6, 1.2])
        with config_col1:
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

        with config_col2:
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

        with config_col3:
            if current_streaming:
                st.markdown('<div class="status-pill streaming">生成中</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="status-pill idle">空闲</div>', unsafe_allow_html=True)

        with st.form(key=f"chat_form_{current_session_id}", clear_on_submit=True, border=False):
            input_col, send_col = st.columns([12, 1.6])
            with input_col:
                prompt = st.text_input(
                    "输入消息",
                    key=f"chat_input_{current_session_id}",
                    label_visibility="collapsed",
                    placeholder="输入消息（Enter 发送）",
                    disabled=current_streaming or not bool(current_session.get("model")),
                )
            with send_col:
                submitted = st.form_submit_button(
                    "发送",
                    use_container_width=True,
                    disabled=current_streaming or not bool(current_session.get("model")),
                )

        if submitted and prompt:
            if not current_session.get("model"):
                st.warning("请先选择模型。")
            else:
                text = prompt.strip()
                if text:
                    state["input_history"].append(text)
                    current_session["messages"].append({"role": "user", "content": text})
                    current_session["messages"].append({"role": "assistant", "content": ""})
                    current_session["is_streaming"] = True
                    current_session["streaming_index"] = len(current_session["messages"]) - 1
                    current_session["title"] = _format_title(current_session["messages"])
                    user_idx = len(current_session["messages"]) - 2
                    assistant_idx = len(current_session["messages"]) - 1
                    _upsert_session_to_db(current_session)
                    _upsert_message_to_db(
                        session_uid=current_session_id,
                        seq=user_idx,
                        role="user",
                        content=text,
                    )
                    _upsert_message_to_db(
                        session_uid=current_session_id,
                        seq=assistant_idx,
                        role="assistant",
                        content="",
                    )

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
    _apply_page_style()

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
