from typing import Any, Dict, Tuple
from .base import BaseInjector


class OpenCodeInjector(BaseInjector):
    """OpenCode 配置注入器"""

    def inject(self) -> Tuple[bool, str]:
        """执行OpenCode配置注入"""
        if not self.load_config():
            return False, "配置文件加载失败"

        providers = self.modified_config.get("provider", {})
        if not isinstance(providers, dict):
            providers = {}

        models_dict = {m: {"name": m} for m in self.model_list}

        aiproxy_provider = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "AIProxy",
            "options": {
                "baseURL": f"{self.proxy_base_url.rstrip('/')}/v1",
                "apiKey": self.proxy_api_key,
            },
            "models": models_dict,
        }

        providers["aiproxy"] = aiproxy_provider
        self.modified_config["provider"] = providers

        self.modified_config["$schema"] = (
            self.modified_config.get("$schema")
            or "https://opencode.ai/config.json"
        )

        return self.validate_config()

    def generate_description(self) -> str:
        """生成配置变更说明"""
        changes = []
        original_providers = self.original_config.get("provider", {})

        if "aiproxy" not in original_providers:
            changes.append("• 新增 aiproxy provider (本地代理服务)")
        else:
            changes.append("• 更新 aiproxy provider 配置")

        changes.append(f"• BaseURL: {self.proxy_base_url.rstrip('/')}/v1")
        changes.append(f"• 注入 {len(self.model_list)} 个代理模型到模型列表")

        model_names = ", ".join(self.model_list[:5])
        if len(self.model_list) > 5:
            model_names += f" 等共 {len(self.model_list)} 个"
        changes.append(f"• 可用模型: {model_names}")

        return "OpenCode 配置变更：\n" + "\n".join(changes)
