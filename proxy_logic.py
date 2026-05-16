"""AI Proxy Gateway - 主入口"""

import datetime
import json
import time
import uuid
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from typing import Any, Optional

import litellm
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, delete

from config import logger, request_id_ctx, settings
from converters import (
    convert_anthropic_messages_to_openai,
    convert_anthropic_tool_choice,
    convert_anthropic_tools_to_openai,
    convert_openai_response_to_anthropic,
    extract_text_from_content_blocks,
    mask_secret,
)
from db import AsyncSessionLocal, ModelMapping, PreviousResponse, init_db
from services import (
    build_completion_params,
    clean_config_value,
    clear_key_failure,
    execute_image_generation,
    execute_nvidia_image_generation,
    get_active_keys,
    get_model_mapping,
    log_usage,
    mark_key_rate_limited,
    _check_circuit_breaker,
    _record_cb_success,
    _record_cb_failure,
)
from streaming import AnthropicToolStreamGenerator, StreamGenerator
from playground_web_api import router as playground_web_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="AI Proxy Gateway", lifespan=lifespan)
app.include_router(playground_web_router)
security = HTTPBearer(auto_error=False)

# --- 简易内存速率限制 ---
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)
_rate_limit_last_cleanup: float = 0.0
# previous_response_id 现在通过数据库持久化存储，不再使用内存 OrderedDict


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Per-IP 速率限制，RATE_LIMIT_PER_MINUTE=0 时不限制。
    注意：基于进程内存，多 worker 模式下每个进程独立计数，实际限流不精确。
    生产环境多 worker 时建议使用 Redis 等外部存储做限流。
    """
    global _rate_limit_last_cleanup
    limit = settings.rate_limit_per_minute
    if limit > 0:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - 60
        # 每 5 分钟清理一次过期 IP 条目，防止内存泄漏
        if now - _rate_limit_last_cleanup > 300:
            _rate_limit_last_cleanup = now
            stale = [ip for ip, ts in _rate_limit_buckets.items() if not ts or ts[-1] < cutoff]
            for ip in stale:
                del _rate_limit_buckets[ip]
        bucket = _rate_limit_buckets[client_ip]
        # 清理 60s 前的记录
        _rate_limit_buckets[client_ip] = bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= limit:
            return JSONResponse(
                status_code=429,
                content={"error": {"message": f"Rate limit exceeded: {limit} requests/min", "type": "rate_limit_error"}},
                headers={"Retry-After": "60"},
            )
        bucket.append(now)
    return await call_next(request)


def _resolve_timeout_value(request_value: object, default_value: float) -> float:
    """解析并约束超时参数，避免客户端把服务端默认超时压得过低。"""
    try:
        requested = float(request_value)
    except (TypeError, ValueError):
        return float(default_value)

    if requested <= 0:
        return float(default_value)

    if settings.allow_client_timeout_override:
        return requested

    return max(float(default_value), requested)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录所有请求的详细信息"""
    request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
    token = request_id_ctx.set(request_id)
    start_time = time.time()

    try:
        logger.info(f"REQUEST: {request.method} {request.url.path}")
        logger.debug(f"Query params: {dict(request.query_params)}")

        headers = dict(request.headers)
        if "authorization" in headers:
            auth_val = headers.get("authorization", "")
            if auth_val.lower().startswith("bearer "):
                auth_token = auth_val[7:]
                headers["authorization"] = f"Bearer {mask_secret(auth_token)}"
            else:
                headers["authorization"] = mask_secret(auth_val)
        if "x-api-key" in headers:
            headers["x-api-key"] = mask_secret(headers.get("x-api-key"))
        logger.debug(f"Headers: {headers}")

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        process_time = time.time() - start_time
        logger.info(f"RESPONSE: Status {response.status_code}, Time: {process_time:.2f}s")

        return response
    finally:
        request_id_ctx.reset(token)


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "service": "AI Proxy Gateway"}


async def verify_auth(credentials: HTTPAuthorizationCredentials = Security(security)):
    """验证认证"""
    if not settings.auth_enabled:
        return True
    if credentials is None:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    if credentials.credentials != settings.master_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return True


