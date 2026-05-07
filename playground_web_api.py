import asyncio
import base64
import datetime
import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from db import (
    APIKey,
    PlaygroundChatAttachment,
    PlaygroundChatMessage,
    PlaygroundChatSession,
    Provider,
    SessionLocal,
)
from utils import classify_model_type, fetch_models, log_custom_usage

router = APIRouter()

_STATIC_DIR = Path(__file__).parent / "playground_web_static"
_ATTACHMENT_DIR = Path(__file__).parent / "data" / "attachments"
_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)

_MODEL_CACHE: dict[str, list[str]] = {}
_MODEL_CACHE_TS: dict[str, datetime.datetime] = {}
_MODEL_CACHE_TTL_SEC = 120
_STREAM_STOP_FLAGS: dict[str, bool] = {}
_NO_STORE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


def _now_utc() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _save_attachment(file_data: bytes, filename: str, mime_type: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    attachment_id = str(uuid.uuid4())
    saved_filename = f"{attachment_id}{ext}"
    file_path = _ATTACHMENT_DIR / saved_filename
    with open(file_path, "wb") as f:
        f.write(file_data)

    attachment_type = "document"
    if mime_type.startswith("image/"):
        attachment_type = "image"

    with SessionLocal() as session:
        session.add(
            PlaygroundChatAttachment(
                attachment_uid=attachment_id,
                filename=filename,
                file_path=str(file_path),
                file_size=len(file_data),
                mime_type=mime_type,
                attachment_type=attachment_type,
            )
        )
        session.commit()
    return attachment_id


def _get_attachment_data(attachment_id: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        db_attachment = (
            session.query(PlaygroundChatAttachment)
            .filter(PlaygroundChatAttachment.attachment_uid == attachment_id)
            .first()
        )
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


def _provider_to_key() -> dict[str, tuple[Provider, APIKey]]:
    with SessionLocal() as session:
        providers = session.query(Provider).all()
        active_keys = session.query(APIKey).filter(APIKey.is_active.is_(True)).all()
    result: dict[str, tuple[Provider, APIKey]] = {}
    for provider in providers:
        key = next((k for k in active_keys if k.provider_id == provider.id), None)
        if key:
            result[provider.name] = (provider, key)
    return result


def _get_text_models(provider: Provider, key: APIKey) -> list[str]:
    cache_key = f"{provider.id}:{key.id}"
    now = _now_utc()
    ts = _MODEL_CACHE_TS.get(cache_key)
    if ts and (now - ts).total_seconds() < _MODEL_CACHE_TTL_SEC and cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]
    models = fetch_models(provider.api_base, key.key)
    text_models = [m for m in models if classify_model_type(m) == "text"]
    _MODEL_CACHE[cache_key] = text_models
    _MODEL_CACHE_TS[cache_key] = now
    return text_models


def _format_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            text = str(msg["content"]).strip()
            if text:
                return text[:30] + ("..." if len(text) > 30 else "")
    return "新对话"


def _upsert_session(session_data: dict[str, Any]) -> None:
    now = _now_utc()
    with SessionLocal() as session:
        db_obj = (
            session.query(PlaygroundChatSession)
            .filter(PlaygroundChatSession.session_uid == session_data["id"])
            .first()
        )
        if db_obj is None:
            session.add(
                PlaygroundChatSession(
                    session_uid=session_data["id"],
                    title=session_data.get("title") or "新对话",
                    provider_name=session_data.get("provider"),
                    model_name=session_data.get("model"),
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            db_obj.title = session_data.get("title") or "新对话"
            db_obj.provider_name = session_data.get("provider")
            db_obj.model_name = session_data.get("model")
            db_obj.updated_at = now
        session.commit()


def _upsert_message(session_uid: str, seq: int, role: str, content: str) -> int:
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


def _delete_session(session_uid: str) -> None:
    with SessionLocal() as session:
        messages = session.query(PlaygroundChatMessage).filter(
            PlaygroundChatMessage.session_uid == session_uid
        ).all()
        message_ids = [msg.id for msg in messages]
        if message_ids:
            session.query(PlaygroundChatAttachment).filter(
                PlaygroundChatAttachment.message_id.in_(message_ids)
            ).delete(synchronize_session=False)
        session.query(PlaygroundChatMessage).filter(
            PlaygroundChatMessage.session_uid == session_uid
        ).delete()
        session.query(PlaygroundChatSession).filter(
            PlaygroundChatSession.session_uid == session_uid
        ).delete()
        session.commit()


def _load_sessions(provider_names: list[str]) -> tuple[dict[str, dict[str, Any]], str | None]:
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
                "provider": provider,
                "model": db_item.model_name,
                "messages": [],
            }
            db_messages = (
                session.query(PlaygroundChatMessage)
                .filter(PlaygroundChatMessage.session_uid == sid)
                .order_by(PlaygroundChatMessage.seq.asc(), PlaygroundChatMessage.id.asc())
                .all()
            )
            for msg in db_messages:
                msg_data: dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
                attachments = (
                    session.query(PlaygroundChatAttachment)
                    .filter(PlaygroundChatAttachment.message_id == msg.id)
                    .all()
                )
                if attachments:
                    msg_data["attachments"] = [
                        {
                            "id": att.attachment_uid,
                            "filename": att.filename,
                            "file_size": att.file_size,
                            "attachment_type": att.attachment_type,
                        }
                        for att in attachments
                    ]
                sess["messages"].append(msg_data)
            sessions[sid] = sess
            current_id = sid
    return sessions, current_id


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
                b64 = _encode_image_to_base64(att_data["file_path"])
                mime_type = att_data.get("mime_type") or "image/jpeg"
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    }
                )
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


