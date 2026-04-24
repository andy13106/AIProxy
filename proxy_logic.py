"""AI Proxy Gateway - 主入口"""

import json
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
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
    execute_image_generation,
    execute_nvidia_image_generation,
    get_active_keys,
    get_model_mapping,
    log_usage,
    mark_key_rate_limited,
)
from streaming import AnthropicToolStreamGenerator, StreamGenerator

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="AI Proxy Gateway", lifespan=lifespan)
security = HTTPBearer(auto_error=False)

# --- 简易内存速率限制 ---
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)
_rate_limit_last_cleanup: float = 0.0


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

                completion_kwargs, clean_real_model, messages = build_completion_params(
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
                    await clear_key_failure(session, api_key.id)

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
