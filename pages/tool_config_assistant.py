import streamlit as st
import os
import json
from db import SessionLocal, ModelMapping
from config_assistant import (
    is_docker_environment, 
    get_environment_info,
    ConfigDetector, 
    BackupManager, 
    ModelSyncer,
    AIConfigAnalyzer,
    get_injector,
)


def render_tool_config_assistant():
    st.header("🛠️ AI 工具配置助手")
    
    base_host = "http://localhost:8000"
    proxy_url_v1 = f"{base_host}/v1"
    master_key = os.getenv("MASTER_KEY", "sk-admin-123456")
    
    with SessionLocal() as session:
        mappings = session.query(ModelMapping).all()
        v_models = [m.virtual_name for m in mappings]
    
    model_list = sorted(set(v_models)) if v_models else ["GLM5"]
    model_hint = "GLM5" if "GLM5" in model_list else model_list[0]
    
    env_info = get_environment_info()
    is_docker = is_docker_environment()
    
    if is_docker:
        render_docker_mode(base_host, proxy_url_v1, master_key, model_list, model_hint)
    else:
        render_local_mode(base_host, proxy_url_v1, master_key, model_list, model_hint, v_models)


def render_docker_mode(base_host, proxy_url_v1, master_key, model_list, model_hint):
    st.warning("""
    ⚠️ **Docker 环境检测到**
    
    自动配置功能需要直接访问宿主机文件系统，当前运行在 Docker 容器中，
    **无法自动修改您的本地 AI 工具配置文件**。
    
    请使用以下方式之一：
    1. 下载配置文件，手动复制到工具配置目录
    2. 在本机直接运行项目（python + streamlit 方式）
    3. 使用下方的手动配置说明
    """)
    
    st.divider()
    st.subheader("📋 手动配置指南")
    
    manual_tab1, manual_tab2, manual_tab3, manual_tab4 = st.tabs(["Claude Code", "OpenCode", "Cursor / Trae", "其他工具"])
    
    with manual_tab1:
        st.markdown("""
        ### Claude Code 配置
        
        Claude Code 需要通过设置环境变量来指向本地代理。
        
        **配置文件位置**: `~/.claude/settings.json`
        
        **配置内容示例**:
        """)
        claude_config = {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
            "env": {
                "ANTHROPIC_BASE_URL": base_host,
                "ANTHROPIC_API_KEY": master_key,
            },
            "model": model_hint,
        }
        st.code(json.dumps(claude_config, ensure_ascii=False, indent=2), language="json")
        
        st.markdown("**启动命令**:")
        st.code(f"$env:ANTHROPIC_BASE_URL='{base_host}'; $env:ANTHROPIC_API_KEY='{master_key}'; claude --model {model_hint}", language="powershell")
    
    with manual_tab2:
        st.markdown("""
        ### OpenCode 配置
        
        **配置文件位置**: `~/.opencode/config.json`
        
        **配置内容示例**:
        """)
        opencode_config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "aiproxy": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "AIProxy",
                    "options": {
                        "baseURL": proxy_url_v1,
                        "apiKey": master_key,
                    },
                    "models": {m: {"name": m} for m in model_list},
                }
            },
        }
        st.code(json.dumps(opencode_config, ensure_ascii=False, indent=2), language="json")
    
    with manual_tab3:
        st.markdown(f"""
        ### Cursor / Trae 配置
        
        **配置参数**:
        - Base URL: `{proxy_url_v1}`
        - API Key: `{master_key}`
        - 模型: `{model_hint}`
        
        **可用模型列表**: {", ".join(model_list)}
        """)
    
    with manual_tab4:
        st.markdown(f"""
        ### 通用配置参数
        
        - **API Base**: `{proxy_url_v1}`
        - **API Key**: `{master_key}`
        - **支持模型**: {", ".join(model_list)}
        """)


def render_local_mode(base_host, proxy_url_v1, master_key, model_list, model_hint, v_models):
    st.info("帮助您将本地代理一键配置到常用的 AI 编程工具中。")
    
    st.divider()
    
    smart_col, manual_col = st.columns([1, 1])
    
    with smart_col:
        render_smart_config(base_host, master_key, model_list, model_hint)
    
    with manual_col:
        render_manual_config(base_host, proxy_url_v1, master_key, model_list, model_hint, v_models)
    
    st.divider()
    render_backup_section()


