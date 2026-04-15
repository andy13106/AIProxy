from typing import List, Dict, Optional
from db import SessionLocal, ModelMapping


class ModelSyncer:
    def __init__(self, proxy_base_url: str = "http://localhost:8000", master_key: str = ""):
        self.proxy_base_url = proxy_base_url
        self.master_key = master_key

    def get_available_models(self) -> List[str]:
        try:
            with SessionLocal() as session:
                mappings = session.query(ModelMapping).all()
                return sorted(set(m.virtual_name for m in mappings))
        except Exception:
            return ["GLM5"]

    def get_default_model(self) -> str:
        models = self.get_available_models()
        preferred = ["GLM5", "gpt-4", "claude-3-5-sonnet-20241022"]
        for p in preferred:
            if p in models:
                return p
        return models[0] if models else "GLM5"

    def get_models_count(self) -> int:
        return len(self.get_available_models())

    def format_models_for_opencode(self) -> Dict[str, Dict]:
        models = self.get_available_models()
        return {m: {"name": m} for m in models}

    def format_models_for_openclaw(self) -> List[Dict]:
        models = self.get_available_models()
        return [{"id": m, "name": m} for m in models]

    def get_proxy_config(self) -> Dict:
        return {
            "base_url": self.proxy_base_url,
            "api_key": self.master_key,
            "models": self.get_available_models(),
            "default_model": self.get_default_model(),
        }