def _extract_api_key_from_request(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    x_api_key = request.headers.get("x-api-key")
    if x_api_key:
        return x_api_key.strip()
    return None


def _require_api_key_for_models(request: Request) -> None:
    if not settings.auth_enabled:
        return
    incoming_key = _extract_api_key_from_request(request)
    if not incoming_key or incoming_key != settings.master_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")


@app.get("/v1/models")
@app.get("/models")
@app.get("/v1/v1/models")
@app.get("/api/v1/models")
async def list_proxy_models(request: Request):
    """OpenAI 兼容模型列表接口，返回已映射的虚拟模型名"""
    _require_api_key_for_models(request)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ModelMapping.virtual_name).order_by(ModelMapping.order, ModelMapping.id))
        names = [row[0] for row in result.all() if row and row[0]]

    uniq_names = []
    seen = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            uniq_names.append(name)

    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": 0,
                "owned_by": "aiproxy",
            }
            for name in uniq_names
        ],
    }


@app.get("/v1/models/{model_id}")
async def get_proxy_model(model_id: str, request: Request):
    """OpenAI 兼容单模型信息接口"""
    _require_api_key_for_models(request)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ModelMapping.virtual_name).where(ModelMapping.virtual_name == model_id)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Model {model_id} not mapped.")
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "aiproxy",
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_proxy(request: Request, auth=Depends(verify_auth)):
    """OpenAI 兼容的聊天接口"""
    body = await request.json()
    logger.debug(f"/v1/chat/completions request - Model: {body.get('model')}, Stream: {body.get('stream', False)}")
    return await handle_completion(body, is_anthropic=False)


def _responses_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "input_text", "output_text", "refusal"):
            if content.get(key) is not None:
                return str(content.get(key))
        return "" if content is None else json.dumps(content, ensure_ascii=False)
    if not isinstance(content, list):
        return "" if content is None else str(content)

    text_parts = []
    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, dict):
            text = part.get("text")
            if text is None:
                text = part.get("input_text")
            if text is None:
                text = part.get("output_text")
            if text is not None:
                text_parts.append(str(text))
    return "\n".join(p for p in text_parts if p)


def _coerce_responses_input_items(input_data: Any) -> list[dict[str, Any]]:
    if isinstance(input_data, str):
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": input_data}]}]
    if isinstance(input_data, dict):
        return [input_data]
    if isinstance(input_data, list):
        return [item for item in input_data if isinstance(item, dict)]
    return []


def _responses_instructions_to_messages(instructions: Any) -> list[dict[str, Any]]:
    text = _responses_content_to_text(instructions)
    if not text:
        return []
    return [{"role": "system", "content": text}]


def _responses_input_to_messages(input_data: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in _coerce_responses_input_items(input_data):

        item_type = item.get("type")
        if item_type == "message" or "role" in item:
            role = item.get("role") or "user"
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            messages.append({"role": role, "content": _responses_content_to_text(item.get("content"))})
        elif item_type == "function_call_output":
            tool_call_id = item.get("call_id") or item.get("id") or ""
            if not tool_call_id:
                continue
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": _responses_content_to_text(item.get("output")),
                }
            )
        elif item_type == "function_call":
            arguments = item.get("arguments") or "{}"
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": item.get("name") or "tool",
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            )

    return messages


def _normalize_chat_messages_for_upstream(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    system_parts = []
    pending_tool_call_ids: set[str] = set()
    normalized = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "user"
        if role == "developer":
            role = "system"

        if role == "system":
            text = _responses_content_to_text(message.get("content"))
            if text:
                system_parts.append(text)
            continue

        msg = dict(message)
        msg["role"] = role if role in {"user", "assistant", "tool"} else "user"

        if msg["role"] == "assistant":
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                clean_tool_calls = []
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function") or {}
                    if not isinstance(function, dict):
                        continue
                    call_id = str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                    arguments = function.get("arguments") or "{}"
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    clean_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": str(function.get("name") or "tool"),
                                "arguments": arguments,
                            },
                        }
                    )
                    pending_tool_call_ids.add(call_id)
                if clean_tool_calls:
                    msg["tool_calls"] = clean_tool_calls
                else:
                    msg.pop("tool_calls", None)

        if msg["role"] == "tool":
            tool_call_id = str(msg.get("tool_call_id") or "")
            content = _responses_content_to_text(msg.get("content"))
            if not tool_call_id or tool_call_id not in pending_tool_call_ids:
                msg = {
                    "role": "user",
                    "content": f"Tool result ({tool_call_id or 'unknown'}):\n{content}",
                }
            else:
                msg["tool_call_id"] = tool_call_id
                msg["content"] = content
                pending_tool_call_ids.discard(tool_call_id)

        if msg["role"] in {"user", "assistant"} and "content" in msg:
            if msg.get("content") is not None:
                msg["content"] = _responses_content_to_text(msg.get("content"))

        normalized.append(msg)

    if system_parts:
        normalized.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})

    return normalized


