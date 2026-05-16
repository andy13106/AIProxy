"""业务逻辑模块"""

import base64
import datetime
import time
import threading
import os
from collections import defaultdict
from typing import Any, Optional

import httpx
import litellm
from fastapi import HTTPException
from sqlalchemy import select, update

# 尝试导入 redis 用于多 worker 共享 round-robin cursor
try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

# === Circuit Breaker 配置 ===
# provider 级别熔断：连续失败 N 次后跳过该 provider
_CB_FAILURE_THRESHOLD = 5  # 连续失败次数阈值
_CB_COOLDOWN_SEC = 60.0     # 熔断冷却时间（秒）
_provider_cb_state = {}     # provider_id -> {"fail_count": int, "state": "closed"|"open", "opened_at": float}
_cb_lock = threading.Lock()


def _check_circuit_breaker(provider_id: int) -> bool:
    """检查 provider 的 circuit breaker 状态。
    返回 True 表示可以请求，False 表示已被熔断。
    """
    with _cb_lock:
        state = _provider_cb_state.get(provider_id)
        if not state or state["state"] == "closed":
            return True

        # 检查是否已过冷却期
        elapsed = time.time() - state["opened_at"]
        if elapsed >= _CB_COOLDOWN_SEC:
            # 半开状态：允许一次请求尝试
            state["state"] = "half-open"
            state["fail_count"] = 0
            return True

        return False


def _record_cb_success(provider_id: int) -> None:
    """记录 provider 请求成功，关闭 circuit breaker"""
    with _cb_lock:
        if provider_id in _provider_cb_state:
            _provider_cb_state[provider_id] = {"fail_count": 0, "state": "closed", "opened_at": 0}


def _record_cb_failure(provider_id: int) -> None:
    """记录 provider 请求失败，可能触发熔断"""
    with _cb_lock:
        state = _provider_cb_state.get(provider_id)
        if not state:
            state = {"fail_count": 0, "state": "closed", "opened_at": 0}
            _provider_cb_state[provider_id] = state

        state["fail_count"] += 1
        if state["fail_count"] >= _CB_FAILURE_THRESHOLD and state["state"] == "closed":
            state["state"] = "open"
            state["opened_at"] = time.time()
            logger.warning(f"Provider {provider_id} circuit breaker OPENED after {state['fail_count']} failures")

from config import logger, settings
from db import UsageLog
from converters import (
    convert_anthropic_messages_to_openai,
    convert_anthropic_tool_choice,
    convert_anthropic_tools_to_openai,
    convert_openai_response_to_anthropic,
    extract_text_from_content_blocks,
    extract_usage,
)
from db import APIKey, AsyncSessionLocal, ModelMapping, Provider

_provider_key_cursor_lock = threading.Lock()
# Round-robin cursor: 多 worker 下使用 Redis 共享，单 worker 下使用本地 dict
_provider_key_cursor = defaultdict(int)
_redis_client = None
_redis_lock = threading.Lock()

# 事件循环中使用的 Redis 连接（每个事件循环一个）
_async_redis = None


def _get_async_redis():
    """获取异步 Redis 客户端（事件循环内）"""
    global _async_redis
    if not _REDIS_AVAILABLE:
        return None
    if _async_redis is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            _async_redis = aioredis.from_url(redis_url, decode_responses=True, max_connections=5)
        except Exception:
            _async_redis = None
    return _async_redis


async def _order_keys_for_provider(provider_id: int, keys: list) -> list:
    """根据策略返回 key 顺序：
    - sticky_failover: 固定顺序主 key 优先，失败再切换
    - round_robin: 请求级轮询起点（多 worker 下通过 Redis 共享 cursor）
    """
    if not keys:
        return keys
    ordered = sorted(keys, key=lambda k: k.id)
    if len(ordered) == 1:
        return ordered
    if settings.key_strategy != "round_robin":
        return ordered

    # 尝试使用 Redis 共享 cursor（多 worker 场景）
    redis_client = _get_async_redis()
    if redis_client is not None:
        cursor_key = f"aiproxy:rr:provider:{provider_id}"
        try:
            start_idx_val = await redis_client.get(cursor_key)
            start_idx = int(start_idx_val) if start_idx_val else 0
            new_idx = (start_idx + 1) % len(ordered)
            await redis_client.set(cursor_key, str(new_idx))
        except Exception:
            # Redis 不可用时降级到本地 cursor
            with _provider_key_cursor_lock:
                start_idx = _provider_key_cursor[provider_id] % len(ordered)
                _provider_key_cursor[provider_id] = (start_idx + 1) % len(ordered)
    else:
        with _provider_key_cursor_lock:
            start_idx = _provider_key_cursor[provider_id] % len(ordered)
            _provider_key_cursor[provider_id] = (start_idx + 1) % len(ordered)

    return ordered[start_idx:] + ordered[:start_idx]


