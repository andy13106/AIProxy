import os
from pathlib import Path
from typing import Dict, List, Optional


class ConfigDetector:
    """配置文件自动扫描检测器"""

    TOOL_CONFIGS = {
        "claude_code": {
            "display_name": "ClaudeCode",
            "filename": "settings.json",
            "standard_paths": [
                "~/.claude/settings.json",
                "%USERPROFILE%/.claude/settings.json",
                "$HOME/.claude/settings.json",
            ],
        },
        "opencode": {
            "display_name": "OpenCode",
            "filename": "opencode.json",
            "standard_paths": [
                "~/.config/opencode/opencode.json",
                "~/.config/opencode/opencode.jsonc",
                "~/.config/opencode/config.json",
                "~/.opencode/config.json",
                "%USERPROFILE%/.opencode/config.json",
                "$HOME/.opencode/config.json",
            ],
        },
        "openclaw": {
            "display_name": "OpenClaw",
            "filename": "config.json",
            "standard_paths": [
                "~/.openclaw/config.json",
                "~/.config/openclaw/config.json",
                "%USERPROFILE%/.openclaw/config.json",
            ],
        },
        "hermes": {
            "display_name": "Hermes",
            "filename": "config.yaml",
            "standard_paths": [
                "~/.hermes/config.yaml",
                "~/.config/hermes/config.yaml",
                "%USERPROFILE%/.hermes/config.yaml",
                "$HOME/.hermes/config.yaml",
            ],
        },
    }

    def __init__(self):
        self.home = Path.home()

    def _resolve_path(self, path_pattern: str) -> Path:
        """解析路径模板为实际路径"""
        resolved = os.path.expandvars(os.path.expanduser(path_pattern))
        return Path(resolved)

    def detect_tool(self, tool_id: str) -> Optional[str]:
        """检测单个工具的配置文件"""
        if tool_id not in self.TOOL_CONFIGS:
            return None

        # OpenCode 支持通过 OPENCODE_CONFIG 指定自定义配置文件路径
        if tool_id == "opencode":
            custom_path = (os.getenv("OPENCODE_CONFIG", "") or "").strip()
            if custom_path:
                try:
                    actual_custom_path = self._resolve_path(custom_path)
                    if actual_custom_path.exists() and actual_custom_path.is_file():
                        return str(actual_custom_path.absolute())
                except Exception:
                    pass

        config = self.TOOL_CONFIGS[tool_id]
        for path_pattern in config["standard_paths"]:
            try:
                actual_path = self._resolve_path(path_pattern)
                if actual_path.exists() and actual_path.is_file():
                    return str(actual_path.absolute())
            except Exception:
                continue
        return None

    def detect_all(self) -> Dict[str, Dict]:
        """检测所有工具配置文件"""
        results = {}
        for tool_id, config in self.TOOL_CONFIGS.items():
            found_path = self.detect_tool(tool_id)
            results[tool_id] = {
                "display_name": config["display_name"],
                "found": found_path is not None,
                "path": found_path,
                "config_path_hint": config["standard_paths"][0],
            }
        return results

    def get_tool_config_path(self, tool_id: str) -> str:
        """获取工具的标准配置路径（即使不存在）"""
        if tool_id not in self.TOOL_CONFIGS:
            return ""

        if tool_id == "opencode":
            custom_path = (os.getenv("OPENCODE_CONFIG", "") or "").strip()
            if custom_path:
                return str(self._resolve_path(custom_path))

        config = self.TOOL_CONFIGS[tool_id]
        return str(self._resolve_path(config["standard_paths"][0]))

    def ensure_config_dir(self, tool_id: str) -> bool:
        """确保工具的配置目录存在"""
        try:
            config_path = Path(self.get_tool_config_path(tool_id))
            config_path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except Exception:
            return False