def _responses_tool_choice_to_chat(tool_choice: Any) -> Any:
    if tool_choice in (None, "", "auto", "required", "none"):
        return "auto" if tool_choice == "required" else tool_choice
    if not isinstance(tool_choice, dict):
        return "auto"
    if tool_choice.get("type") == "function" and tool_choice.get("name"):
        return "auto"
    if tool_choice.get("type") == "function" and isinstance(tool_choice.get("function"), dict):
        return "auto"
    if tool_choice.get("type") == "allowed_tools":
        return "auto"
    return "auto"


def _responses_tools_to_chat_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []

    chat_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") not in {"function", "custom"}:
            continue
        name = tool.get("name")
        if not name:
            continue
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description") or "",
                    "parameters": (
                        tool.get("parameters")
                        if isinstance(tool.get("parameters"), dict)
                        else {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return chat_tools


def _responses_body_to_chat_body(body: dict[str, Any], input_items: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    if input_items is None:
        input_items = _build_responses_context(body)
    messages = _responses_instructions_to_messages(body.get("instructions"))
    messages.extend(_responses_input_to_messages(input_items))
    messages = _normalize_chat_messages_for_upstream(messages)

    chat_body = {
        "model": body.get("model"),
        "messages": messages,
        "stream": body.get("stream", False),
        "temperature": body.get("temperature", 0.7),
        "max_tokens": body.get("max_output_tokens") or body.get("max_tokens") or 4096,
    }

    tools = _responses_tools_to_chat_tools(body.get("tools"))
    if tools:
        chat_body["tools"] = tools
        if body.get("tool_choice"):
            chat_body["tool_choice"] = _responses_tool_choice_to_chat(body.get("tool_choice"))

    return chat_body


def _build_responses_context(body: dict[str, Any]) -> list[dict[str, Any]]:
    current_input = _coerce_responses_input_items(body.get("input"))
    if not current_input and isinstance(body.get("messages"), list):
        current_input = _coerce_responses_input_items(body.get("messages"))
    previous_response_id = body.get("previous_response_id")
    if not previous_response_id:
        return current_input

    previous = _responses_store.get(str(previous_response_id))
    if not previous:
        logger.warning(f"previous_response_id not found in in-memory store: {previous_response_id}")
        return current_input

    return list(previous.get("input") or []) + list(previous.get("output") or []) + current_input


async def _remember_response(response_payload: dict[str, Any], input_items: list[dict[str, Any]], body: dict) -> None:
    """将 previous_response 持久化到数据库"""
    response_id = response_payload.get("id")
    if not response_id:
        return

    output_items = response_payload.get("output", [])
    # 设置 TTL 为 24 小时
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=24)

    async with AsyncSessionLocal() as session:
        prev = PreviousResponse(
            response_id=str(response_id),
            input_items_json=json.dumps(input_items, ensure_ascii=False),
            output_items_json=json.dumps(output_items, ensure_ascii=False),
            body_json=json.dumps(body, ensure_ascii=False),
            expires_at=expires_at,
        )
        session.add(prev)
        await session.commit()

        # 清理过期记录
        await session.execute(
            delete(PreviousResponse).where(PreviousResponse.expires_at < datetime.datetime.utcnow())
        )
        await session.commit()


def _chat_response_to_responses(
    response: Any,
    model_name: str,
    body: Optional[dict[str, Any]] = None,
    input_items: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    body = body or {}
    input_items = input_items or []
    if hasattr(response, "model_dump"):
        response_data = response.model_dump()
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = json.loads(json.dumps(response, default=str))

    choice = (response_data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    output_items = []

    content = _responses_content_to_text(message.get("content"))
    if content:
        output_items.append(
            {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [],
                    }
                ],
            }
        )

    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        output_items.append(
            {
                "id": tool_call.get("id") or f"fc_{uuid.uuid4().hex[:24]}",
                "type": "function_call",
                "status": "completed",
                "call_id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": function.get("name") or "",
                "arguments": function.get("arguments") or "{}",
            }
        )

    usage = response_data.get("usage") or {}
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": body.get("instructions"),
        "max_output_tokens": body.get("max_output_tokens") or body.get("max_tokens"),
        "model": model_name,
        "input": input_items,
        "output": output_items,
        "output_text": content,
        "parallel_tool_calls": body.get("parallel_tool_calls", True),
        "previous_response_id": body.get("previous_response_id"),
        "reasoning": body.get("reasoning") or {"effort": None, "summary": None},
        "store": body.get("store", True),
        "temperature": body.get("temperature", 0.7),
        "text": body.get("text") or {"format": {"type": "text"}},
        "tool_choice": body.get("tool_choice", "auto"),
        "tools": body.get("tools", []),
        "top_p": body.get("top_p", 1),
        "truncation": body.get("truncation", "disabled"),
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "user": body.get("user"),
        "metadata": body.get("metadata") or {},
    }


def _build_responses_response_payload(
    response_id: str,
    body: dict[str, Any],
    input_items: list[dict[str, Any]],
    status: str,
    output: list[dict[str, Any]],
    usage: dict[str, int],
) -> dict[str, Any]:
    """构建 Responses API 的 response payload。"""
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": body.get("instructions"),
        "max_output_tokens": body.get("max_output_tokens") or body.get("max_tokens"),
        "model": body.get("model"),
        "input": input_items,
        "output": output,
        "output_text": "",
        "parallel_tool_calls": body.get("parallel_tool_calls", True),
        "previous_response_id": body.get("previous_response_id"),
        "reasoning": body.get("reasoning") or {"effort": None, "summary": None},
        "store": body.get("store", True),
        "temperature": body.get("temperature", 0.7),
        "text": body.get("text") or {"format": {"type": "text"}},
        "tool_choice": body.get("tool_choice", "auto"),
        "tools": body.get("tools", []),
        "top_p": body.get("top_p", 1),
        "truncation": body.get("truncation", "disabled"),
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "user": body.get("user"),
        "metadata": body.get("metadata") or {},
    }


