from typing import Any, Dict, Tuple
from .base import BaseInjector


class ClaudeCodeInjector(BaseInjector):
    """ClaudeCode 配置注入器"""

    def inject(self) -> Tuple[bool, str]:
        """执行ClaudeCode配置注入"""
        if not self.load_config():
            return False, "配置文件加载失败"

        env_config = self.modified_config.get("env", {})
        if not isinstance(env_config, dict):
            env_config = {}

        env_config["ANTHROPIC_BASE_URL"] = self.proxy_base_url
        env_config["ANTHROPIC_API_KEY"] = self.proxy_api_key

        self.modified_config["env"] = env_config
        self.modified_config["model"] = self.default_model

        self.modified_config["$schema"] = (
            self.modified_config.get("$schema")
            or "https://json.schemastore.org/claude-code-settings.json"
        )

        return self.validate_config()

    def generate_description(self) -> str:
        """生成配置变更说明"""
        changes = []
        original_env = self.original_config.get("env", {})

        if original_env.get("ANTHROPIC_BASE_URL") != self.proxy_base_url:
            changes.append(
                f"• 设置 ANTHROPIC_BASE_URL = {self.proxy_base_url}"
            )

        if "ANTHROPIC_API_KEY" not in original_env:
            changes.append("• 更新 ANTHROPIC_API_KEY 为本地代理密钥")
        else:
            changes.append("• 添加 ANTHROPIC_API_KEY（本地代理访问密钥）")

        original_model = self.original_config.get("model", "")
        if original_model != self.default_model:
            changes.append(f"• 默认模型: {original_model or '(无'} -> {self.default_model}")

        if not changes:
            return "配置已为最新状态，无需变更。"

        return "ClaudeCode 配置变更：\n" + "\n".join(changes)
