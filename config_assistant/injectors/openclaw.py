"""OpenClaw配置注入器

自动配置OpenClaw:
- 添加本地代理作为可选的AI服务提供商
- 保持原有提供商配置完全可用
"""

import os
import json
from typing import Optional, List, Dict, Any
from .base import BaseInjector, InjectResult
from ..models_sync import ModelsSync


class OpenClawInjector(BaseInjector):
    """OpenClaw配置注入器"""

    PROVIDER_NAME = "aiproxy"

    def get_tool_name(self) -> str:
        return "OpenClaw"

    def get_default_config_path(self) -> str:
        """获取默认配置文件路径"""
        home = os.path.expanduser("~")
        # OpenClaw可能有多个可能的配置位置
        candidates = [
            os.path.join(home, ".openclaw", "config.json"),
            os.path.join(home, ".config", "openclaw", "config.json"),
        ]

        # 返回第一个存在的路径，或默认路径
        for path in candidates:
            if os.path.exists(path):
                return path

        return candidates[0]

    def find_config_path(self) -> Optional[str]:
        """查找配置文件路径"""
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".openclaw", "config.json"),
            os.path.join(home, ".config", "openclaw", "config.json"),
            os.path.join(home, "AppData", "Roaming", "OpenClaw", "config.json"),
        ]

        for path in candidates:
            if os.path.exists(path):
                return path

        return None

    def inject_config(self, config_path: Optional[str] = None) -> InjectResult:
        """注入OpenClaw配置

        Args:
            config_path: 配置文件路径，None则自动查找

        Returns:
            InjectResult: 注入结果
        """
        if config_path is None:
            config_path = self.find_config_path() or self.get_default_config_path()

        # 读取现有配置
        original_config, error = self.read_config(config_path)
        if error and original_config is None:
            return InjectResult(
                success=False,
                message=f"读取配置文件失败: {error}",
                error_details=error
            )

        original_content = json.dumps(original_config, ensure_ascii=False, indent=2) if original_config else None

        # 获取代理配置和模型列表
        models_sync = ModelsSync()
        proxy_config = models_sync.get_proxy_config()
        models = models_sync.get_models_for_openclaw()

        # 构建要注入的配置
        aiproxy_provider = {
            "name": self.PROVIDER_NAME,
            "type": "openai-compatible",
            "baseUrl": proxy_config["base_url_v1"],
            "apiKey": proxy_config["api_key"],
            "models": models,
        }

        # OpenClaw的配置结构可能是 providers 数组
        injection = {
            "providers": [aiproxy_provider]
        }

        # 合并配置
        if original_config is None:
            new_config = injection
        else:
            # 特殊处理：如果已有providers数组，追加而不是替换
            if "providers" in original_config and isinstance(original_config["providers"], list):
                # 检查是否已存在aiproxy
                existing_providers = original_config["providers"]
                aiproxy_exists = any(
                    p.get("name") == self.PROVIDER_NAME for p in existing_providers
                )

                if aiproxy_exists:
                    # 更新现有配置
                    new_providers = []
                    for p in existing_providers:
                        if p.get("name") == self.PROVIDER_NAME:
                            new_providers.append(aiproxy_provider)
                        else:
                            new_providers.append(p)
                    original_config["providers"] = new_providers
                    new_config = original_config
                else:
                    # 追加新provider
                    original_config["providers"].append(aiproxy_provider)
                    new_config = original_config
            else:
                # 使用标准合并
                new_config = self.merge_configs(original_config, injection)

        # 验证配置
        is_valid, error = self.validate_config(new_config)
        if not is_valid:
            return InjectResult(
                success=False,
                message="配置验证失败",
                original_content=original_content,
                error_details=error
            )

        # 创建备份
        backup_success, backup_msg, backup_path = self.create_backup(config_path)
        if not backup_success:
            return InjectResult(
                success=False,
                message=f"备份失败: {backup_msg}",
                original_content=original_content,
                error_details=backup_msg
            )

        # 写入新配置
        write_success, write_error = self.write_config(config_path, new_config)
        if not write_success:
            return InjectResult(
                success=False,
                message=f"写入配置失败: {write_error}",
                original_content=original_content,
                backup_path=backup_path,
                error_details=write_error
            )

        new_content = json.dumps(new_config, ensure_ascii=False, indent=2)
        models_count = len(models)

        return InjectResult(
            success=True,
            message=f"OpenClaw配置成功！已添加 aiproxy provider，包含 {models_count} 个模型",
            original_content=original_content,
            new_content=new_content,
            backup_path=backup_path
        )

    def get_current_config(self, config_path: Optional[str] = None) -> Optional[dict]:
        """获取当前配置

        Args:
            config_path: 配置文件路径

        Returns:
            当前配置字典
        """
        if config_path is None:
            config_path = self.find_config_path() or self.get_default_config_path()

        config, _ = self.read_config(config_path)
        return config

    def is_configured(self, config_path: Optional[str] = None) -> bool:
        """检查是否已配置代理

        Args:
            config_path: 配置文件路径

        Returns:
            如果已配置返回True
        """
        config = self.get_current_config(config_path)
        if not config:
            return False

        providers = config.get("providers", [])
        if isinstance(providers, list):
            return any(p.get("name") == self.PROVIDER_NAME for p in providers)
        return False

    def get_injected_models_count(self, config_path: Optional[str] = None) -> int:
        """获取已注入的模型数量

        Args:
            config_path: 配置文件路径

        Returns:
            模型数量
        """
        config = self.get_current_config(config_path)
        if not config:
            return 0

        providers = config.get("providers", [])
        if isinstance(providers, list):
            for p in providers:
                if p.get("name") == self.PROVIDER_NAME:
                    return len(p.get("models", []))
        return 0