async def get_model_mapping(session: Any, model_name: str, is_anthropic: bool) -> tuple:
    """获取模型映射，支持 fallback"""
    stmt = select(ModelMapping, Provider).join(Provider).where(ModelMapping.virtual_name == model_name)
    result = await session.execute(stmt)
    mapping_data = result.first()

    if not mapping_data:
        if is_anthropic and isinstance(model_name, str) and model_name.startswith("claude-"):
            fallback_candidates = []
            for candidate in [
                settings.default_fallback_virtual_model,
                settings.anthropic_fallback_virtual_model,
                "claude-sonnet-4-20250514",
                "GLM5",
            ]:
                if candidate and candidate not in fallback_candidates:
                    fallback_candidates.append(candidate)

            for fallback_name in fallback_candidates:
                fallback_stmt = (
                    select(ModelMapping, Provider)
                    .join(Provider)
                    .where(ModelMapping.virtual_name == fallback_name)
                )
                fallback_result = await session.execute(fallback_stmt)
                fallback_mapping_data = fallback_result.first()
                if fallback_mapping_data:
                    mapping_data = fallback_mapping_data
                    logger.warning(f"Model '{model_name}' not mapped. Fallback to '{fallback_name}'.")
                    break

        if (
            not mapping_data
            and not is_anthropic
            and isinstance(model_name, str)
            and model_name.startswith(("gpt-", "o1", "o3", "o4", "codex-"))
        ):
            fallback_candidates = []
            for candidate in [settings.default_fallback_virtual_model, "GLM5.1", "GLM5"]:
                if candidate and candidate not in fallback_candidates:
                    fallback_candidates.append(candidate)

            for fallback_name in fallback_candidates:
                fallback_stmt = (
                    select(ModelMapping, Provider)
                    .join(Provider)
                    .where(ModelMapping.virtual_name == fallback_name)
                )
                fallback_result = await session.execute(fallback_stmt)
                fallback_mapping_data = fallback_result.first()
                if fallback_mapping_data:
                    mapping_data = fallback_mapping_data
                    logger.warning(f"Model '{model_name}' not mapped. Fallback to '{fallback_name}'.")
                    break

            if not mapping_data:
                fallback_stmt = (
                    select(ModelMapping, Provider)
                    .join(Provider)
                    .order_by(ModelMapping.order, ModelMapping.id)
                    .limit(1)
                )
                fallback_result = await session.execute(fallback_stmt)
                fallback_mapping_data = fallback_result.first()
                if fallback_mapping_data:
                    mapping_data = fallback_mapping_data
                    fallback_mapping, _ = fallback_mapping_data
                    logger.warning(
                        f"Model '{model_name}' not mapped. Fallback to first mapped model "
                        f"'{fallback_mapping.virtual_name}'."
                    )

    if not mapping_data:
        logger.error(f"Model '{model_name}' not found in database")
        raise HTTPException(status_code=404, detail=f"Model {model_name} not mapped.")

    return mapping_data


async def get_active_keys(session: Any, provider_id: int) -> list:
    """获取供应商的活跃 API Key"""
    stmt = select(APIKey).where(APIKey.provider_id == provider_id, APIKey.is_active.is_(True))
    result = await session.execute(stmt)
    keys = result.scalars().all()

    if not keys:
        logger.error(f"No active API keys for provider {provider_id}")
        raise HTTPException(status_code=503, detail="No active API keys available.")

    cooldown_sec = max(0.0, settings.key_rate_limit_cooldown_sec)
    if cooldown_sec <= 0:
        return await _order_keys_for_provider(provider_id, keys)

    now = datetime.datetime.utcnow()
    eligible_keys = []
    earliest_available_in = None

    for key in keys:
        if not key.last_failure:
            eligible_keys.append(key)
            continue

        elapsed = (now - key.last_failure).total_seconds()
        remaining = cooldown_sec - elapsed
        if remaining <= 0:
            eligible_keys.append(key)
        else:
            if earliest_available_in is None or remaining < earliest_available_in:
                earliest_available_in = remaining

    if eligible_keys:
        return await _order_keys_for_provider(provider_id, eligible_keys)

    retry_after = max(1, int(earliest_available_in or cooldown_sec))
    raise HTTPException(
        status_code=429,
        detail=f"All upstream keys are rate limited. Retry in ~{retry_after}s.",
        headers={"Retry-After": str(retry_after)},
    )


