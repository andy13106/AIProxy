from typing import Any, Dict, Tuple
from .base import BaseInjector


class OpenClawInjector(BaseInjector):
    """OpenClaw 配置注入器"""

    def inject(self) -> Tuple[bool, str]:
        """执行OpenClaw配置注入"""
        if not self.load_config():
            return False, "配置文件加载失败"

        ai_providers = self.modified_config.get("aiProviders", [])
        if not isinstance(ai_providers, list):
            ai_providers = []

        existing_idx = None
        for i, provider in enumerate(ai_providers):
            if isinstance(provider, dict) and provider.get("id") == "aiproxy":
                existing_idx = i
                break

        aiproxy_config = {
            "id": "aiproxy",
            "name": "AI Proxy Local",
            "type": "openai-compatible",
            "baseUrl": f"{self.proxy_base_url.rstrip('/')}/v1",
            "apiKey": self.proxy_api_key,
            "models": [{"id": m, "name": m} for m in self.model_list],
            "enabled": True,
            "defaultModel": self.default_model,
        }

        if existing_idx is not None:
            ai_providers[existing_idx] = aiproxy_config
        else:
            ai_providers.append(aiproxy_config)

        self.modified_config["aiProviders"] = ai_providers

        return self.validate_config()

    def generate_description(self) -> str:
        """生成配置变更说明"""
        changes = []
        original_providers = self.original_config.get("aiProviders", [])

        has_existing = any(
            isinstance(p, dict) and p.get("id") == "aiproxy"
            for p in original_providers
        )

        if has_existing:
            changes.append("• 更新 aiproxy 服务提供商配置")
        else:
            changes.append("• 添加 aiproxy 作为新的 AI 服务提供商")

        changes.append(f"• BaseURL: {self.proxy_base_url.rstrip('/')}/v1")
        changes.append(f"• 添加 {len(self.model_list)} 个模型到可选模型列表")
        changes.append(f"• 默认模型: {self.default_model}")

        return "OpenClaw 配置变更：\n" + "\n".join(changes)