def _responses_sse(event_type: str, payload: dict[str, Any], sequence_number: int) -> str:
    data = {"type": event_type, "sequence_number": sequence_number}
    data.update(payload)
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _ensure_mapped_response_model(body: dict[str, Any]) -> None:
    model_name = body.get("model")
    if not model_name:
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ModelMapping.virtual_name)
            .where(ModelMapping.virtual_name == model_name)
            .limit(1)
        )
        if result.first():
            return

        fallback_result = await session.execute(
            select(ModelMapping.virtual_name).order_by(ModelMapping.order, ModelMapping.id).limit(1)
        )
        fallback_row = fallback_result.first()

    if fallback_row and fallback_row[0]:
        fallback_model = fallback_row[0]
        logger.warning(
            f"Responses model '{model_name}' not mapped; fallback to proxy model '{fallback_model}'."
        )
        body["model"] = fallback_model


@app.post("/v1/responses")
@app.post("/responses")
@app.post("/v1/v1/responses")
@app.post("/api/v1/responses")
async def responses_proxy(request: Request, auth=Depends(verify_auth)):
    """OpenAI Responses 兼容接口，主要用于 Codex。"""
    body = await request.json()
    logger.debug(f"/v1/responses request - Model: {body.get('model')}, Stream: {body.get('stream', False)}")
    body = dict(body)
    await _ensure_mapped_response_model(body)

    input_items = _build_responses_context(body)
    chat_body = _responses_body_to_chat_body(body, input_items=input_items)

    if body.get("stream"):
        logger.info("Responses stream requested; using streaming upstream for real-time SSE.")
        # 流式请求：上游也走 stream，实时转换 chat completion SSE 为 Responses API SSE
        chat_body["stream"] = True
        streaming_response = await handle_completion(chat_body, is_anthropic=False)

        async def _consume_and_convert():
            """消费 chat completion SSE 流，实时转换为 Responses API SSE 事件。"""
            response_id = f"resp_{uuid.uuid4().hex[:24]}"
            message_id = f"msg_{uuid.uuid4().hex[:24]}"
            seq = 0
            text_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            finish_reason = None

            def next_seq():
                nonlocal seq
                seq += 1
                return seq

            def sse(event_type: str, payload: dict[str, Any]) -> str:
                data = {"type": event_type, "sequence_number": next_seq()}
                data.update(payload)
                return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            # response.created
            created_payload = _build_responses_response_payload(
                response_id, body, input_items, status="in_progress", output=[], usage=usage_totals
            )
            yield sse("response.created", {"response": created_payload})

            # output_item.added (message)
            msg_item = {
                "id": message_id,
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            }
            yield sse("response.output_item.added", {"output_index": 0, "item": msg_item})

            # content_part.added
            content_part = {"type": "output_text", "text": "", "annotations": []}
            yield sse(
                "response.content_part.added",
                {"item_id": message_id, "output_index": 0, "content_index": 0, "part": content_part},
            )

            text_started = False
            # 消费上游 chat completion SSE
            body_iterator = streaming_response.body_iterator
            try:
                async for raw_chunk in body_iterator:
                    if not raw_chunk or not raw_chunk.strip():
                        continue
                    # 跳过 SSE 注释行（心跳）
                    if raw_chunk.startswith(":"):
                        yield raw_chunk + "\n\n" if not raw_chunk.endswith("\n\n") else raw_chunk
                        continue
                    # 跳过 [DONE]
                    if raw_chunk.strip() == "data: [DONE]":
                        continue
                    # 解析 data: 行
                    line = raw_chunk.strip()
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        chunk_data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # 提取 usage
                    chunk_usage = chunk_data.get("usage") or {}
                    if chunk_usage.get("prompt_tokens"):
                        usage_totals["prompt_tokens"] = chunk_usage["prompt_tokens"]
                    if chunk_usage.get("completion_tokens"):
                        usage_totals["completion_tokens"] = chunk_usage["completion_tokens"]
                    if chunk_usage.get("total_tokens"):
                        usage_totals["total_tokens"] = chunk_usage["total_tokens"]
                    # 提取 delta
                    choices = chunk_data.get("choices") or [{}]
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    # 文本增量
                    content = delta.get("content")
                    if content:
                        text_started = True
                        text_parts.append(content)
                        yield sse(
                            "response.output_text.delta",
                            {
                                "item_id": message_id,
                                "output_index": 0,
                                "content_index": 0,
                                "delta": content,
                            },
                        )
                    # tool call 增量
                    for tc_delta in delta.get("tool_calls") or []:
                        tc_index = tc_delta.get("index", 0)
                        if tc_index not in tool_calls:
                            tool_calls[tc_index] = {
                                "id": tc_delta.get("id") or f"fc_{uuid.uuid4().hex[:24]}",
                                "type": "function_call",
                                "status": "in_progress",
                                "call_id": tc_delta.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                                "name": "",
                                "arguments": "",
                            }
                        tc = tool_calls[tc_index]
                        fn_delta = tc_delta.get("function") or {}
                        if fn_delta.get("name"):
                            tc["name"] = fn_delta["name"]
                        if fn_delta.get("arguments"):
                            tc["arguments"] += fn_delta["arguments"]
            except Exception as e:
                logger.error(f"Responses stream conversion error: {type(e).__name__}: {e}")

            # 发送 text done
            full_text = "".join(text_parts)
            yield sse(
                "response.output_text.done",
                {"item_id": message_id, "output_index": 0, "content_index": 0, "text": full_text},
            )
            # content_part.done
            content_part_done = {"type": "output_text", "text": full_text, "annotations": []}
            yield sse(
                "response.content_part.done",
                {"item_id": message_id, "output_index": 0, "content_index": 0, "part": content_part_done},
            )
            # output_item.done (message)
            msg_item_done = {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": full_text, "annotations": []}],
            }
            yield sse("response.output_item.done", {"output_index": 0, "item": msg_item_done})

            # tool call items
            for tc_index in sorted(tool_calls.keys()):
                tc = tool_calls[tc_index]
                output_index = 1 + tc_index
                tc["status"] = "completed"
                yield sse("response.output_item.added", {"output_index": output_index, "item": tc})
                yield sse(
                    "response.function_call_arguments.delta",
                    {
                        "item_id": tc["id"],
                        "call_id": tc.get("call_id"),
                        "output_index": output_index,
                        "delta": tc.get("arguments") or "",
                    },
                )
                yield sse(
                    "response.function_call_arguments.done",
                    {
                        "item_id": tc["id"],
                        "call_id": tc.get("call_id"),
                        "name": tc.get("name"),
                        "output_index": output_index,
                        "arguments": tc.get("arguments") or "{}",
                    },
                )
                yield sse("response.output_item.done", {"output_index": output_index, "item": tc})

            # 构建 output items
            output_items = []
            if full_text:
                output_items.append(msg_item_done)
            for tc_index in sorted(tool_calls.keys()):
                output_items.append(tool_calls[tc_index])

            # response.completed
            completed_payload = _build_responses_response_payload(
                response_id, body, input_items, status="completed", output=output_items, usage=usage_totals
            )
            yield sse("response.completed", {"response": completed_payload})
            yield "data: [DONE]\n\n"

            # 记住响应（用于 previous_response_id）
            await _remember_response(completed_payload, input_items, body)

        return StreamingResponse(
            _consume_and_convert(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 非流式请求：走原有逻辑
    chat_response = await handle_completion(chat_body, is_anthropic=False)
    responses_payload = _chat_response_to_responses(chat_response, body.get("model"), body=body, input_items=input_items)
    await _remember_response(responses_payload, input_items, body)
    return responses_payload


@app.get("/v1/responses/{response_id}")
@app.get("/responses/{response_id}")
@app.get("/v1/v1/responses/{response_id}")
@app.get("/api/v1/responses/{response_id}")
async def get_response(response_id: str, auth=Depends(verify_auth)):
    """查询 Responses 响应（从持久化存储）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PreviousResponse).where(PreviousResponse.response_id == response_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail=f"Response {response_id} not found.")

    try:
        body = json.loads(row.body_json)
    except Exception:
        raise HTTPException(status_code=500, detail="Corrupted response data")

    # 重建响应 payload
    input_items = json.loads(row.input_items_json)
    output_items = json.loads(row.output_items_json)

    return {
        "id": row.response_id,
        "object": "response",
        "created_at": int(row.created_at.timestamp()) if row.created_at else 0,
        "status": "completed",
        "input": input_items,
        "output": output_items,
        "model": body.get("model"),
    }


@app.get("/v1/responses/{response_id}/input_items")
@app.get("/responses/{response_id}/input_items")
@app.get("/v1/v1/responses/{response_id}/input_items")
@app.get("/api/v1/responses/{response_id}/input_items")
async def get_response_input_items(response_id: str, auth=Depends(verify_auth)):
    """返回 Responses 输入项（从持久化存储）。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PreviousResponse).where(PreviousResponse.response_id == response_id)
        )
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail=f"Response {response_id} not found.")

    try:
        input_items = json.loads(row.input_items_json)
    except Exception:
        input_items = []

    return {
        "object": "list",
        "data": input_items,
        "has_more": False,
        "first_id": None,
        "last_id": None,
    }


