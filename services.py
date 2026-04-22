"""业务逻辑模块"""

import datetime
import threading
from collections import defaultdict
from typing import Any, Optional

import litellm
from fastapi import HTTPException
from sqlalchemy import select, update

from config import logger, settings
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
_provider_key_cursor = defaultdict(int)


def _order_keys_for_provider(provider_id: int, keys: list) -> list:
    """根据策略返回 key 顺序：
    - sticky_failover: 固定顺序主 key 优先，失败再切换
    - round_robin: 请求级轮询起点
    """
    if not keys:
        return keys
    ordered = sorted(keys, key=lambda k: k.id)
    if len(ordered) == 1:
        return ordered
    if settings.key_strategy != "round_robin":
        return ordered
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
        return _order_keys_for_provider(provider_id, keys)

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
        return _order_keys_for_provider(provider_id, eligible_keys)

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
) -> dict:
    """构建 litellm 请求参数"""
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

    logger.debug(
        f"Mapping {body.get('model')} -> {clean_real_model} "
        f"via {clean_api_base}, key_suffix={clean_api_key[-6:] if len(clean_api_key) >= 6 else clean_api_key}"
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

    return completion_kwargs, clean_real_model, messages


async def log_usage(key_id: int, model_name: str, response_data: Any, is_image: bool = False) -> None:
    """记录使用量"""
    async with AsyncSessionLocal() as session:
        if is_image:
            from db import UsageLog

            log = UsageLog(key_id=key_id, model_name=model_name, images_count=1)
        else:
            usage = extract_usage(response_data)
            if (
                (usage["prompt_tokens"] or 0) == 0
                and (usage["completion_tokens"] or 0) == 0
                and (usage["total_tokens"] or 0) == 0
            ):
                return
            from db import UsageLog

            log = UsageLog(
                key_id=key_id,
                model_name=model_name,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                total_tokens=usage["total_tokens"],
            )

        session.add(log)
        await session.execute(
            __import__("sqlalchemy", fromlist=["update"]).update(APIKey)
            .where(APIKey.id == key_id)
            .values(usage_count=APIKey.usage_count + 1)
        )
        await session.commit()


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
        keys = await get_active_keys(session, provider.id)

        for api_key in keys:
            try:
                response = await litellm.aimage_generation(
                    model=mapping.real_name,
                    prompt=body.get("prompt"),
                    api_key=api_key.key,
                    api_base=provider.api_base,
                    n=body.get("n", 1),
                    size=body.get("size", "1024x1024"),
                    timeout=body.get("timeout", settings.upstream_timeout_sec),
                )
                await clear_key_failure(session, api_key.id)
                await log_usage(api_key.id, model_name, response, is_image=True)
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
                continue

        raise HTTPException(status_code=502, detail="All upstream keys failed for image generation.")