@router.get("/playground-ui")
def playground_ui() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html", headers=_NO_STORE_HEADERS)


@router.get("/playground-ui/style.css")
def playground_ui_css() -> FileResponse:
    return FileResponse(_STATIC_DIR / "style.css", headers=_NO_STORE_HEADERS)


@router.get("/playground-ui/app.js")
def playground_ui_js() -> FileResponse:
    return FileResponse(_STATIC_DIR / "app.js", headers=_NO_STORE_HEADERS)


@router.get("/playground-api/bootstrap")
def playground_bootstrap() -> dict[str, Any]:
    provider_map = _provider_to_key()
    if not provider_map:
        return {"providers": [], "sessions": {}, "current_session_id": None}
    providers = []
    for name, (provider, key) in provider_map.items():
        providers.append({"name": name, "models": _get_text_models(provider, key)})
    provider_names = [p["name"] for p in providers]
    sessions, current_id = _load_sessions(provider_names)
    if not sessions:
        sid = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        default_provider = provider_names[0]
        default_model = next((p["models"][0] for p in providers if p["name"] == default_provider and p["models"]), None)
        new_session = {"id": sid, "title": "新对话", "provider": default_provider, "model": default_model}
        _upsert_session(new_session)
        sessions[sid] = {**new_session, "messages": []}
        current_id = sid
    return {"providers": providers, "sessions": sessions, "current_session_id": current_id}


@router.post("/playground-api/session/new")
def playground_new_session(payload: dict[str, Any]) -> dict[str, Any]:
    provider = payload.get("provider")
    model = payload.get("model")
    sid = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    session_data = {"id": sid, "title": "新对话", "provider": provider, "model": model}
    _upsert_session(session_data)
    return {"session": {**session_data, "messages": []}}


@router.delete("/playground-api/session/{session_id}")
def playground_drop_session(session_id: str) -> dict[str, bool]:
    _delete_session(session_id)
    return {"ok": True}


@router.post("/playground-api/upload")
async def playground_upload(file: UploadFile) -> dict[str, Any]:
    raw = await file.read()
    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    att_id = _save_attachment(raw, file.filename or "upload.bin", mime_type)
    data = _get_attachment_data(att_id)
    if not data:
        raise HTTPException(status_code=500, detail="附件保存失败")
    return {
        "id": data["id"],
        "filename": data["filename"],
        "file_size": data["file_size"],
        "attachment_type": data["attachment_type"],
    }


@router.post("/playground-api/chat/stop/{stream_id}")
def playground_stop_stream(stream_id: str) -> dict[str, bool]:
    _STREAM_STOP_FLAGS[stream_id] = True
    return {"ok": True}


