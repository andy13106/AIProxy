import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import select, delete, update, func
from sqlalchemy.orm import Session
from db import SessionLocal, sync_engine, Provider, APIKey, ModelMapping, UsageLog, Base
import pandas as pd
import datetime
import requests
import os
import json
import base64

# --- 导入配置助手模块 ---
from config_assistant import (
    ConfigDetector,
    BackupManager,
    ModelsSync,
    ClaudeCodeInjector,
    OpenCodeInjector,
    OpenClawInjector,
)

# --- 数据库初始化 ---
# 仅在第一次运行时初始化
if 'db_initialized' not in st.session_state:
    Base.metadata.create_all(bind=sync_engine)
    st.session_state.db_initialized = True

st.set_page_config(page_title="AI Proxy Master Admin", layout="wide", initial_sidebar_state="expanded")

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
                return [m.get("id", m) for m in data]
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
        st.caption("可按模型清理统计数据（仅删除 usage_logs，不影响模型映射）。")
        for row in today_summary:
            c_view, c_del = st.columns([3, 1])
            if c_view.button(f"查看 {row.model_name}", key=f"usage_model_{row.model_name}"):
                st.session_state.usage_selected_model = row.model_name
            if c_del.button(f"删除 {row.model_name}", key=f"usage_delete_model_{row.model_name}"):
                with SessionLocal() as session:
                    deleted = session.query(UsageLog).filter(UsageLog.model_name == row.model_name).delete()
                    session.commit()
                if st.session_state.usage_selected_model == row.model_name:
                    st.session_state.usage_selected_model = None
                st.toast(f"已删除模型 {row.model_name} 的 {deleted} 条统计记录", icon="🧹")
                st.rerun()

    else:
        st.info("今天还没有有效使用记录。")

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
    
    # 1. 添加表单
    with st.expander("➕ 添加新供应商", expanded=False):
        with st.form("add_p", clear_on_submit=True):
            name = st.text_input("供应商名称").strip()
            base_url = st.text_input("API Base URL", value="https://integrate.api.nvidia.com/v1").strip()
            if st.form_submit_button("保存供应商", width="stretch"):
                base_url = base_url.strip().strip("`").strip("'").strip("\"")
                if not name or not base_url:
                    st.error("请完整填写信息")
                else:
                    with SessionLocal() as session:
                        if session.query(Provider).filter(Provider.name == name).first():
                            st.error(f"供应商 '{name}' 已存在")
                        else:
                            new_p = Provider(name=name, api_base=base_url)
                            session.add(new_p)
                            session.commit()
                            st.toast(f"已添加供应商: {name}")
                            st.rerun()

    # 2. 列表展示
    st.subheader("现有供应商列表")
    with SessionLocal() as session:
        providers = session.query(Provider).all()
    
    if providers:
        h1, h2, h3, h4 = st.columns([1, 2, 5, 1])
        h1.write("**ID**")
        h2.write("**名称**")
        h3.write("**Base URL**")
        h4.write("**操作**")
        st.divider()
        
        for p in providers:
            c1, c2, c3, c4 = st.columns([1, 2, 5, 1])
            c1.write(f"`{p.id}`")
            c2.write(p.name)
            c3.write(f"`{p.api_base}`")
            if c4.button("🗑️", key=f"del_p_{p.id}"):
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
                target_p = st.selectbox("选择供应商", options=list(p_map.keys()))
                key_val = st.text_input("API Key", type="password").strip()
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
            sel_p_name = st.selectbox("选择供应商", options=list(p_map.keys()))
            sel_p = p_map[sel_p_name]
            
            with SessionLocal() as session:
                first_key = session.query(APIKey).filter(APIKey.provider_id == sel_p.id).first()
            
            with st.form("add_m", clear_on_submit=True):
                v_name = st.text_input("虚拟模型名称 (工具调用时使用)", placeholder="gpt-4o").strip()
                
                real_model = ""
                if first_key:
                    models = fetch_models(sel_p.api_base, first_key.key)
                    if models:
                        real_model = st.selectbox("选择真实模型", options=models)
                    else:
                        st.caption("⚠️ 无法自动获取模型列表，请手动输入")
                        real_model = st.text_input("真实模型名称").strip()
                else:
                    st.caption("⚠️ 该供应商下无 Key，请手动输入真实模型名")
                    real_model = st.text_input("真实模型名称").strip()
                
                if st.form_submit_button("保存映射", width="stretch"):
                    if not v_name or not real_model:
                        st.error("请完整填写映射信息")
                    else:
                        with SessionLocal() as session:
                            if session.query(ModelMapping).filter(ModelMapping.virtual_name == v_name).first():
                                st.error(f"虚拟名称 '{v_name}' 已占用")
                            else:
                                new_m = ModelMapping(virtual_name=v_name, real_name=real_model, provider_id=sel_p.id)
                                session.add(new_m)
                                session.commit()
                                st.toast(f"映射已保存: {v_name} -> {real_model}")
                                st.rerun()

        # 2. 列表展示
        st.subheader("映射列表")
        with SessionLocal() as session:
            mappings = session.query(ModelMapping, Provider).join(Provider).all()
            
        if mappings:
            h1, h2, h3, h4, h5 = st.columns([1, 3, 5, 2, 1])
            h1.write("**ID**")
            h2.write("**虚拟名称**")
            h3.write("**真实模型**")
            h4.write("**供应商**")
            h5.write("**操作**")
            st.divider()
            
            for m, p in mappings:
                c1, c2, c3, c4, c5 = st.columns([1, 3, 5, 2, 1])
                c1.write(f"`{m.id}`")
                c2.write(f"**{m.virtual_name}**")
                c3.write(f"`{m.real_name}`")
                c4.write(p.name)
                if c5.button("🗑️", key=f"del_m_{m.id}"):
                    if delete_item(ModelMapping, m.id, "模型映射已删除"):
                        st.rerun()
        else:
            st.info("尚未配置模型映射")

