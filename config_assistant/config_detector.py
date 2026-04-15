"""配置文件路径扫描模块

自动检测常见系统路径，识别已存在的AI工具配置文件。
支持Windows/Mac/Linux跨平台路径适配。
"""

import os
import platform
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ConfigFileInfo:
    """配置文件信息"""
    tool_name: str
    file_path: str
    exists: bool
    content: Optional[str] = None
    error: Optional[str] = None


class ConfigDetector:
    """AI工具配置文件检测器"""

    # 各工具的配置文件路径模板（按操作系统）
    CONFIG_PATHS = {
        "claude_code": {
            "Windows": [
                "{home}/.claude/settings.json",
                "{home}/AppData/Roaming/Claude/settings.json",
            ],
            "Darwin": [  # macOS
                "{home}/.claude/settings.json",
                "{home}/Library/Application Support/Claude/settings.json",
            ],
            "Linux": [
                "{home}/.claude/settings.json",
                "{home}/.config/claude/settings.json",
            ],
        },
        "opencode": {
            "Windows": [
                "{home}/.opencode/config.json",
                "{home}/AppData/Roaming/opencode/config.json",
            ],
            "Darwin": [
                "{home}/.opencode/config.json",
                "{home}/Library/Application Support/opencode/config.json",
            ],
            "Linux": [
                "{home}/.opencode/config.json",
                "{home}/.config/opencode/config.json",
            ],
        },
        "openclaw": {
            "Windows": [
                "{home}/.openclaw/config.json",
                "{home}/AppData/Roaming/OpenClaw/config.json",
                "{home}/.config/openclaw/config.json",
            ],
            "Darwin": [
                "{home}/.openclaw/config.json",
                "{home}/Library/Application Support/OpenClaw/config.json",
            ],
            "Linux": [
                "{home}/.openclaw/config.json",
                "{home}/.config/openclaw/config.json",
            ],
        },
    }

    def __init__(self):
        self.system = platform.system()
        self.home_dir = str(Path.home())

    def _expand_path(self, path_template: str) -> str:
        """展开路径模板中的变量"""
        return path_template.format(home=self.home_dir)

    def _read_file_content(self, file_path: str) -> Tuple[Optional[str], Optional[str]]:
        """读取文件内容，返回(content, error)"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read(), None
        except FileNotFoundError:
            return None, "文件不存在"
        except PermissionError:
            return None, "权限不足，无法读取"
        except Exception as e:
            return None, f"读取错误: {str(e)}"

    def detect_tool(self, tool_name: str) -> ConfigFileInfo:
        """检测指定工具的配置文件

        Args:
            tool_name: 工具名称 (claude_code, opencode, openclaw)

        Returns:
            ConfigFileInfo: 配置文件信息
        """
        if tool_name not in self.CONFIG_PATHS:
            return ConfigFileInfo(
                tool_name=tool_name,
                file_path="",
                exists=False,
                error=f"未知工具: {tool_name}"
            )

        # 获取当前系统的候选路径
        paths_for_system = self.CONFIG_PATHS[tool_name].get(self.system, [])
        if not paths_for_system:
            # 如果当前系统没有特定路径，尝试通用路径
            paths_for_system = self.CONFIG_PATHS[tool_name].get("Linux", [])

        for path_template in paths_for_system:
            full_path = self._expand_path(path_template)

            if os.path.exists(full_path):
                content, error = self._read_file_content(full_path)
                return ConfigFileInfo(
                    tool_name=tool_name,
                    file_path=full_path,
                    exists=True,
                    content=content,
                    error=error
                )

        # 没有找到任何配置文件，返回第一个候选路径作为建议位置
        suggested_path = self._expand_path(paths_for_system[0]) if paths_for_system else ""
        return ConfigFileInfo(
            tool_name=tool_name,
            file_path=suggested_path,
            exists=False
        )

    def detect_all_tools(self) -> Dict[str, ConfigFileInfo]:
        """检测所有支持的工具

        Returns:
            Dict[str, ConfigFileInfo]: 工具名称到配置信息的映射
        """
        results = {}
        for tool_name in self.CONFIG_PATHS.keys():
            results[tool_name] = self.detect_tool(tool_name)
        return results

    def get_detected_tools_summary(self) -> Dict[str, any]:
        """获取检测结果的摘要信息

        Returns:
            包含检测统计和详细信息的字典
        """
        results = self.detect_all_tools()

        detected = []
        not_detected = []

        for tool_name, info in results.items():
            if info.exists:
                detected.append({
                    "name": tool_name,
                    "path": info.file_path,
                    "has_content": info.content is not None
                })
            else:
                not_detected.append({
                    "name": tool_name,
                    "suggested_path": info.file_path
                })

        return {
            "total_tools": len(results),
            "detected_count": len(detected),
            "not_detected_count": len(not_detected),
            "detected": detected,
            "not_detected": not_detected,
            "details": results
        }

    @staticmethod
    def is_running_in_docker() -> bool:
        """检测当前是否运行在Docker容器中

        Returns:
            bool: 如果在Docker中返回True
        """
        # 检查 /.dockerenv 文件
        if os.path.exists("/.dockerenv"):
            return True

        # 检查 /proc/1/cgroup
        try:
            with open("/proc/1/cgroup", "r") as f:
                cgroup_content = f.read()
                if "docker" in cgroup_content.lower():
                    return True
        except (FileNotFoundError, PermissionError):
            pass

        return False

    def get_environment_info(self) -> Dict[str, str]:
        """获取当前环境信息

        Returns:
            包含系统信息的字典
        """
        return {
            "system": self.system,
            "home_dir": self.home_dir,
            "in_docker": str(self.is_running_in_docker()),
            "python_version": platform.python_version(),
        }
