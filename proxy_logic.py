import datetime
import json
import os
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
    return await handle_completion(body, is_anthropic=False)


@app.post("/v1/messages")
@app.post("/messages")
@app.post("/v1/v1/messages")
async def anthropic_proxy(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    auth_header = request.headers.get("Authorization")
    api_key = x_api_key

    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header.split(" ")[1]

    print(f"DEBUG: Auth attempt with key: {api_key}")

    if AUTH_ENABLED:
        if not api_key or api_key != MASTER_KEY:
            print(f"DEBUG: Auth failed. Expected: {MASTER_KEY}, Got: {api_key}")
            return JSONResponse(
                status_code=401,
                content={"error": {"type": "authentication_error", "message": "Invalid API Key"}},
            )

    body = await request.json()
    return await handle_completion(body, is_anthropic=True)


async def handle_completion(body: dict, is_anthropic: bool = False):
    model_name = body.get("model")
    stream = body.get("stream", False)

    async with AsyncSessionLocal() as session:
        stmt = select(ModelMapping, Provider).join(Provider).where(ModelMapping.virtual_name == model_name)
        result = await session.execute(stmt)
        mapping_data = result.first()

        if not mapping_data:
            raise HTTPException(status_code=404, detail=f"Model {model_name} not mapped.")

        mapping, provider = mapping_data

        stmt = select(APIKey).where(APIKey.provider_id == provider.id, APIKey.is_active.is_(True))
        result = await session.execute(stmt)
        keys = result.scalars().all()

    if not keys:
        raise HTTPException(status_code=503, detail="No active API keys available.")

    last_error = ""

    for api_key in keys:
        try:
            messages = body.get("messages")
            system_prompt = body.get("system", "")
            if system_prompt and is_anthropic:
                messages = [{"role": "system", "content": system_prompt}] + messages

            clean_api_key = (api_key.key or "").strip().strip("`").strip("'").strip("\"")
            clean_api_base = (provider.api_base or "").strip().strip("`").strip("'").strip("\"")
            clean_real_model = (mapping.real_name or "").strip().strip("`").strip("'").strip("\"")

            print(
                f"DEBUG: Mapping {model_name} -> {clean_real_model} "
                f"via {clean_api_base}, key_suffix={clean_api_key[-6:] if len(clean_api_key) >= 6 else clean_api_key}"
            )

            extra_body = body.get("extra_body") or {}
            if stream:
                extra_body = dict(extra_body)
                stream_options = dict(extra_body.get("stream_options") or {})
                stream_options["include_usage"] = True
                extra_body["stream_options"] = stream_options

            response = await litellm.acompletion(
                model=clean_real_model,
                messages=messages,
                api_key=clean_api_key,
                api_base=clean_api_base,
                custom_llm_provider="openai",
                stream=stream,
                max_tokens=body.get("max_tokens", 4096),
                temperature=body.get("temperature", 0.7),
                extra_body=extra_body,
            )

            if stream:
                usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                streamed_text_parts = []

                async def stream_generator():
                    try:
                        async for chunk in response:
                            _merge_usage(usage_totals, getattr(chunk, "usage", None))

                            if is_anthropic:
                                content = chunk.choices[0].delta.content or ""
                                if content:
                                    streamed_text_parts.append(content)
                                    anthropic_chunk = {
                                        "type": "content_block_delta",
                                        "index": 0,
                                        "delta": {"type": "text_delta", "text": content},
                                    }
                                    yield f"data: {json.dumps(anthropic_chunk)}\n\n"
                            else:
                                payload = _serialize_stream_chunk(chunk)
                                try:
                                    content = payload["choices"][0]["delta"].get("content")
                                    if content:
                                        streamed_text_parts.append(content)
                                except Exception:
                                    pass
                                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

                        if is_anthropic:
                            yield f"data: {json.dumps({'type': 'message_stop'})}\n\n"
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
                content = response.choices[0].message.content
                return {
                    "id": response.id,
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": content}],
                    "model": model_name,
                    "usage": {
                        "input_tokens": _extract_usage(response)["prompt_tokens"],
                        "output_tokens": _extract_usage(response)["completion_tokens"],
                    },
                }

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
