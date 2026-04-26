import streamlit as st
from db import SessionLocal, Provider, APIKey
from utils import fetch_models, classify_model_type, log_custom_usage
import datetime
import requests
import json
import re


def render_playground_page():
    st.markdown("""
    <style>
    .main-chat-container {
        height: calc(100vh - 200px);
        display: flex;
        flex-direction: column;
    }
    .chat-messages-container {
        flex: 1;
        overflow-y: auto;
        padding-right: 10px;
    }
    .user-message-container {
        display: flex;
        justify-content: flex-end;
        margin-bottom: 16px;
    }
    .user-message {
        max-width: 70%;
        background-color: #007AFF;
        color: white;
        padding: 12px 16px;
        border-radius: 18px;
        border-bottom-right-radius: 4px;
        word-wrap: break-word;
    }
    .assistant-message-container {
        display: flex;
        justify-content: flex-start;
        margin-bottom: 16px;
    }
    .assistant-message {
        max-width: 100%;
        background-color: #F0F0F0;
        padding: 12px 16px;
        border-radius: 18px;
        border-bottom-left-radius: 4px;
        word-wrap: break-word;
    }
    .code-block-container {
        position: relative;
        background-color: #1E1E1E;
        border-radius: 8px;
        margin: 8px 0;
        overflow: hidden;
    }
    .code-block-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background-color: #2D2D2D;
        padding: 4px 12px;
        font-size: 12px;
        color: #858585;
    }
    .code-block-content {
        padding: 12px;
        overflow-x: auto;
        font-family: 'Courier New', monospace;
        font-size: 14px;
        line-height: 1.5;
        color: #D4D4D4;
    }
    .copy-button {
        background-color: transparent;
        border: 1px solid #404040;
        color: #858585;
        padding: 2px 8px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
    }
    .copy-button:hover {
        background-color: #404040;
        color: #D4D4D4;
    }
    .thinking-block {
        background-color: #FFF9E6;
        border-left: 3px solid #FFD700;
        border-radius: 4px;
        margin: 8px 0;
        overflow: hidden;
    }
    .thinking-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 12px;
        background-color: #FFF3CC;
        cursor: pointer;
        font-weight: 500;
    }
    .thinking-content {
        padding: 12px;
        display: block;
    }
    .thinking-content.collapsed {
        display: none;
    }
    .sidebar-history-item {
        padding: 10px;
        border-radius: 8px;
        margin-bottom: 8px;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .sidebar-history-item:hover {
        background-color: #F0F0F0;
    }
    .sidebar-history-item.active {
        background-color: #E0E0E0;
    }
    .delete-button {
        background-color: transparent;
        border: none;
        color: #FF4500;
        cursor: pointer;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 16px;
    }
    .delete-button:hover {
        background-color: #FFE4E1;
    }
    .model-selector {
        display: flex;
        gap: 10px;
        align-items: center;
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

    with SessionLocal() as session:
        providers = session.query(Provider).all()
        active_keys = session.query(APIKey).filter(APIKey.is_active.is_(True)).all()

    if "chat_sessions" not in st.session_state:
        st.session_state.chat_sessions = {}
    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = None
    if "sidebar_collapsed" not in st.session_state:
        st.session_state.sidebar_collapsed = False

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
            st.warning("暂无配置了有效 API Key 的供应商。")
        else:
            provider_names = list(provider_to_key.keys())

            with st.sidebar:
                st.markdown("## 📁 对话历史")
                
                col1, col2 = st.columns([3, 1])
                with col1:
                    if st.button("➕ 新建对话", use_container_width=True):
                        new_session_id = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        st.session_state.chat_sessions[new_session_id] = {
                            "messages": [],
                            "provider": provider_names[0] if provider_names else None,
                            "model": None,
                            "title": "新对话",
                            "created_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        }
                        st.session_state.current_session_id = new_session_id
                        st.rerun()

                st.markdown("---")
                
                if st.session_state.chat_sessions:
                    for session_id in reversed(st.session_state.chat_sessions.keys()):
                        session_data = st.session_state.chat_sessions[session_id]
                        is_active = st.session_state.current_session_id == session_id
                        
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            if st.button(
                                f"💬 {session_data['title']}",
                                key=f"load_{session_id}",
                                use_container_width=True,
                                type="primary" if is_active else "secondary"
                            ):
                                st.session_state.current_session_id = session_id
                                st.rerun()
                        with col2:
                            if st.button("🗑️", key=f"delete_{session_id}"):
                                del st.session_state.chat_sessions[session_id]
                                if st.session_state.current_session_id == session_id:
                                    if st.session_state.chat_sessions:
                                        st.session_state.current_session_id = next(iter(st.session_state.chat_sessions.keys()))
                                    else:
                                        st.session_state.current_session_id = None
                                st.rerun()
                else:
                    st.info("暂无对话历史，开始一个新对话吧！")

            if not st.session_state.current_session_id or st.session_state.current_session_id not in st.session_state.chat_sessions:
                if st.session_state.chat_sessions:
                    st.session_state.current_session_id = next(iter(st.session_state.chat_sessions.keys()))
                else:
                    new_session_id = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    st.session_state.chat_sessions[new_session_id] = {
                        "messages": [],
                        "provider": provider_names[0] if provider_names else None,
                        "model": None,
                        "title": "新对话",
                        "created_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                    st.session_state.current_session_id = new_session_id

            current_session = st.session_state.chat_sessions[st.session_state.current_session_id]
            
            st.markdown(f"### {current_session['title']}")
            
            provider_col, model_col = st.columns([1, 1])
            with provider_col:
                selected_provider_name = st.selectbox(
                    "选择供应商",
                    options=provider_names,
                    key="selected_provider",
                    index=provider_names.index(current_session["provider"]) if current_session["provider"] in provider_names else 0
                )
                if selected_provider_name != current_session["provider"]:
                    current_session["provider"] = selected_provider_name
                    current_session["model"] = None

            selected_provider, selected_key = provider_to_key[selected_provider_name]
            
            cache_key = f"provider_models_{selected_provider.id}"
            if cache_key not in st.session_state:
                st.session_state[cache_key] = fetch_models(selected_provider.api_base, selected_key.key)
            
            provider_models = st.session_state[cache_key]
            text_models = [m for m in provider_models if classify_model_type(m) == "text"]
            
            with model_col:
                if text_models:
                    selected_model = st.selectbox(
                        "选择模型",
                        options=text_models,
                        key="selected_model",
                        index=text_models.index(current_session["model"]) if current_session["model"] in text_models else 0
                    )
                    current_session["model"] = selected_model
                else:
                    st.warning("未获取到文本模型列表。")
                    selected_model = None

            st.markdown("---")
            
            chat_container = st.container()
            with chat_container:
                if not current_session["messages"]:
                    st.markdown("""
                    <div style="text-align: center; margin-top: 100px; color: #888;">
                        <h2>👋 你好！</h2>
                        <p>选择一个模型，开始与 AI 对话吧。</p>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    for msg in current_session["messages"]:
                        if msg["role"] == "user":
                            st.markdown(f"""
                            <div class="user-message-container">
                                <div class="user-message">{msg['content']}</div>
                            </div>
                            """, unsafe_allow_html=True)
                        elif msg["role"] == "assistant":
                            content = msg['content']
                            thinking_content = None
                            thinking_pattern = r'<thinking>([\s\S]*?)</thinking>'
                            thinking_match = re.search(thinking_pattern, content)
                            if thinking_match:
                                thinking_content = thinking_match.group(1).strip()
                                content = content[thinking_match.end():].strip()
                            
                            final_html = ""
                            if thinking_content:
                                thinking_id = f"thinking_{id(thinking_content)}"
                                final_html += f"""
                                <div class="thinking-block">
                                    <div class="thinking-header" onclick="
                                        var content = document.getElementById('{thinking_id}');
                                        if (content.classList.contains('collapsed')) {{
                                            content.classList.remove('collapsed');
                                        }} else {{
                                            content.classList.add('collapsed');
                                        }}
                                    ">
                                        <span>🤔 思考过程</span>
                                        <span>▼</span>
                                    </div>
                                    <div id="{thinking_id}" class="thinking-content collapsed">
                                        <pre style="white-space: pre-wrap; margin: 0; font-family: inherit;">{thinking_content}</pre>
                                    </div>
                                </div>
                                """
                            
                            parts = re.split(r'(```[\s\S]*?```)', content)
                            for part in parts:
                                if part.startswith('```') and part.endswith('```'):
                                    code_content = part[3:-3].strip()
                                    first_line_end = code_content.find('\n')
                                    if first_line_end > 0:
                                        language = code_content[:first_line_end].strip()
                                        actual_code = code_content[first_line_end+1:]
                                    else:
                                        language = "code"
                                        actual_code = code_content
                                    
                                    code_id = f"code_{id(actual_code)}"
                                    final_html += f"""
                                    <div class="code-block-container">
                                        <div class="code-block-header">
                                            <span>{language}</span>
                                            <button class="copy-button" onclick="
                                                var codeElement = document.getElementById('{code_id}');
                                                var range = document.createRange();
                                                range.selectNode(codeElement);
                                                window.getSelection().removeAllRanges();
                                                window.getSelection().addRange(range);
                                                document.execCommand('copy');
                                                window.getSelection().removeAllRanges();
                                                this.textContent = '已复制!';
                                                setTimeout(() => {{ this.textContent = '复制'; }}, 2000);
                                            ">复制</button>
                                        </div>
                                        <div class="code-block-content">
                                            <pre id="{code_id}" style="margin: 0; white-space: pre-wrap;">{actual_code}</pre>
                                        </div>
                                    </div>
                                    """
                                else:
                                    if part.strip():
                                        final_html += f"<p style='margin: 8px 0; white-space: pre-wrap;'>{part}</p>"
                            
                            st.markdown(f"""
                            <div class="assistant-message-container">
                                <div class="assistant-message">{final_html}</div>
                            </div>
                            """, unsafe_allow_html=True)

            st.markdown("---")
            
            user_input = st.chat_input("输入消息并回车发送...", key="main_chat_input")
            
            if user_input:
                if not selected_model:
                    st.error("请先选择一个模型。")
                else:
                    if not current_session["messages"]:
                        current_session["title"] = user_input[:30] + ("..." if len(user_input) > 30 else "")
                    
                    current_session["messages"].append({"role": "user", "content": user_input})
                    
                    url = f"{selected_provider.api_base.rstrip('/')}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {selected_key.key}",
                        "Content-Type": "application/json",
                    }
                    
                    _send_messages = []
                    _send_messages.extend(current_session["messages"])
                    
                    payload = {
                        "model": selected_model,
                        "messages": _send_messages,
                        "max_tokens": 4096,
                        "temperature": 0.7,
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
                            
                            current_session["messages"].append({"role": "assistant", "content": final_text})
                            
                            ptk = (usage_data.get("prompt_tokens") or 0)
                            ctk = (usage_data.get("completion_tokens") or 0)
                            ttk = (usage_data.get("total_tokens") or (ptk + ctk))
                            log_custom_usage(
                                key_id=selected_key.id,
                                model_name=selected_model,
                                prompt_tokens=ptk,
                                completion_tokens=ctk,
                                total_tokens=ttk,
                            )
                            
                            st.rerun()
                        else:
                            st.error(f"请求失败：HTTP {resp.status_code}，{resp.text[:300]}")
                    except Exception as e:
                        st.error(f"请求异常：{e}")
