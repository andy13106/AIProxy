from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional
import json


class BaseInjector(ABC):
    def __init__(self, proxy_base_url: str = "http://localhost:8000", master_key: str = ""):
        self.proxy_base_url = proxy_base_url
        self.master_key = master_key

    @abstractmethod
    def inject_config(self, current_content: str, models: list, default_model: str) -> Tuple[bool, str, str]:
        pass

    @abstractmethod
    def get_tool_name(self) -> str:
        pass

    def _parse_json(self, content: str) -> Tuple[bool, Dict]:
        try:
            return True, json.loads(content)
        except json.JSONDecodeError as e:
            return False, {"error": str(e)}

    def _validate_json(self, content: str) -> Tuple[bool, str]:
        try:
            json.loads(content)
            return True, ""
        except json.JSONDecodeError as e:
            return False, str(e)
