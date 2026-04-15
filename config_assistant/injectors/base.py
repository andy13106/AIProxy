"""配置注入器基类

定义统一的注入器接口和通用功能。
"""

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple


@dataclass
class InjectResult:
    """注入结果"""
    success: bool
    message: str
    original_content: Optional[str] = None
    new_content: Optional[str] = None
    backup_path: Optional[str] = None
    error_details: Optional[str] = None


class BaseInjector(ABC):
    """配置注入器基类"""

    def __init__(self, backup_manager=None):
        """
        Args:
            backup_manager: 备份管理器实例
        """
        self.backup_manager = backup_manager

    @abstractmethod
    def get_tool_name(self) -> str:
        """返回工具名称"""
        pass

    @abstractmethod
    def get_default_config_path(self) -> str:
        """返回默认配置文件路径"""
        pass

    @abstractmethod
    def inject_config(self, config_path: Optional[str] = None) -> InjectResult:
        """注入配置到目标文件

        Args:
            config_path: 配置文件路径，None则使用默认路径

        Returns:
            InjectResult: 注入结果
        """
        pass

    def read_config(self, config_path: str) -> Tuple[Optional[Dict], Optional[str]]:
        """读取配置文件

        Args:
            config_path: 配置文件路径

        Returns:
            (config_dict, error_message)
        """
        try:
            if not os.path.exists(config_path):
                return {}, None

            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
                if not content.strip():
                    return {}, None
                return json.loads(content), None
        except json.JSONDecodeError as e:
            return None, f"JSON解析错误: {str(e)}"
        except Exception as e:
            return None, f"读取错误: {str(e)}"

    def write_config(self, config_path: str, config: Dict) -> Tuple[bool, Optional[str]]:
        """写入配置文件

        Args:
            config_path: 配置文件路径
            config: 配置字典

        Returns:
            (success, error_message)
        """
        try:
            # 确保目录存在
            config_dir = os.path.dirname(config_path)
            if config_dir and not os.path.exists(config_dir):
                os.makedirs(config_dir, exist_ok=True)

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True, None
        except PermissionError:
            return False, "权限不足，无法写入文件"
        except Exception as e:
            return False, f"写入错误: {str(e)}"

    def create_backup(self, config_path: str) -> Tuple[bool, str, Optional[str]]:
        """创建备份

        Args:
            config_path: 配置文件路径

        Returns:
            (success, message, backup_path)
        """
        # 如果原文件不存在，跳过备份（这是正常情况，不是错误）
        if not os.path.exists(config_path):
            return True, "原文件不存在，无需备份", None

        if self.backup_manager:
            success, msg, backup_info = self.backup_manager.create_backup(config_path)
            if success and backup_info:
                return True, msg, backup_info.backup_path
            # 如果backup_manager返回失败，但文件存在，使用简单备份
            if not success:
                return self._simple_backup(config_path)
            return success, msg, None
        else:
            return self._simple_backup(config_path)

    def _simple_backup(self, config_path: str) -> Tuple[bool, str, Optional[str]]:
        """简单的备份实现"""
        from datetime import datetime
        backup_path = f"{config_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        try:
            import shutil
            shutil.copy2(config_path, backup_path)
            return True, f"备份成功: {backup_path}", backup_path
        except Exception as e:
            return False, f"备份失败: {str(e)}", None

    def merge_configs(self, original: Dict, injection: Dict) -> Dict:
        """合并配置，递归更新

        核心原则：只做增量添加，不破坏原有配置

        Args:
            original: 原始配置
            injection: 要注入的配置

        Returns:
            合并后的配置
        """
        result = original.copy()

        for key, value in injection.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # 递归合并字典
                result[key] = self.merge_configs(result[key], value)
            else:
                # 直接覆盖或添加
                result[key] = value

        return result

    def validate_config(self, config: Dict) -> Tuple[bool, Optional[str]]:
        """验证配置是否有效

        Args:
            config: 配置字典

        Returns:
            (is_valid, error_message)
        """
        try:
            # 尝试序列化为JSON
            json.dumps(config, ensure_ascii=False)
            return True, None
        except Exception as e:
            return False, f"配置验证失败: {str(e)}"

    def generate_preview(self, original: Optional[Dict], new: Dict) -> str:
        """生成配置变更预览

        Args:
            original: 原始配置
            new: 新配置

        Returns:
            格式化的差异字符串
        """
        original_str = json.dumps(original, ensure_ascii=False, indent=2) if original else "(空文件)"
        new_str = json.dumps(new, ensure_ascii=False, indent=2)

        preview = f"""=== 配置变更预览 ===

【原始配置】
{original_str}

【新配置】
{new_str}
"""
        return preview
