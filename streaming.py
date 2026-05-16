"""流式响应处理模块"""

import asyncio
import time
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
                try:
                    payload = serialize_stream_chunk(chunk)
                except Exception as e:
                    logger.warning(f"流式chunk解析失败，跳过: {e}")
                    continue
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

        except (asyncio.CancelledError, ConnectionError, Exception) as e:
            if not isinstance(e, asyncio.CancelledError):
                logger.error(f"流式响应中断: {type(e).__name__}: {e}")
            if self.is_anthropic:
                yield sse_event("error", {"type": "error", "error": {"type": "stream_error", "message": str(e)}})
            else:
                yield f"data: {{\"error\": {{\"message\": \"流式响应中断: {type(e).__name__}\", \"type\": \"stream_error\"}}}}\n\n"
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
        final_response: Any,
        model_name: str,
        clean_real_model: str,
        messages: list,
        key_id: int,
        log_usage_callback: Callable,
        heartbeat_sec: float = 15.0,
    ):
        self.final_response = final_response
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
        try:
            anthropic_message = convert_openai_response_to_anthropic(self.final_response, self.model_name)
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

class ResponsesStreamGenerator:
    """将 OpenAI Chat Completion 流式响应实时转换为 Responses API SSE 事件。"""

    def __init__(
        self,
        response: Any,
        body: dict[str, Any],
        input_items: list[dict[str, Any]],
        model_name: str,
        clean_real_model: str,
        messages: list,
        key_id: int,
        log_usage_callback: Callable,
        heartbeat_sec: float = 15.0,
    ):
        self.response = response
        self.body = body
        self.input_items = input_items
        self.model_name = model_name
        self.clean_real_model = clean_real_model
        self.messages = messages
        self.key_id = key_id
        self.log_usage_callback = log_usage_callback
        self.heartbeat_sec = heartbeat_sec
        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.streamed_text_parts: list[str] = []
        self.response_id = f"resp_{uuid.uuid4().hex[:24]}"
        self.message_id = f"msg_{uuid.uuid4().hex[:24]}"
        self.sequence_number = 0
        # Tool call 累积状态
        self.tool_calls: dict[int, dict[str, Any]] = {}

    def _next_seq(self) -> int:
        self.sequence_number += 1
        return self.sequence_number

    def _sse(self, event_type: str, payload: dict[str, Any]) -> str:
        data = {"type": event_type, "sequence_number": self._next_seq()}
        data.update(payload)
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate(self) -> AsyncGenerator[str, None]:
        # 发送 response.created
        created_payload = self._build_response_payload(status="in_progress", output=[])
        yield self._sse("response.created", {"response": created_payload})

        # 发送 output_item.added (message item)
        msg_item = {
            "id": self.message_id,
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
        yield self._sse("response.output_item.added", {"output_index": 0, "item": msg_item})

        # 发送 content part added
        content_part = {"type": "output_text", "text": "", "annotations": []}
        yield self._sse(
            "response.content_part.added",
            {"item_id": self.message_id, "output_index": 0, "content_index": 0, "part": content_part},
        )

        text_started = False
        final_finish_reason = None

        try:
            async for maybe_chunk in iter_stream_with_heartbeat(self.response, self.heartbeat_sec):
                if maybe_chunk is None:
                    yield ": ping\n\n"
                    continue

                chunk = maybe_chunk
                try:
                    payload = serialize_stream_chunk(chunk)
                except Exception as e:
                    logger.warning(f"流式chunk解析失败，跳过: {e}")
                    continue

                merge_usage(self.usage_totals, payload.get("usage"))
                choices = payload.get("choices") or [{}]
                choice = choices[0]
                final_finish_reason = choice.get("finish_reason") or final_finish_reason
                delta = choice.get("delta") or {}

                # 处理文本内容
                content = delta.get("content")
                if content:
                    if not text_started:
                        text_started = True
                    self.streamed_text_parts.append(content)
                    yield self._sse(
                        "response.output_text.delta",
                        {
                            "item_id": self.message_id,
                            "output_index": 0,
                            "content_index": 0,
                            "delta": content,
                        },
                    )

                # 处理 tool call 增量
                for tc_delta in delta.get("tool_calls") or []:
                    tc_index = tc_delta.get("index", 0)
                    if tc_index not in self.tool_calls:
                        self.tool_calls[tc_index] = {
                            "id": tc_delta.get("id") or f"fc_{uuid.uuid4().hex[:24]}",
                            "type": "function_call",
                            "status": "in_progress",
                            "call_id": tc_delta.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                            "name": "",
                            "arguments": "",
                        }
                    tc = self.tool_calls[tc_index]
                    fn_delta = tc_delta.get("function") or {}
                    if fn_delta.get("name"):
                        tc["name"] = fn_delta["name"]
                    if fn_delta.get("arguments"):
                        tc["arguments"] += fn_delta["arguments"]

        except (asyncio.CancelledError, ConnectionError, Exception) as e:
            if not isinstance(e, asyncio.CancelledError):
                logger.error(f"Responses流式响应中断: {type(e).__name__}: {e}")

        # 发送 text done
        full_text = "".join(self.streamed_text_parts)
        yield self._sse(
            "response.output_text.done",
            {
                "item_id": self.message_id,
                "output_index": 0,
                "content_index": 0,
                "text": full_text,
            },
        )

        # 发送 content part done
        content_part_done = {"type": "output_text", "text": full_text, "annotations": []}
        yield self._sse(
            "response.content_part.done",
            {"item_id": self.message_id, "output_index": 0, "content_index": 0, "part": content_part_done},
        )

        # 发送 message item done
        msg_item_done = {
            "id": self.message_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text, "annotations": []}],
        }
        yield self._sse("response.output_item.done", {"output_index": 0, "item": msg_item_done})

        # 处理 tool calls（如果有）
        for tc_index in sorted(self.tool_calls.keys()):
            tc = self.tool_calls[tc_index]
            output_index = 1 + tc_index  # message 在 index 0
            tc["status"] = "completed"
            yield self._sse("response.output_item.added", {"output_index": output_index, "item": tc})
            yield self._sse(
                "response.function_call_arguments.delta",
                {
                    "item_id": tc["id"],
                    "call_id": tc.get("call_id"),
                    "output_index": output_index,
                    "delta": tc.get("arguments") or "",
                },
            )
            yield self._sse(
                "response.function_call_arguments.done",
                {
                    "item_id": tc["id"],
                    "call_id": tc.get("call_id"),
                    "name": tc.get("name"),
                    "output_index": output_index,
                    "arguments": tc.get("arguments") or "{}",
                },
            )
            yield self._sse("response.output_item.done", {"output_index": output_index, "item": tc})

        # 补全 usage（如果流中没有提供）
        if self.usage_totals["total_tokens"] == 0:
            prompt_tokens = safe_token_count(self.clean_real_model, messages=self.messages)
            completion_tokens = safe_token_count(self.clean_real_model, text=full_text)
            self.usage_totals["prompt_tokens"] = prompt_tokens
            self.usage_totals["completion_tokens"] = completion_tokens
            self.usage_totals["total_tokens"] = prompt_tokens + completion_tokens

        await self.log_usage_callback(self.key_id, self.model_name, {"usage": self.usage_totals})

        # 构建 output items
        output_items = []
        if full_text:
            output_items.append(msg_item_done)
        for tc_index in sorted(self.tool_calls.keys()):
            output_items.append(self.tool_calls[tc_index])

        # 发送 response.completed
        completed_payload = self._build_response_payload(status="completed", output=output_items)
        yield self._sse("response.completed", {"response": completed_payload})

        yield "data: [DONE]\n\n"

    def _build_response_payload(self, status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
        body = self.body
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": status,
            "error": None,
            "incomplete_details": None,
            "instructions": body.get("instructions"),
            "max_output_tokens": body.get("max_output_tokens") or body.get("max_tokens"),
            "model": self.model_name,
            "input": self.input_items,
            "output": output,
            "output_text": "".join(self.streamed_text_parts),
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
                "input_tokens": self.usage_totals["prompt_tokens"],
                "output_tokens": self.usage_totals["completion_tokens"],
                "total_tokens": self.usage_totals["total_tokens"],
            },
            "user": body.get("user"),
            "metadata": body.get("metadata") or {},
        }
