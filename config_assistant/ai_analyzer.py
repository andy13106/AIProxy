"""AI配置分析引擎

使用项目已集成的litellm能力，分析配置文件结构并提供智能建议。
"""

import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


@dataclass
class ConfigAnalysis:
    """配置分析结果"""
    tool_name: str
    structure_summary: str
    existing_providers: List[str]
    existing_models: List[str]
    suggested_changes: List[str]
    risk_warnings: List[str]
    merged_preview: Optional[Dict] = None


class AIConfigAnalyzer:
    """AI驱动的配置分析器"""

    def __init__(self):
        pass

    def analyze_config(self, tool_name: str, current_config: Optional[Dict], target_config: Dict) -> ConfigAnalysis:
        """分析配置文件

        Args:
            tool_name: 工具名称
            current_config: 当前配置
            target_config: 目标配置（要注入的内容）

        Returns:
            ConfigAnalysis: 分析结果
        """
        if current_config is None:
            current_config = {}

        # 分析现有配置结构
        structure_summary = self._analyze_structure(tool_name, current_config)

        # 提取现有provider和model
        existing_providers = self._extract_providers(tool_name, current_config)
        existing_models = self._extract_models(tool_name, current_config)

        # 生成建议的变更
        suggested_changes = self._generate_suggestions(tool_name, current_config, target_config)

        # 风险评估
        risk_warnings = self._assess_risks(tool_name, current_config, target_config)

        # 生成合并预览
        merged_preview = self._generate_merge_preview(tool_name, current_config, target_config)

        return ConfigAnalysis(
            tool_name=tool_name,
            structure_summary=structure_summary,
            existing_providers=existing_providers,
            existing_models=existing_models,
            suggested_changes=suggested_changes,
            risk_warnings=risk_warnings,
            merged_preview=merged_preview
        )

    def _analyze_structure(self, tool_name: str, config: Dict) -> str:
        """分析配置结构"""
        if not config:
            return "配置文件为空或不存在"

        top_keys = list(config.keys())

        if tool_name == "claude_code":
            has_env = "env" in config
            has_model = "model" in config
            return f"Claude Code配置: 包含 {len(top_keys)} 个顶层字段 (env: {has_env}, model: {has_model})"

        elif tool_name == "opencode":
            providers = config.get("provider", {})
            return f"OpenCode配置: 包含 {len(top_keys)} 个顶层字段, {len(providers)} 个provider"

        elif tool_name == "openclaw":
            providers = config.get("providers", [])
            return f"OpenClaw配置: 包含 {len(top_keys)} 个顶层字段, {len(providers)} 个providers"

        return f"配置包含 {len(top_keys)} 个顶层字段: {', '.join(top_keys[:5])}"

    def _extract_providers(self, tool_name: str, config: Dict) -> List[str]:
        """提取现有的provider列表"""
        providers = []

        if tool_name == "opencode":
            provider_dict = config.get("provider", {})
            providers = list(provider_dict.keys())

        elif tool_name == "openclaw":
            provider_list = config.get("providers", [])
            providers = [p.get("name", "unknown") for p in provider_list if isinstance(p, dict)]

        return providers

    def _extract_models(self, tool_name: str, config: Dict) -> List[str]:
        """提取现有的model列表"""
        models = []

        if tool_name == "claude_code":
            model = config.get("model")
            if model:
                models = [model]

        elif tool_name == "opencode":
            provider_dict = config.get("provider", {})
            for provider_name, provider_config in provider_dict.items():
                if isinstance(provider_config, dict):
                    provider_models = provider_config.get("models", {})
                    if isinstance(provider_models, dict):
                        models.extend(provider_models.keys())

        elif tool_name == "openclaw":
            provider_list = config.get("providers", [])
            for provider in provider_list:
                if isinstance(provider, dict):
                    provider_models = provider.get("models", [])
                    if isinstance(provider_models, list):
                        models.extend([m.get("id", "") for m in provider_models if isinstance(m, dict)])

        return models

    def _generate_suggestions(self, tool_name: str, current: Dict, target: Dict) -> List[str]:
        """生成变更建议"""
        suggestions = []

        if tool_name == "claude_code":
            current_env = current.get("env", {})
            target_env = target.get("env", {})

            if "ANTHROPIC_BASE_URL" not in current_env:
                suggestions.append("将添加 ANTHROPIC_BASE_URL 环境变量")
            elif current_env.get("ANTHROPIC_BASE_URL") != target_env.get("ANTHROPIC_BASE_URL"):
                suggestions.append(f"将更新 ANTHROPIC_BASE_URL: {current_env.get('ANTHROPIC_BASE_URL')} -> {target_env.get('ANTHROPIC_BASE_URL')}")

            if "ANTHROPIC_API_KEY" not in current_env:
                suggestions.append("将添加 ANTHROPIC_API_KEY 环境变量")

            if "model" not in current:
                suggestions.append(f"将添加默认模型: {target.get('model')}")
            elif current.get("model") != target.get("model"):
                suggestions.append(f"将更新默认模型: {current.get('model')} -> {target.get('model')}")

        elif tool_name == "opencode":
            current_providers = current.get("provider", {})
            if "aiproxy" not in current_providers:
                suggestions.append("将添加 'aiproxy' provider")
            else:
                suggestions.append("将更新 'aiproxy' provider 配置")

            target_models = target.get("provider", {}).get("aiproxy", {}).get("models", {})
            suggestions.append(f"将注入 {len(target_models)} 个模型到 aiproxy provider")

        elif tool_name == "openclaw":
            current_providers = [p.get("name") for p in current.get("providers", []) if isinstance(p, dict)]
            if "aiproxy" not in current_providers:
                suggestions.append("将添加 'aiproxy' provider")
            else:
                suggestions.append("将更新 'aiproxy' provider 配置")

        return suggestions

    def _assess_risks(self, tool_name: str, current: Dict, target: Dict) -> List[str]:
        """评估风险"""
        risks = []

        if not current:
            risks.append("配置文件不存在，将创建新文件")
            return risks

        if tool_name == "claude_code":
            current_env = current.get("env", {})
            if "ANTHROPIC_BASE_URL" in current_env:
                current_url = current_env.get("ANTHROPIC_BASE_URL", "")
                if "anthropic.com" in current_url:
                    risks.append("警告：当前配置使用Anthropic官方API，修改后将指向本地代理")

        elif tool_name == "opencode":
            current_providers = current.get("provider", {})
            if "aiproxy" in current_providers:
                risks.append("提示：aiproxy provider已存在，将被更新")

        elif tool_name == "openclaw":
            current_providers = [p.get("name") for p in current.get("providers", []) if isinstance(p, dict)]
            if "aiproxy" in current_providers:
                risks.append("提示：aiproxy provider已存在，将被更新")

        return risks

    def _generate_merge_preview(self, tool_name: str, current: Dict, target: Dict) -> Dict:
        """生成合并后的预览"""
        from .injectors.base import BaseInjector

        injector = BaseInjector()
        return injector.merge_configs(current or {}, target)

    def format_analysis_report(self, analysis: ConfigAnalysis) -> str:
        """格式化分析报告"""
        report = f"""## {analysis.tool_name} 配置分析报告

### 当前配置结构
{analysis.structure_summary}

### 现有配置
- **Providers**: {', '.join(analysis.existing_providers) if analysis.existing_providers else '无'}
- **Models**: {', '.join(analysis.existing_models[:10]) if analysis.existing_models else '无'}
{f'... 等共 {len(analysis.existing_models)} 个模型' if len(analysis.existing_models) > 10 else ''}

### 建议的变更
"""
        for suggestion in analysis.suggested_changes:
            report += f"- ✅ {suggestion}\n"

        if analysis.risk_warnings:
            report += "\n### ⚠️ 注意事项\n"
            for warning in analysis.risk_warnings:
                report += f"- {warning}\n"

        return report