def render_smart_config(base_host, master_key, model_list, model_hint):
    st.subheader("🤖 智能配置")
    st.markdown("自动扫描并配置已安装的 AI 工具")
    
    if "scan_results" not in st.session_state:
        st.session_state.scan_results = None
    
    if st.button("🔍 扫描系统配置文件", key="scan_config_btn", use_container_width=True):
        with st.spinner("正在扫描系统中的配置文件..."):
            detector = ConfigDetector()
            st.session_state.scan_results = detector.detect_all_tools()
            st.session_state.selected_tools = {}
    
    if st.session_state.scan_results:
        st.markdown("#### 检测结果")
        
        for tool_name, config_info in st.session_state.scan_results.items():
            if config_info.exists:
                st.success(f"✅ 找到 {tool_name} 配置：`{config_info.config_path}`")
            else:
                st.info(f"❌ 未检测到 {tool_name} 配置")
        
        st.markdown("#### 选择要配置的工具")
        
        tools_to_config = {}
        for tool_name, config_info in st.session_state.scan_results.items():
            if config_info.exists:
                default_model = model_hint
                if tool_name == "ClaudeCode":
                    desc = f"将设置默认模型为 {default_model}"
                elif tool_name == "OpenCode":
                    desc = f"将注入 {len(model_list)} 个代理模型"
                else:
                    desc = f"将添加代理配置"
                
                checked = st.checkbox(
                    f"**{tool_name}** - {desc}",
                    key=f"check_{tool_name}",
                    value=True
                )
                if checked:
                    tools_to_config[tool_name] = config_info
        
        if tools_to_config:
            col_preview, col_execute = st.columns(2)
            
            with col_preview:
                if st.button("👁️ 查看配置变更预览", key="preview_btn", use_container_width=True):
                    st.session_state.show_preview = True
            
            with col_execute:
                if st.button("✅ 确认执行配置", key="execute_btn", use_container_width=True, type="primary"):
                    execute_config(tools_to_config, base_host, master_key, model_list, model_hint)
            
            if st.session_state.get("show_preview"):
                render_preview(tools_to_config, base_host, master_key, model_list, model_hint)


