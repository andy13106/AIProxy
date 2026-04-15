import os
import platform
from dataclasses import dataclass
from typing import Optional, List, Dict


@dataclass
class ToolConfigInfo:
    tool_name: str
    config_path: str
    exists: bool
    is_valid: bool = False
    current_content: Optional[str] = None
    error_message: Optional[str] = None


class ConfigDetector:
    DEFAULT_CONFIG_PATHS = {
        "ClaudeCode": {
            "Darwin": ["~/.claude/settings.json"],
            "Windows": ["~/.claude/settings.json", "~/AppData/Roaming/Claude/settings.json"],
            "Linux": ["~/.claude/settings.json"],
        },
        "OpenCode": {
            "Darwin": ["~/.opencode/config.json", "~/.config/opencode/config.json"],
            "Windows": ["~/.opencode/config.json", "~/AppData/Roaming/opencode/config.json"],
            "Linux": ["~/.opencode/config.json", "~/.config/opencode/config.json"],
        },
        "OpenClaw": {
            "Darwin": ["~/.openclaw/config.json", "~/.config/openclaw/config.json"],
            "Windows": ["~/.openclaw/config.json", "~/AppData/Roaming/openclaw/config.json"],
            "Linux": ["~/.openclaw/config.json", "~/.config/openclaw/config.json"],
        },
    }

    def __init__(self):
        self.system = platform.system()
        self.home = os.path.expanduser("~")

    def _expand_path(self, path: str) -> str:
        return os.path.expanduser(path)

    def _get_platform_paths(self, tool_name: str) -> List[str]:
        tool_configs = self.DEFAULT_CONFIG_PATHS.get(tool_name, {})
        platform_key = self.system
        if self.system == "Darwin":
            platform_key = "Darwin"
        elif self.system == "Windows":
            platform_key = "Windows"
        else:
            platform_key = "Linux"
        
        return tool_configs.get(platform_key, [])

    def detect_tool_config(self, tool_name: str) -> ToolConfigInfo:
        paths = self._get_platform_paths(tool_name)
        
        for path in paths:
            expanded_path = self._expand_path(path)
            if os.path.exists(expanded_path):
                try:
                    with open(expanded_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    return ToolConfigInfo(
                        tool_name=tool_name,
                        config_path=expanded_path,
                        exists=True,
                        is_valid=True,
                        current_content=content,
                    )
                except Exception as e:
                    return ToolConfigInfo(
                        tool_name=tool_name,
                        config_path=expanded_path,
                        exists=True,
                        is_valid=False,
                        error_message=str(e),
                    )
        
        if paths:
            return ToolConfigInfo(
                tool_name=tool_name,
                config_path=self._expand_path(paths[0]),
                exists=False,
            )
        
        return ToolConfigInfo(
            tool_name=tool_name,
            config_path="",
            exists=False,
            error_message=f"未找到 {tool_name} 的配置路径定义",
        )

    def detect_all_tools(self) -> Dict[str, ToolConfigInfo]:
        results = {}
        for tool_name in self.DEFAULT_CONFIG_PATHS.keys():
            results[tool_name] = self.detect_tool_config(tool_name)
        return results

    def get_default_config_path(self, tool_name: str) -> str:
        paths = self._get_platform_paths(tool_name)
        if paths:
            return self._expand_path(paths[0])
        return ""
