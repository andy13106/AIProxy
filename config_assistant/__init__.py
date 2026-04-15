"""AI工具配置助手模块

提供自动检测、智能分析和一键配置AI编程工具的能力。
"""

from .config_detector import ConfigDetector
from .backup_manager import BackupManager
from .models_sync import ModelsSync
from .ai_analyzer import AIConfigAnalyzer, ConfigAnalysis
from .injectors.claude_code import ClaudeCodeInjector
from .injectors.opencode import OpenCodeInjector
from .injectors.openclaw import OpenClawInjector

__all__ = [
    "ConfigDetector",
    "BackupManager",
    "ModelsSync",
    "AIConfigAnalyzer",
    "ConfigAnalysis",
    "ClaudeCodeInjector",
    "OpenCodeInjector",
    "OpenClawInjector",
]