async def mark_key_rate_limited(session: Any, key_id: int) -> None:
    """标记 key 进入限流冷却"""
    await session.execute(
        update(APIKey).where(APIKey.id == key_id).values(last_failure=datetime.datetime.utcnow())
    )
    await session.commit()


async def clear_key_failure(session: Any, key_id: int) -> None:
    """请求成功后清除 key 失败状态"""
    await session.execute(update(APIKey).where(APIKey.id == key_id).values(last_failure=None))
    await session.commit()


def clean_config_value(value: Optional[str]) -> str:
    """清理配置值"""
    return (value or "").strip().strip("`").strip("'").strip("\\")


def build_completion_params(
    body: dict,
    mapping: ModelMapping,
    provider: Provider,
    api_key: APIKey,
    is_anthropic: bool,
    request_stream: bool,
) -> tuple[dict, str, list, list]:
    """构建 litellm 请求参数"""
    messages = body.get("messages") or []
    tools = body.get("tools") or []

    # 获取上游供应商协议类型
    upstream_type = (getattr(provider, "provider_type", None) or "openai").strip().lower()

    if is_anthropic:
        messages = convert_anthropic_messages_to_openai(messages)
        system_prompt = extract_text_from_content_blocks(body.get("system", ""))
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

    clean_api_key = clean_config_value(api_key.key)
    clean_api_base = clean_config_value(provider.api_base)
    clean_real_model = clean_config_value(mapping.real_name)

    logger.debug(
        f"Mapping {body.get('model')} -> {clean_real_model} "
        f"via {clean_api_base} (type={upstream_type}), key_suffix={clean_api_key[-6:] if len(clean_api_key) >= 6 else clean_api_key}"
    )

    extra_body = body.get("extra_body") or {}

    if request_stream:
        extra_body = dict(extra_body)
        stream_options = dict(extra_body.get("stream_options") or {})
        stream_options["include_usage"] = True
        extra_body["stream_options"] = stream_options

    completion_kwargs = {
        "model": clean_real_model,
        "messages": messages,
        "api_key": clean_api_key,
        "stream": request_stream,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
        "extra_body": extra_body,
        "timeout": body.get("timeout", settings.upstream_timeout_sec),
    }

    # 根据上游类型设置 litellm 参数
    if upstream_type == "anthropic":
        # Anthropic 原生 API：litellm 用 "anthropic/model" 前缀路由
        if not clean_real_model.startswith("anthropic/"):
            completion_kwargs["model"] = f"anthropic/{clean_real_model}"
        if clean_api_base:
            completion_kwargs["api_base"] = clean_api_base
    elif upstream_type == "gemini":
        if not clean_real_model.startswith("gemini/"):
            completion_kwargs["model"] = f"gemini/{clean_real_model}"
        # Gemini 用 api_key 直接传，不需要 api_base
    elif upstream_type == "bedrock":
        if not clean_real_model.startswith("bedrock/"):
            completion_kwargs["model"] = f"bedrock/{clean_real_model}"
        # Bedrock 通过 AWS credentials 认证，不需要 api_key/api_base
        completion_kwargs.pop("api_key", None)
    elif upstream_type == "vertex_ai":
        if not clean_real_model.startswith("vertex_ai/"):
            completion_kwargs["model"] = f"vertex_ai/{clean_real_model}"
        completion_kwargs.pop("api_key", None)
    elif upstream_type == "azure":
        if not clean_real_model.startswith("azure/"):
            completion_kwargs["model"] = f"azure/{clean_real_model}"
        completion_kwargs["api_base"] = clean_api_base
    elif upstream_type == "ollama":
        if not clean_real_model.startswith("ollama/"):
            completion_kwargs["model"] = f"ollama/{clean_real_model}"
        completion_kwargs["api_base"] = clean_api_base
    elif upstream_type == "cohere":
        # litellm 要求 Cohere 使用 cohere_chat/ 前缀
        if not clean_real_model.startswith(("cohere_chat/", "cohere/")):
            completion_kwargs["model"] = f"cohere_chat/{clean_real_model}"
    elif upstream_type == "mistral":
        if not clean_real_model.startswith("mistral/"):
            completion_kwargs["model"] = f"mistral/{clean_real_model}"
    else:
        # openai 兼容（默认）
        completion_kwargs["api_base"] = clean_api_base
        completion_kwargs["custom_llm_provider"] = "openai"

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

    # raw_messages 保留原始格式用于 token 计数
    return completion_kwargs, clean_real_model, messages, list(messages)


