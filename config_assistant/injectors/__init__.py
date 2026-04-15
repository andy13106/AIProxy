"""配置注入器模块

提供各AI工具的配置注入实现。
"""

from .base import BaseInjector, InjectResult
from .claude_code import ClaudeCodeInjector
from .opencode import OpenCodeInjector
from .openclaw import OpenClawInjector

__all__ = [
    "BaseInjector",
    "InjectResult",
    "ClaudeCodeInjector",
    "OpenCodeInjector",
    "OpenClawInjector",
]
