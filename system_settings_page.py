import streamlit as st
from utils import upsert_env_value
import os


def render_system_settings_page():
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
