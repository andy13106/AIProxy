"""AI Proxy Gateway - 主入口"""

import time
import uuid
from typing import Optional

import litellm
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from config import logger, request_id_ctx, settings
from converters import (
    convert_anthropic_messages_to_openai,
    convert_anthropic_tool_choice,
    convert_anthropic_tools_to_openai,
    convert_openai_response_to_anthropic,
    extract_text_from_content_blocks,
    mask_secret,
)
from db import AsyncSessionLocal, ModelMapping, init_db
from services import (
    build_completion_params,
    clean_config_value,
    clear_key_failure,
    get_active_keys,
    get_model_mapping,
    log_usage,
    mark_key_rate_limited,
)
from streaming import AnthropicToolStreamGenerator, StreamGenerator

app = FastAPI(title="AI Proxy Gateway")
security = HTTPBearer()


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


@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("Database initialized")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "service": "AI Proxy Gateway"}


async def verify_auth(credentials: HTTPAuthorizationCredentials = Security(security)):
    """验证认证"""
    if not settings.auth_enabled:
        return True
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
async def list_proxy_models(request: Request):
    """OpenAI 兼容模型列表接口，返回已映射的虚拟模型名"""
    _require_api_key_for_models(request)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ModelMapping.virtual_name).order_by(ModelMapping.virtual_name.asc()))
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


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_proxy(request: Request, auth=Depends(verify_auth)):
    """OpenAI 兼容的聊天接口"""
    body = await request.json()
    logger.debug(f"/v1/chat/completions request - Model: {body.get('model')}, Stream: {body.get('stream', False)}")
    return await handle_completion(body, is_anthropic=False)


@app.post("/v1/messages")
@app.post("/messages")
@app.post("/v1/v1/messages")
async def anthropic_proxy(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Anthropic 兼容的消息接口"""
    auth_header = request.headers.get("Authorization")
    api_key = x_api_key

    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header.split(" ")[1]

    logger.debug(f"Auth attempt with key: {mask_secret(api_key)}")

    if settings.auth_enabled:
        if not api_key or api_key != settings.master_key:
            logger.warning(f"Auth failed. Expected: {settings.master_key}, Got: {api_key}")
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
    stream = body.get("stream", False)

    logger.debug(f"Request for model: {model_name}, stream: {stream}, is_anthropic: {is_anthropic}")

    async with AsyncSessionLocal() as session:
        # 获取模型映射
        mapping, provider = await get_model_mapping(session, model_name, is_anthropic)

        # 获取活跃的 API Key
        keys = await get_active_keys(session, provider.id)

        last_error = ""
        had_rate_limit_error = False

        for api_key in keys:
            try:
                messages = body.get("messages") or []
                tools = body.get("tools") or []

                if is_anthropic:
                    messages = convert_anthropic_messages_to_openai(messages)
                    system_prompt = extract_text_from_content_blocks(body.get("system", ""))
                    if system_prompt:
                        messages = [{"role": "system", "content": system_prompt}] + messages

                clean_api_key = clean_config_value(api_key.key)
                clean_api_base = clean_config_value(provider.api_base)
                clean_real_model = clean_config_value(mapping.real_name)

                extra_body = body.get("extra_body") or {}
                request_stream = stream

                # Anthropic 工具调用的流式事件结构与 OpenAI 差异较大，先走非流式上游再转换回 SSE
                if is_anthropic and tools:
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
                    "timeout": body.get("timeout", settings.upstream_timeout_sec),
                }

                if body.get("top_p") is not None:
                    completion_kwargs["top_p"] = body.get("top_p")
                if body.get("stop_sequences"):
                    completion_kwargs["stop"] = body.get("stop_sequences")
                elif body.get("stop"):
                    completion_kwargs["stop"] = body.get("stop")

                # 工具调用参数
                if is_anthropic and tools:
                    converted_tools = convert_anthropic_tools_to_openai(tools)
                    if converted_tools:
                        completion_kwargs["tools"] = converted_tools
                    converted_tool_choice = convert_anthropic_tool_choice(body.get("tool_choice"))
                    if converted_tool_choice:
                        completion_kwargs["tool_choice"] = converted_tool_choice
                elif not is_anthropic and body.get("tools"):
                    completion_kwargs["tools"] = body.get("tools")
                    if body.get("tool_choice") is not None:
                        completion_kwargs["tool_choice"] = body.get("tool_choice")

                # 处理 Anthropic 工具调用的流式响应
                if stream and is_anthropic and not request_stream:
                    await clear_key_failure(session, api_key.id)
                    generator = AnthropicToolStreamGenerator(
                        completion_kwargs=completion_kwargs,
                        model_name=model_name,
                        clean_real_model=clean_real_model,
                        messages=messages,
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

                response = await litellm.acompletion(**completion_kwargs)
                await clear_key_failure(session, api_key.id)

                # 处理流式响应
                if stream:
                    generator = StreamGenerator(
                        response=response,
                        model_name=model_name,
                        clean_real_model=clean_real_model,
                        messages=messages,
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

                # 转换响应
                if is_anthropic:
                    return convert_openai_response_to_anthropic(response, model_name)

                return response

            except Exception as e:
                import traceback

                logger.error(f"Key {api_key.id} failed: {e}")
                traceback.print_exc()
                last_error = str(e)
                error_text = str(e).lower()
                if "ratelimiterror" in error_text or "error code: 429" in error_text or "'status': 429" in error_text:
                    had_rate_limit_error = True
                    try:
                        await mark_key_rate_limited(session, api_key.id)
                    except Exception as mark_err:
                        logger.warning(f"Failed to mark key {api_key.id} as rate-limited: {mark_err}")
                continue

        if had_rate_limit_error:
            retry_after = max(1, int(settings.key_rate_limit_cooldown_sec))
            raise HTTPException(
                status_code=429,
                detail="Upstream rate limited. Please retry later.",
                headers={"Retry-After": str(retry_after)},
            )
        raise HTTPException(status_code=502, detail=f"All upstream keys failed. Last error: {last_error}")


@app.post("/v1/images/generations")
@app.post("/images/generations")
async def image_proxy(request: Request, auth=Depends(verify_auth)):
    """图片生成接口"""
    from services import execute_image_generation

    body = await request.json()
    return await execute_image_generation(body)
