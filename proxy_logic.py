import datetime
import json
import os
import uuid
import asyncio
from typing import Optional

import litellm
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update

from db import APIKey, AsyncSessionLocal, ModelMapping, Provider, UsageLog, init_db

app = FastAPI(title="AI Proxy Gateway")
security = HTTPBearer()

MASTER_KEY = os.getenv("MASTER_KEY", "sk-admin-123456")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"
UPSTREAM_TIMEOUT_SEC = float(os.getenv("UPSTREAM_TIMEOUT_SEC", "180"))
STREAM_HEARTBEAT_SEC = float(os.getenv("STREAM_HEARTBEAT_SEC", "15"))


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录所有请求的详细信息"""
    import time
    start_time = time.time()

    # 记录请求信息
    print(f"\n{'='*80}")
    print(f"REQUEST: {request.method} {request.url.path}")
    print(f"Query params: {dict(request.query_params)}")
    headers = dict(request.headers)
    if "authorization" in headers:
        auth_val = headers.get("authorization", "")
        if auth_val.lower().startswith("bearer "):
            token = auth_val[7:]
            headers["authorization"] = f"Bearer {_mask_secret(token)}"
        else:
            headers["authorization"] = _mask_secret(auth_val)
    if "x-api-key" in headers:
        headers["x-api-key"] = _mask_secret(headers.get("x-api-key"))
    print(f"Headers: {headers}")

    # 处理请求
    response = await call_next(request)

    # 记录响应信息
    process_time = time.time() - start_time
    print(f"RESPONSE: Status {response.status_code}, Time: {process_time:.2f}s")
    print(f"{'='*80}\n")

    return response


@app.on_event("startup")
async def startup():
    await init_db()


async def verify_auth(credentials: HTTPAuthorizationCredentials = Security(security)):
    if not AUTH_ENABLED:
        return True
    if credentials.credentials != MASTER_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return True


def _extract_usage(response_data) -> dict:
    if isinstance(response_data, dict):
        usage = response_data.get("usage", {}) or {}
    else:
        usage = getattr(response_data, "usage", None) or {}

    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        completion_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        total_tokens = usage.get("total_tokens")
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)) or 0
        completion_tokens = getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)) or 0
        total_tokens = getattr(usage, "total_tokens", None)

    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _merge_usage(usage_totals: dict, usage_obj) -> None:
    if not usage_obj:
        return

    usage = _extract_usage({"usage": usage_obj})
    usage_totals["prompt_tokens"] = max(usage_totals["prompt_tokens"], usage["prompt_tokens"])
    usage_totals["completion_tokens"] = max(usage_totals["completion_tokens"], usage["completion_tokens"])
    usage_totals["total_tokens"] = max(usage_totals["total_tokens"], usage["total_tokens"])


def _serialize_stream_chunk(chunk):
    if hasattr(chunk, "model_dump"):
        return chunk.model_dump(exclude_none=True)
    if hasattr(chunk, "dict"):
        return chunk.dict()
    return chunk


def _safe_token_count(model: str, messages=None, text: str = "") -> int:
    try:
        if messages is not None:
            return litellm.token_counter(model=model, messages=messages)
        return litellm.token_counter(model=model, text=text)
    except Exception:
        return 0


def _extract_text_from_content_blocks(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text") or "")
    return "".join(texts)


def _convert_anthropic_tools_to_openai(anthropic_tools) -> list:
    openai_tools = []
    for tool in anthropic_tools or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return openai_tools


def _convert_anthropic_tool_choice(tool_choice):
    if not tool_choice:
        return None

    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return "auto"
        if tool_choice == "any":
            return "required"
        return None

    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "auto":
            return "auto"
        if tool_choice.get("type") == "any":
            return "required"
        if tool_choice.get("type") == "tool" and tool_choice.get("name"):
            return {"type": "function", "function": {"name": tool_choice.get("name")}}

    return None


def _convert_anthropic_messages_to_openai(messages) -> list:
    converted = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "user")
        content = msg.get("content", "")

        if not isinstance(content, list):
            converted.append({"role": role, "content": content if isinstance(content, str) else str(content)})
            continue

        text_parts = []
        assistant_tool_calls = []
        tool_results = []

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use" and role == "assistant":
                tool_input = block.get("input", {})
                if isinstance(tool_input, str):
                    arguments = tool_input
                else:
                    arguments = json.dumps(tool_input or {}, ensure_ascii=False)
                assistant_tool_calls.append(
                    {
                        "id": block.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {"name": block.get("name", "tool"), "arguments": arguments},
                    }
                )
            elif block_type == "tool_result" and role == "user":
                tool_content = block.get("content", "")
                if isinstance(tool_content, list):
                    tool_content = _extract_text_from_content_blocks(tool_content)
                elif isinstance(tool_content, dict):
                    tool_content = json.dumps(tool_content, ensure_ascii=False)
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": tool_content if isinstance(tool_content, str) else str(tool_content),
                    }
                )

        if role == "assistant":
            assistant_message = {"role": "assistant", "content": "".join(text_parts)}
            if assistant_tool_calls:
                assistant_message["tool_calls"] = assistant_tool_calls
            converted.append(assistant_message)
        else:
            if text_parts:
                converted.append({"role": role, "content": "".join(text_parts)})
            elif not tool_results:
                converted.append({"role": role, "content": ""})

        for tool_msg in tool_results:
            if tool_msg.get("tool_call_id"):
                converted.append(tool_msg)

    return converted


def _serialize_response_obj(response):
    if hasattr(response, "model_dump"):
        return response.model_dump(exclude_none=True)
    if hasattr(response, "dict"):
        return response.dict()
    if isinstance(response, dict):
        return response
    return {}


def _map_finish_reason_to_anthropic(finish_reason: Optional[str], has_tool_use: bool) -> str:
    if has_tool_use or finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    if finish_reason == "stop_sequence":
        return "stop_sequence"
    return "end_turn"


def _convert_openai_response_to_anthropic(response, model_name: str) -> dict:
    payload = _serialize_response_obj(response)
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    finish_reason = choice.get("finish_reason")

    blocks = []
    text = message.get("content")
    if text:
        blocks.append({"type": "text", "text": text})

    tool_calls = message.get("tool_calls") or []
    for tool_call in tool_calls:
        function_data = tool_call.get("function") or {}
        raw_args = function_data.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                tool_input = json.loads(raw_args)
            except Exception:
                tool_input = {"raw_arguments": raw_args}
        elif isinstance(raw_args, dict):
            tool_input = raw_args
        else:
            tool_input = {}

        blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": function_data.get("name", "tool"),
                "input": tool_input,
            }
        )

    if not blocks:
        blocks = [{"type": "text", "text": ""}]

    usage = _extract_usage(payload)
    has_tool_use = any(block.get("type") == "tool_use" for block in blocks)
    stop_reason = _map_finish_reason_to_anthropic(finish_reason, has_tool_use)

    return {
        "id": payload.get("id", f"msg_{uuid.uuid4().hex[:20]}"),
        "type": "message",
        "role": "assistant",
        "content": blocks,
        "model": model_name,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage["prompt_tokens"],
            "output_tokens": usage["completion_tokens"],
        },
    }


def _sse_event(event_name: str, data: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _mask_secret(secret: Optional[str]) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


async def _iter_stream_with_heartbeat(response, heartbeat_sec: float):
    iterator = response.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=heartbeat_sec)
            yield chunk
        except asyncio.TimeoutError:
            # SSE 注释行作为心跳，避免中间层空闲超时断开连接。
            yield None
        except StopAsyncIteration:
            break


async def log_usage(key_id: int, model_name: str, response_data, is_image: bool = False):
    async with AsyncSessionLocal() as session:
        if is_image:
            log = UsageLog(key_id=key_id, model_name=model_name, images_count=1)
        else:
            usage = _extract_usage(response_data)
            if (
                (usage["prompt_tokens"] or 0) == 0
                and (usage["completion_tokens"] or 0) == 0
                and (usage["total_tokens"] or 0) == 0
            ):
                return
            log = UsageLog(
                key_id=key_id,
                model_name=model_name,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                total_tokens=usage["total_tokens"],
            )

        session.add(log)
        await session.execute(
            update(APIKey).where(APIKey.id == key_id).values(usage_count=APIKey.usage_count + 1)
        )
        await session.commit()


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_proxy(request: Request, auth=Depends(verify_auth)):
    body = await request.json()
    print(f"DEBUG: /v1/chat/completions request - Model: {body.get('model')}, Stream: {body.get('stream', False)}")
    print(f"DEBUG: Request keys: {list(body.keys())}")
    if 'messages' in body:
        print(f"DEBUG: Messages count: {len(body.get('messages', []))}")
    return await handle_completion(body, is_anthropic=False)


@app.post("/v1/messages")
@app.post("/messages")
@app.post("/v1/v1/messages")
async def anthropic_proxy(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    auth_header = request.headers.get("Authorization")
    api_key = x_api_key

    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header.split(" ")[1]

    print(f"DEBUG: Auth attempt with key: {_mask_secret(api_key)}")
    print(f"DEBUG: Request URL: {request.url}")

    if AUTH_ENABLED:
        if not api_key or api_key != MASTER_KEY:
            print(f"DEBUG: Auth failed. Expected: {MASTER_KEY}, Got: {api_key}")
            return JSONResponse(
                status_code=401,
                content={"error": {"type": "authentication_error", "message": "Invalid API Key"}},
            )

    body = await request.json()
    print(f"DEBUG: /messages request body - Model: {body.get('model')}, Stream: {body.get('stream', False)}")
    print(f"DEBUG: Request body keys: {list(body.keys())}")
    if 'messages' in body:
        print(f"DEBUG: Messages count: {len(body.get('messages', []))}")
    return await handle_completion(body, is_anthropic=True)


async def handle_completion(body: dict, is_anthropic: bool = False):
    model_name = body.get("model")
    stream = body.get("stream", False)

    print(f"DEBUG: Request for model: {model_name}, stream: {stream}, is_anthropic: {is_anthropic}")

    async with AsyncSessionLocal() as session:
        stmt = select(ModelMapping, Provider).join(Provider).where(ModelMapping.virtual_name == model_name)
        result = await session.execute(stmt)
        mapping_data = result.first()

        if not mapping_data:
            print(f"ERROR: Model '{model_name}' not found in database")
            raise HTTPException(status_code=404, detail=f"Model {model_name} not mapped.")

        mapping, provider = mapping_data

        stmt = select(APIKey).where(APIKey.provider_id == provider.id, APIKey.is_active.is_(True))
        result = await session.execute(stmt)
        keys = result.scalars().all()

    if not keys:
        print(f"ERROR: No active API keys for provider {provider.name}")
        raise HTTPException(status_code=503, detail="No active API keys available.")

    last_error = ""

    for api_key in keys:
        try:
            messages = body.get("messages") or []
            tools = body.get("tools") or []
            if is_anthropic:
                messages = _convert_anthropic_messages_to_openai(messages)
                system_prompt = _extract_text_from_content_blocks(body.get("system", ""))
                if system_prompt:
                    messages = [{"role": "system", "content": system_prompt}] + messages
            else:
                system_prompt = ""

            clean_api_key = (api_key.key or "").strip().strip("`").strip("'").strip("\"")
            clean_api_base = (provider.api_base or "").strip().strip("`").strip("'").strip("\"")
            clean_real_model = (mapping.real_name or "").strip().strip("`").strip("'").strip("\"")

            print(
                f"DEBUG: Mapping {model_name} -> {clean_real_model} "
                f"via {clean_api_base}, key_suffix={clean_api_key[-6:] if len(clean_api_key) >= 6 else clean_api_key}"
            )

            extra_body = body.get("extra_body") or {}
            request_stream = stream
            if is_anthropic and tools:
                # Anthropic 工具调用的流式事件结构与 OpenAI 差异较大，先走非流式上游再转换回 SSE。
                request_stream = False

            if request_stream:
                extra_body = dict(extra_body)
                stream_options = dict(extra_body.get("stream_options") or {})
                stream_options["include_usage"] = True
                extra_body["stream_options"] = stream_options

            completion_kwargs = {
                "model": clean_real_model,
                "messages": messages,
                "api_key": clean_api_key,
                "api_base": clean_api_base,
                "custom_llm_provider": "openai",
                "stream": request_stream,
                "max_tokens": body.get("max_tokens", 4096),
                "temperature": body.get("temperature", 0.7),
                "extra_body": extra_body,
                "timeout": body.get("timeout", UPSTREAM_TIMEOUT_SEC),
            }
            if body.get("top_p") is not None:
                completion_kwargs["top_p"] = body.get("top_p")
            if body.get("stop_sequences"):
                completion_kwargs["stop"] = body.get("stop_sequences")
            elif body.get("stop"):
                completion_kwargs["stop"] = body.get("stop")

            if is_anthropic and tools:
                converted_tools = _convert_anthropic_tools_to_openai(tools)
                if converted_tools:
                    completion_kwargs["tools"] = converted_tools
                converted_tool_choice = _convert_anthropic_tool_choice(body.get("tool_choice"))
                if converted_tool_choice:
                    completion_kwargs["tool_choice"] = converted_tool_choice
            elif not is_anthropic and body.get("tools"):
                completion_kwargs["tools"] = body.get("tools")
                if body.get("tool_choice") is not None:
                    completion_kwargs["tool_choice"] = body.get("tool_choice")

            if stream and is_anthropic and not request_stream:
                usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                streamed_text_parts = []

                async def anthropic_tool_stream_generator():
                    try:
                        pending_response = asyncio.create_task(litellm.acompletion(**completion_kwargs))
                        while not pending_response.done():
                            yield ": ping\n\n"
                            await asyncio.sleep(STREAM_HEARTBEAT_SEC)

                        final_response = await pending_response
                        anthropic_message = _convert_openai_response_to_anthropic(final_response, model_name)
                        usage = anthropic_message.get("usage", {})
                        yield _sse_event(
                            "message_start",
                            {
                                "type": "message_start",
                                "message": {
                                    "id": anthropic_message["id"],
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [],
                                    "model": anthropic_message.get("model"),
                                    "stop_reason": None,
                                    "stop_sequence": None,
                                    "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": 0},
                                },
                            },
                        )

                        for idx, block in enumerate(anthropic_message.get("content", [])):
                            if block.get("type") == "text":
                                yield _sse_event(
                                    "content_block_start",
                                    {
                                        "type": "content_block_start",
                                        "index": idx,
                                        "content_block": {"type": "text", "text": ""},
                                    },
                                )
                                if block.get("text"):
                                    streamed_text_parts.append(block.get("text"))
                                    yield _sse_event(
                                        "content_block_delta",
                                        {
                                            "type": "content_block_delta",
                                            "index": idx,
                                            "delta": {"type": "text_delta", "text": block.get("text")},
                                        },
                                    )
                                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
                            elif block.get("type") == "tool_use":
                                yield _sse_event(
                                    "content_block_start",
                                    {"type": "content_block_start", "index": idx, "content_block": block},
                                )
                                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})

                        output_tokens = usage.get("output_tokens", 0)
                        usage_totals["prompt_tokens"] = usage.get("input_tokens", 0)
                        usage_totals["completion_tokens"] = output_tokens
                        usage_totals["total_tokens"] = usage_totals["prompt_tokens"] + output_tokens

                        yield _sse_event(
                            "message_delta",
                            {
                                "type": "message_delta",
                                "delta": {
                                    "stop_reason": anthropic_message.get("stop_reason"),
                                    "stop_sequence": anthropic_message.get("stop_sequence"),
                                },
                                "usage": {"output_tokens": output_tokens},
                            },
                        )
                        yield _sse_event("message_stop", {"type": "message_stop"})
                    finally:
                        if usage_totals["total_tokens"] == 0:
                            prompt_tokens = _safe_token_count(clean_real_model, messages=messages)
                            completion_tokens = _safe_token_count(clean_real_model, text="".join(streamed_text_parts))
                            usage_totals["prompt_tokens"] = prompt_tokens
                            usage_totals["completion_tokens"] = completion_tokens
                            usage_totals["total_tokens"] = prompt_tokens + completion_tokens
                        await log_usage(api_key.id, model_name, {"usage": usage_totals})

                return StreamingResponse(
                    anthropic_tool_stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )

            response = await litellm.acompletion(
                **completion_kwargs
            )

            if stream:
                usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                streamed_text_parts = []

                async def stream_generator():
                    try:
                        if is_anthropic and not request_stream:
                            anthropic_message = _convert_openai_response_to_anthropic(response, model_name)
                            usage = anthropic_message.get("usage", {})
                            yield _sse_event(
                                "message_start",
                                {
                                    "type": "message_start",
                                    "message": {
                                        "id": anthropic_message["id"],
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [],
                                        "model": anthropic_message.get("model"),
                                        "stop_reason": None,
                                        "stop_sequence": None,
                                        "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": 0},
                                    },
                                },
                            )

                            for idx, block in enumerate(anthropic_message.get("content", [])):
                                if block.get("type") == "text":
                                    yield _sse_event(
                                        "content_block_start",
                                        {
                                            "type": "content_block_start",
                                            "index": idx,
                                            "content_block": {"type": "text", "text": ""},
                                        },
                                    )
                                    if block.get("text"):
                                        streamed_text_parts.append(block.get("text"))
                                        yield _sse_event(
                                            "content_block_delta",
                                            {
                                                "type": "content_block_delta",
                                                "index": idx,
                                                "delta": {"type": "text_delta", "text": block.get("text")},
                                            },
                                        )
                                    yield _sse_event(
                                        "content_block_stop",
                                        {"type": "content_block_stop", "index": idx},
                                    )
                                elif block.get("type") == "tool_use":
                                    yield _sse_event(
                                        "content_block_start",
                                        {
                                            "type": "content_block_start",
                                            "index": idx,
                                            "content_block": block,
                                        },
                                    )
                                    yield _sse_event(
                                        "content_block_stop",
                                        {"type": "content_block_stop", "index": idx},
                                    )

                            output_tokens = usage.get("output_tokens", 0)
                            usage_totals["prompt_tokens"] = usage.get("input_tokens", 0)
                            usage_totals["completion_tokens"] = output_tokens
                            usage_totals["total_tokens"] = usage_totals["prompt_tokens"] + output_tokens

                            yield _sse_event(
                                "message_delta",
                                {
                                    "type": "message_delta",
                                    "delta": {
                                        "stop_reason": anthropic_message.get("stop_reason"),
                                        "stop_sequence": anthropic_message.get("stop_sequence"),
                                    },
                                    "usage": {"output_tokens": output_tokens},
                                },
                            )
                            yield _sse_event("message_stop", {"type": "message_stop"})
                        else:
                            text_block_started = False
                            final_finish_reason = None
                            if is_anthropic:
                                stream_message_id = f"msg_{uuid.uuid4().hex[:20]}"
                                yield _sse_event(
                                    "message_start",
                                    {
                                        "type": "message_start",
                                        "message": {
                                            "id": stream_message_id,
                                            "type": "message",
                                            "role": "assistant",
                                            "content": [],
                                            "model": model_name,
                                            "stop_reason": None,
                                            "stop_sequence": None,
                                            "usage": {"input_tokens": 0, "output_tokens": 0},
                                        },
                                    },
                                )
                            async for maybe_chunk in _iter_stream_with_heartbeat(response, STREAM_HEARTBEAT_SEC):
                                if maybe_chunk is None:
                                    yield ": ping\n\n"
                                    continue

                                chunk = maybe_chunk
                                payload = _serialize_stream_chunk(chunk)
                                _merge_usage(usage_totals, payload.get("usage"))
                                final_finish_reason = ((payload.get("choices") or [{}])[0].get("finish_reason"))

                                if is_anthropic:
                                    delta = ((payload.get("choices") or [{}])[0].get("delta") or {})
                                    content = delta.get("content") or ""
                                    if content:
                                        if not text_block_started:
                                            text_block_started = True
                                            yield _sse_event(
                                                "content_block_start",
                                                {
                                                    "type": "content_block_start",
                                                    "index": 0,
                                                    "content_block": {"type": "text", "text": ""},
                                                },
                                            )
                                        streamed_text_parts.append(content)
                                        yield _sse_event(
                                            "content_block_delta",
                                            {
                                                "type": "content_block_delta",
                                                "index": 0,
                                                "delta": {"type": "text_delta", "text": content},
                                            },
                                        )
                                else:
                                    try:
                                        content = ((payload.get("choices") or [{}])[0].get("delta") or {}).get("content")
                                        if content:
                                            streamed_text_parts.append(content)
                                    except Exception:
                                        pass
                                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

                            if is_anthropic:
                                if not text_block_started:
                                    yield _sse_event(
                                        "content_block_start",
                                        {
                                            "type": "content_block_start",
                                            "index": 0,
                                            "content_block": {"type": "text", "text": ""},
                                        },
                                    )
                                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
                                yield _sse_event(
                                    "message_delta",
                                    {
                                        "type": "message_delta",
                                        "delta": {
                                            "stop_reason": _map_finish_reason_to_anthropic(final_finish_reason, False),
                                            "stop_sequence": None,
                                        },
                                        "usage": {"output_tokens": usage_totals["completion_tokens"]},
                                    },
                                )
                                yield _sse_event("message_stop", {"type": "message_stop"})
                            else:
                                yield "data: [DONE]\n\n"
                    finally:
                        if usage_totals["total_tokens"] == 0:
                            prompt_tokens = _safe_token_count(clean_real_model, messages=messages)
                            completion_tokens = _safe_token_count(
                                clean_real_model, text="".join(streamed_text_parts)
                            )
                            usage_totals["prompt_tokens"] = prompt_tokens
                            usage_totals["completion_tokens"] = completion_tokens
                            usage_totals["total_tokens"] = prompt_tokens + completion_tokens
                        await log_usage(api_key.id, model_name, {"usage": usage_totals})

                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )

            await log_usage(api_key.id, model_name, response)

            if is_anthropic:
                return _convert_openai_response_to_anthropic(response, model_name)

            return response

        except Exception as e:
            import traceback

            print(f"Key {api_key.id} failed: {e}")
            traceback.print_exc()
            last_error = str(e)
            continue

    raise HTTPException(status_code=502, detail=f"All upstream keys failed. Last error: {last_error}")


@app.post("/v1/images/generations")
async def image_proxy(request: Request, auth=Depends(verify_auth)):
    body = await request.json()
    model_name = body.get("model")

    async with AsyncSessionLocal() as session:
        stmt = select(ModelMapping, Provider).join(Provider).where(ModelMapping.virtual_name == model_name)
        result = await session.execute(stmt)
        mapping_data = result.first()

        if not mapping_data:
            raise HTTPException(status_code=404, detail=f"Image model {model_name} not mapped.")

        mapping, provider = mapping_data
        stmt = select(APIKey).where(APIKey.provider_id == provider.id, APIKey.is_active.is_(True))
        result = await session.execute(stmt)
        keys = result.scalars().all()

    if not keys:
        raise HTTPException(status_code=503, detail="No active keys for image generation.")

    for api_key in keys:
        try:
            response = await litellm.aimage_generation(
                model=mapping.real_name,
                prompt=body.get("prompt"),
                api_key=api_key.key,
                api_base=provider.api_base,
                n=body.get("n", 1),
                size=body.get("size", "1024x1024"),
                timeout=body.get("timeout", UPSTREAM_TIMEOUT_SEC),
            )
            await log_usage(api_key.id, model_name, response, is_image=True)
            return response
        except Exception as e:
            print(f"Image key {api_key.id} failed: {e}")
            continue

    raise HTTPException(status_code=502, detail="All image backend APIs failed.")


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.datetime.utcnow()}
