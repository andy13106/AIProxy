import json
from typing import Dict, Tuple, Optional
from .base import BaseInjector


class ClaudeCodeInjector(BaseInjector):
    def get_tool_name(self) -> str:
        return "ClaudeCode"

    def inject_config(self, current_content: str, models: list, default_model: str) -> Tuple[bool, str, str]:
        success, config = self._parse_json(current_content)
        
        if not success:
            config = {}
        
        if "env" not in config:
            config["env"] = {}
        
        config["env"]["ANTHROPIC_BASE_URL"] = self.proxy_base_url
        config["env"]["ANTHROPIC_API_KEY"] = self.master_key
        
        if default_model:
            config["model"] = default_model
        
        try:
            new_content = json.dumps(config, ensure_ascii=False, indent=2)
            return True, "配置注入成功", new_content
        except Exception as e:
            return False, f"JSON序列化失败: {str(e)}", current_content

    def generate_new_config(self, models: list, default_model: str) -> str:
        config = {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
            "env": {
                "ANTHROPIC_BASE_URL": self.proxy_base_url,
                "ANTHROPIC_API_KEY": self.master_key,
            },
            "model": default_model,
        }
        return json.dumps(config, ensure_ascii=False, indent=2)

    def preview_changes(self, current_content: str, models: list, default_model: str) -> Dict:
        success, old_config = self._parse_json(current_content)
        if not success:
            old_config = {}
        
        success, _, new_content = self.inject_config(current_content, models, default_model)
        _, new_config = self._parse_json(new_content)
        
        return {
            "old": old_config,
            "new": new_config,
            "changes": {
                "env.ANTHROPIC_BASE_URL": {
                    "old": old_config.get("env", {}).get("ANTHROPIC_BASE_URL", "未设置"),
                    "new": self.proxy_base_url,
                },
                "env.ANTHROPIC_API_KEY": {
                    "old": old_config.get("env", {}).get("ANTHROPIC_API_KEY", "未设置"),
                    "new": self.master_key[:8] + "..." if len(self.master_key) > 8 else self.master_key,
                },
                "model": {
                    "old": old_config.get("model", "未设置"),
                    "new": default_model,
                },
            },
        }
