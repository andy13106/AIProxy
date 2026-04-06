"""流式响应处理模块"""

import asyncio
import json
import uuid
from typing import Any, AsyncGenerator, Callable, Optional

from config import logger, settings
from converters import (
    convert_openai_response_to_anthropic,
    extract_usage,
    map_finish_reason_to_anthropic,
    merge_usage,
    safe_token_count,
    serialize_stream_chunk,
    sse_event,
)


async def iter_stream_with_heartbeat(response: Any, heartbeat_sec: float) -> AsyncGenerator[Any, None]:
    """带心跳的流式迭代器"""
    iterator = response.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=heartbeat_sec)
            yield chunk
        except asyncio.TimeoutError:
            # SSE 注释行作为心跳，避免中间层空闲超时断开连接
            yield None
        except StopAsyncIteration:
            break


class StreamGenerator:
    """统一的流式响应生成器"""

    def __init__(
        self,
        response: Any,
        model_name: str,
        clean_real_model: str,
        messages: list,
        is_anthropic: bool,
        key_id: int,
        log_usage_callback: Callable,
        heartbeat_sec: float = 15.0,
    ):
        self.response = response
        self.model_name = model_name
        self.clean_real_model = clean_real_model
        self.messages = messages
        self.is_anthropic = is_anthropic
        self.key_id = key_id
        self.log_usage_callback = log_usage_callback
        self.heartbeat_sec = heartbeat_sec

        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.streamed_text_parts: list[str] = []

    async def generate(self) -> AsyncGenerator[str, None]:
        """生成流式响应"""
        text_block_started = False
        final_finish_reason = None

        if self.is_anthropic:
            stream_message_id = f"msg_{uuid.uuid4().hex[:20]}"
            yield sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": stream_message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": self.model_name,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )

        try:
            async for maybe_chunk in iter_stream_with_heartbeat(self.response, self.heartbeat_sec):
                if maybe_chunk is None:
                    yield ": ping\n\n"
                    continue

                chunk = maybe_chunk
                payload = serialize_stream_chunk(chunk)
                merge_usage(self.usage_totals, payload.get("usage"))
                final_finish_reason = ((payload.get("choices") or [{}])[0].get("finish_reason"))

                if self.is_anthropic:
                    delta = ((payload.get("choices") or [{}])[0].get("delta") or {})
                    content = delta.get("content") or ""
                    if content:
                        if not text_block_started:
                            text_block_started = True
                            yield sse_event(
                                "content_block_start",
                                {
                                    "type": "content_block_start",
                                    "index": 0,
                                    "content_block": {"type": "text", "text": ""},
                                },
                            )
                        self.streamed_text_parts.append(content)
                        yield sse_event(
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
                            self.streamed_text_parts.append(content)
                    except Exception:
                        pass
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        finally:
            await self._finalize_usage()

        if self.is_anthropic:
            if not text_block_started:
                yield sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            yield sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": map_finish_reason_to_anthropic(final_finish_reason, False),
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": self.usage_totals["completion_tokens"]},
                },
            )
            yield sse_event("message_stop", {"type": "message_stop"})
        else:
            yield "data: [DONE]\n\n"

    async def _finalize_usage(self) -> None:
        """完成 usage 统计"""
        if self.usage_totals["total_tokens"] == 0:
            prompt_tokens = safe_token_count(self.clean_real_model, messages=self.messages)
            completion_tokens = safe_token_count(self.clean_real_model, text="".join(self.streamed_text_parts))
            self.usage_totals["prompt_tokens"] = prompt_tokens
            self.usage_totals["completion_tokens"] = completion_tokens
            self.usage_totals["total_tokens"] = prompt_tokens + completion_tokens
        await self.log_usage_callback(self.key_id, self.model_name, {"usage": self.usage_totals})


class AnthropicToolStreamGenerator:
    """Anthropic 工具调用流式响应生成器（非流式上游转 SSE）"""

    def __init__(
        self,
        completion_kwargs: dict,
        model_name: str,
        clean_real_model: str,
        messages: list,
        key_id: int,
        log_usage_callback: Callable,
        heartbeat_sec: float = 15.0,
    ):
        self.completion_kwargs = completion_kwargs
        self.model_name = model_name
        self.clean_real_model = clean_real_model
        self.messages = messages
        self.key_id = key_id
        self.log_usage_callback = log_usage_callback
        self.heartbeat_sec = heartbeat_sec

        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.streamed_text_parts: list[str] = []

    async def generate(self) -> AsyncGenerator[str, None]:
        """生成流式响应"""
        import litellm

        try:
            pending_response = asyncio.create_task(litellm.acompletion(**self.completion_kwargs))

            while not pending_response.done():
                yield ": ping\n\n"
                await asyncio.sleep(self.heartbeat_sec)

            final_response = await pending_response
            anthropic_message = convert_openai_response_to_anthropic(final_response, self.model_name)
            usage = anthropic_message.get("usage", {})

            yield sse_event(
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
                    yield sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                    if block.get("text"):
                        self.streamed_text_parts.append(block.get("text"))
                        yield sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": idx,
                                "delta": {"type": "text_delta", "text": block.get("text")},
                            },
                        )
                    yield sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})

                elif block.get("type") == "tool_use":
                    tool_input = block.get("input")
                    if not isinstance(tool_input, dict):
                        tool_input = {}
                    input_json = json.dumps(tool_input, ensure_ascii=False)
                    yield sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": {
                                "type": "tool_use",
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input": {},
                            },
                        },
                    )
                    yield sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {"type": "input_json_delta", "partial_json": input_json},
                        },
                    )
                    yield sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})

            output_tokens = usage.get("output_tokens", 0)
            self.usage_totals["prompt_tokens"] = usage.get("input_tokens", 0)
            self.usage_totals["completion_tokens"] = output_tokens
            self.usage_totals["total_tokens"] = self.usage_totals["prompt_tokens"] + output_tokens

            yield sse_event(
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
            yield sse_event("message_stop", {"type": "message_stop"})

        finally:
            if self.usage_totals["total_tokens"] == 0:
                prompt_tokens = safe_token_count(self.clean_real_model, messages=self.messages)
                completion_tokens = safe_token_count(self.clean_real_model, text="".join(self.streamed_text_parts))
                self.usage_totals["prompt_tokens"] = prompt_tokens
                self.usage_totals["completion_tokens"] = completion_tokens
                self.usage_totals["total_tokens"] = prompt_tokens + completion_tokens
            await self.log_usage_callback(self.key_id, self.model_name, {"usage": self.usage_totals})