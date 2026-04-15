import json
from typing import Dict, Tuple, Optional
from .base import BaseInjector


class OpenCodeInjector(BaseInjector):
    PROVIDER_NAME = "aiproxy"

    def get_tool_name(self) -> str:
        return "OpenCode"

    def inject_config(self, current_content: str, models: list, default_model: str) -> Tuple[bool, str, str]:
        success, config = self._parse_json(current_content)
        
        if not success:
            config = {}
        
        if "provider" not in config:
            config["provider"] = {}
        
        models_dict = {m: {"name": m} for m in models}
        
        config["provider"][self.PROVIDER_NAME] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "AIProxy",
            "options": {
                "baseURL": f"{self.proxy_base_url}/v1",
                "apiKey": self.master_key,
            },
            "models": models_dict,
        }
        
        try:
            new_content = json.dumps(config, ensure_ascii=False, indent=2)
            return True, f"配置注入成功，已添加 {len(models)} 个模型", new_content
        except Exception as e:
            return False, f"JSON序列化失败: {str(e)}", current_content

    def generate_new_config(self, models: list, default_model: str) -> str:
        models_dict = {m: {"name": m} for m in models}
        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                self.PROVIDER_NAME: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "AIProxy",
                    "options": {
                        "baseURL": f"{self.proxy_base_url}/v1",
                        "apiKey": self.master_key,
                    },
                    "models": models_dict,
                }
            },
        }
        return json.dumps(config, ensure_ascii=False, indent=2)

    def preview_changes(self, current_content: str, models: list, default_model: str) -> Dict:
        success, old_config = self._parse_json(current_content)
        if not success:
            old_config = {}
        
        success, _, new_content = self.inject_config(current_content, models, default_model)
        _, new_config = self._parse_json(new_content)
        
        old_providers = list(old_config.get("provider", {}).keys())
        new_providers = list(new_config.get("provider", {}).keys())
        
        return {
            "old": old_config,
            "new": new_config,
            "changes": {
                "providers": {
                    "old": old_providers,
                    "new": new_providers,
                },
                "aiproxy_models_count": {
                    "old": len(old_config.get("provider", {}).get(self.PROVIDER_NAME, {}).get("models", {})),
                    "new": len(models),
                },
            },
        }

    def has_aiproxy_provider(self, current_content: str) -> bool:
        success, config = self._parse_json(current_content)
        if not success:
            return False
        return self.PROVIDER_NAME in config.get("provider", {})