async def log_usage(key_id: int, model_name: str, response_data: Any, is_image: bool = False) -> None:
    """记录使用量"""
    async with AsyncSessionLocal() as session:
        try:
            if is_image:
                log = UsageLog(key_id=key_id, model_name=model_name, images_count=1)
            else:
                usage = extract_usage(response_data)
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
                update(APIKey)
                .where(APIKey.id == key_id)
                .values(usage_count=APIKey.usage_count + 1)
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"记录使用量失败: {e}")


async def execute_image_generation(body: dict) -> dict:
    """执行图片生成"""
    model_name = body.get("model")

    async with AsyncSessionLocal() as session:
        stmt = select(ModelMapping, Provider).join(Provider).where(ModelMapping.virtual_name == model_name)
        result = await session.execute(stmt)
        mapping_data = result.first()

        if not mapping_data:
            raise HTTPException(status_code=404, detail=f"Image model {model_name} not mapped.")

        mapping, provider = mapping_data

        # 检查 provider 级别 circuit breaker
        if not _check_circuit_breaker(provider.id):
            logger.warning(f"Provider {provider.id} ({provider.name}) is in circuit breaker OPEN state for image generation")
            raise HTTPException(
                status_code=503,
                detail=f"Provider {provider.name} is temporarily unavailable. Please retry later."
            )

        keys = await get_active_keys(session, provider.id)

        for api_key in keys:
            try:
                upstream_type = (getattr(provider, "provider_type", None) or "openai").strip().lower()
                img_model = mapping.real_name
                img_kwargs = {
                    "model": img_model,
                    "prompt": body.get("prompt"),
                    "api_key": api_key.key,
                    "n": body.get("n", 1),
                    "size": body.get("size", "1024x1024"),
                    "timeout": body.get("timeout", settings.upstream_timeout_sec),
                }
                if upstream_type == "openai":
                    img_kwargs["api_base"] = provider.api_base
                elif upstream_type == "azure":
                    if not img_model.startswith("azure/"):
                        img_kwargs["model"] = f"azure/{img_model}"
                    img_kwargs["api_base"] = provider.api_base
                # 其他类型 litellm 会根据 model 前缀自动路由
                response = await litellm.aimage_generation(**img_kwargs)
                await clear_key_failure(session, api_key.id)
                await log_usage(api_key.id, model_name, response, is_image=True)
                _record_cb_success(provider.id)
                return response
            except Exception as e:
                logger.error(f"Key {api_key.id} failed for image generation: {e}")
                error_text = str(e).lower()
                if "ratelimiterror" in error_text or "error code: 429" in error_text or "'status': 429" in error_text:
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
                    try:
                        await mark_key_rate_limited(session, api_key.id)
                    except Exception as mark_err:
                        logger.warning(f"Failed to cool down transient-failed key {api_key.id}: {mark_err}")
                # 记录 provider 级别失败
                _record_cb_failure(provider.id)
                continue

        raise HTTPException(status_code=502, detail="All upstream keys failed for image generation.")


