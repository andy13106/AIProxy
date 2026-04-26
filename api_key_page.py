import streamlit as st
from db import SessionLocal, Provider, APIKey, ModelMapping
from utils import delete_item, test_upstream_connectivity, test_upstream_chat


def render_api_key_page():
    st.header("🔑 API Key 管理")
    
    with SessionLocal() as session:
        providers = session.query(Provider).all()
    
    if not providers:
        st.warning("请先在'供应商管理'中添加供应商")
    else:
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
