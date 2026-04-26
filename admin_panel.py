import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import select, delete, update, func
from sqlalchemy.orm import Session
from db import SessionLocal, sync_engine, Provider, APIKey, ModelMapping, UsageLog, Base, ToolDefaultModel, SUPPORTED_PROVIDER_TYPES
import pandas as pd
import datetime
import requests
import os
import json
import base64
import socket
try:
    from streamlit_sortables import sort_items
    HAS_SORTABLES = True
except Exception:
    HAS_SORTABLES = False

from config_assistant.env_detector import is_running_in_docker
from config_assistant.config_detector import ConfigDetector
from config_assistant.backup_manager import BackupManager
from config_assistant.injectors.claude_code import ClaudeCodeInjector
from config_assistant.injectors.opencode import OpenCodeInjector
from config_assistant.injectors.openclaw import OpenClawInjector
from config_assistant.injectors.hermes import HermesInjector
from config_assistant.ai_analyzer import AIAnalyzer

# --- 数据库初始化 ---
# 仅在第一次运行时初始化
if 'db_initialized' not in st.session_state:
    Base.metadata.create_all(bind=sync_engine)
    st.session_state.db_initialized = True

st.set_page_config(page_title="AI Proxy Master Admin", layout="wide", initial_sidebar_state="expanded")

# --- 密码认证 ---
_admin_password = os.getenv("ADMIN_PASSWORD", "")
_admin_host = (os.getenv("ADMIN_HOST", "0.0.0.0") or "").strip().lower()
_is_local_admin_host = _admin_host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

if not _admin_password and not _is_local_admin_host:
    st.error("安全限制：ADMIN_HOST 非本地地址时，必须设置 ADMIN_PASSWORD。")
    st.code("请在 .env 中设置 ADMIN_PASSWORD=一个高强度密码，然后重启服务。")
    st.stop()

if _admin_password:
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False
    if not st.session_state.admin_authenticated:
        st.title("🔐 管理面板登录")
        pwd = st.text_input("请输入管理密码", type="password")
        if st.button("登录"):
            if pwd == _admin_password:
                st.session_state.admin_authenticated = True
                st.rerun()
            else:
                st.error("密码错误")
        st.stop()

