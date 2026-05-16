import json
from typing import Tuple

import toml

from .base import BaseInjector


class CodexInjector(BaseInjector):
    """Codex 配置注入器（config.toml）"""

    provider_id = "aiproxy"

    def load_config(self) -> bool:
        try:
            if self.config_path.exists():
                self.original_text = self.config_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                self.original_config = toml.loads(self.original_text or "")
            else:
                self.original_text = ""
                self.original_config = {}
            self.modified_config = toml.loads(toml.dumps(self.original_config))
            return True
        except Exception:
            return False

    def save_config(self) -> Tuple[bool, str]:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.modified_text = toml.dumps(self.modified_config)
            self.config_path.write_text(self.modified_text, encoding="utf-8")
            self._save_auth_config()
            return True, ""
        except Exception as e:
            return False, str(e)

    def validate_config(self) -> Tuple[bool, str]:
        try:
            toml.dumps(self.modified_config)
            return True, ""
        except Exception as e:
            return False, str(e)

    def inject(self) -> Tuple[bool, str]:
        if not self.load_config():
            return False, "配置文件加载失败"

        providers = self.modified_config.get("model_providers", {})
        if not isinstance(providers, dict):
            providers = {}

        providers[self.provider_id] = {
            "name": "AIProxy",
            "base_url": f"{self.proxy_base_url.rstrip('/')}/v1",
            "wire_api": "responses",
            "requires_openai_auth": True,
        }

        self.modified_config["model_providers"] = providers
        self.modified_config["model_provider"] = self.provider_id
        self.modified_config["model"] = self.default_model
        self.modified_text = toml.dumps(self.modified_config)

        return self.validate_config()

    def generate_description(self) -> str:
        changes = []
        original_providers = self.original_config.get("model_providers", {})
        if not isinstance(original_providers, dict):
            original_providers = {}

        if self.provider_id not in original_providers:
            changes.append("• 新增 aiproxy model provider (本地代理服务)")
        else:
            changes.append("• 更新 aiproxy model provider 配置")

        original_provider = self.original_config.get("model_provider", "")
        if original_provider != self.provider_id:
            changes.append(
                f"• 默认 provider: {original_provider or '(无)'} -> {self.provider_id}"
            )

        original_model = self.original_config.get("model", "")
        if original_model != self.default_model:
            changes.append(f"• 默认模型: {original_model or '(无)'} -> {self.default_model}")

        changes.append(f"• Base URL: {self.proxy_base_url.rstrip('/')}/v1")
        changes.append("• wire_api: responses")
        changes.append("• 更新 ~/.codex/auth.json 中的 OPENAI_API_KEY 为本地代理密钥")

        return "Codex 配置变更：\n" + "\n".join(changes)

    def _save_auth_config(self) -> None:
        auth_path = self.config_path.parent / "auth.json"
        if auth_path.exists():
            try:
                auth_config = json.loads(auth_path.read_text(encoding="utf-8") or "{}")
            except Exception:
                auth_config = {}
        else:
            auth_config = {}

        if not isinstance(auth_config, dict):
            auth_config = {}

        auth_config["OPENAI_API_KEY"] = self.proxy_api_key
        auth_path.write_text(
            json.dumps(auth_config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
