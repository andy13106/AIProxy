import streamlit as st
from sqlalchemy import select, delete, update, func
from sqlalchemy.orm import Session
from db import SessionLocal, sync_engine, Provider, APIKey, ModelMapping, UsageLog, Base
import pandas as pd
import datetime
import requests
import os

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

menu = st.sidebar.radio("导航", ["使用概览", "供应商管理", "API Key 管理", "模型映射管理", "工具配置助手", "系统设置"])

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
                return [m["id"] for m in data["data"]]
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

        st.caption("点击下方模型按钮可查看该模型明细；默认上方仅统计当天消耗。")
        model_cols = st.columns(min(len(today_summary), 4) or 1)
        for idx, row in enumerate(today_summary):
            if model_cols[idx % len(model_cols)].button(f"查看 {row.model_name}", key=f"usage_model_{row.model_name}"):
                st.session_state.usage_selected_model = row.model_name
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
    # 实际应用中这些应当来自环境变量或数据库
    base_host = "http://localhost:8000"
    proxy_url_v1 = f"{base_host}/v1"
    master_key = "sk-admin-123456"
    
    with SessionLocal() as session:
        mappings = session.query(ModelMapping).all()
        v_models = [m.virtual_name for m in mappings]
        
    model_hint = v_models[0] if v_models else "GLM5"
    opencode_config_content = (
        '{\n'
        '  "$schema": "https://opencode.ai/config.json",\n'
        '  "provider": {\n'
        '    "aiproxy": {\n'
        '      "npm": "@ai-sdk/openai-compatible",\n'
        '      "name": "AIProxy",\n'
        '      "options": {\n'
        f'        "baseURL": "{proxy_url_v1}",\n'
        '        "apiKey": "{env:AIPROXY_KEY}"\n'
        '      },\n'
        '      "models": {\n'
        f'        "{model_hint}": {{\n'
        f'          "name": "{model_hint}"\n'
        '        }\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}'
    )
    opencode_start_content = (
        f"$env:AIPROXY_KEY='{master_key}'\n"
        "$env:OPENCODE_CONFIG='opencode.json'\n"
        "Write-Host 'Starting OpenCode with AIProxy...' -ForegroundColor Cyan\n"
        "Write-Host \"Config: $env:OPENCODE_CONFIG\" -ForegroundColor DarkGray\n"
        f"Write-Host 'Model: aiproxy/{model_hint}' -ForegroundColor DarkGray\n"
        f"opencode -m aiproxy/{model_hint}\n"
        "Write-Host ''\n"
        "Read-Host 'OpenCode exited. Press Enter to close'"
    )
    claude_settings_content = (
        '{\n'
        '  "$schema": "https://json.schemastore.org/claude-code-settings.json",\n'
        '  "env": {\n'
        f'    "ANTHROPIC_BASE_URL": "{base_host}",\n'
        f'    "ANTHROPIC_API_KEY": "{master_key}"\n'
        '  }\n'
        '}'
    )

    t1, t2, t3, t4 = st.tabs(["Claude Code", "OpenCode", "Cursor / Trae", "Other Tools"])
    
    with t1:
        st.subheader("Claude Code 配置与启动")
        st.markdown(f"""
        Claude Code 需要通过设置 Anthropic 的环境变量来指向本地代理。
        这里的 Key 是**本地代理服务的访问 Key**，不是上游平台的 Key。
        上游平台 Key 请在「API Key 管理」中配置。
        
        **1. 一键启动脚本**:
        点击下方按钮生成脚本，它将设置环境变量并直接启动 Claude。
        """)
        
        if st.button("生成一键启动脚本 (claude_start.ps1)"):
            script_content = f"$env:ANTHROPIC_BASE_URL='{base_host}'; $env:ANTHROPIC_API_KEY='{master_key}'; claude --model {model_hint}"
            with open("claude_start.ps1", "w", encoding="utf-8") as f:
                f.write(script_content)
            st.success(f"脚本已生成！请右键运行 `claude_start.ps1`。")
            st.info(f"注意：脚本默认使用了模型 `{model_hint}`。")

        st.markdown("""
        **2. 手动运行命令**:
        在 PowerShell 中运行以下命令：
        """)
        st.code(f"$env:ANTHROPIC_BASE_URL='{base_host}'; $env:ANTHROPIC_API_KEY='{master_key}'; claude --model {model_hint}", language="powershell")

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
            st.info(f"默认启动模型：`aiproxy/{model_hint}`")

        st.markdown("""
        **3. 手动运行命令**:
        在 PowerShell 中运行以下命令：
        """)
        st.code(
            f"$env:AIPROXY_KEY='{master_key}'; $env:OPENCODE_CONFIG='opencode.json'; opencode -m aiproxy/{model_hint}",
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

elif menu == "系统设置":
    st.header("⚙️ 系统配置")
    st.info("代理后端地址: `http://localhost:8000/v1`")
    st.text_input("Master API Key", value="sk-admin-123456", type="password", disabled=True)
    st.checkbox("启用 API Key 验证", value=True, disabled=True)
    st.caption("注：系统设置目前需通过修改 .env 文件生效。")
