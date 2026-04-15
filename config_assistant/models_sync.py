"""模型列表智能同步模块

实现配置与代理能力的联动：
- 从数据库读取当前已配置的ModelMapping
- 提取所有可用的虚拟模型名称列表
- 智能判断目标工具是否支持自定义模型列表
"""

import os
from typing import List, Dict, Optional, Any
from dataclasses import dataclass


@dataclass
class ModelInfo:
    """模型信息"""
    virtual_name: str
    real_name: str
    provider_name: str


class ModelsSync:
    """模型列表同步器"""

    def __init__(self, db_session_factory=None):
        """
        Args:
            db_session_factory: 数据库会话工厂函数
        """
        self.db_session_factory = db_session_factory

    def get_all_models(self) -> List[ModelInfo]:
        """从数据库获取所有已配置的模型映射

        Returns:
            ModelInfo列表
        """
        if self.db_session_factory is None:
            # 如果没有提供数据库会话，尝试直接导入
            try:
                from db import SessionLocal, ModelMapping, Provider
                session_factory = SessionLocal
            except ImportError:
                return []
        else:
            session_factory = self.db_session_factory

        try:
            with session_factory() as session:
                mappings = session.query(ModelMapping).all()
                providers = {p.id: p.name for p in session.query(Provider).all()}

                models = []
                for m in mappings:
                    models.append(ModelInfo(
                        virtual_name=m.virtual_name,
                        real_name=m.real_name,
                        provider_name=providers.get(m.provider_id, "Unknown")
                    ))
                return models
        except Exception:
            return []

    def get_virtual_model_names(self) -> List[str]:
        """获取所有虚拟模型名称列表

        Returns:
            按字母排序的虚拟模型名列表
        """
        models = self.get_all_models()
        return sorted(set(m.virtual_name for m in models))

    def get_default_model(self) -> str:
        """获取默认模型

        优先返回GLM5，如果不存在则返回第一个模型
        """
        models = self.get_virtual_model_names()
        if not models:
            return "GLM5"

        if "GLM5" in models:
            return "GLM5"

        return models[0]

    def get_models_for_opencode(self) -> Dict[str, Dict[str, str]]:
        """生成OpenCode格式的模型配置

        Returns:
            {模型名: {name: 模型名}} 格式的字典
        """
        models = self.get_virtual_model_names()
        return {m: {"name": m} for m in models}

    def get_models_for_claude(self) -> str:
        """获取ClaudeCode的默认模型

        Returns:
            模型名称字符串
        """
        return self.get_default_model()

    def get_models_for_openclaw(self) -> List[Dict[str, Any]]:
        """生成OpenClaw格式的模型列表

        Returns:
            模型配置列表
        """
        models = self.get_all_models()
        result = []
        for m in models:
            result.append({
                "id": m.virtual_name,
                "name": m.virtual_name,
                "description": f"{m.virtual_name} -> {m.real_name} ({m.provider_name})"
            })
        return result

    def get_proxy_config(self) -> Dict[str, Any]:
        """获取代理服务配置信息

        Returns:
            包含base_url, api_key等的配置字典
        """
        base_host = os.getenv("PROXY_HOST", "localhost")
        base_port = os.getenv("PROXY_PORT", "8000")
        master_key = os.getenv("MASTER_KEY", "sk-admin-123456")

        return {
            "base_host": f"http://{base_host}:{base_port}",
            "base_url_v1": f"http://{base_host}:{base_port}/v1",
            "api_key": master_key,
            "host": base_host,
            "port": base_port,
        }

    def generate_opencode_config(self) -> Dict[str, Any]:
        """生成完整的OpenCode配置

        Returns:
            OpenCode配置字典
        """
        proxy_config = self.get_proxy_config()
        models = self.get_models_for_opencode()

        return {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "aiproxy": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "AIProxy",
                    "options": {
                        "baseURL": proxy_config["base_url_v1"],
                        "apiKey": proxy_config["api_key"],
                    },
                    "models": models,
                }
            },
        }

    def generate_claude_code_config(self) -> Dict[str, Any]:
        """生成完整的ClaudeCode配置

        Returns:
            ClaudeCode配置字典
        """
        proxy_config = self.get_proxy_config()
        default_model = self.get_models_for_claude()

        return {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
            "env": {
                "ANTHROPIC_BASE_URL": proxy_config["base_host"],
                "ANTHROPIC_API_KEY": proxy_config["api_key"],
            },
            "model": default_model,
        }

    def generate_openclaw_config(self) -> Dict[str, Any]:
        """生成完整的OpenClaw配置

        Returns:
            OpenClaw配置字典
        """
        proxy_config = self.get_proxy_config()
        models = self.get_models_for_openclaw()

        return {
            "providers": [
                {
                    "name": "aiproxy",
                    "type": "openai-compatible",
                    "baseUrl": proxy_config["base_url_v1"],
                    "apiKey": proxy_config["api_key"],
                    "models": models,
                }
            ]
        }

    def get_models_count(self) -> int:
        """获取模型数量

        Returns:
            模型数量
        """
        return len(self.get_virtual_model_names())

    def has_models(self) -> bool:
        """检查是否有配置的模型

        Returns:
            如果有模型返回True
        """
        return self.get_models_count() > 0