elif menu == "工具配置助手":
    st.header("🛠️ AI 工具配置助手")
    st.info("帮助您将本地代理一键配置到常用的 AI 编程工具中。")

    # 获取当前配置
    base_host = "http://localhost:8000"
    proxy_url_v1 = f"{base_host}/v1"
    master_key = os.getenv("MASTER_KEY", "sk-admin-123456")

    with SessionLocal() as session:
        mappings = session.query(ModelMapping).all()
        v_models = [m.virtual_name for m in mappings]

    model_list = sorted(set(v_models)) if v_models else ["GLM5"]
    model_hint = "GLM5" if "GLM5" in model_list else model_list[0]

    # --- 智能配置区域 ---
    st.divider()
    st.subheader("🤖 智能自动配置")

    # 初始化配置助手
    config_detector = ConfigDetector()
    backup_manager = BackupManager()
    models_sync = ModelsSync()

    # 检测Docker环境
    in_docker = config_detector.is_running_in_docker()

    if in_docker:
        st.warning("""
        ⚠️ **Docker环境检测到**

        自动配置功能需要直接访问宿主机文件系统，当前运行在Docker容器中，
        **无法自动修改您的本地AI工具配置文件**。

        请使用以下方式之一：
        1. 下载配置文件，手动复制到工具配置目录
        2. 在本机直接运行项目（python + streamlit方式）
        3. 使用下方的手动配置说明
        """)
    else:
        st.success("✅ 本地运行环境，支持自动配置")

        # 配置文件扫描
        if st.button("🔍 扫描系统中的配置文件", type="primary"):
            with st.spinner("正在扫描..."):
                detection_results = config_detector.get_detected_tools_summary()

            st.session_state["config_detection_results"] = detection_results

            # 显示扫描结果
            st.markdown("#### 扫描结果")

            for tool in detection_results["detected"]:
                st.success(f"✅ 找到 **{tool['name']}** 配置：`{tool['path']}`")

            for tool in detection_results["not_detected"]:
                st.info(f"❌ 未检测到 **{tool['name']}** 配置 (建议位置：`{tool['suggested_path']}`)")

        # 如果有扫描结果，显示配置选项
        if "config_detection_results" in st.session_state:
            detection_results = st.session_state["config_detection_results"]

            st.markdown("---")
            st.markdown("#### 选择要配置的工具")

            # 构建工具选择
            tools_to_configure = {}

            # Claude Code
            claude_info = detection_results["details"].get("claude_code")
            if claude_info and claude_info.exists:
                default_model = models_sync.get_default_model()
                tools_to_configure["claude_code"] = st.checkbox(
                    f"**Claude Code** (将设置默认模型为 {default_model})",
                    value=True
                )
            else:
                st.checkbox("**Claude Code** (未检测到配置文件)", value=False, disabled=True)

            # OpenCode
            opencode_info = detection_results["details"].get("opencode")
            models_count = models_sync.get_models_count()
            if opencode_info and opencode_info.exists:
                tools_to_configure["opencode"] = st.checkbox(
                    f"**OpenCode** (将注入 {models_count} 个代理模型)",
                    value=True
                )
            else:
                st.checkbox("**OpenCode** (未检测到配置文件)", value=False, disabled=True)

            # OpenClaw
            openclaw_info = detection_results["details"].get("openclaw")
            if openclaw_info and openclaw_info.exists:
                tools_to_configure["openclaw"] = st.checkbox(
                    f"**OpenClaw** (将注入 {models_count} 个代理模型)",
                    value=False
                )
            else:
                st.checkbox("**OpenClaw** (未检测到配置文件)", value=False, disabled=True)

            # 配置预览和执行
            if any(tools_to_configure.values()):
                col1, col2 = st.columns(2)

                with col1:
                    if st.button("👁️ 查看配置变更预览", use_container_width=True):
                        # 使用expander来组织对比内容
                        if tools_to_configure.get("claude_code"):
                            with st.expander("Claude Code 配置对比", expanded=True):
                                injector = ClaudeCodeInjector(backup_manager)
                                original = injector.get_current_config()
                                target = models_sync.generate_claude_code_config()
                                merged = injector.merge_configs(original or {}, target)

                                col_orig, col_new = st.columns(2)
                                with col_orig:
                                    st.markdown("**📄 原配置**")
                                    if original:
                                        st.code(json.dumps(original, ensure_ascii=False, indent=2), language="json")
                                    else:
                                        st.info("(文件不存在，将创建新配置)")
                                with col_new:
                                    st.markdown("**✨ 新配置**")
                                    st.code(json.dumps(merged, ensure_ascii=False, indent=2), language="json")

                        if tools_to_configure.get("opencode"):
                            with st.expander("OpenCode 配置对比", expanded=True):
                                injector = OpenCodeInjector(backup_manager)
                                original = injector.get_current_config()
                                target = models_sync.generate_opencode_config()
                                merged = injector.merge_configs(original or {}, target)

                                col_orig, col_new = st.columns(2)
                                with col_orig:
                                    st.markdown("**📄 原配置**")
                                    if original:
                                        st.code(json.dumps(original, ensure_ascii=False, indent=2), language="json")
                                    else:
                                        st.info("(文件不存在，将创建新配置)")
                                with col_new:
                                    st.markdown("**✨ 新配置**")
                                    st.code(json.dumps(merged, ensure_ascii=False, indent=2), language="json")

                        if tools_to_configure.get("openclaw"):
                            with st.expander("OpenClaw 配置对比", expanded=True):
                                injector = OpenClawInjector(backup_manager)
                                original = injector.get_current_config()
                                target = models_sync.generate_openclaw_config()
                                merged = injector.merge_configs(original or {}, target)

                                col_orig, col_new = st.columns(2)
                                with col_orig:
                                    st.markdown("**📄 原配置**")
                                    if original:
                                        st.code(json.dumps(original, ensure_ascii=False, indent=2), language="json")
                                    else:
                                        st.info("(文件不存在，将创建新配置)")
                                with col_new:
                                    st.markdown("**✨ 新配置**")
                                    st.code(json.dumps(merged, ensure_ascii=False, indent=2), language="json")

                with col2:
                    if st.button("⚡ 执行自动配置", type="primary", use_container_width=True):
                        results = []

                        # 配置 Claude Code
                        if tools_to_configure.get("claude_code"):
                            injector = ClaudeCodeInjector(backup_manager)
                            result = injector.inject_config()
                            if result.success:
                                st.success(f"✅ Claude Code: {result.message}")
                                if result.backup_path:
                                    st.caption(f"备份: `{result.backup_path}`")
                            else:
                                st.error(f"❌ Claude Code: {result.message}")
                            results.append(("Claude Code", result.success))

                        # 配置 OpenCode
                        if tools_to_configure.get("opencode"):
                            injector = OpenCodeInjector(backup_manager)
                            result = injector.inject_config()
                            if result.success:
                                st.success(f"✅ OpenCode: {result.message}")
                                if result.backup_path:
                                    st.caption(f"备份: `{result.backup_path}`")
                            else:
                                st.error(f"❌ OpenCode: {result.message}")
                            results.append(("OpenCode", result.success))

                        # 配置 OpenClaw
                        if tools_to_configure.get("openclaw"):
                            injector = OpenClawInjector(backup_manager)
                            result = injector.inject_config()
                            if result.success:
                                st.success(f"✅ OpenClaw: {result.message}")
                                if result.backup_path:
                                    st.caption(f"备份: `{result.backup_path}`")
                            else:
                                st.error(f"❌ OpenClaw: {result.message}")
                            results.append(("OpenClaw", result.success))

                        # 总结
                        success_count = sum(1 for _, success in results if success)
                        total_count = len(results)
                        if success_count == total_count:
                            st.balloons()
                            st.success(f"🎉 全部配置成功！({success_count}/{total_count})")
                        else:
                            st.warning(f"⚠️ 部分配置成功 ({success_count}/{total_count})")

    # 备份管理
    st.divider()
    with st.expander("📦 备份管理", expanded=False):
        # 扫描所有可能的备份位置
        all_backups = []

        # 从注入器获取配置路径来查找备份
        injectors = {
            "Claude Code": ClaudeCodeInjector(backup_manager),
            "OpenCode": OpenCodeInjector(backup_manager),
            "OpenClaw": OpenClawInjector(backup_manager),
        }

        for tool_name, injector in injectors.items():
            config_path = injector.get_default_config_path()
            config_dir = os.path.dirname(config_path)
            if os.path.exists(config_dir):
                tool_backups = backup_manager.list_backups(config_dir)
                for backup in tool_backups:
                    backup["tool"] = tool_name
                    backup["config_path"] = config_path
                    all_backups.append(backup)

        # 也去backup_dir查找
        if backup_manager.backup_dir and os.path.exists(backup_manager.backup_dir):
            dir_backups = backup_manager.list_backups(backup_manager.backup_dir)
            for backup in dir_backups:
                if "tool" not in backup:
                    backup["tool"] = "未知"
                    backup["config_path"] = ""
                all_backups.append(backup)

        # 去重并按时间排序
        seen_paths = set()
        unique_backups = []
        for backup in sorted(all_backups, key=lambda x: x["modified"], reverse=True):
            if backup["path"] not in seen_paths:
                seen_paths.add(backup["path"])
                unique_backups.append(backup)

        if unique_backups:
            st.markdown(f"找到 {len(unique_backups)} 个备份文件")

            # 按工具分组显示
            for backup in unique_backups[:10]:  # 只显示最近10个
                with st.container():
                    col1, col2, col3, col4 = st.columns([2, 2, 1.5, 1])

                    tool_name = backup.get("tool", "未知")
                    col1.markdown(f"**{tool_name}**")
                    col2.caption(f"{backup['filename']}")
                    col3.caption(f"{backup['modified']}")

                    # 恢复按钮
                    if col4.button("恢复", key=f"restore_{backup['filename']}"):
                        # 尝试确定恢复目标路径
                        target_path = backup.get("config_path")
                        if not target_path:
                            # 从备份文件名解析
                            target_path = backup_manager._parse_original_path_from_backup(backup['path'])

                        success, msg = backup_manager.restore_from_backup(backup['path'], target_path)
                        if success:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")

                    # 显示备份内容预览
                    with st.expander("查看备份内容", expanded=False):
                        try:
                            with open(backup['path'], 'r', encoding='utf-8') as f:
                                content = f.read()
                                st.code(content, language="json")
                        except Exception as e:
                            st.error(f"无法读取备份: {e}")

            # 清理旧备份按钮
            if len(unique_backups) > 5:
                if st.button("🧹 清理旧备份（只保留最近5个）"):
                    deleted = 0
                    for backup in unique_backups[5:]:
                        try:
                            os.remove(backup['path'])
                            deleted += 1
                        except:
                            pass
                    st.success(f"已清理 {deleted} 个旧备份")
                    st.rerun()
        else:
            st.info("暂无备份文件")
            st.caption("备份文件会在自动配置修改前自动创建，格式：文件名.backup.时间戳.json")

    st.divider()
    st.subheader("📖 手动配置指南")

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

    t1, t2, t3, t4 = st.tabs(["Claude Code", "OpenCode", "Cursor / Trae", "Other Tools"])
    
    with t1:
        st.subheader("Claude Code 配置与启动")
        selected_claude_model = st.selectbox(
            "选择 Claude Code 默认模型",
            options=model_list,
            index=model_list.index(model_hint) if model_hint in model_list else 0,
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
            index=model_list.index(model_hint) if model_hint in model_list else 0,
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
                with st.expander("查看供应商返回的原始模型列表（调试）", expanded=False):
                    st.write(provider_models)
                force_all_for_image = st.checkbox(
                    "高级：图片模型下拉显示全部供应商模型（用于识别遗漏）",
                    value=False,
                    key=f"force_all_image_models_{selected_provider.id}",
                )
                image_model_options = provider_models if force_all_for_image else image_models

                t_chat, t_image = st.tabs(["文本对话", "图片生成"])

                with t_chat:
                    st.subheader("文本模型对话")
                    if not text_models:
                        st.info("当前供应商模型列表中未识别到文本模型。")
                    else:
                        chat_model = st.selectbox("选择文本模型", options=text_models, key="playground_chat_model")
                        chat_max_tokens = st.slider("max_tokens", min_value=32, max_value=4096, value=512, step=32)
                        chat_temperature = st.slider("temperature", min_value=0.0, max_value=1.5, value=0.7, step=0.1)

                        chat_state_key = f"chat_history_{selected_provider.id}_{chat_model}"
                        if chat_state_key not in st.session_state:
                            st.session_state[chat_state_key] = []

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
                            payload = {
                                "model": chat_model,
                                "messages": st.session_state[chat_state_key],
                                "max_tokens": chat_max_tokens,
                                "temperature": chat_temperature,
                                "stream": False,
                            }
                            try:
                                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                                if resp.status_code == 200:
                                    data = resp.json()
                                    assistant_text = (
                                        (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
                                        or "(空响应)"
                                    )
                                    st.session_state[chat_state_key].append({"role": "assistant", "content": assistant_text})
                                    with st.chat_message("assistant"):
                                        st.markdown(assistant_text)

                                    usage = data.get("usage", {}) if isinstance(data, dict) else {}
                                    ptk = usage.get("prompt_tokens", 0) or 0
                                    ctk = usage.get("completion_tokens", 0) or 0
                                    ttk = usage.get("total_tokens", ptk + ctk) or (ptk + ctk)
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

                        if st.button("清空当前对话", key=f"clear_chat_{selected_provider.id}_{chat_model}"):
                            st.session_state[chat_state_key] = []
                            st.rerun()

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
                                            if item.get("url"):
                                                img_url = item["url"]
                                                st.image(img_url, caption=f"{image_model} - {image_size}")
                                                try:
                                                    img_resp = requests.get(img_url, timeout=60)
                                                    if img_resp.status_code == 200:
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
                                                st.image(img_bytes, caption=f"{image_model} - {image_size}")
                                                st.download_button(
                                                    label=f"下载图片 {idx + 1}",
                                                    data=img_bytes,
                                                    file_name=f"{image_model.replace('/', '_')}_{idx+1}.png",
                                                    mime="image/png",
                                                    key=f"download_b64_img_{selected_provider.id}_{idx}",
                                                )
                                    log_custom_usage(
                                        key_id=selected_key.id,
                                        model_name=image_model,
                                        images_count=max(1, len(images)),
                                    )
                                else:
                                    st.error(f"请求失败：HTTP {resp.status_code}，{resp.text[:500]}")
                            except Exception as e:
                                st.error(f"请求异常：{e}")

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