async def execute_nvidia_image_generation(body: dict) -> dict:
    """NVIDIA 专有文生图 API（SD3, FLUX 等）

    NVIDIA 端点格式: POST {api_base}/v1/genai/{real_name}
    请求体: {prompt, mode, negative_prompt, aspect_ratio, cfg_scale, steps, seed, output_format, ...}
    响应体: {image: "base64...", ...} 或 直接返回图片二进制
    转换为 OpenAI 格式返回: {data: [{b64_json: "..."}]}
    """
    model_name = body.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="Missing required field: model")

    async with AsyncSessionLocal() as session:
        stmt = select(ModelMapping, Provider).join(Provider).where(ModelMapping.virtual_name == model_name)
        result = await session.execute(stmt)
        mapping_data = result.first()

        if not mapping_data:
            raise HTTPException(status_code=404, detail=f"Image model {model_name} not mapped.")

        mapping, provider = mapping_data

        # 检查 provider 级别 circuit breaker
        if not _check_circuit_breaker(provider.id):
            logger.warning(f"Provider {provider.id} ({provider.name}) is in circuit breaker OPEN state for image generation")
            raise HTTPException(
                status_code=503,
                detail=f"Provider {provider.name} is temporarily unavailable. Please retry later."
            )

        keys = await get_active_keys(session, provider.id)

        clean_api_base = clean_config_value(provider.api_base).rstrip("/")
        clean_real_model = clean_config_value(mapping.real_name)

        # 构建 NVIDIA 专有请求体
        nvidia_body = {
            "prompt": body.get("prompt", ""),
            "mode": "text-to-image",
        }
        # 可选参数透传
        for key in ("negative_prompt", "aspect_ratio", "cfg_scale", "steps", "seed", "output_format", "model"):
            if key in body and body[key] is not None:
                nvidia_body[key] = body[key]
        # 如果客户端用 OpenAI 格式传了 size，尝试转换为 aspect_ratio
        if "aspect_ratio" not in nvidia_body and body.get("size"):
            nvidia_body["aspect_ratio"] = _size_to_aspect_ratio(body["size"])

        # NVIDIA 图片模型的 model 字段是简写（如 "sd3"），不是完整名
        # 如果 real_name 包含斜杠（如 stabilityai/stable-diffusion-3-medium），用它做 URL 路径
        # model 字段用简写或不传
        if "/" in clean_real_model:
            url_path = clean_real_model
        else:
            url_path = clean_real_model

        url = f"{clean_api_base}/v1/genai/{url_path}"
        timeout_sec = body.get("timeout", settings.upstream_timeout_sec)

        # 设置输出格式默认为 jpeg（返回 base64）
        if "output_format" not in nvidia_body:
            nvidia_body["output_format"] = "jpeg"

        # 移除不属于 NVIDIA API 的字段
        nvidia_body.pop("model", None)

        for api_key in keys:
            clean_key = clean_config_value(api_key.key)
            headers = {
                "Authorization": f"Bearer {clean_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            try:
                logger.debug(f"NVIDIA image request: {url}, key_id={api_key.id}")
                async with httpx.AsyncClient(timeout=timeout_sec) as client:
                    resp = await client.post(url, json=nvidia_body, headers=headers)

                if resp.status_code == 200:
                    await clear_key_failure(session, api_key.id)
                    content_type = resp.headers.get("content-type", "")

                    if "application/json" in content_type:
                        data = resp.json()
                        # NVIDIA 返回 {image: "base64..."} 或 {artifacts: [{base64: "..."}]}
                        b64 = data.get("image") or ""
                        if not b64 and isinstance(data.get("artifacts"), list):
                            for art in data["artifacts"]:
                                if isinstance(art, dict) and art.get("base64"):
                                    b64 = art["base64"]
                                    break
                    else:
                        # 直接返回二进制图片
                        b64 = base64.b64encode(resp.content).decode("utf-8")

                    await log_usage(api_key.id, model_name, {}, is_image=True)
                    _record_cb_success(provider.id)

                    # 转换为 OpenAI 兼容格式返回
                    return {
                        "created": int(datetime.datetime.utcnow().timestamp()),
                        "data": [{"b64_json": b64}] if b64 else [],
                    }

                # 错误处理
                error_text = resp.text[:500]
                logger.error(f"NVIDIA image key {api_key.id} failed: HTTP {resp.status_code} - {error_text}")

                if resp.status_code == 429:
                    try:
                        await mark_key_rate_limited(session, api_key.id)
                    except Exception as mark_err:
                        logger.warning(f"Failed to mark key {api_key.id} as rate-limited: {mark_err}")
                elif resp.status_code in (401, 403):
                    pass  # key 无效，跳过试下一个

            except Exception as e:
                logger.error(f"NVIDIA image key {api_key.id} exception: {e}")
                error_text = str(e).lower()
                if any(t in error_text for t in ("timeout", "timed out", "connection")):
                    try:
                        await mark_key_rate_limited(session, api_key.id)
                    except Exception:
                        pass
                # 记录 provider 级别失败
                _record_cb_failure(provider.id)
                continue

        raise HTTPException(status_code=502, detail="All upstream keys failed for NVIDIA image generation.")


def _size_to_aspect_ratio(size: str) -> str:
    """将 OpenAI 的 size 格式 (1024x1024) 转换为 NVIDIA 的 aspect_ratio 格式 (1:1)"""
    try:
        w, h = size.lower().split("x")
        w, h = int(w), int(h)
        from math import gcd
        g = gcd(w, h)
        return f"{w // g}:{h // g}"
    except Exception:
        return "1:1"
