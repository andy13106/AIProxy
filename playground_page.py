import streamlit as st
from db import SessionLocal, Provider, APIKey
from utils import fetch_models, classify_model_type, log_custom_usage
import datetime
import requests
import json
import base64


def render_playground_page():
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

                        chat_max_tokens = st.slider("max_tokens", min_value=32, max_value=65536, value=4096, step=32)
                        chat_temperature = st.slider("temperature", min_value=0.0, max_value=1.5, value=0.7, step=0.1)

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
                            _send_messages = []
                            if system_prompt.strip():
                                _send_messages.append({"role": "system", "content": system_prompt.strip()})
                            _send_messages.extend(st.session_state[chat_state_key])

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
                                                if chunk.get("usage"):
                                                    usage_data = chunk["usage"]
                                            except (json.JSONDecodeError, Exception):
                                                continue
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
                                            if _img_data_for_history:
                                                st.session_state[_img_history_key].append({
                                                    "prompt": image_prompt,
                                                    "model": image_model,
                                                    "size": image_size,
                                                    "b64": _img_data_for_history,
                                                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                })
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