def execute_config(tools_to_config, base_host, master_key, model_list, model_hint):
    backup_mgr = BackupManager()
    success_count = 0
    
    for tool_name, config_info in tools_to_config.items():
        try:
            backup_success, backup_msg = backup_mgr.create_backup(config_info.config_path)
            if not backup_success:
                st.error(f"{tool_name} 备份失败: {backup_msg}")
                continue
            
            injector = get_injector(tool_name, base_host, master_key)
            current_content = config_info.current_content or "{}"
            success, msg, new_content = injector.inject_config(
                current_content, model_list, model_hint
            )
            
            if success:
                with open(config_info.config_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                st.success(f"✅ {tool_name}: {msg}")
                success_count += 1
            else:
                st.error(f"❌ {tool_name}: {msg}")
                
        except Exception as e:
            st.error(f"❌ {tool_name} 配置失败: {str(e)}")
    
    if success_count > 0:
        st.toast(f"成功配置 {success_count} 个工具！", icon="🎉")
        st.session_state.scan_results = None


def render_preview(tools_to_config, base_host, master_key, model_list, model_hint):
    st.markdown("#### 配置变更预览")
    
    for tool_name, config_info in tools_to_config.items():
        with st.expander(f"📋 {tool_name} 配置预览", expanded=False):
            injector = get_injector(tool_name, base_host, master_key)
            current_content = config_info.current_content or "{}"
            
            preview = injector.inject_config(current_content, model_list, model_hint)
            if preview[0]:
                st.markdown("**新配置内容**:")
                st.code(preview[2], language="json")
            else:
                st.error(f"预览生成失败: {preview[1]}")


def render_manual_config(base_host, proxy_url_v1, master_key, model_list, model_hint, v_models):
    st.subheader("📝 手动配置")
    st.markdown("生成配置文件和启动脚本")
    
    manual_tab1, manual_tab2, manual_tab3, manual_tab4 = st.tabs(["Claude Code", "OpenCode", "Cursor / Trae", "其他工具"])
    
    with manual_tab1:
        st.markdown("### Claude Code 配置与启动")
        selected_claude_model = st.selectbox(
            "选择默认模型",
            options=model_list,
            index=model_list.index(model_hint) if model_hint in model_list else 0,
            key="claude_default_model_manual",
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
        
        st.markdown("**1. 一键启动脚本**:")
        if st.button("生成启动脚本 (claude_start.ps1)", key="gen_claude_script"):
            script_content = f"$env:ANTHROPIC_BASE_URL='{base_host}'; $env:ANTHROPIC_API_KEY='{master_key}'; claude --model {selected_claude_model}"
            with open("claude_start.ps1", "w", encoding="utf-8") as f:
                f.write(script_content)
            st.success("脚本已生成！")
        
        st.markdown("**2. 手动运行命令**:")
        st.code(f"$env:ANTHROPIC_BASE_URL='{base_host}'; $env:ANTHROPIC_API_KEY='{master_key}'; claude --model {selected_claude_model}", language="powershell")
        
        st.markdown("**3. 配置文件示例**:")
        if st.button("生成配置示例 (claude_settings.json)", key="gen_claude_config"):
            with open("claude_settings.json", "w", encoding="utf-8") as f:
                f.write(claude_settings_content)
            st.success("配置示例已生成！")
        st.code(claude_settings_content, language="json")
        
        st.warning("注意：Claude Code 的 Base URL 请使用 `http://localhost:8000` (不要带 /v1)。")
    
    with manual_tab2:
        st.markdown("### OpenCode 配置与启动")
        selected_opencode_model = st.selectbox(
            "选择启动模型",
            options=model_list,
            index=model_list.index(model_hint) if model_hint in model_list else 0,
            key="opencode_default_model_manual",
        )
        
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
        
        st.markdown("**1. 生成配置文件**:")
        if st.button("生成配置文件 (opencode.json)", key="gen_opencode_config"):
            with open("opencode.json", "w", encoding="utf-8") as f:
                f.write(opencode_config_content)
            st.success("配置文件已生成！")
        st.code(opencode_config_content, language="json")
        
        st.markdown("**2. 启动命令**:")
        st.code(f"$env:OPENCODE_CONFIG='opencode.json'; opencode -m aiproxy/{selected_opencode_model}", language="powershell")
        
        st.caption("说明：OpenCode 应使用 OpenAI 兼容入口，Base URL 需要带 `/v1`。")
    
    with manual_tab3:
        st.markdown("### Cursor / Trae 配置指南")
        st.markdown(f"""
        **配置参数**:
        - Provider 类型: `OpenAI Compatible` / `Custom OpenAI`
        - Base URL: `{proxy_url_v1}`
        - API Key: `{master_key}`
        - 示例模型: `{model_hint}`
        
        **可用模型列表**: {", ".join(model_list) if model_list else "尚未配置"}
        
        **注意事项**:
        - Base URL 必须带 `/v1`
        - API Key 是本地代理访问 Key，不是上游平台 Key
        - 模型名使用虚拟模型名，不是真实模型名
        """)
    
    with manual_tab4:
        st.markdown("### 其他工具通用配置")
        st.markdown(f"""
        大部分兼容 OpenAI 协议的工具都适用以下参数：
        
        - **API Base**: `{proxy_url_v1}`
        - **API Key**: `{master_key}`
        - **支持模型**: {", ".join(model_list) if model_list else "尚未配置"}
        """)


def render_backup_section():
    with st.expander("🔄 备份与恢复", expanded=False):
        st.markdown("### 配置备份管理")
        
        backup_mgr = BackupManager()
        backups = backup_mgr.list_backups()
        
        if backups:
            st.markdown(f"**共有 {len(backups)} 个备份**")
            
            for backup in backups[:10]:
                col1, col2, col3 = st.columns([3, 2, 1])
                col1.text(backup["original_path"])
                col2.text(backup["backup_time"][:19] if backup["backup_time"] else "未知时间")
                if col3.button("恢复", key=f"restore_{backup['backup_path']}"):
                    success, msg = backup_mgr.restore_backup(backup["backup_path"])
                    if success:
                        st.success(msg)
                    else:
                        st.error(msg)
        else:
            st.info("暂无备份记录")
        
        if st.button("清理旧备份（保留最近10个）", key="cleanup_backups"):
            count, msg = backup_mgr.cleanup_old_backups(10)
            st.info(msg)
