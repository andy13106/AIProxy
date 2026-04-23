import json
import os
from typing import Any, Dict, Optional, Tuple

try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False


class AIAnalyzer:
    """AI驱动的配置文件分析引擎"""

    def __init__(self, proxy_base_url: str = "http://localhost:8000", api_key: str = ""):
        self.proxy_base_url = proxy_base_url
        self.api_key = api_key or os.getenv("MASTER_KEY", "")
        self.default_model = "GLM5"

    def _build_prompt(self, config_content: str, tool_type: str) -> str:
        """构建分析提示词"""
        return f"""
你是专业的配置文件解析专家。请分析以下 {tool_type} 配置文件。
我需要添加本地OpenAI兼容代理到这个配置中。

配置文件内容：
```json
{config_content}
```

请分析并告诉我：
1. 现有配置的结构说明
2. 我需要修改哪些字段来添加本地代理（base_url: {self.proxy_base_url}, api_key: {self.api_key}）
3. 返回修改后的完整JSON，保持所有原有配置不变，只做增量添加

要求：
- 绝对不要删除或修改用户原有的任何配置
- 只添加和更新必要的字段
- 保持JSON格式合法有效

请直接以JSON格式返回修改后的完整配置。
"""

    def analyze_config(
        self,
        config_content: str,
        tool_type: str = "general",
        model: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """使用AI分析配置文件

        Returns:
            (是否成功, 分析结果, 错误信息)
        """
        if not LITELLM_AVAILABLE:
            return False, {}, "litellm 不可用，跳过AI分析"

        if not config_content.strip():
            config_content = "{}"

        try:
            prompt = self._build_prompt(config_content, tool_type)

            response = litellm.completion(
                model=model or self.default_model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是专业的配置文件解析专家，擅长分析和修改JSON配置文件。只输出修改后的完整JSON，不要添加任何额外说明。",
                    },
                    {"role": "user", "content": prompt},
                ],
                api_base=f"{self.proxy_base_url.rstrip('/')}/v1",
                api_key=self.api_key,
                temperature=0.1,
                timeout=30,
            )

            result_text = response.choices[0].message.content.strip()

            json_start = result_text.find("{")
            json_end = result_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result_text = result_text[json_start:json_end]

            parsed_json = json.loads(result_text)
            return True, parsed_json, ""

        except Exception as e:
            return False, {}, f"AI分析失败: {str(e)}"

    def generate_config_preview(
        self,
        original_config: Dict[str, Any],
        modified_config: Dict[str, Any],
    ) -> str:
        """生成配置变更预览说明"""
        preview = []
        preview.append("=== 配置变更预览 ===\n")

        original_json = json.dumps(original_config, ensure_ascii=False, indent=2)
        modified_json = json.dumps(modified_config, ensure_ascii=False, indent=2)

        preview.append("原始配置:")
        preview.append("```json")
        preview.append(original_json)
        preview.append("```")

        preview.append("\n修改后配置:")
        preview.append("```json")
        preview.append(modified_json)
        preview.append("```")

        return "\n".join(preview)