@app.post("/v1/messages/count_tokens")
@app.post("/messages/count_tokens")
async def anthropic_count_tokens(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Anthropic token 计数接口（估算）"""
    auth_header = request.headers.get("Authorization")
    api_key = x_api_key
    if auth_header and auth_header.lower().startswith("bearer "):
        api_key = auth_header.split(" ", 1)[1].strip()

    if settings.auth_enabled:
        if not api_key or api_key != settings.master_key:
            return JSONResponse(
                status_code=401,
                content={"error": {"type": "authentication_error", "message": "Invalid API Key"}},
            )

    body = await request.json()
    messages = body.get("messages", [])
    system_text = body.get("system", "")
    if isinstance(system_text, list):
        system_text = " ".join(
            block.get("text", "") for block in system_text if isinstance(block, dict)
        )

    total_chars = len(system_text or "")
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(block.get("text", ""))
        else:
            total_chars += len(str(content))

    # 粗略估算：约 4 字符 = 1 token（英文为主），中文约 1.5 字符 = 1 token
    # 取中间值约 3 字符 = 1 token
    estimated_tokens = max(1, total_chars // 3)

    return {"id": f"ct_{uuid.uuid4().hex[:12]}", "type": "token_count", "token_count": estimated_tokens}


@app.post("/v1/messages")
@app.post("/messages")
@app.post("/v1/v1/messages")
async def anthropic_proxy(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Anthropic 兼容的消息接口"""
    auth_header = request.headers.get("Authorization")
    api_key = x_api_key

    if auth_header and auth_header.lower().startswith("bearer "):
        api_key = auth_header.split(" ", 1)[1].strip()

    logger.debug(f"Auth attempt with key: {mask_secret(api_key)}")

    if settings.auth_enabled:
        if not api_key or api_key != settings.master_key:
            logger.warning(f"Auth failed. Expected: {mask_secret(settings.master_key)}, Got: {mask_secret(api_key)}")
            return JSONResponse(
                status_code=401,
                content={"error": {"type": "authentication_error", "message": "Invalid API Key"}},
            )

    body = await request.json()
    logger.debug(f"/messages request - Model: {body.get('model')}, Stream: {body.get('stream', False)}")
    return await handle_completion(body, is_anthropic=True)


async def handle_completion(body: dict, is_anthropic: bool = False):
    """处理完成请求"""
    model_name = body.get("model")
    
    # 检查模型名是否为空
    if not model_name:
        logger.error("Request missing model name")
        raise HTTPException(status_code=400, detail="Missing required field: model")
    
    stream = body.get("stream", False)
    per_key_timeout = _resolve_timeout_value(body.get("timeout"), settings.upstream_timeout_sec)
    total_timeout = _resolve_timeout_value(
        body.get("proxy_total_timeout"),
        settings.request_total_timeout_sec,
    )
    total_deadline = time.monotonic() + max(1.0, total_timeout)

    logger.debug(
        f"Request for model: {model_name}, stream: {stream}, is_anthropic: {is_anthropic}, "
        f"per_key_timeout={per_key_timeout}, total_timeout={total_timeout}"
    )

    async with AsyncSessionLocal() as session:
        # 获取模型映射
        mapping, provider = await get_model_mapping(session, model_name, is_anthropic)

        # 检查 provider 级别 circuit breaker
        if not _check_circuit_breaker(provider.id):
            logger.warning(f"Provider {provider.id} ({provider.name}) is in circuit breaker OPEN state, skipping")
            raise HTTPException(
                status_code=503,
                detail=f"Provider {provider.name} is temporarily unavailable due to repeated failures. Please retry later."
            )

        # 获取活跃的 API Key
        keys = await get_active_keys(session, provider.id)

        last_error = ""
        had_rate_limit_error = False
        had_transient_network_error = False
        hit_total_timeout = False

        for api_key in keys:
            remaining = total_deadline - time.monotonic()
            if remaining <= 0:
                hit_total_timeout = True
                break
            try:
                # 使用 services.py 中的统一函数构建参数
                tools = body.get("tools") or []
                request_stream = stream

                # Anthropic 工具调用的流式事件结构与 OpenAI 差异较大，先走非流式上游再转换回 SSE
                if is_anthropic and tools:
                    request_stream = False

                completion_kwargs, clean_real_model, messages, raw_messages = build_completion_params(
                    body=body,
                    mapping=mapping,
                    provider=provider,
                    api_key=api_key,
                    is_anthropic=is_anthropic,
                    request_stream=request_stream,
                )
                completion_kwargs["timeout"] = max(1.0, min(per_key_timeout, remaining))
                completion_kwargs["num_retries"] = max(0, int(settings.upstream_max_retries))
                logger.debug(
                    f"Trying provider={provider.name}, key_id={api_key.id}, model={clean_real_model}, "
                    f"timeout={completion_kwargs['timeout']:.2f}, remaining={remaining:.2f}"
                )

                # 处理 Anthropic 工具调用的流式响应
                if stream and is_anthropic and not request_stream:
                    response = await litellm.acompletion(**completion_kwargs)

                    # 检查非流式响应大小，防止超大响应占满内存（50MB 上限）
                    try:
                        from converters import serialize_response_obj
                        response_size = len(json.dumps(serialize_response_obj(response), ensure_ascii=False))
                        if response_size > 50 * 1024 * 1024:
                            logger.warning(f"Upstream response too large ({response_size} bytes), rejecting")
                            raise HTTPException(status_code=502, detail="Upstream response too large")
                    except (TypeError, HTTPException):
                        raise
                    except Exception:
                        pass  # 序列化失败不阻塞正常流程

                    generator = AnthropicToolStreamGenerator(
                        final_response=response,
                        model_name=model_name,
                        clean_real_model=clean_real_model,
                        messages=messages,
                        raw_messages=raw_messages,
                        key_id=api_key.id,
                        session=session,  # 传入 session 用于 clear_key_failure
                        log_usage_callback=log_usage,
                        heartbeat_sec=settings.stream_heartbeat_sec,
                    )
                    return StreamingResponse(
                        generator.generate(),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no",
                        },
                    )

                response = await litellm.acompletion(**completion_kwargs)
                await clear_key_failure(session, api_key.id)

                # 处理流式响应
                if stream:
                    generator = StreamGenerator(
                        response=response,
                        model_name=model_name,
                        clean_real_model=clean_real_model,
                        messages=messages,
                        raw_messages=raw_messages,
                        is_anthropic=is_anthropic,
                        key_id=api_key.id,
                        log_usage_callback=log_usage,
                        heartbeat_sec=settings.stream_heartbeat_sec,
                    )
                    return StreamingResponse(
                        generator.generate(),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no",
                        },
                    )

                # 记录使用量
                await log_usage(api_key.id, model_name, response)

                # 记录 provider 请求成功，关闭 circuit breaker
                _record_cb_success(provider.id)

                # 转换响应
                if is_anthropic:
                    return convert_openai_response_to_anthropic(response, model_name)

                return response

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Key {api_key.id} failed: {e}")
                last_error = str(e)
                error_text = str(e).lower()
                if "ratelimiterror" in error_text or "error code: 429" in error_text or "'status': 429" in error_text:
                    had_rate_limit_error = True
                    try:
                        await mark_key_rate_limited(session, api_key.id)
                    except Exception as mark_err:
                        logger.warning(f"Failed to mark key {api_key.id} as rate-limited: {mark_err}")
                elif any(
                    token in error_text
                    for token in [
                        "connection error",
                        "server disconnected",
                        "readerror",
                        "read timeout",
                        "timed out",
                        "sockettimeouterror",
                        "apitimeouterror",
                    ]
                ):
                    had_transient_network_error = True
                    try:
                        await mark_key_rate_limited(session, api_key.id)
                    except Exception as mark_err:
                        logger.warning(f"Failed to cool down transient-failed key {api_key.id}: {mark_err}")
                # 记录 provider 级别失败，可能触发 circuit breaker
                _record_cb_failure(provider.id)
                continue

        if hit_total_timeout:
            raise HTTPException(
                status_code=504,
                detail=(
                    f"Proxy total timeout reached ({int(total_timeout)}s). "
                    f"Last upstream error: {last_error or 'N/A'}"
                ),
            )
        if had_rate_limit_error:
            retry_after = max(1, int(settings.key_rate_limit_cooldown_sec))
            raise HTTPException(
                status_code=429,
                detail="Upstream rate limited. Please retry later.",
                headers={"Retry-After": str(retry_after)},
            )
        if had_transient_network_error:
            raise HTTPException(
                status_code=503,
                detail=f"Upstream network unstable. Last error: {last_error}",
            )
        raise HTTPException(status_code=502, detail=f"All upstream keys failed. Last error: {last_error}")


