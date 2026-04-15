"""Claude Code配置注入器

自动配置 ~/.claude/settings.json:
- 设置 ANTHROPIC_BASE_URL 和 ANTHROPIC_API_KEY
- 更新 model 字段为代理中支持的默认模型
- 保证用户原有其他配置项完全保留
"""

import os
from typing import Optional
from .base import BaseInjector, InjectResult
from ..models_sync import ModelsSync


class ClaudeCodeInjector(BaseInjector):
    """Claude Code配置注入器"""

    def get_tool_name(self) -> str:
        return "Claude Code"

    def get_default_config_path(self) -> str:
        """获取默认配置文件路径"""
        home = os.path.expanduser("~")
        return os.path.join(home, ".claude", "settings.json")

    def inject_config(self, config_path: Optional[str] = None) -> InjectResult:
        """注入Claude Code配置

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

        # 获取代理配置
        models_sync = ModelsSync()
        proxy_config = models_sync.get_proxy_config()
        default_model = models_sync.get_models_for_claude()

        # 构建要注入的配置
        injection = {
            "env": {
                "ANTHROPIC_BASE_URL": proxy_config["base_host"],
                "ANTHROPIC_API_KEY": proxy_config["api_key"],
            },
            "model": default_model,
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

        return InjectResult(
            success=True,
            message=f"Claude Code配置成功！默认模型设置为: {default_model}",
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

        env = config.get("env", {})
        base_url = env.get("ANTHROPIC_BASE_URL", "")

        # 检查是否指向本地代理
        return "localhost" in base_url or "127.0.0.1" in base_url


import json  # noqa: E402
