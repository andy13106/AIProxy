from .base import BaseInjector
from .claude_code import ClaudeCodeInjector
from .opencode import OpenCodeInjector
from .openclaw import OpenClawInjector

__all__ = [
    "BaseInjector",
    "ClaudeCodeInjector",
    "OpenCodeInjector",
    "OpenClawInjector",
]

INJECTOR_MAP = {
    "ClaudeCode": ClaudeCodeInjector,
    "OpenCode": OpenCodeInjector,
    "OpenClaw": OpenClawInjector,
}


def get_injector(tool_name: str, proxy_base_url: str = "http://localhost:8000", master_key: str = "") -> BaseInjector:
    injector_class = INJECTOR_MAP.get(tool_name)
    if injector_class:
        return injector_class(proxy_base_url=proxy_base_url, master_key=master_key)
    raise ValueError(f"未找到工具 {tool_name} 的配置注入器")
