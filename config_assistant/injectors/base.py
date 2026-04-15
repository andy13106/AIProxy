import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class BaseInjector(ABC):
    """配置注入器基类"""

    def __init__(
        self,
        config_path: str,
        proxy_base_url: str,
        proxy_api_key: str,
        model_list: list,
        default_model: str,
    ):
        self.config_path = Path(config_path)
        self.proxy_base_url = proxy_base_url
        self.proxy_api_key = proxy_api_key
        self.model_list = model_list
        self.default_model = default_model
        self.original_config: Dict[str, Any] = {}
        self.modified_config: Dict[str, Any] = {}

    def load_config(self) -> bool:
        """加载现有配置文件"""
        try:
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.original_config = json.load(f)
            else:
                self.original_config = {}
            self.modified_config = self.original_config.copy()
            return True
        except (json.JSONDecodeError, UnicodeDecodeError, IOError):
            return False

    def save_config(self) -> Tuple[bool, str]:
        """保存修改后的配置
        
        Returns:
            (是否成功, 错误信息)
        """
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.modified_config, f, ensure_ascii=False, indent=2)
            return True, ""
        except IOError as e:
            return False, str(e)

    def validate_config(self) -> Tuple[bool, str]:
        """验证配置合法性
        
        Returns:
            (是否有效, 错误信息)
        """
        try:
            json.dumps(self.modified_config)
            return True, ""
        except Exception as e:
            return False, str(e)

    def get_config_diff(self) -> Dict[str, Any]:
        """获取配置变更对比"""
        return {
            "original": self.original_config,
            "modified": self.modified_config,
        }

    def safe_deep_merge(self, target: Dict, source: Dict) -> Dict:
        """安全地深度合并两个字典（只增不改）"""
        result = target.copy()
        for key, value in source.items():
            if isinstance(value, dict) and key in result and isinstance(result[key], dict):
                result[key] = self.safe_deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @abstractmethod
    def inject(self) -> Tuple[bool, str]:
        """执行配置注入
        
        Returns:
            (是否成功, 错误信息)
        """
        pass

    @abstractmethod
    def generate_description(self) -> str:
        """生成配置变更说明"""
        pass