@app.post("/v1/images/generations")
@app.post("/images/generations")
async def image_proxy(request: Request, auth=Depends(verify_auth)):
    """图片生成接口，根据 provider_type 自动路由"""
    body = await request.json()
    model_name = body.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="Missing required field: model")

    # 查询 provider_type 决定走哪个处理器
    async with AsyncSessionLocal() as session:
        from db import ModelMapping, Provider
        stmt = select(ModelMapping, Provider).join(Provider).where(ModelMapping.virtual_name == model_name)
        result = await session.execute(stmt)
        mapping_data = result.first()
        if mapping_data:
            _, provider = mapping_data
            p_type = (getattr(provider, "provider_type", None) or "openai").strip().lower()
            if p_type == "nvidia_image":
                return await execute_nvidia_image_generation(body)

    return await execute_image_generation(body)


# --- 兼容性端点：避免 AI 工具探测时返回 404 ---

@app.get("/props")
@app.get("/v1/props")
async def props_endpoint(request: Request):
    """兼容 Ollama 等工具的属性探测端点"""
    _require_api_key_for_models(request)
    return {"status": "ok", "service": "AI Proxy Gateway"}


@app.get("/version")
async def version_endpoint(request: Request):
    """兼容 Ollama 等工具的版本探测端点"""
    _require_api_key_for_models(request)
    return {"version": "1.0.0"}


