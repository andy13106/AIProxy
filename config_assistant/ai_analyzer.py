import json
from typing import Dict, Tuple, Optional
import re


class AIConfigAnalyzer:
    def __init__(self):
        pass

    def analyze_config_structure(self, config_content: str, tool_name: str) -> Dict:
        try:
            config = json.loads(config_content)
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"JSON解析失败: {str(e)}",
                "structure": {},
                "fields": [],
            }
        
        structure = self._analyze_structure(config)
        fields = self._extract_fields(config)
        
        analysis = {
            "success": True,
            "structure": structure,
            "fields": fields,
            "tool_specific": self._analyze_tool_specific(config, tool_name),
        }
        
        return analysis

    def _analyze_structure(self, config: Dict, prefix: str = "") -> Dict:
        result = {}
        for key, value in config.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                result[key] = {
                    "type": "object",
                    "children": self._analyze_structure(value, full_key),
                }
            elif isinstance(value, list):
                result[key] = {
                    "type": "array",
                    "length": len(value),
                    "item_type": type(value[0]).__name__ if value else "unknown",
                }
            else:
                result[key] = {
                    "type": type(value).__name__,
                    "value": value if not isinstance(value, str) or len(value) < 50 else value[:47] + "...",
                }
        return result

    def _extract_fields(self, config: Dict, prefix: str = "") -> list:
        fields = []
        for key, value in config.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                fields.extend(self._extract_fields(value, full_key))
            else:
                fields.append({
                    "path": full_key,
                    "type": type(value).__name__,
                    "value": str(value)[:100] if value else "",
                })
        return fields

    def _analyze_tool_specific(self, config: Dict, tool_name: str) -> Dict:
        result = {}
        
        if tool_name == "ClaudeCode":
            result["has_env"] = "env" in config
            result["has_model"] = "model" in config
            result["current_base_url"] = config.get("env", {}).get("ANTHROPIC_BASE_URL", "")
            result["current_api_key"] = bool(config.get("env", {}).get("ANTHROPIC_API_KEY"))
            result["current_model"] = config.get("model", "")
        
        elif tool_name == "OpenCode":
            result["has_provider"] = "provider" in config
            result["providers"] = list(config.get("provider", {}).keys())
            result["has_aiproxy"] = "aiproxy" in config.get("provider", {})
            if result["has_aiproxy"]:
                aiproxy = config["provider"]["aiproxy"]
                result["aiproxy_models_count"] = len(aiproxy.get("models", {}))
        
        elif tool_name == "OpenClaw":
            result["has_providers"] = "providers" in config
            result["providers"] = list(config.get("providers", {}).keys())
            result["has_aiproxy"] = "aiproxy" in config.get("providers", {})
            result["default_provider"] = config.get("defaultProvider", "")
        
        return result

    def generate_config_summary(self, config_content: str, tool_name: str) -> str:
        analysis = self.analyze_config_structure(config_content, tool_name)
        
        if not analysis["success"]:
            return f"配置文件解析失败: {analysis['error']}"
        
        summary_lines = [f"## {tool_name} 配置分析结果\n"]
        
        tool_specific = analysis.get("tool_specific", {})
        
        if tool_name == "ClaudeCode":
            summary_lines.append(f"- 环境变量配置: {'已设置' if tool_specific.get('has_env') else '未设置'}")
            summary_lines.append(f"- 默认模型: {tool_specific.get('current_model', '未设置')}")
            summary_lines.append(f"- Base URL: {tool_specific.get('current_base_url', '未设置')}")
            summary_lines.append(f"- API Key: {'已配置' if tool_specific.get('current_api_key') else '未配置'}")
        
        elif tool_name == "OpenCode":
            summary_lines.append(f"- Provider 配置: {'已设置' if tool_specific.get('has_provider') else '未设置'}")
            summary_lines.append(f"- 现有 Providers: {', '.join(tool_specific.get('providers', [])) or '无'}")
            if tool_specific.get("has_aiproxy"):
                summary_lines.append(f"- AIProxy 已配置，模型数: {tool_specific.get('aiproxy_models_count', 0)}")
        
        elif tool_name == "OpenClaw":
            summary_lines.append(f"- Providers 配置: {'已设置' if tool_specific.get('has_providers') else '未设置'}")
            summary_lines.append(f"- 现有 Providers: {', '.join(tool_specific.get('providers', [])) or '无'}")
            summary_lines.append(f"- 默认 Provider: {tool_specific.get('default_provider', '未设置')}")
        
        return "\n".join(summary_lines)

    def validate_config_for_injection(self, config_content: str, tool_name: str) -> Tuple[bool, str]:
        try:
            config = json.loads(config_content)
        except json.JSONDecodeError as e:
            return False, f"JSON格式无效: {str(e)}"
        
        if tool_name == "ClaudeCode":
            return True, "配置格式有效"
        
        elif tool_name == "OpenCode":
            if "provider" in config:
                if not isinstance(config["provider"], dict):
                    return False, "provider 字段应该是对象类型"
            return True, "配置格式有效"
        
        elif tool_name == "OpenClaw":
            if "providers" in config:
                if not isinstance(config["providers"], dict):
                    return False, "providers 字段应该是对象类型"
            return True, "配置格式有效"
        
        return True, "配置格式有效"
