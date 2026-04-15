"""OpenCode配置注入器

自动配置 ~/.opencode/config.json:
- 新增名为 aiproxy 的provider配置
- 自动注入代理服务中已配置的所有模型到模型列表
- baseURL 和 apiKey 使用本地代理服务地址和MASTER_KEY
- 不影响其他已配置的provider
"""

import os
import json
from typing import Optional
from .base import BaseInjector, InjectResult
from ..models_sync import ModelsSync


class OpenCodeInjector(BaseInjector):
    """OpenCode配置注入器"""

    PROVIDER_NAME = "aiproxy"

    def get_tool_name(self) -> str:
        return "OpenCode"

    def get_default_config_path(self) -> str:
        """获取默认配置文件路径"""
        home = os.path.expanduser("~")
        return os.path.join(home, ".opencode", "config.json")

    def inject_config(self, config_path: Optional[str] = None) -> InjectResult:
        """注入OpenCode配置

        Args:
            config_path: 配置文件路径，None则使用默认路径

        Returns:
            InjectResult: 注入结果
        """
        if config_path is None:
            config_path = self.get_default_config_path()

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
        models = models_sync.get_models_for_opencode()

        # 构建要注入的provider配置
        aiproxy_provider = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "AIProxy",
            "options": {
                "baseURL": proxy_config["base_url_v1"],
                "apiKey": proxy_config["api_key"],
            },
            "models": models,
        }

        # 构建要注入的配置
        injection = {
            "provider": {
                self.PROVIDER_NAME: aiproxy_provider
            }
        }

        # 合并配置
        if original_config is None:
            new_config = injection
        else:
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
            message=f"OpenCode配置成功！已注入 {models_count} 个代理模型",
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
            config_path = self.get_default_config_path()

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

        providers = config.get("provider", {})
        return self.PROVIDER_NAME in providers

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

        provider = config.get("provider", {}).get(self.PROVIDER_NAME, {})
        models = provider.get("models", {})
        return len(models)
