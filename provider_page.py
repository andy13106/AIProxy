import streamlit as st
from db import SessionLocal, Provider, SUPPORTED_PROVIDER_TYPES
from utils import delete_item


def render_provider_page():
    st.header("🔌 供应商配置")

    with st.expander("📖 各类型供应商配置说明", expanded=False):
        st.markdown("""
**OpenAI 兼容**（默认）  
适用于 NVIDIA、DeepSeek、vLLM、OneAPI 等 OpenAI 兼容接口。  
- 填写 API Base URL 和 API Key 即可。

**Anthropic 原生**  
直连 Claude API。  
- API Base URL 填 `https://api.anthropic.com`（或自定义代理地址）  
- API Key 填 Anthropic API Key

**Google Gemini（API Key）**  
通过 Google AI Studio 的 API Key 访问 Gemini 模型。  
- API Base URL：留空（自动处理）  
- API Key：填 Google AI Studio 的 API Key

**Google Vertex AI（OAuth / Service Account）**  
通过 GCP Service Account 认证，无需 API Key。  
- API Base URL：留空  
- API Key：填任意占位符（如 `placeholder`，不会实际使用）  
- 需要在 `.env` 中配置：
  ```
  GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/service-account.json
  VERTEXAI_PROJECT=your-gcp-project-id
  VERTEXAI_LOCATION=us-central1
  ```
- Docker 部署时需挂载 credentials 目录

**AWS Bedrock（IAM 认证）**  
通过 AWS IAM 认证，无需 API Key。  
- API Base URL：留空  
- API Key：填任意占位符  
- 需要在 `.env` 中配置：
  ```
  AWS_ACCESS_KEY_ID=your-access-key
  AWS_SECRET_ACCESS_KEY=your-secret-key
  AWS_DEFAULT_REGION=us-east-1
  ```

**Azure OpenAI**  
- API Base URL：填 Azure 部署的 endpoint（如 `https://xxx.openai.azure.com`）  
- API Key：填 Azure API Key

**Ollama**  
- API Base URL：填 Ollama 地址（如 `http://localhost:11434`）  
- API Key：填任意占位符
        """)

    _NO_BASE_URL_TYPES = {"vertex_ai", "bedrock", "gemini", "cohere", "mistral"}
    
    with st.expander("➕ 添加新供应商", expanded=False):
        with st.form("add_p", clear_on_submit=True):
            name = st.text_input("供应商名称").strip()
            provider_type_options = list(SUPPORTED_PROVIDER_TYPES.keys())
            provider_type_labels = [f"{k} - {v}" for k, v in SUPPORTED_PROVIDER_TYPES.items()]
            selected_type_idx = st.selectbox(
                "上游协议类型",
                options=range(len(provider_type_options)),
                format_func=lambda i: provider_type_labels[i],
                index=0,
                help="选择上游 API 的协议类型。OpenAI 兼容适用于 NVIDIA、DeepSeek、vLLM 等；Anthropic 原生适用于直连 Claude API。",
            )
            selected_type = provider_type_options[selected_type_idx]
            _base_url_required = selected_type not in _NO_BASE_URL_TYPES
            base_url_help = "留空即可，该类型无需 API Base URL" if not _base_url_required else ""
            base_url = st.text_input(
                "API Base URL",
                value="https://integrate.api.nvidia.com/v1" if _base_url_required else "",
                help=base_url_help,
            ).strip()
            if st.form_submit_button("保存供应商", width="stretch"):
                base_url = base_url.strip().strip("`").strip("'").strip("\"")
                if not name:
                    st.error("请填写供应商名称")
                elif _base_url_required and not base_url:
                    st.error("该供应商类型需要填写 API Base URL")
                else:
                    with SessionLocal() as session:
                        if session.query(Provider).filter(Provider.name == name).first():
                            st.error(f"供应商 '{name}' 已存在")
                        else:
                            new_p = Provider(name=name, api_base=base_url, provider_type=selected_type)
                            session.add(new_p)
                            session.commit()
                            st.toast(f"已添加供应商: {name} ({selected_type})")
                            st.rerun()

    st.subheader("现有供应商列表")
    with SessionLocal() as session:
        providers = session.query(Provider).all()
    
    if providers:
        h1, h2, h3, h4, h5 = st.columns([1, 2, 2, 4, 1])
        h1.write("**ID**")
        h2.write("**名称**")
        h3.write("**协议类型**")
        h4.write("**Base URL**")
        h5.write("**操作**")
        st.divider()
        
        for p in providers:
            c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 4, 1])
            c1.write(f"`{p.id}`")
            c2.write(p.name)
            p_type = getattr(p, 'provider_type', 'openai') or 'openai'
            type_label = SUPPORTED_PROVIDER_TYPES.get(p_type, p_type)
            c3.write(f"`{p_type}` {type_label.split('（')[0].split('(')[0].strip()}")
            c4.write(f"`{p.api_base}`")
            if c5.button("🗑️", key=f"del_p_{p.id}"):
                if delete_item(Provider, p.id, f"已删除供应商: {p.name}"):
                    st.rerun()
    else:
        st.info("尚未配置供应商")
