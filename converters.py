"""协议转换模块 - Anthropic <-> OpenAI 协议转换"""

import ast
import json
import uuid
from typing import Any, Optional

import litellm

from config import logger


def extract_usage(response_data: Any) -> dict:
    """从响应中提取 usage 信息"""
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


def merge_usage(usage_totals: dict, usage_obj: Any) -> None:
    """合并 usage 信息"""
    if not usage_obj:
        return

    usage = extract_usage({"usage": usage_obj})
    usage_totals["prompt_tokens"] = max(usage_totals["prompt_tokens"], usage["prompt_tokens"])
    usage_totals["completion_tokens"] = max(usage_totals["completion_tokens"], usage["completion_tokens"])
    usage_totals["total_tokens"] = max(usage_totals["total_tokens"], usage["total_tokens"])


def serialize_stream_chunk(chunk: Any) -> Any:
    """序列化流式响应块"""
    if hasattr(chunk, "model_dump"):
        return chunk.model_dump(exclude_none=True)
    if hasattr(chunk, "dict"):
        return chunk.dict()
    return chunk


def safe_token_count(model: str, messages: Optional[list] = None, text: str = "") -> int:
    """安全的 token 计数"""
    try:
        if messages is not None:
            return litellm.token_counter(model=model, messages=messages)
        return litellm.token_counter(model=model, text=text)
    except Exception:
        return 0


def extract_text_from_content_blocks(content: Any) -> str:
    """从 content blocks 中提取文本"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text") or "")
    return "".join(texts)


def convert_anthropic_tools_to_openai(anthropic_tools: Optional[list]) -> list:
    """将 Anthropic 工具定义转换为 OpenAI 格式"""
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


def convert_anthropic_tool_choice(tool_choice: Any) -> Any:
    """将 Anthropic tool_choice 转换为 OpenAI 格式"""
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


def convert_anthropic_messages_to_openai(messages: Optional[list]) -> list:
    """将 Anthropic 消息格式转换为 OpenAI 格式"""
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
                tool_input = normalize_tool_input(block.get("name", "tool"), tool_input)
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
                    tool_content = extract_text_from_content_blocks(tool_content)
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


def normalize_tool_input(tool_name: str, tool_input: Any) -> dict:
    """规范化工具输入参数"""
    name = (tool_name or "").lower()
    if isinstance(tool_input, dict):
        normalized = dict(tool_input)
    elif isinstance(tool_input, str):
        text = tool_input.strip()
        parsed = None
        if text and len(text) <= 1_000_000:  # 限制解析长度，防止 DoS
            try:
                parsed = json.loads(text)
            except Exception:
                try:
                    parsed = ast.literal_eval(text)
                except (ValueError, SyntaxError, RecursionError, MemoryError):
                    parsed = None
        if isinstance(parsed, dict):
            normalized = dict(parsed)
        else:
            normalized = {"raw_arguments": tool_input}
    else:
        normalized = {}

    # 某些模型会把真正参数包在 input/args/arguments 字段里，这里先展开一层
    for wrapper_key in ("input", "args", "arguments"):
        wrapped = normalized.get(wrapper_key)
        if isinstance(wrapped, dict):
            merged = dict(wrapped)
            for k, v in normalized.items():
                if k not in ("input", "args", "arguments") and k not in merged:
                    merged[k] = v
            normalized = merged
            break
        if isinstance(wrapped, str):
            text = wrapped.strip()
            if text:
                try:
                    wrapped_obj = json.loads(text)
                except Exception:
                    try:
                        wrapped_obj = ast.literal_eval(text)
                    except Exception:
                        wrapped_obj = None
                if isinstance(wrapped_obj, dict):
                    merged = dict(wrapped_obj)
                    for k, v in normalized.items():
                        if k not in ("input", "args", "arguments") and k not in merged:
                            merged[k] = v
                    normalized = merged
                    break

    # Claude Code / OpenCode 常见 bash 工具参数兼容
    if "bash" in name and "command" not in normalized:
        for candidate in ("cmd", "bash_command", "script", "shell_command"):
            if candidate in normalized and normalized[candidate]:
                normalized["command"] = normalized[candidate]
                break
        # 保留原始 commands 字段（某些工具期望数组格式）
        if "command" not in normalized and isinstance(normalized.get("commands"), list):
            normalized["command"] = "\n".join(str(x) for x in normalized["commands"] if x is not None)
            # 同时保留原始 commands，不删除

    # Glob 工具常见参数兼容：统一补齐 pattern
    if "glob" in name:
        pattern = normalized.get("pattern")
        if not pattern:
            for candidate in ("query", "search", "glob", "mask", "file_pattern"):
                value = normalized.get(candidate)
                if isinstance(value, str) and value.strip():
                    pattern = value.strip()
                    break

        if not pattern:
            for candidate in ("path", "dir", "directory", "cwd", "root", "base_path"):
                value = normalized.get(candidate)
                if isinstance(value, str) and value.strip():
                    base = value.strip().rstrip("/")
                    pattern = f"{base}/**/*" if base else "**/*"
                    break

        if not pattern:
            raw = normalized.get("raw_arguments")
            if isinstance(raw, str) and raw.strip():
                pattern = raw.strip()

        if not pattern:
            pattern = "**/*"

        normalized["pattern"] = pattern

    return normalized


def serialize_response_obj(response: Any) -> dict:
    """序列化响应对象"""
    if hasattr(response, "model_dump"):
        return response.model_dump(exclude_none=True)
    if hasattr(response, "dict"):
        return response.dict()
    if isinstance(response, dict):
        return response
    return {}


def map_finish_reason_to_anthropic(finish_reason: Optional[str], has_tool_use: bool) -> str:
    """将 OpenAI finish_reason 映射到 Anthropic 格式"""
    if has_tool_use or finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    if finish_reason == "stop_sequence":
        return "stop_sequence"
    return "end_turn"


def convert_openai_response_to_anthropic(response: Any, model_name: str) -> dict:
    """将 OpenAI 响应转换为 Anthropic 格式"""
    payload = serialize_response_obj(response)
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
                try:
                    parsed = ast.literal_eval(raw_args)
                    tool_input = parsed if isinstance(parsed, dict) else {"raw_arguments": raw_args}
                except (ValueError, SyntaxError, RecursionError, MemoryError):
                    tool_input = {"raw_arguments": raw_args}
        elif isinstance(raw_args, dict):
            tool_input = raw_args
        else:
            tool_input = {}
        tool_input = normalize_tool_input(function_data.get("name", "tool"), tool_input)

        blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": function_data.get("name", "tool"),
                "input": tool_input,
            }
        )
        if "glob" in (function_data.get("name", "").lower()):
            logger.debug(f"Normalized glob tool_use input: {json.dumps(tool_input, ensure_ascii=False)}")

    if not blocks:
        blocks = [{"type": "text", "text": ""}]

    usage = extract_usage(payload)
    has_tool_use = any(block.get("type") == "tool_use" for block in blocks)
    stop_reason = map_finish_reason_to_anthropic(finish_reason, has_tool_use)

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


def sse_event(event_name: str, data: dict) -> str:
    """生成 SSE 事件字符串"""
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def mask_secret(secret: Optional[str]) -> str:
    """遮蔽敏感信息"""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"