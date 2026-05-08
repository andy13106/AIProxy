import streamlit as st
import os

from db import Base, sync_engine

from overview_page import render_overview_page
from provider_page import render_provider_page
from api_key_page import render_api_key_page
from model_mapping_page import render_model_mapping_page
from tool_assistant_page import render_tool_assistant_page
from playground_page import render_playground_page
from system_settings_page import render_system_settings_page
from streamlit_theme import apply_playground_theme


if 'db_initialized' not in st.session_state:
    Base.metadata.create_all(bind=sync_engine)
    st.session_state.db_initialized = True

st.set_page_config(page_title="AI Proxy Master Admin", layout="wide", initial_sidebar_state="expanded")

apply_playground_theme()

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

st.sidebar.title("🤖 AI-Proxy Admin")
if st.sidebar.button("🔄 刷新页面", width="stretch"):
    st.rerun()

menu = st.sidebar.radio("导航", ["使用概览", "供应商管理", "API Key 管理", "模型映射管理", "工具配置助手", "模型体验", "系统设置"])

if menu == "使用概览":
    render_overview_page()
elif menu == "供应商管理":
    render_provider_page()
elif menu == "API Key 管理":
    render_api_key_page()
elif menu == "模型映射管理":
    render_model_mapping_page()
elif menu == "工具配置助手":
    render_tool_assistant_page()
elif menu == "模型体验":
    render_playground_page()
elif menu == "系统设置":
    render_system_settings_page()
