import streamlit as st
from sqlalchemy import func
from db import SessionLocal, Provider, APIKey, ModelMapping
from utils import delete_item, get_ordered_model_mappings, move_model_mapping, fetch_models


def render_model_mapping_page():
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

        with st.expander("➕ 添加新映射", expanded=False):
            p_map = {p.name: p for p in providers}
            sel_p_name = st.selectbox("选择供应商", options=list(p_map.keys()), key="add_map_provider")
            sel_p = p_map[sel_p_name]

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

        st.subheader("映射列表")
        mappings = get_ordered_model_mappings()

        if "edit_mapping_id" not in st.session_state:
            st.session_state.edit_mapping_id = None

        if mappings:
            st.caption("💡 使用 ⬆️⬇️ 按钮调整模型顺序，顺序将影响工具中的默认选择。点击编辑或删除按钮进行相应操作。")

            h1, h2, h3, h4, h5, h6, h7 = st.columns([0.5, 1.2, 3, 5, 2, 1, 1])
            h1.write("**#**")
            h2.write("**排序**")
            h3.write("**虚拟名称**")
            h4.write("**真实模型**")
            h5.write("**供应商**")
            h6.write("**编辑**")
            h7.write("**删除**")
            st.divider()

            for idx, (m, p) in enumerate(mappings):
                c1, c2, c3, c4, c5, c6, c7 = st.columns([0.5, 1.2, 3, 5, 2, 1, 1])
                c1.write(f"{idx + 1}")
                with c2:
                    btn_up, btn_down = st.columns(2)
                    if btn_up.button("⬆️", key=f"up_m_{m.id}", disabled=(idx == 0), use_container_width=True):
                        move_model_mapping(m.id, "up")
                        st.rerun()
                    if btn_down.button("⬇️", key=f"down_m_{m.id}", disabled=(idx == len(mappings) - 1), use_container_width=True):
                        move_model_mapping(m.id, "down")
                        st.rerun()
                c3.write(f"**{m.virtual_name}**")
                c4.write(f"`{m.real_name}`")
                c5.write(p.name)
                if c6.button("✏️", key=f"edit_m_{m.id}"):
                    st.session_state.edit_mapping_id = m.id
                    st.rerun()
                if c7.button("🗑️", key=f"del_m_{m.id}"):
                    if delete_item(ModelMapping, m.id, "模型映射已删除"):
                        st.rerun()

            edit_id = st.session_state.edit_mapping_id
            if edit_id is not None:
                with st.expander("✏️ 编辑映射", expanded=True):
                    with SessionLocal() as session:
                        edit_m = session.query(ModelMapping).filter(ModelMapping.id == edit_id).first()
                        if edit_m:
                            edit_providers = session.query(Provider).all()
                            edit_p_map = {p.name: p for p in edit_providers}
                            edit_provider_obj = session.query(Provider).filter(Provider.id == edit_m.provider_id).first()
                            current_p_name = edit_provider_obj.name if edit_provider_obj else list(edit_p_map.keys())[0]

                            edit_p_name = st.selectbox("供应商", options=list(edit_p_map.keys()),
                                                        index=list(edit_p_map.keys()).index(current_p_name),
                                                        key="edit_map_provider")
                            edit_p = edit_p_map[edit_p_name]

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