# --- 界面样式优化 ---
st.markdown("""
    <style>
    .stButton>button {
        width: 100%;
        border-radius: 5px;
    }
    .status-active { color: #28a745; font-weight: bold; }
    .status-inactive { color: #dc3545; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 侧边栏 ---
st.sidebar.title("🤖 AI-Proxy Admin")
if st.sidebar.button("🔄 刷新页面", width="stretch"):
    st.rerun()

menu = st.sidebar.radio("导航", ["使用概览", "供应商管理", "API Key 管理", "模型映射管理", "工具配置助手", "模型体验", "系统设置"])

# --- 通用操作函数 ---
def delete_item(model_class, item_id, success_msg):
    with SessionLocal() as session:
        try:
            if model_class == Provider:
                # 级联删除关联数据
                key_ids = [k.id for k in session.query(APIKey).filter(APIKey.provider_id == item_id).all()]
                if key_ids:
                    session.query(UsageLog).filter(UsageLog.key_id.in_(key_ids)).delete(synchronize_session=False)
                session.query(APIKey).filter(APIKey.provider_id == item_id).delete()
                session.query(ModelMapping).filter(ModelMapping.provider_id == item_id).delete()
            
            session.query(model_class).filter(model_class.id == item_id).delete()
            session.commit()
            st.toast(success_msg, icon="✅")
            return True
        except Exception as e:
            session.rollback()
            st.error(f"删除失败: {e}")
            return False


def get_ordered_model_mappings():
    """读取并规范化模型映射顺序（1..N 连续），返回 (ModelMapping, Provider) 列表。"""
    with SessionLocal() as session:
        mappings = (
            session.query(ModelMapping, Provider)
            .join(Provider)
            .order_by(ModelMapping.order, ModelMapping.id)
            .all()
        )
        changed = False
        for idx, (m, _) in enumerate(mappings, start=1):
            if m.order != idx:
                m.order = idx
                changed = True
        if changed:
            session.commit()
            mappings = (
                session.query(ModelMapping, Provider)
                .join(Provider)
                .order_by(ModelMapping.order, ModelMapping.id)
                .all()
            )
        return mappings


def move_model_mapping(model_id: int, direction: str) -> bool:
    """上移/下移某个模型映射。direction: up | down"""
    with SessionLocal() as session:
        mappings = session.query(ModelMapping).order_by(ModelMapping.order, ModelMapping.id).all()
        ids = [m.id for m in mappings]
        if model_id not in ids:
            return False

        idx = ids.index(model_id)
        target_idx = idx - 1 if direction == "up" else idx + 1
        if target_idx < 0 or target_idx >= len(mappings):
            return False

        current = mappings[idx]
        target = mappings[target_idx]
        current.order, target.order = target.order, current.order
        session.commit()
        return True


def apply_model_order(ordered_ids: list[int]) -> bool:
    """按给定 ID 顺序更新模型映射 order。"""
    if not ordered_ids:
        return False
    with SessionLocal() as session:
        models = session.query(ModelMapping).filter(ModelMapping.id.in_(ordered_ids)).all()
        by_id = {m.id: m for m in models}
        for idx, model_id in enumerate(ordered_ids, start=1):
            model = by_id.get(model_id)
            if model is not None:
                model.order = idx
        session.commit()
    return True


def upsert_env_value(key: str, value: str, env_path: str = ".env") -> None:
    """更新 .env 中的某个 key（不存在则追加）"""
    existing_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            existing_lines = f.read().splitlines()

    updated = []
    found = False
    for line in existing_lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            updated.append(line)
            continue
        k, _ = line.split("=", 1)
        if k.strip() == key:
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)

    if not found:
        updated.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated).rstrip() + "\n")


def copy_button(label: str, value: str, key: str):
    safe_value = json.dumps(value)
    safe_label = json.dumps(label)
    html = f"""
    <button id="{key}" style="padding:0.35rem 0.7rem;border:1px solid #bbb;border-radius:6px;cursor:pointer;">
      {label}
    </button>
    <script>
      const btn = document.getElementById({json.dumps(key)});
      if (btn) {{
        btn.onclick = async () => {{
          try {{
            await navigator.clipboard.writeText({safe_value});
            btn.innerText = "已复制";
            setTimeout(() => btn.innerText = {safe_label}, 1200);
          }} catch (e) {{
            btn.innerText = "复制失败";
            setTimeout(() => btn.innerText = {safe_label}, 1200);
          }}
        }};
      }}
    </script>
    """
    components.html(html, height=40)


def fetch_models(api_base, api_key):
    try:
        url = api_base.rstrip("/")
        if not url.endswith("/models"):
            url = f"{url}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                models = []
                for m in data["data"]:
                    if isinstance(m, dict):
                        model_id = m.get("id") or m.get("model") or m.get("name")
                        if model_id:
                            models.append(model_id)
                    elif isinstance(m, str):
                        models.append(m)
                return models
            elif isinstance(data, list):
                models = []
                for m in data:
                    if isinstance(m, dict):
                        model_id = m.get("id") or m.get("model") or m.get("name")
                        if model_id:
                            models.append(model_id)
                    elif isinstance(m, str):
                        models.append(m)
                return models
        return []
    except Exception as e:
        return []

def test_upstream_connectivity(api_base, api_key, test_model=None):
    clean_base = (api_base or "").strip().strip("`").strip("'").strip("\"").rstrip("/")
    clean_key = (api_key or "").strip().strip("`").strip("'").strip("\"")
    if not clean_base or not clean_key:
        return False, "配置不完整：缺少 API Base 或 Key"
    url = f"{clean_base}/models"
    headers = {"Authorization": f"Bearer {clean_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        status = resp.status_code
        if status == 200:
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                models = [m.get("id", "") for m in data["data"] if isinstance(m, dict)]
                model_count = len(models)
                if test_model:
                    if test_model in models:
                        return True, f"连通成功：HTTP 200，可用模型数 {model_count}，已找到映射模型 {test_model}"
                    return False, f"连通成功但模型不匹配：未在 /models 中找到 {test_model}（共 {model_count} 个模型）"
                return True, f"连通成功：HTTP 200，可用模型数 {model_count}"
            return True, "连通成功：HTTP 200"
        if status in (401, 403):
            return False, f"鉴权失败：HTTP {status}，请检查上游 Key 权限"
        if status == 404:
            return False, "接口路径异常：HTTP 404，请检查 API Base 是否正确（通常应包含 /v1）"
        body = resp.text[:200].replace("\n", " ")
        return False, f"连通失败：HTTP {status}，响应：{body}"
    except Exception as e:
        return False, f"请求异常：{str(e)}"

def test_upstream_chat(api_base, api_key, model_name):
    clean_base = (api_base or "").strip().strip("`").strip("'").strip("\"").rstrip("/")
    clean_key = (api_key or "").strip().strip("`").strip("'").strip("\"")
    clean_model = (model_name or "").strip().strip("`").strip("'").strip("\"")
    if not clean_base or not clean_key or not clean_model:
        return False, "配置不完整：缺少 API Base、Key 或模型名"
    url = f"{clean_base}/chat/completions"
    headers = {"Authorization": f"Bearer {clean_key}", "Content-Type": "application/json"}
    payload = {
        "model": clean_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        status = resp.status_code
        if status == 200:
            return True, f"对话测试成功：HTTP 200，模型 {clean_model} 可调用"
        body = resp.text[:220].replace("\n", " ")
        if status in (401, 403):
            return False, f"对话鉴权失败：HTTP {status}，模型 {clean_model}，响应：{body}"
        return False, f"对话失败：HTTP {status}，模型 {clean_model}，响应：{body}"
    except Exception as e:
        return False, f"对话请求异常：{str(e)}"


def classify_model_type(model_name: str) -> str:
    """粗略区分模型类型：image / text"""
    lower = (model_name or "").lower()
    image_keywords = [
        "stable-diffusion",
        "stable_diffusion",
        "stable diffusion",
        "sd3",
        "sd-3",
        "sd 3",
        "sd3.5",
        "sd-3.5",
        "sd 3.5",
        "sdxl",
        "diffusion",
        "flux",
        "imagen",
        "dall-e",
        "image",
        "stabilityai/",
        "black-forest-labs/",
        "playgroundai/",
    ]
    if any(k in lower for k in image_keywords):
        return "image"
    if "stable" in lower and "diffusion" in lower:
        return "image"
    return "text"


def log_custom_usage(
    key_id: int,
    model_name: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    images_count: int = 0,
):
    """把模型体验页调用写入 usage 统计"""
    with SessionLocal() as session:
        log = UsageLog(
            key_id=key_id,
            model_name=model_name,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            total_tokens=total_tokens or ((prompt_tokens or 0) + (completion_tokens or 0)),
            images_count=images_count or 0,
        )
        session.add(log)
        session.query(APIKey).filter(APIKey.id == key_id).update(
            {"usage_count": APIKey.usage_count + 1},
            synchronize_session=False,
        )
        session.commit()


def detect_proxy_base_url() -> tuple[str, list[str]]:
    """推断代理可访问地址，并返回候选列表"""
    proxy_port = (os.getenv("PROXY_PORT", "8000") or "8000").strip()
    proxy_host = (os.getenv("PROXY_HOST", "0.0.0.0") or "0.0.0.0").strip()
    explicit = (os.getenv("PROXY_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    candidates = []
    if explicit:
        candidates.append(explicit)

    if proxy_host and proxy_host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
        candidates.append(f"http://{proxy_host}:{proxy_port}")

    candidates.append(f"http://localhost:{proxy_port}")
    candidates.append(f"http://127.0.0.1:{proxy_port}")

    # 在局域网部署时给出一个可参考的网卡 IP
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            if lan_ip and lan_ip not in {"127.0.0.1", "0.0.0.0"}:
                candidates.append(f"http://{lan_ip}:{proxy_port}")
    except Exception:
        pass

    uniq = []
    for item in candidates:
        normalized = (item or "").strip().rstrip("/")
        if normalized and normalized not in uniq:
            uniq.append(normalized)

    default_base = uniq[0] if uniq else f"http://localhost:{proxy_port}"
    return default_base, uniq

# --- 页面逻辑 ---

if menu == "使用概览":
    st.header("📊 使用统计概览")
    today = datetime.date.today()
    today_start = datetime.datetime.combine(today, datetime.time.min)
    today_end = datetime.datetime.combine(today, datetime.time.max)
    page_size = 20

    if "usage_selected_model" not in st.session_state:
        st.session_state.usage_selected_model = None

    with SessionLocal() as session:
        # 清理历史上无有效用量的脏记录
        session.query(UsageLog).filter(
            UsageLog.prompt_tokens == 0,
            UsageLog.completion_tokens == 0,
            UsageLog.total_tokens == 0,
            UsageLog.images_count == 0
        ).delete()
        session.commit()

        today_summary = session.query(
            UsageLog.model_name,
            func.count(UsageLog.id).label("request_count"),
            func.sum(UsageLog.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageLog.completion_tokens).label("completion_tokens"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.images_count).label("images_count"),
        ).filter(
            UsageLog.timestamp >= today_start,
            UsageLog.timestamp <= today_end
        ).group_by(UsageLog.model_name).order_by(func.sum(UsageLog.total_tokens).desc()).all()

        all_time_summary = session.query(
            UsageLog.model_name,
            func.count(UsageLog.id).label("request_count"),
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.images_count).label("images_count"),
        ).group_by(UsageLog.model_name).order_by(func.sum(UsageLog.total_tokens).desc()).all()

    if today_summary:
        total_requests_today = sum(row.request_count or 0 for row in today_summary)
        total_tokens_today = sum(row.total_tokens or 0 for row in today_summary)
        total_images_today = sum(row.images_count or 0 for row in today_summary)

        c1, c2, c3 = st.columns(3)
        c1.metric("今日总消耗 Tokens", f"{total_tokens_today:,}")
        c2.metric("今日请求次数", f"{total_requests_today:,}")
        c3.metric("今日生成图片", f"{total_images_today:,}")

        st.subheader("今日按模型汇总")
        summary_df = pd.DataFrame([{
            "模型": row.model_name,
            "请求次数": row.request_count or 0,
            "Prompt Tokens": row.prompt_tokens or 0,
            "Completion Tokens": row.completion_tokens or 0,
            "Total Tokens": row.total_tokens or 0,
            "图片数": row.images_count or 0,
        } for row in today_summary])
        st.dataframe(summary_df, width="stretch", hide_index=True)
    else:
        st.info("今天还没有有效使用记录。")

    st.subheader("模型数据清理（全历史）")
    st.caption("这里列出所有历史出现过的模型（不仅是今天），可删除任意模型统计数据。")
    if all_time_summary:
        cleanup_df = pd.DataFrame([{
            "模型": row.model_name,
            "历史请求次数": row.request_count or 0,
            "历史总 Tokens": row.total_tokens or 0,
            "历史图片数": row.images_count or 0,
        } for row in all_time_summary])
        st.dataframe(cleanup_df, width="stretch", hide_index=True)

        for row in all_time_summary:
            c_view, c_del = st.columns([3, 1])
            if c_view.button(f"查看 {row.model_name}", key=f"usage_model_all_{row.model_name}"):
                st.session_state.usage_selected_model = row.model_name
            if c_del.button(f"删除 {row.model_name}", key=f"usage_delete_all_{row.model_name}"):
                with SessionLocal() as session:
                    deleted = session.query(UsageLog).filter(UsageLog.model_name == row.model_name).delete()
                    session.commit()
                if st.session_state.usage_selected_model == row.model_name:
                    st.session_state.usage_selected_model = None
                st.toast(f"已删除模型 {row.model_name} 的 {deleted} 条统计记录", icon="🧹")
                st.rerun()
    else:
        st.info("暂无可清理的历史模型数据。")

    st.divider()
    st.subheader("明细查询")

    with st.form("usage_detail_filter"):
        with SessionLocal() as session:
            all_models = [r[0] for r in session.query(UsageLog.model_name).distinct().order_by(UsageLog.model_name).all()]

        if all_models and st.session_state.usage_selected_model not in all_models:
            st.session_state.usage_selected_model = all_models[0]

        selected_model = st.selectbox(
            "模型",
            options=all_models if all_models else ["暂无数据"],
            index=(all_models.index(st.session_state.usage_selected_model) if all_models and st.session_state.usage_selected_model in all_models else 0),
            disabled=not all_models
        )
        d1, d2 = st.columns(2)
        start_date = d1.date_input("开始日期", value=today)
        end_date = d2.date_input("结束日期", value=today)
        query_submitted = st.form_submit_button("查询明细", width="stretch")

    if query_submitted and all_models:
        st.session_state.usage_selected_model = selected_model
        st.session_state.usage_start_date = start_date
        st.session_state.usage_end_date = end_date
        st.session_state.usage_page = 1

    if "usage_start_date" not in st.session_state:
        st.session_state.usage_start_date = today
    if "usage_end_date" not in st.session_state:
        st.session_state.usage_end_date = today
    if "usage_page" not in st.session_state:
        st.session_state.usage_page = 1

    if all_models and st.session_state.usage_selected_model:
        detail_start = datetime.datetime.combine(st.session_state.usage_start_date, datetime.time.min)
        detail_end = datetime.datetime.combine(st.session_state.usage_end_date, datetime.time.max)

        with SessionLocal() as session:
            detail_query = session.query(UsageLog).filter(
                UsageLog.model_name == st.session_state.usage_selected_model,
                UsageLog.timestamp >= detail_start,
                UsageLog.timestamp <= detail_end
            ).order_by(UsageLog.timestamp.desc())

            total_detail_count = detail_query.count()
            total_pages = max(1, (total_detail_count + page_size - 1) // page_size)
            if st.session_state.usage_page > total_pages:
                st.session_state.usage_page = total_pages

            detail_logs = detail_query.offset((st.session_state.usage_page - 1) * page_size).limit(page_size).all()

            totals = session.query(
                func.count(UsageLog.id),
                func.sum(UsageLog.prompt_tokens),
                func.sum(UsageLog.completion_tokens),
                func.sum(UsageLog.total_tokens),
                func.sum(UsageLog.images_count),
            ).filter(
                UsageLog.model_name == st.session_state.usage_selected_model,
                UsageLog.timestamp >= detail_start,
                UsageLog.timestamp <= detail_end
            ).first()

        st.caption(
            f"当前模型：{st.session_state.usage_selected_model} | "
            f"日期范围：{st.session_state.usage_start_date} 至 {st.session_state.usage_end_date}"
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("范围内总 Tokens", f"{(totals[3] or 0):,}")
        c2.metric("范围内请求数", f"{(totals[0] or 0):,}")
        c3.metric("范围内图片数", f"{(totals[4] or 0):,}")

        if detail_logs:
            detail_df = pd.DataFrame([{
                "ID": log.id,
                "Prompt": log.prompt_tokens,
                "Completion": log.completion_tokens,
                "Total": log.total_tokens,
                "图片": log.images_count,
                "时间": log.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            } for log in detail_logs])
            st.dataframe(detail_df, width="stretch", hide_index=True)

            p1, p2, p3 = st.columns([1, 2, 1])
            if p1.button("上一页", disabled=st.session_state.usage_page <= 1, width="stretch"):
                st.session_state.usage_page -= 1
                st.rerun()
            p2.markdown(f"<div style='text-align:center;padding-top:8px;'>第 {st.session_state.usage_page} / {total_pages} 页</div>", unsafe_allow_html=True)
            if p3.button("下一页", disabled=st.session_state.usage_page >= total_pages, width="stretch"):
                st.session_state.usage_page += 1
                st.rerun()
        else:
            st.info("这个条件下没有查到明细记录。")
    elif not all_models:
        st.info("暂无可查询的模型明细。")

elif menu == "供应商管理":
    st.header("🔌 供应商配置")

    # 使用说明
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

    # 不需要 API Base URL 的供应商类型
    _NO_BASE_URL_TYPES = {"vertex_ai", "bedrock", "gemini", "cohere", "mistral"}
    
    # 1. 添加表单
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

    # 2. 列表展示
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

elif menu == "API Key 管理":
    st.header("🔑 API Key 管理")
    
    with SessionLocal() as session:
        providers = session.query(Provider).all()
    
    if not providers:
        st.warning("请先在'供应商管理'中添加供应商")
    else:
        # 1. 添加表单
        with st.expander("➕ 添加 API Key", expanded=False):
            with st.form("add_k", clear_on_submit=True):
                p_map = {p.name: p.id for p in providers}
                p_type_map = {p.name: getattr(p, 'provider_type', 'openai') or 'openai' for p in providers}
                target_p = st.selectbox("选择供应商", options=list(p_map.keys()))
                _selected_p_type = p_type_map.get(target_p, 'openai')
                _is_oauth_type = _selected_p_type in ('vertex_ai', 'bedrock')
                key_help = "该供应商通过环境变量认证，此处填任意占位符即可（如 placeholder）" if _is_oauth_type else ""
                key_val = st.text_input("API Key", type="password", help=key_help).strip()
                if st.form_submit_button("保存 Key", width="stretch"):
                    key_val = key_val.strip().strip("`").strip("'").strip("\"")
                    if not key_val:
                        st.error("Key 不能为空")
                    else:
                        with SessionLocal() as session:
                            if session.query(APIKey).filter(APIKey.key == key_val).first():
                                st.error("该 Key 已存在")
                            else:
                                new_k = APIKey(provider_id=p_map[target_p], key=key_val)
                                session.add(new_k)
                                session.commit()
                                st.toast("API Key 已保存")
                                st.rerun()

        # 2. 列表展示
        st.subheader("密钥列表")
        with SessionLocal() as session:
            keys = session.query(APIKey, Provider).join(Provider).all()
            mappings = session.query(ModelMapping).all()
        provider_first_model = {}
        for m in mappings:
            if m.provider_id not in provider_first_model:
                provider_first_model[m.provider_id] = (m.virtual_name, m.real_name)
            
        if keys:
            h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([1, 2, 4, 2, 2, 1, 1, 1])
            h1.write("**ID**")
            h2.write("**供应商**")
            h3.write("**Key**")
            h4.write("**状态**")
            h5.write("**使用次数**")
            h6.write("**操作**")
            h7.write("**测试**")
            h8.write("**对话**")
            st.divider()
            
            for k, p in keys:
                c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1, 2, 4, 2, 2, 1, 1, 1])
                c1.write(f"`{k.id}`")
                c2.write(p.name)
                masked = k.key[:8] + "..." + k.key[-4:] if len(k.key) > 12 else "****"
                c3.write(f"`{masked}`")
                c4.write("✅ 正常" if k.is_active else "❌ 失效")
                c5.write(str(k.usage_count))
                if c6.button("🗑️", key=f"del_k_{k.id}"):
                    if delete_item(APIKey, k.id, "API Key 已删除"):
                        st.rerun()
                if c7.button("🔍", key=f"test_k_{k.id}"):
                    mapping_pair = provider_first_model.get(p.id)
                    test_model = mapping_pair[1] if mapping_pair else None
                    ok, msg = test_upstream_connectivity(p.api_base, k.key, test_model)
                    if ok:
                        st.success(f"[{p.name}] Key#{k.id} {msg}")
                    else:
                        st.error(f"[{p.name}] Key#{k.id} {msg}")
                    if mapping_pair:
                        st.caption(f"[{p.name}] 当前映射示例：{mapping_pair[0]} -> {mapping_pair[1]}")
                    else:
                        st.warning(f"[{p.name}] 尚无模型映射，建议先在「模型映射管理」中配置后再测。")
                if c8.button("💬", key=f"chat_k_{k.id}"):
                    mapping_pair = provider_first_model.get(p.id)
                    if not mapping_pair:
                        st.warning(f"[{p.name}] 尚无模型映射，无法进行对话测试。")
                    else:
                        ok, msg = test_upstream_chat(p.api_base, k.key, mapping_pair[1])
                        if ok:
                            st.success(f"[{p.name}] Key#{k.id} {msg}")
                        else:
                            st.error(f"[{p.name}] Key#{k.id} {msg}")
        else:
            st.info("尚未配置 API Key")

elif menu == "模型映射管理":
    st.header("🗺️ 模型映射管理")
    
    with SessionLocal() as session:
        providers = session.query(Provider).all()
        
    if not providers:
        st.warning("请先配置供应商和 API Key")
    else:
        with st.expander("🖼️ 快速添加图片模型映射（NVIDIA 常见）", expanded=False):
            st.caption("用于 `/v1/images/generations` 接口。可按需修改虚拟名与真实模型名。")
            with SessionLocal() as session:
                all_providers = session.query(Provider).all()
            provider_name_list = [p.name for p in all_providers]
            default_provider_idx = provider_name_list.index("Nvidia") if "Nvidia" in provider_name_list else 0
            target_provider_name = st.selectbox(
                "选择供应商（图片模型）",
                options=provider_name_list,
                index=default_provider_idx if provider_name_list else 0,
                key="image_model_provider_select",
            )
            preset_text = st.text_area(
                "预设映射（每行: 虚拟名=真实模型名）",
                value=(
                    "sd3=stabilityai/stable-diffusion-3-medium\n"
                    "sd3.5=stabilityai/stable-diffusion-3.5-large"
                ),
                height=90,
            )
            if st.button("添加图片模型预设", key="add_image_model_presets"):
                target_provider = next((p for p in all_providers if p.name == target_provider_name), None)
                if not target_provider:
                    st.error("未找到目标供应商")
                else:
                    lines = [ln.strip() for ln in preset_text.splitlines() if ln.strip() and "=" in ln]
                    added = 0
                    skipped = 0
                    with SessionLocal() as session:
                        for line in lines:
                            virtual_name, real_name = [x.strip() for x in line.split("=", 1)]
                            if not virtual_name or not real_name:
                                continue
                            exists = session.query(ModelMapping).filter(ModelMapping.virtual_name == virtual_name).first()
                            if exists:
                                skipped += 1
                                continue
                            session.add(
                                ModelMapping(
                                    virtual_name=virtual_name,
                                    real_name=real_name,
                                    provider_id=target_provider.id,
                                )
                            )
                            added += 1
                        session.commit()
                    st.success(f"完成：新增 {added} 条，跳过 {skipped} 条（已存在）。")
                    st.rerun()

        # 1. 添加表单
        with st.expander("➕ 添加新映射", expanded=False):
            p_map = {p.name: p for p in providers}
            sel_p_name = st.selectbox("选择供应商", options=list(p_map.keys()), key="add_map_provider")
            sel_p = p_map[sel_p_name]

            # 缓存模型列表到 session_state，避免表单内重复请求导致 selectbox 状态丢失
            cache_key = f"add_map_models_{sel_p.id}"
            with SessionLocal() as session:
                first_key = session.query(APIKey).filter(APIKey.provider_id == sel_p.id).first()
            if first_key:
                if cache_key not in st.session_state or st.session_state.get(f"{cache_key}_key") != first_key.key:
                    st.session_state[cache_key] = fetch_models(sel_p.api_base, first_key.key)
                    st.session_state[f"{cache_key}_key"] = first_key.key
                models = st.session_state[cache_key]
            else:
                models = []

            with st.form("add_m", clear_on_submit=True):
                v_name = st.text_input("虚拟模型名称 (工具调用时使用)", placeholder="gpt-4o", key="add_map_vname").strip()

                real_model = ""
                if models:
                    real_model = st.selectbox("选择真实模型", options=models, key="add_map_real_model")
                elif first_key:
                    st.caption("⚠️ 无法自动获取模型列表，请手动输入")
                    real_model = st.text_input("真实模型名称", key="add_map_real_manual").strip()
                else:
                    st.caption("⚠️ 该供应商下无 Key，请手动输入真实模型名")
                    real_model = st.text_input("真实模型名称", key="add_map_real_manual2").strip()

                if st.form_submit_button("保存映射", width="stretch"):
                    if not v_name or not real_model:
                        st.error("请完整填写映射信息")
                    else:
                        with SessionLocal() as session:
                            if session.query(ModelMapping).filter(ModelMapping.virtual_name == v_name).first():
                                st.error(f"虚拟名称 '{v_name}' 已占用")
                            else:
                                max_order = session.query(func.max(ModelMapping.order)).scalar() or 0
                                new_m = ModelMapping(
                                    virtual_name=v_name,
                                    real_name=real_model,
                                    provider_id=sel_p.id,
                                    order=int(max_order) + 1,
                                )
                                session.add(new_m)
                                session.commit()
                                st.toast(f"映射已保存: {v_name} -> {real_model}")
                                st.rerun()

        # 2. 列表展示
        st.subheader("映射列表")
        mappings = get_ordered_model_mappings()

        # 编辑映射的 session_state 管理
        if "edit_mapping_id" not in st.session_state:
            st.session_state.edit_mapping_id = None

        if mappings:
            if HAS_SORTABLES:
                st.caption("💡 拖拽下方条目后点击“保存拖拽顺序”，顺序将影响工具中的默认选择。")
                drag_items = [
                    f"{m.id} · {m.virtual_name} -> {m.real_name} ({p.name})"
                    for m, p in mappings
                ]
                sorted_items = sort_items(drag_items, direction="vertical")
                if st.button("💾 保存拖拽顺序", key="save_drag_model_order", width="stretch"):
                    try:
                        ordered_ids = [int(item.split(" · ", 1)[0]) for item in sorted_items]
                        if apply_model_order(ordered_ids):
                            st.success("模型顺序已保存")
                            st.rerun()
                    except Exception as e:
                        st.error(f"保存拖拽顺序失败: {e}")
            else:
                st.info("未安装拖拽组件，当前使用 ⬆️⬇️ 排序。安装 `streamlit-sortables` 后可启用拖拽。")
                st.caption("💡 使用 ⬆️⬇️ 按钮调整模型顺序，顺序将影响工具中的默认选择")

            h1, h2, h3, h4, h5, h6, h7 = st.columns([1, 3, 5, 2, 2, 1, 1])
            h1.write("**#**")
            h2.write("**虚拟名称**")
            h3.write("**真实模型**")
            h4.write("**供应商**")
            h5.write("**排序**")
            h6.write("**编辑**")
            h7.write("**删除**")
            st.divider()

            for idx, (m, p) in enumerate(mappings):
                c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 3, 5, 2, 2, 1, 1])
                c1.write(f"{idx + 1}")
                c2.write(f"**{m.virtual_name}**")
                c3.write(f"`{m.real_name}`")
                c4.write(p.name)
                if HAS_SORTABLES:
                    c5.caption("拖拽区调整")
                else:
                    # 上下移动按钮（拖拽组件不可用时的后备方案）
                    btn_up = c5.button("⬆️", key=f"up_m_{m.id}", disabled=(idx == 0))
                    btn_down = c5.button("⬇️", key=f"down_m_{m.id}", disabled=(idx == len(mappings) - 1))
                    if btn_up and idx > 0:
                        move_model_mapping(m.id, "up")
                        st.rerun()
                    if btn_down and idx < len(mappings) - 1:
                        move_model_mapping(m.id, "down")
                        st.rerun()
                if c6.button("✏️", key=f"edit_m_{m.id}"):
                    st.session_state.edit_mapping_id = m.id
                    st.rerun()
                if c7.button("🗑️", key=f"del_m_{m.id}"):
                    if delete_item(ModelMapping, m.id, "模型映射已删除"):
                        st.rerun()

            # 编辑映射表单
            edit_id = st.session_state.edit_mapping_id
            if edit_id is not None:
                with st.expander("✏️ 编辑映射", expanded=True):
                    with SessionLocal() as session:
                        edit_m = session.query(ModelMapping).filter(ModelMapping.id == edit_id).first()
                        if edit_m:
                            edit_providers = session.query(Provider).all()
                            edit_p_map = {p.name: p for p in edit_providers}
                            # 找到当前映射的供应商名
                            edit_provider_obj = session.query(Provider).filter(Provider.id == edit_m.provider_id).first()
                            current_p_name = edit_provider_obj.name if edit_provider_obj else list(edit_p_map.keys())[0]

                            edit_p_name = st.selectbox("供应商", options=list(edit_p_map.keys()),
                                                        index=list(edit_p_map.keys()).index(current_p_name),
                                                        key="edit_map_provider")
                            edit_p = edit_p_map[edit_p_name]

                            # 缓存编辑表单的模型列表
                            edit_cache_key = f"edit_map_models_{edit_p.id}"
                            edit_first_key = session.query(APIKey).filter(APIKey.provider_id == edit_p.id).first()
                            if edit_first_key:
                                if edit_cache_key not in st.session_state or st.session_state.get(f"{edit_cache_key}_key") != edit_first_key.key:
                                    st.session_state[edit_cache_key] = fetch_models(edit_p.api_base, edit_first_key.key)
                                    st.session_state[f"{edit_cache_key}_key"] = edit_first_key.key
                                edit_models = st.session_state[edit_cache_key]
                            else:
                                edit_models = []

                            with st.form("edit_m_form"):
                                ev_name = st.text_input("虚拟模型名称", value=edit_m.virtual_name, key="edit_map_vname", disabled=True)
                                st.caption("虚拟名称为映射唯一标识，不可修改。如需更改请删除后重新添加。")

                                edit_real_model = ""
                                if edit_models:
                                    # 尝试将当前 real_name 设为默认选中
                                    default_idx = 0
                                    if edit_m.real_name in edit_models:
                                        default_idx = edit_models.index(edit_m.real_name)
                                    edit_real_model = st.selectbox("选择真实模型", options=edit_models,
                                                                    index=default_idx, key="edit_map_real_model")
                                elif edit_first_key:
                                    st.caption("⚠️ 无法自动获取模型列表，请手动输入")
                                    edit_real_model = st.text_input("真实模型名称", value=edit_m.real_name, key="edit_map_real_manual").strip()
                                else:
                                    st.caption("⚠️ 该供应商下无 Key，请手动输入真实模型名")
                                    edit_real_model = st.text_input("真实模型名称", value=edit_m.real_name, key="edit_map_real_manual2").strip()

                                save_col, cancel_col = st.columns(2)
                                saved = save_col.form_submit_button("💾 保存修改", width="stretch", type="primary")
                                cancelled = cancel_col.form_submit_button("取消", width="stretch")

                                if saved:
                                    if not edit_real_model:
                                        st.error("请填写真实模型名称")
                                    else:
                                        edit_m.real_name = edit_real_model
                                        edit_m.provider_id = edit_p.id
                                        session.commit()
                                        st.session_state.edit_mapping_id = None
                                        st.toast(f"映射已更新: {edit_m.virtual_name} -> {edit_real_model}")
                                        st.rerun()

                                if cancelled:
                                    st.session_state.edit_mapping_id = None
                                    st.rerun()
                        else:
                            st.warning("映射不存在，可能已被删除")
                            st.session_state.edit_mapping_id = None
                            st.rerun()
        else:
            st.info("尚未配置模型映射")

elif menu == "工具配置助手":
    st.header("🛠️ AI 工具配置助手")

    detected_base_host, base_candidates = detect_proxy_base_url()
    if "assistant_proxy_base_host" not in st.session_state:
        st.session_state.assistant_proxy_base_host = detected_base_host

    st.caption("自动配置写入的是当前服务端机器上的配置文件，无法直接改浏览器所在远端电脑的本地文件。")
    st.caption("如果你在 Docker 或局域网给其它电脑使用，请把下方地址改成客户端可访问的代理地址。")

    base_host_input = st.text_input(
        "客户端访问代理地址（不带 /v1）",
        value=st.session_state.assistant_proxy_base_host,
        help="示例: http://192.168.1.10:8000 或 https://your-domain",
    ).strip()
    base_host = (base_host_input or detected_base_host).rstrip("/")
    st.session_state.assistant_proxy_base_host = base_host
    proxy_url_v1 = f"{base_host}/v1"

    if base_candidates:
        st.caption("检测到的候选地址: " + " | ".join(base_candidates[:4]))
    master_key = os.getenv("MASTER_KEY", "sk-admin-123456")
    
    with SessionLocal() as session:
        mappings = session.query(ModelMapping).order_by(ModelMapping.order, ModelMapping.id).all()
        v_models = [m.virtual_name for m in mappings]
        tool_default_models = {tdm.tool_id: tdm.default_model for tdm in session.query(ToolDefaultModel).all()}

    model_list = list(dict.fromkeys(v_models)) if v_models else ["GLM5"]
    model_hint = "GLM5" if "GLM5" in model_list else model_list[0]

    st.subheader("🎯 默认模型配置")
    st.caption("为不同工具选择默认模型，例如 ClaudeCode/OpenCode 适合编程模型，OpenClaw/Hermes 适合对话模型。")

    tool_model_config = {}
    tool_display_names = {
        "claude_code": "Claude Code",
        "opencode": "OpenCode",
        "openclaw": "OpenClaw",
        "hermes": "Hermes",
    }
    tool_cols = st.columns(4)
    for i, (tool_id, display_name) in enumerate(tool_display_names.items()):
        saved_default = tool_default_models.get(tool_id, model_hint)
        if saved_default not in model_list:
            saved_default = model_hint
        selected = tool_cols[i].selectbox(
            display_name,
            options=model_list,
            index=model_list.index(saved_default) if saved_default in model_list else 0,
            key=f"tool_default_model_{tool_id}",
        )
        tool_model_config[tool_id] = selected

    if st.button("💾 保存默认模型配置", key="save_tool_default_models", width="stretch"):
        with SessionLocal() as session:
            for tool_id, selected_model in tool_model_config.items():
                existing = session.query(ToolDefaultModel).filter(ToolDefaultModel.tool_id == tool_id).first()
                if existing:
                    existing.default_model = selected_model
                else:
                    session.add(ToolDefaultModel(tool_id=tool_id, default_model=selected_model))
            session.commit()
        st.success("工具默认模型已保存")

    opencode_models_dict = {m: {"name": m} for m in model_list}
    opencode_config_data = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "aiproxy": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "AIProxy",
                "options": {
                    "baseURL": proxy_url_v1,
                    "apiKey": master_key,
                },
                "models": opencode_models_dict,
            }
        },
    }
    opencode_config_content = json.dumps(opencode_config_data, ensure_ascii=False, indent=2)
    hermes_default_model = tool_model_config.get("hermes", model_hint)
    hermes_config_content = "\n".join(
        [
            "model:",
            '  provider: "custom"',
            f'  api_base: "{proxy_url_v1}"',
            f'  api_key: "{master_key}"',
            f'  model_name: "{hermes_default_model}"',
            "",
        ]
    )
    
    running_in_docker = is_running_in_docker()
    
    if running_in_docker:
        st.warning("""
        ⚠️ **Docker环境检测到**
        
        自动配置功能需要直接访问宿主机文件系统，当前运行在Docker容器中，
        **无法自动修改您的本地AI工具配置文件**。
        
        请使用以下方式之一：
        1. 下载配置文件，手动复制到工具配置目录
        2. 在本机直接运行项目（python + streamlit方式）
        3. 使用下方的手动配置说明
        """)
        st.divider()
    
    if not running_in_docker:
        st.subheader("🤖 智能配置")
        st.info("自动扫描并配置本地AI工具，实现零手动配置体验。")
        
        config_detector = ConfigDetector()
        backup_manager = BackupManager()

        detector_signature = json.dumps(config_detector.TOOL_CONFIGS, ensure_ascii=False, sort_keys=True)
        if st.session_state.get("config_detector_signature") != detector_signature:
            st.session_state.config_detector_signature = detector_signature
            st.session_state.scan_result = None
            st.session_state.selected_tools = {}
        
        if "scan_result" not in st.session_state:
            st.session_state.scan_result = None
        if "selected_tools" not in st.session_state:
            st.session_state.selected_tools = {}
        
        c_scan, c_restore = st.columns([3, 1])
        with c_scan:
            if st.button("🔍 一键智能扫描配置文件", type="primary", width="stretch"):
                with st.spinner("AI正在扫描系统中的配置文件..."):
                    st.session_state.scan_result = config_detector.detect_all()
        with c_restore:
            if st.button("↩️ 一键恢复备份", width="stretch"):
                backups = backup_manager.list_backups()
                if backups:
                    st.session_state.show_restore = True
                else:
                    st.info("暂无备份文件可恢复")
        
        if st.session_state.get("show_restore", False):
            with st.expander("选择备份文件恢复", expanded=True):
                backups = backup_manager.list_backups()
                if backups:
                    for b in backups[:5]:
                        col1, col2 = st.columns([4, 1])
                        col1.write(f"📄 {b['filename']}")
                        if col2.button("恢复", key=f"restore_{b['filename']}"):
                            st.info("请手动选择要恢复到的配置文件路径")
                st.button("关闭恢复面板", on_click=lambda: st.session_state.__setitem__("show_restore", False))
        
        if st.session_state.scan_result:
            st.markdown("---")
            st.markdown("**检测结果：**")
            
            detected_any = False
            for tool_id, result in st.session_state.scan_result.items():
                col1, col2, col3, col4 = st.columns([1, 3, 5, 2])
                if result["found"]:
                    detected_any = True
                    col1.success("✅")
                    col2.write(f"**{result['display_name']}**")
                    col3.code(result["path"], language="text")
                    default_checked = tool_id in ["claude_code", "opencode"]
                    st.session_state.selected_tools[tool_id] = col4.checkbox(
                        "自动配置",
                        value=default_checked,
                        key=f"check_{tool_id}"
                    )
                else:
                    col1.error("❌")
                    col2.write(f"**{result['display_name']}**")
                    if tool_id == "opencode":
                        opencode_paths = config_detector.TOOL_CONFIGS["opencode"]["standard_paths"]
                        col3.caption("未检测到，候选位置（按优先级）：")
                        col3.code("\n".join(opencode_paths), language="text")
                    else:
                        col3.caption(f"未检测到，标准位置: {result['config_path_hint']}")
                    st.session_state.selected_tools[tool_id] = col4.checkbox(
                        "创建并配置",
                        value=False,
                        key=f"check_create_{tool_id}"
                    )
            
            st.markdown("---")
            
            selected_count = sum(1 for v in st.session_state.selected_tools.values() if v)
            if selected_count > 0:
                st.caption(f"已选择 {selected_count} 个工具进行自动配置")
                tool_hints = []
                if st.session_state.selected_tools.get("claude_code"):
                    tool_hints.append(f"ClaudeCode (默认模型: {tool_model_config.get('claude_code', model_hint)})")
                if st.session_state.selected_tools.get("opencode"):
                    tool_hints.append(f"OpenCode (默认模型: {tool_model_config.get('opencode', model_hint)}, 注入 {len(model_list)} 个代理模型)")
                if st.session_state.selected_tools.get("openclaw"):
                    tool_hints.append(f"OpenClaw (默认模型: {tool_model_config.get('openclaw', model_hint)})")
                if st.session_state.selected_tools.get("hermes"):
                    tool_hints.append(f"Hermes (默认模型: {tool_model_config.get('hermes', model_hint)})")
                if tool_hints:
                    st.info("将为以下工具执行配置：\n- " + "\n- ".join(tool_hints))
                
                col_exec, col_preview = st.columns(2)
                if col_exec.button("✅ 确认执行配置", width="stretch"):
                    success_count = 0
                    fail_count = 0
                    messages = []
                    
                    with st.spinner("正在配置..."):
                        for tool_id, selected in st.session_state.selected_tools.items():
                            if not selected:
                                continue
                            
                            result = st.session_state.scan_result[tool_id]
                            config_path = result["path"] or config_detector.get_tool_config_path(tool_id)
                            
                            if result["path"]:
                                backup_path = backup_manager.create_backup(config_path)
                                if backup_path:
                                    messages.append(f"✅ [{result['display_name']}] 已备份: {os.path.basename(backup_path)}")
                            
                            config_detector.ensure_config_dir(tool_id)
                            
                            injector = None
                            if tool_id == "claude_code":
                                injector = ClaudeCodeInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            elif tool_id == "opencode":
                                injector = OpenCodeInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            elif tool_id == "openclaw":
                                injector = OpenClawInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            elif tool_id == "hermes":
                                injector = HermesInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            
                            if injector:
                                ok, err = injector.inject()
                                if ok:
                                    save_ok, save_err = injector.save_config()
                                    if save_ok:
                                        if backup_manager.validate_config_file(config_path):
                                            success_count += 1
                                            messages.append(f"✅ [{result['display_name']}] 配置写入成功")
                                            messages.append(injector.generate_description())
                                        else:
                                            fail_count += 1
                                            backup_manager.restore_latest(tool_id, config_path)
                                            messages.append(f"❌ [{result['display_name']}] 配置验证失败，已自动回滚")
                                    else:
                                        fail_count += 1
                                        messages.append(f"❌ [{result['display_name']}] 写入失败: {save_err}")
                                else:
                                    fail_count += 1
                                    messages.append(f"❌ [{result['display_name']}] 注入失败: {err}")
                    
                    st.divider()
                    if fail_count == 0:
                        st.success(f"🎉 全部配置完成！成功 {success_count} 个工具")
                    else:
                        st.warning(f"配置完成: 成功 {success_count} 个，失败 {fail_count} 个")
                    
                    for msg in messages:
                        if msg.startswith("✅"):
                            st.write(msg)
                        elif msg.startswith("❌"):
                            st.error(msg)
                        else:
                            st.caption(msg)
                
                if col_preview.button("👁️ 查看配置变更预览", width="stretch"):
                    st.session_state.show_preview = True
                
                if st.session_state.get("show_preview", False):
                    with st.expander("配置变更预览", expanded=True):
                        for tool_id, selected in st.session_state.selected_tools.items():
                            if not selected:
                                continue
                            result = st.session_state.scan_result[tool_id]
                            config_path = result["path"] or config_detector.get_tool_config_path(tool_id)
                            
                            injector = None
                            if tool_id == "claude_code":
                                injector = ClaudeCodeInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            elif tool_id == "opencode":
                                injector = OpenCodeInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            elif tool_id == "openclaw":
                                injector = OpenClawInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            elif tool_id == "hermes":
                                injector = HermesInjector(
                                    config_path=config_path,
                                    proxy_base_url=base_host,
                                    proxy_api_key=master_key,
                                    model_list=model_list,
                                    default_model=tool_model_config.get(tool_id, model_hint),
                                )
                            
                            if injector:
                                ok, err = injector.inject()
                                st.markdown(f"**{result['display_name']}**")
                                if not ok:
                                    st.error(f"预览生成失败: {err or '未知错误'}")
                                    st.caption(f"配置路径: {config_path}")
                                    try:
                                        with open(config_path, "r", encoding="utf-8") as f:
                                            raw_text = f.read()
                                        if raw_text.strip():
                                            st.caption("原始文件内容（无法解析为标准 JSON）")
                                            st.code(raw_text, language="json")
                                        else:
                                            st.caption("原始配置文件为空。")
                                    except Exception:
                                        st.caption("无法读取原始配置文件。")
                                    st.markdown("---")
                                    continue

                                st.info(injector.generate_description())
                                col_orig, col_mod = st.columns(2)
                                col_orig.caption("原始配置")
                                col_mod.caption("修改后配置")

                                # Hermes 为 YAML 文本，优先展示原始/修改后文本
                                if hasattr(injector, "original_text") and hasattr(injector, "modified_text"):
                                    original_text = (getattr(injector, "original_text", "") or "").strip()
                                    modified_text = (getattr(injector, "modified_text", "") or "").strip()
                                    col_orig.code(original_text or "(空)", language="yaml")
                                    col_mod.code(modified_text or "(空)", language="yaml")
                                else:
                                    col_orig.json(injector.original_config)
                                    col_mod.json(injector.modified_config)
                                st.markdown("---")
                        if st.button("关闭预览"):
                            st.session_state.show_preview = False
            else:
                st.info("请选择至少一个工具进行配置")
        
        st.divider()
        st.subheader("📋 手动配置")
        st.info("高级用户可以使用手动配置方式，生成配置示例和启动脚本。")

    t1, t2, t3, t4 = st.tabs(["Claude Code", "OpenCode", "Cursor / Trae", "Other Tools"])
    
    with t1:
        st.subheader("Claude Code 配置与启动")
        selected_claude_model = st.selectbox(
            "选择 Claude Code 默认模型",
            options=model_list,
            index=model_list.index(tool_model_config.get("claude_code", model_hint))
            if tool_model_config.get("claude_code", model_hint) in model_list
            else 0,
            key="claude_default_model",
        )
        claude_settings_content = json.dumps(
            {
                "$schema": "https://json.schemastore.org/claude-code-settings.json",
                "env": {
                    "ANTHROPIC_BASE_URL": base_host,
                    "ANTHROPIC_API_KEY": master_key,
                },
                "model": selected_claude_model,
            },
            ensure_ascii=False,
            indent=2,
        )
        st.markdown(f"""
        Claude Code 需要通过设置 Anthropic 的环境变量来指向本地代理。
        这里的 Key 是**本地代理服务的访问 Key**，不是上游平台的 Key。
        上游平台 Key 请在「API Key 管理」中配置。
        
        **1. 一键启动脚本**:
        点击下方按钮生成脚本，它将设置环境变量并直接启动 Claude。
        """)
        
        if st.button("生成一键启动脚本 (claude_start.ps1)"):
            script_content = f"$env:ANTHROPIC_BASE_URL='{base_host}'; $env:ANTHROPIC_API_KEY='{master_key}'; claude --model {selected_claude_model}"
            with open("claude_start.ps1", "w", encoding="utf-8") as f:
                f.write(script_content)
            st.success(f"脚本已生成！请右键运行 `claude_start.ps1`。")
            st.info(f"注意：脚本默认使用了模型 `{selected_claude_model}`。")

        st.markdown("""
        **2. 手动运行命令**:
        在 PowerShell 中运行以下命令：
        """)
        st.code(f"$env:ANTHROPIC_BASE_URL='{base_host}'; $env:ANTHROPIC_API_KEY='{master_key}'; claude --model {selected_claude_model}", language="powershell")

        st.markdown("""
        **3. 配置文件方式**:
        也可以把代理配置写到 Claude Code 的 `settings.json` 里。建议放到：
        `C:\\Users\\你的用户名\\.claude\\settings.json`
        """)

        if st.button("生成 Claude 配置示例 (claude_settings.json)"):
            with open("claude_settings.json", "w", encoding="utf-8") as f:
                f.write(claude_settings_content)
            st.success("配置示例已生成：`claude_settings.json`")
            st.info("你可以把它的内容复制到 `~/.claude/settings.json`。")

        st.code(claude_settings_content, language="json")
        
        st.markdown("---")
        st.warning("注意：Claude Code 的 Base URL 请使用 `http://localhost:8000` (不要带 /v1)。")

    with t2:
        st.subheader("OpenCode 配置与启动")
        selected_opencode_model = st.selectbox(
            "选择 OpenCode 启动模型",
            options=model_list,
            index=model_list.index(tool_model_config.get("opencode", model_hint))
            if tool_model_config.get("opencode", model_hint) in model_list
            else 0,
            key="opencode_default_model",
        )
        opencode_start_content = (
            "$env:OPENCODE_CONFIG='opencode.json'\n"
            "Write-Host 'Starting OpenCode with AIProxy...' -ForegroundColor Cyan\n"
            "Write-Host \"Config: $env:OPENCODE_CONFIG\" -ForegroundColor DarkGray\n"
            f"Write-Host 'Model: aiproxy/{selected_opencode_model}' -ForegroundColor DarkGray\n"
            f"opencode -m aiproxy/{selected_opencode_model}\n"
            "Write-Host ''\n"
            "Read-Host 'OpenCode exited. Press Enter to close'"
        )
        st.markdown(f"""
        OpenCode 更适合通过 OpenAI 兼容 Provider 配置接入本地代理。
        这里的 Key 同样是**本地代理访问 Key**，不是上游平台的 Key。
        OpenCode 全局默认配置文件通常位于：`~/.config/opencode/opencode.json`。

        **1. 生成配置文件**:
        点击下方按钮会在当前目录生成 `opencode.json`，内容已经指向 `{proxy_url_v1}`。
        """)

        if st.button("生成 OpenCode 配置文件 (opencode.json)"):
            with open("opencode.json", "w", encoding="utf-8") as f:
                f.write(opencode_config_content)
            st.success("配置文件已生成：`opencode.json`")

        st.code(opencode_config_content, language="json")

        st.markdown("""
        **2. 生成一键启动脚本**:
        脚本会设置本地代理访问 Key、指定配置文件，并直接启动 OpenCode。
        """)

        if st.button("生成一键启动脚本 (opencode_start.ps1)"):
            with open("opencode_start.ps1", "w", encoding="utf-8") as f:
                f.write(opencode_start_content)
            st.success("脚本已生成：`opencode_start.ps1`")
            st.info(f"默认启动模型：`aiproxy/{selected_opencode_model}`")

        st.markdown("""
        **3. 手动运行命令**:
        在 PowerShell 中运行以下命令：
        """)
        st.code(
            f"$env:OPENCODE_CONFIG='opencode.json'; opencode -m aiproxy/{selected_opencode_model}",
            language="powershell"
        )
        st.caption("如果你不想每次设置环境变量，请把配置写入 `~/.config/opencode/opencode.json`。")

        st.markdown("---")
        st.caption("说明：OpenCode 这里应使用 OpenAI 兼容入口，所以 Base URL 需要带 `/v1`。")

    with t3:
        st.subheader("Cursor 配置指南")
        st.markdown(f"""
        Cursor 对接本代理时，建议按 **OpenAI 兼容接口** 的方式配置。
        下面按“你在 Cursor 里实际会看到的步骤”来写。

        **建议先理解两个 Key 的区别**：
        - 这里填入软件设置页面的 **API Key**，应该是你的**本地代理访问 Key**：`{master_key}`
        - 上游平台的真实 Key（例如 Nvidia / OpenAI / Google）不要填到 Cursor / Trae 里，而是保存在本代理后台的「API Key 管理」中

        **一、打开 Cursor 设置页**
        - 打开 Cursor
        - 进入 `Settings`
        - 找到 `Models`
        - 如果你看到 `OpenAI`, `OpenAI Compatible`, `Custom OpenAI API`, `Manage Models` 或类似入口，都可以进入

        **二、优先使用自定义 OpenAI 入口**
        - 如果 Cursor 同时提供“官方 OpenAI”和“自定义 OpenAI API”，请优先选择自定义入口
        - 如果有原厂 OpenAI 开关，建议先关闭，避免请求直连官方而不是走你的本地代理

        **二、填写代理地址**
        - `Base URL` / `API Base URL` / `Endpoint`：填写 `{proxy_url_v1}`
        - 注意这里**必须带 `/v1`**
        - 不要填写成 `http://localhost:8000`

        **三、填写认证信息**
        - `API Key`：填写 `{master_key}`
        - 不要填写上游厂商 Key，例如 `nvapi-...`

        **四、添加模型名称**
        - 在模型列表里手动新增你在本代理中配置过的“虚拟模型名”
        - 工具里看到的模型名，必须和代理后台「模型映射管理」中的虚拟模型名完全一致
        - 例如你后台里配置的是 `GLM5 -> z-ai/glm5`，那 Cursor 里就应该添加 `GLM5`
        - 不要添加 `z-ai/glm5`

        **五、推荐你在 Cursor 里这样填**
        - Provider 类型：`OpenAI Compatible` / `Custom OpenAI`
        - Base URL：`{proxy_url_v1}`
        - API Key：`{master_key}`
        - Model Name：`{model_hint}`

        **六、保存后如何验证是否生效**
        - 在 Cursor 中选择你刚添加的模型，例如 `GLM5`
        - 发起一次对话
        - 如果代理后端日志里出现 `POST /v1/chat/completions` 并返回 `200`，说明已经走到本地代理
        - 如果日志里完全没有请求，说明 Cursor 还没有真正走到你的自定义入口

        **七、最常见的错误**
        - 填成了 `http://localhost:8000`：这通常是给 Claude Code 用的，Cursor / Trae 这里应使用 `{proxy_url_v1}`
        - 把上游真实 Key 填到了客户端里：应改为 `{master_key}`
        - 模型名填成了真实模型名，例如 `z-ai/glm5`：应改为你代理中定义的虚拟模型名，例如 `GLM5`
        - 保存后仍不生效：尝试重启 Cursor / Trae
        - 如果提示模型不存在：通常是 Cursor 里的模型名和代理后台“虚拟模型名”不一致
        - 如果提示鉴权失败：通常是你填成了上游厂商 Key，而不是本地代理 Key

        **八、关于 Trae**
        - Trae 当前大概率不支持手动填写自定义 `Base URL`
        - 所以现阶段建议优先使用 Cursor 或 OpenCode 来接这个本地代理

        **当前可直接添加的虚拟模型名**：
        """)
        if v_models:
            st.info(", ".join(v_models))
        else:
            st.warning("您尚未在'模型映射管理'中添加任何虚拟模型！")

        st.code(
            f"Provider: OpenAI Compatible / Custom OpenAI\nBase URL: {proxy_url_v1}\nAPI Key: {master_key}\n示例模型: {model_hint}",
            language="text"
        )

    with t4:
        st.subheader("其他工具通用配置")
        st.markdown(f"""
        大部分兼容 OpenAI 协议的工具都适用以下参数：
        
        - **API Base**: `{proxy_url_v1}`
        - **API Key（本地代理访问 Key）**: `{master_key}`
        - **支持模型**: `{", ".join(v_models) if v_models else "尚未配置"}`
        """)
        st.caption("说明：上游平台的真实 API Key 来自「API Key 管理」，代理会自动按模型映射与供应商配置转发。")

        st.markdown("---")
        st.subheader("Hermes 配置示例")
        st.caption("Hermes 建议在 `~/.hermes/config.yaml` 中写入 custom provider 配置。")
        if st.button("生成 Hermes 配置示例 (hermes_config.yaml)"):
            with open("hermes_config.yaml", "w", encoding="utf-8") as f:
                f.write(hermes_config_content)
            st.success("配置示例已生成：`hermes_config.yaml`")
        st.code(hermes_config_content, language="yaml")
        
        st.button("📋 复制全部配置信息", on_click=lambda: st.write("信息已复制到剪贴板 (模拟行为)"))

elif menu == "模型体验":
    st.header("🎛️ 模型体验")
    st.info("直接使用供应商模型列表进行文本对话和图片生成（不依赖映射列表），并计入使用概览统计。")

    with SessionLocal() as session:
        providers = session.query(Provider).all()
        active_keys = session.query(APIKey).filter(APIKey.is_active.is_(True)).all()

    if not providers:
        st.warning("暂无供应商，请先到「供应商管理」添加。")
    elif not active_keys:
        st.warning("暂无可用 API Key，请先到「API Key 管理」添加并启用。")
    else:
        provider_to_key = {}
        for p in providers:
            key = next((k for k in active_keys if k.provider_id == p.id), None)
            if key:
                provider_to_key[p.name] = (p, key)

        if not provider_to_key:
            st.warning("当前供应商都没有可用 Key。")
        else:
            provider_names = list(provider_to_key.keys())
            selected_provider_name = st.selectbox("选择供应商", options=provider_names)
            selected_provider, selected_key = provider_to_key[selected_provider_name]

            cache_key = f"provider_models_{selected_provider.id}"
            if cache_key not in st.session_state:
                st.session_state[cache_key] = []

            c_refresh, c_hint = st.columns([1, 3])
            if c_refresh.button("刷新供应商模型列表", key=f"refresh_models_{selected_provider.id}"):
                st.session_state[cache_key] = fetch_models(selected_provider.api_base, selected_key.key)
            c_hint.caption("模型列表来源：供应商 `/models` 接口。")

            provider_models = st.session_state[cache_key] or fetch_models(selected_provider.api_base, selected_key.key)
            st.session_state[cache_key] = provider_models

            if not provider_models:
                st.warning("未获取到模型列表。请检查供应商 Base URL / Key。")
            else:
                text_models = [m for m in provider_models if classify_model_type(m) == "text"]
                image_models = [m for m in provider_models if classify_model_type(m) == "image"]
                st.caption(f"检测结果：文本模型 {len(text_models)} 个，图片模型 {len(image_models)} 个。")

                # --- 改进7: 模型搜索过滤 ---
                model_filter = st.text_input("🔍 搜索模型名称", value="", placeholder="输入关键词过滤模型列表...", key=f"model_filter_{selected_provider.id}")
                if model_filter.strip():
                    _filter_lower = model_filter.strip().lower()
                    text_models = [m for m in text_models if _filter_lower in m.lower()]
                    image_models = [m for m in image_models if _filter_lower in m.lower()]

                with st.expander("查看供应商返回的原始模型列表（调试）", expanded=False):
                    st.write(provider_models)
                force_all_for_image = st.checkbox(
                    "高级：图片模型下拉显示全部供应商模型（用于识别遗漏）",
                    value=False,
                    key=f"force_all_image_models_{selected_provider.id}",
                )
                if force_all_for_image:
                    image_model_options = [m for m in provider_models if (not model_filter.strip() or model_filter.strip().lower() in m.lower())]
                else:
                    image_model_options = image_models

                t_chat, t_image = st.tabs(["文本对话", "图片生成"])

                with t_chat:
                    st.subheader("文本模型对话")
                    if not text_models:
                        st.info("当前供应商模型列表中未识别到文本模型。" + (" (可能被搜索过滤)" if model_filter.strip() else ""))
                    else:
                        chat_model = st.selectbox("选择文本模型", options=text_models, key="playground_chat_model")

                        # --- 改进3: 提高 max_tokens 上限 ---
                        chat_max_tokens = st.slider("max_tokens", min_value=32, max_value=65536, value=4096, step=32)
                        chat_temperature = st.slider("temperature", min_value=0.0, max_value=1.5, value=0.7, step=0.1)

                        # --- 改进2: System Prompt ---
                        sys_prompt_key = f"system_prompt_{selected_provider.id}_{chat_model}"
                        if sys_prompt_key not in st.session_state:
                            st.session_state[sys_prompt_key] = ""
                        system_prompt = st.text_area(
                            "System Prompt（可选）",
                            value=st.session_state[sys_prompt_key],
                            key=f"sys_prompt_input_{selected_provider.id}_{chat_model}",
                            placeholder="设置系统提示词，留空则不发送 system 消息...",
                            height=80,
                        )
                        st.session_state[sys_prompt_key] = system_prompt

                        chat_state_key = f"chat_history_{selected_provider.id}_{chat_model}"
                        if chat_state_key not in st.session_state:
                            st.session_state[chat_state_key] = []

                        # --- 改进4: 对话管理按钮 ---
                        btn_col1, btn_col2, btn_col3 = st.columns(3)
                        with btn_col1:
                            if st.button("🆕 新建对话", key=f"new_chat_{selected_provider.id}_{chat_model}", use_container_width=True):
                                st.session_state[chat_state_key] = []
                                st.rerun()
                        with btn_col2:
                            if st.button("🗑️ 清空当前对话", key=f"clear_chat_{selected_provider.id}_{chat_model}", use_container_width=True):
                                st.session_state[chat_state_key] = []
                                st.rerun()
                        with btn_col3:
                            # --- 改进5: 对话导出 ---
                            if st.session_state[chat_state_key]:
                                _export_lines = [f"# 模型体验对话记录\n", f"- 供应商: {selected_provider_name}\n", f"- 模型: {chat_model}\n", f"- 导出时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"]
                                for _msg in st.session_state[chat_state_key]:
                                    _role_label = "🧑 用户" if _msg["role"] == "user" else "🤖 助手"
                                    _export_lines.append(f"### {_role_label}\n\n{_msg['content']}\n\n")
                                _export_md = "".join(_export_lines)
                                st.download_button(
                                    "📥 导出对话",
                                    data=_export_md,
                                    file_name=f"chat_{chat_model.replace('/', '_')}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                                    mime="text/markdown",
                                    key=f"export_chat_{selected_provider.id}_{chat_model}",
                                    use_container_width=True,
                                )
                            else:
                                st.button("📥 导出对话", disabled=True, key=f"export_chat_disabled_{selected_provider.id}_{chat_model}", use_container_width=True)

                        if st.session_state[chat_state_key]:
                            st.caption(f"当前对话 {len(st.session_state[chat_state_key])} 条消息 · 模型: {chat_model}")

                        for msg in st.session_state[chat_state_key]:
                            with st.chat_message(msg["role"]):
                                st.markdown(msg["content"])

                        user_input = st.chat_input("输入消息并回车发送", key=f"chat_input_{selected_provider.id}_{chat_model}")
                        if user_input:
                            st.session_state[chat_state_key].append({"role": "user", "content": user_input})
                            with st.chat_message("user"):
                                st.markdown(user_input)

                            url = f"{selected_provider.api_base.rstrip('/')}/chat/completions"
                            headers = {
                                "Authorization": f"Bearer {selected_key.key}",
                                "Content-Type": "application/json",
                            }
                            # 构建消息列表，含可选 system prompt
                            _send_messages = []
                            if system_prompt.strip():
                                _send_messages.append({"role": "system", "content": system_prompt.strip()})
                            _send_messages.extend(st.session_state[chat_state_key])

                            # --- 改进1: 流式输出 ---
                            payload = {
                                "model": chat_model,
                                "messages": _send_messages,
                                "max_tokens": chat_max_tokens,
                                "temperature": chat_temperature,
                                "stream": True,
                            }
                            try:
                                resp = requests.post(url, headers=headers, json=payload, timeout=120, stream=True)
                                if resp.status_code == 200:
                                    # 流式解析 SSE
                                    collected_text = []
                                    usage_data = {}
                                    with st.chat_message("assistant"):
                                        placeholder = st.empty()
                                        for line in resp.iter_lines(decode_unicode=True):
                                            if not line or not line.startswith("data: "):
                                                continue
                                            data_str = line[6:]
                                            if data_str.strip() == "[DONE]":
                                                break
                                            try:
                                                chunk = json.loads(data_str)
                                                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                                                content_piece = delta.get("content") or ""
                                                if content_piece:
                                                    collected_text.append(content_piece)
                                                    placeholder.markdown("".join(collected_text) + "▌")
                                                # 提取最后一个 chunk 的 usage
                                                if chunk.get("usage"):
                                                    usage_data = chunk["usage"]
                                            except (json.JSONDecodeError, Exception):
                                                continue
                                        # 最终显示（去掉光标）
                                        final_text = "".join(collected_text) or "(空响应)"
                                        placeholder.markdown(final_text)

                                    st.session_state[chat_state_key].append({"role": "assistant", "content": final_text})
                                    ptk = (usage_data.get("prompt_tokens") or 0)
                                    ctk = (usage_data.get("completion_tokens") or 0)
                                    ttk = (usage_data.get("total_tokens") or (ptk + ctk))
                                    log_custom_usage(
                                        key_id=selected_key.id,
                                        model_name=chat_model,
                                        prompt_tokens=ptk,
                                        completion_tokens=ctk,
                                        total_tokens=ttk,
                                    )
                                else:
                                    st.error(f"请求失败：HTTP {resp.status_code}，{resp.text[:300]}")
                            except Exception as e:
                                st.error(f"请求异常：{e}")

                with t_image:
                    st.subheader("文生图模型生成")
                    manual_image_model = st.text_input(
                        "手动输入图片模型 ID（可选）",
                        value="stabilityai/stable-diffusion-3.5-large",
                        help="当下拉里没有 SD3/SD3.5 时可直接填写模型 ID。",
                    ).strip()
                    use_manual_image_model = st.checkbox("优先使用手动模型 ID", value=False)

                    if image_model_options:
                        selected_image_model = st.selectbox("选择图片模型", options=image_model_options, key="playground_image_model")
                    else:
                        selected_image_model = manual_image_model or ""
                        st.info("当前下拉没有识别到图片模型，可勾选上方手动模型 ID 直接调用。")

                    image_model = manual_image_model if (use_manual_image_model and manual_image_model) else selected_image_model
                    image_prompt = st.text_area("Prompt", value="A cinematic cyberpunk city at dusk, ultra detailed")
                    image_size = st.selectbox("尺寸", options=["1024x1024", "1024x1792", "1792x1024"], index=0)
                    image_n = st.slider("生成数量", min_value=1, max_value=4, value=1, step=1)

                    # --- 改进6: 图片生成历史 ---
                    _img_history_key = f"image_history_{selected_provider.id}"
                    if _img_history_key not in st.session_state:
                        st.session_state[_img_history_key] = []

                    if st.button("生成图片", key=f"generate_image_{selected_provider.id}", width="stretch"):
                        if not image_model:
                            st.warning("请选择或输入图片模型 ID")
                        elif not image_prompt.strip():
                            st.warning("请输入 prompt")
                        else:
                            url = f"{selected_provider.api_base.rstrip('/')}/images/generations"
                            headers = {
                                "Authorization": f"Bearer {selected_key.key}",
                                "Content-Type": "application/json",
                            }
                            payload = {
                                "model": image_model,
                                "prompt": image_prompt,
                                "n": image_n,
                                "size": image_size,
                            }
                            try:
                                resp = requests.post(url, headers=headers, json=payload, timeout=180)
                                if resp.status_code == 200:
                                    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                                    images = data.get("data", []) if isinstance(data, dict) else []
                                    if not images:
                                        st.warning("返回成功但未解析到图片数据。")
                                    else:
                                        for idx, item in enumerate(images):
                                            st.markdown(f"**图片 {idx + 1}**")
                                            _img_data_for_history = None
                                            if item.get("url"):
                                                img_url = item["url"]
                                                st.image(img_url, caption=f"{image_model} - {image_size}")
                                                try:
                                                    img_resp = requests.get(img_url, timeout=60)
                                                    if img_resp.status_code == 200:
                                                        _img_data_for_history = base64.b64encode(img_resp.content).decode()
                                                        st.download_button(
                                                            label=f"下载图片 {idx + 1}",
                                                            data=img_resp.content,
                                                            file_name=f"{image_model.replace('/', '_')}_{idx+1}.png",
                                                            mime="image/png",
                                                            key=f"download_url_img_{selected_provider.id}_{idx}",
                                                        )
                                                except Exception:
                                                    pass
                                            elif item.get("b64_json"):
                                                img_bytes = base64.b64decode(item["b64_json"])
                                                _img_data_for_history = item["b64_json"]
                                                st.image(img_bytes, caption=f"{image_model} - {image_size}")
                                                st.download_button(
                                                    label=f"下载图片 {idx + 1}",
                                                    data=img_bytes,
                                                    file_name=f"{image_model.replace('/', '_')}_{idx+1}.png",
                                                    mime="image/png",
                                                    key=f"download_b64_img_{selected_provider.id}_{idx}",
                                                )
                                            # 保存到历史
                                            if _img_data_for_history:
                                                st.session_state[_img_history_key].append({
                                                    "prompt": image_prompt,
                                                    "model": image_model,
                                                    "size": image_size,
                                                    "b64": _img_data_for_history,
                                                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                })
                                                # 限制历史最多 20 张
                                                if len(st.session_state[_img_history_key]) > 20:
                                                    st.session_state[_img_history_key] = st.session_state[_img_history_key][-20:]
                                    log_custom_usage(
                                        key_id=selected_key.id,
                                        model_name=image_model,
                                        images_count=max(1, len(images)),
                                    )
                                else:
                                    st.error(f"请求失败：HTTP {resp.status_code}，{resp.text[:500]}")
                            except Exception as e:
                                st.error(f"请求异常：{e}")

                    # 显示图片生成历史
                    if st.session_state[_img_history_key]:
                        with st.expander(f"📸 图片生成历史（{len(st.session_state[_img_history_key])} 张）", expanded=False):
                            for _hi, _hitem in enumerate(reversed(st.session_state[_img_history_key])):
                                st.caption(f"{_hitem['time']} · {_hitem['model']} · {_hitem['size']}")
                                st.text(f"Prompt: {_hitem['prompt'][:100]}{'...' if len(_hitem['prompt']) > 100 else ''}")
                                try:
                                    _h_bytes = base64.b64decode(_hitem["b64"])
                                    st.image(_h_bytes, width=256)
                                    st.download_button(
                                        label=f"下载",
                                        data=_h_bytes,
                                        file_name=f"{_hitem['model'].replace('/', '_')}_{_hi}.png",
                                        mime="image/png",
                                        key=f"download_history_img_{selected_provider.id}_{_hi}",
                                    )
                                except Exception:
                                    st.warning("图片数据已失效")
                                st.divider()
                            if st.button("🗑️ 清空图片历史", key=f"clear_img_history_{selected_provider.id}"):
                                st.session_state[_img_history_key] = []
                                st.rerun()

elif menu == "系统设置":
    st.header("⚙️ 系统配置")
    st.info("代理后端地址: `http://localhost:8000/v1`")
    current_master_key = os.getenv("MASTER_KEY", "sk-admin-123456")
    new_master_key = st.text_input("Master API Key", value=current_master_key, type="password")
    auth_enabled_now = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    new_auth_enabled = st.checkbox("启用 API Key 验证", value=auth_enabled_now)
    c_save, c_copy = st.columns(2)
    if c_save.button("保存系统设置", width="stretch"):
        if not new_master_key.strip():
            st.error("Master API Key 不能为空")
        else:
            upsert_env_value("MASTER_KEY", new_master_key.strip())
            upsert_env_value("AUTH_ENABLED", "true" if new_auth_enabled else "false")
            st.success("系统设置已写入 .env，重启服务后生效。")
    with c_copy:
        copy_button("一键复制 Master Key", new_master_key, "copy-master-key")
    st.caption("说明：这里会修改项目根目录 `.env` 文件。")
