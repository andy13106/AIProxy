from typing import Tuple

from .base import BaseInjector


class HermesInjector(BaseInjector):
    """Hermes 配置注入器（config.yaml）"""

    def __init__(
        self,
        config_path: str,
        proxy_base_url: str,
        proxy_api_key: str,
        model_list: list,
        default_model: str,
    ):
        super().__init__(config_path, proxy_base_url, proxy_api_key, model_list, default_model)
        self.original_text = ""
        self.modified_text = ""

    def _build_model_block(self) -> str:
        model_name = self.default_model if self.default_model in self.model_list else self.model_list[0]
        return "\n".join(
            [
                "model:",
                f'  provider: "custom"',
                f'  api_base: "{self.proxy_base_url.rstrip("/")}/v1"',
                f'  api_key: "{self.proxy_api_key}"',
                f'  model_name: "{model_name}"',
                "",
            ]
        )

    def load_config(self) -> bool:
        try:
            if self.config_path.exists():
                self.original_text = self.config_path.read_text(encoding="utf-8")
            else:
                self.original_text = ""
            self.modified_text = self.original_text
            self.original_config = {"_raw_yaml": self.original_text}
            self.modified_config = {"_raw_yaml": self.modified_text}
            return True
        except Exception:
            return False

    def save_config(self) -> Tuple[bool, str]:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(self.modified_text, encoding="utf-8")
            return True, ""
        except Exception as e:
            return False, str(e)

    def inject(self) -> Tuple[bool, str]:
        if not self.load_config():
            return False, "配置文件加载失败"

        new_block = self._build_model_block().splitlines()
        lines = self.original_text.splitlines()

        model_start = None
        for idx, line in enumerate(lines):
            if line.strip() == "model:" and (line.startswith("model:") or not line[:1].isspace()):
                model_start = idx
                break

        if model_start is None:
            merged = list(lines)
            if merged and merged[-1].strip():
                merged.append("")
            merged.extend(new_block)
            self.modified_text = "\n".join(merged).rstrip() + "\n"
        else:
            model_end = len(lines)
            for idx in range(model_start + 1, len(lines)):
                line = lines[idx]
                if line.startswith((" ", "\t")):
                    continue
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if ":" in stripped:
                    model_end = idx
                    break

            merged = lines[:model_start] + new_block + lines[model_end:]
            self.modified_text = "\n".join(merged).rstrip() + "\n"

        self.modified_config = {
            "format": "yaml",
            "model.provider": "custom",
            "model.api_base": f"{self.proxy_base_url.rstrip('/')}/v1",
            "model.api_key": "***",
            "model.model_name": self.default_model,
        }
        return self.validate_config()

    def validate_config(self) -> Tuple[bool, str]:
        if not self.modified_text.strip():
            return False, "生成的配置为空"
        if "model:" not in self.modified_text:
            return False, "缺少 model 配置块"
        return True, ""

    def generate_description(self) -> str:
        return (
            "Hermes 配置变更：\n"
            f"• 写入/更新 model.provider = custom\n"
            f"• api_base -> {self.proxy_base_url.rstrip('/')}/v1\n"
            f"• 默认模型 -> {self.default_model}\n"
            "• api_key 使用本地代理访问密钥"
        )