@app.get("/api/tags")
async def ollama_tags_endpoint(request: Request):
    """兼容 Ollama 客户端的模型列表端点"""
    _require_api_key_for_models(request)
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ModelMapping.virtual_name).order_by(ModelMapping.order, ModelMapping.id))
        names = [row[0] for row in result.all() if row and row[0]]
        uniq_names = []
        seen = set()
        for name in names:
            if name not in seen:
                seen.add(name)
                uniq_names.append(name)
        return {
            "models": [
                {
                    "name": name,
                    "model": name,
                    "modified_at": "",
                    "size": 0,
                }
                for name in uniq_names
            ]
        }


@app.post("/api/show")
async def ollama_show_endpoint(request: Request):
    """兼容 Ollama 客户端的模型详情探测接口"""
    _require_api_key_for_models(request)
    body = await request.json()
    model_name = body.get("name") or body.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="Missing required field: name")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ModelMapping.virtual_name, ModelMapping.real_name).where(ModelMapping.virtual_name == model_name)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Model {model_name} not mapped.")
        virtual_name, real_name = row

    return {
        "license": "unknown",
        "modelfile": f"FROM {real_name}",
        "parameters": "",
        "template": "",
        "details": {"family": "openai-compatible", "format": "proxy", "parameter_size": "unknown"},
        "model_info": {"virtual_name": virtual_name, "real_name": real_name},
    }