@router.post("/playground-api/chat/stream")
async def playground_chat_stream(payload: dict[str, Any]) -> StreamingResponse:
    session_id = payload.get("session_id")
    provider_name = payload.get("provider")
    model_name = payload.get("model")
    prompt = str(payload.get("prompt") or "")
    attachment_ids = payload.get("attachment_ids") or []
    if not session_id or not provider_name or not model_name:
        raise HTTPException(status_code=400, detail="缺少必要参数")
    provider_map = _provider_to_key()
    if provider_name not in provider_map:
        raise HTTPException(status_code=400, detail="供应商不可用")
    provider, key = provider_map[provider_name]
    stream_id = f"stream_{uuid.uuid4().hex[:12]}"
    _STREAM_STOP_FLAGS[stream_id] = False

    with SessionLocal() as session:
        messages = (
            session.query(PlaygroundChatMessage)
            .filter(PlaygroundChatMessage.session_uid == session_id)
            .order_by(PlaygroundChatMessage.seq.asc(), PlaygroundChatMessage.id.asc())
            .all()
        )
        history: list[dict[str, Any]] = []
        max_seq = -1
        for msg in messages:
            if msg.seq > max_seq:
                max_seq = msg.seq
            m: dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
            atts = (
                session.query(PlaygroundChatAttachment)
                .filter(PlaygroundChatAttachment.message_id == msg.id)
                .all()
            )
            if atts:
                m["attachments"] = [{"id": a.attachment_uid} for a in atts]
            history.append(m)

        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if attachment_ids:
            user_msg["attachments"] = [{"id": i} for i in attachment_ids]
        history.append(user_msg)
        history.append({"role": "assistant", "content": ""})
        title = _format_title(history)
        _upsert_session({"id": session_id, "title": title, "provider": provider_name, "model": model_name})
        user_idx = max_seq + 1
        assistant_idx = max_seq + 2
        
        user_db_msg = (
            session.query(PlaygroundChatMessage)
            .filter(
                PlaygroundChatMessage.session_uid == session_id,
                PlaygroundChatMessage.seq == user_idx,
            )
            .first()
        )
        if user_db_msg is None:
            user_db_msg = PlaygroundChatMessage(
                session_uid=session_id,
                seq=user_idx,
                role="user",
                content=prompt or "",
            )
            session.add(user_db_msg)
            session.flush()
        else:
            # 复用消息时，先清空旧的附件关联，防止幽灵附件残留
            session.query(PlaygroundChatAttachment).filter(
                PlaygroundChatAttachment.message_id == user_db_msg.id
            ).update(
                {PlaygroundChatAttachment.message_id: None, PlaygroundChatAttachment.session_uid: None},
                synchronize_session=False
            )
            # 更新消息内容和角色
            user_db_msg.role = "user"
            user_db_msg.content = prompt or ""
        
        user_message_id = user_db_msg.id
        
        if attachment_ids:
            session.query(PlaygroundChatAttachment).filter(
                PlaygroundChatAttachment.message_id == user_message_id
            ).update(
                {PlaygroundChatAttachment.message_id: None, PlaygroundChatAttachment.session_uid: None},
                synchronize_session=False
            )
            for aid in attachment_ids:
                db_attachment = (
                    session.query(PlaygroundChatAttachment)
                    .filter(PlaygroundChatAttachment.attachment_uid == aid)
                    .first()
                )
                if db_attachment:
                    db_attachment.session_uid = session_id
                    db_attachment.message_id = user_message_id
        
        assistant_db_msg = (
            session.query(PlaygroundChatMessage)
            .filter(
                PlaygroundChatMessage.session_uid == session_id,
                PlaygroundChatMessage.seq == assistant_idx,
            )
            .first()
        )
        if assistant_db_msg is None:
            assistant_db_msg = PlaygroundChatMessage(
                session_uid=session_id,
                seq=assistant_idx,
                role="assistant",
                content="",
            )
            session.add(assistant_db_msg)
            session.flush()
        
        session.commit()
        
    api_messages = _build_api_messages(history[:-1])

    async def event_gen():
        usage_data: dict[str, Any] = {}
        full_text = ""
        url = f"{provider.api_base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {key.key}", "Content-Type": "application/json"}
        body = {
            "model": model_name,
            "messages": api_messages,
            "max_tokens": 4096,
            "temperature": 0.7,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code != 200:
                        msg = f"请求失败: HTTP {resp.status_code}"
                        yield f"event: error\ndata: {json.dumps({'message': msg}, ensure_ascii=False)}\n\n"
                        return
                    yield f"event: ready\ndata: {json.dumps({'stream_id': stream_id}, ensure_ascii=False)}\n\n"
                    async for raw_line in resp.aiter_lines():
                        if _STREAM_STOP_FLAGS.get(stream_id):
                            yield "event: done\ndata: {\"stopped\": true}\n\n"
                            break
                        if not raw_line or not raw_line.startswith("data: "):
                            continue
                        chunk_data = raw_line[6:]
                        if chunk_data.strip() == "[DONE]":
                            yield "event: done\ndata: {\"stopped\": false}\n\n"
                            break
                        try:
                            chunk = json.loads(chunk_data)
                        except json.JSONDecodeError:
                            continue
                        delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                        piece = delta.get("content") or ""
                        if piece:
                            full_text += piece
                            yield f"event: token\ndata: {json.dumps({'text': piece}, ensure_ascii=False)}\n\n"
                        if chunk.get("usage"):
                            usage_data = chunk["usage"]
                        await asyncio.sleep(0)
        finally:
            if _STREAM_STOP_FLAGS.get(stream_id):
                if full_text.strip():
                    full_text += "\n\n[用户已停止生成]"
                else:
                    full_text = "[用户已停止生成]"
            if not full_text.strip():
                full_text = "(空响应)"
            _upsert_message(session_id, assistant_idx, "assistant", full_text)
            _upsert_session({"id": session_id, "title": title, "provider": provider_name, "model": model_name})
            prompt_tokens = int(usage_data.get("prompt_tokens") or 0)
            completion_tokens = int(usage_data.get("completion_tokens") or 0)
            total_tokens = int(usage_data.get("total_tokens") or (prompt_tokens + completion_tokens))
            if total_tokens > 0:
                log_custom_usage(
                    key_id=key.id,
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            _STREAM_STOP_FLAGS.pop(stream_id, None)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
