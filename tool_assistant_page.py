import streamlit as st
from db import SessionLocal, ModelMapping, ToolDefaultModel
from utils import detect_proxy_base_url
import json
import os

from config_assistant.env_detector import is_running_in_docker
from config_assistant.config_detector import ConfigDetector
from config_assistant.backup_manager import BackupManager
from config_assistant.injectors.claude_code import ClaudeCodeInjector
from config_assistant.injectors.opencode import OpenCodeInjector
from config_assistant.injectors.openclaw import OpenClawInjector
from config_assistant.injectors.hermes import HermesInjector
from config_assistant.ai_analyzer import AIAnalyzer


def render_tool_assistant_page():
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

    tool_display_names = {
        "claude_code": "Claude Code",
        "opencode": "OpenCode",
        "openclaw": "OpenClaw",
        "hermes": "Hermes",
    }

    running_in_docker = is_running_in_docker()

    tab_smart, tab_manual = st.tabs(["🤖 智能配置", "📋 手动配置"])

    with tab_smart:
        st.subheader("🎯 默认模型配置")
        st.caption("为不同工具选择默认模型，例如 ClaudeCode/OpenCode 适合编程模型，OpenClaw/Hermes 适合对话模型。")

        tool_model_config = {}
        tool_cols = st.columns(4)
        for i, (tool_id, display_name) in enumerate(tool_display_names.items()):
            saved_default = tool_default_models.get(tool_id, model_hint)
            if saved_default not in model_list:
                saved_default = model_hint
            selected = tool_cols[i].selectbox(
                display_name,
                options=model_list,
                index=model_list.index(saved_default) if saved_default in model_list else 0,
                key=f"tool_default_model_smart_{tool_id}",
            )
            tool_model_config[tool_id] = selected

        if st.button("💾 保存默认模型配置", key="save_tool_default_models_smart", width="stretch"):
            with SessionLocal() as session:
                for tool_id, selected_model in tool_model_config.items():
                    existing = session.query(ToolDefaultModel).filter(ToolDefaultModel.tool_id == tool_id).first()
                    if existing:
                        existing.default_model = selected_model
                    else:
                        session.add(ToolDefaultModel(tool_id=tool_id, default_model=selected_model))
                session.commit()
            st.success("工具默认模型已保存")

        st.divider()

        if running_in_docker:
            st.warning("""
            ⚠️ **Docker环境检测到**
            
            自动配置功能需要直接访问宿主机文件系统，当前运行在Docker容器中，
            **无法自动修改您的本地AI工具配置文件**。
            
            请使用以下方式之一：
            1. 下载配置文件，手动复制到工具配置目录
            2. 在本机直接运行项目（python + streamlit方式）
            3. 使用「手动配置」Tab
            """)
        else:
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
                if st.button("🔍 一键智能扫描配置文件", type="primary", width="stretch", key="scan_smart"):
                    with st.spinner("AI正在扫描系统中的配置文件..."):
                        st.session_state.scan_result = config_detector.detect_all()
            with c_restore:
                if st.button("↩️ 一键恢复备份", width="stretch", key="restore_smart"):
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
                            if col2.button("恢复", key=f"restore_{b['filename']}_smart"):
                                st.info("请手动选择要恢复到的配置文件路径")
                    st.button("关闭恢复面板", on_click=lambda: st.session_state.__setitem__("show_restore", False), key="close_restore_smart")
            
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
                            key=f"check_{tool_id}_smart"
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
                            key=f"check_create_{tool_id}_smart"
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
                    if col_exec.button("✅ 确认执行配置", width="stretch", key="exec_config_smart"):
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
                    
                    if col_preview.button("👁️ 查看配置变更预览", width="stretch", key="preview_smart"):
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

                                    if hasattr(injector, "original_text") and hasattr(injector, "modified_text"):
                                        original_text = (getattr(injector, "original_text", "") or "").strip()
                                        modified_text = (getattr(injector, "modified_text", "") or "").strip()
                                        col_orig.code(original_text or "(空)", language="yaml")
                                        col_mod.code(modified_text or "(空)", language="yaml")
                                    else:
                                        col_orig.json(injector.original_config)
                                        col_mod.json(injector.modified_config)
                                    st.markdown("---")
                            if st.button("关闭预览", key="close_preview_smart"):
                                st.session_state.show_preview = False
                else:
                    st.info("请选择至少一个工具进行配置")

    with tab_manual:
        st.info("高级用户可以使用手动配置方式，生成配置示例和启动脚本。")
        
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
        
        hermes_default_model = tool_default_models.get("hermes", model_hint)
        if hermes_default_model not in model_list:
            hermes_default_model = model_hint
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

        t1, t2, t3, t4 = st.tabs(["Claude Code", "OpenCode", "Cursor / Trae", "Other Tools"])
    
        with t1:
            st.subheader("Claude Code 配置与启动")
            selected_claude_model = st.selectbox(
                "选择 Claude Code 默认模型",
                options=model_list,
                index=model_list.index(tool_default_models.get("claude_code", model_hint))
                if tool_default_models.get("claude_code", model_hint) in model_list
                else 0,
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
            st.markdown(f"""
            Claude Code 需要通过设置 Anthropic 的环境变量来指向本地代理。
            这里的 Key 是**本地代理服务的访问 Key**，不是上游平台的 Key。
            上游平台 Key 请在「API Key 管理」中配置。
            
            **1. 一键启动脚本**:
            点击下方按钮生成脚本，它将设置环境变量并直接启动 Claude。
            """)
            
            if st.button("生成一键启动脚本 (claude_start.ps1)", key="claude_script_manual"):
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

            if st.button("生成 Claude 配置示例 (claude_settings.json)", key="claude_config_manual"):
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
                index=model_list.index(tool_default_models.get("opencode", model_hint))
                if tool_default_models.get("opencode", model_hint) in model_list
                else 0,
                key="opencode_default_model_manual",
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

            if st.button("生成 OpenCode 配置文件 (opencode.json)", key="opencode_config_manual"):
                with open("opencode.json", "w", encoding="utf-8") as f:
                    f.write(opencode_config_content)
                st.success("配置文件已生成：`opencode.json`")

            st.code(opencode_config_content, language="json")

            st.markdown("""
            **2. 生成一键启动脚本**:
            脚本会设置本地代理访问 Key、指定配置文件，并直接启动 OpenCode。
            """)

            if st.button("生成一键启动脚本 (opencode_start.ps1)", key="opencode_script_manual"):
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
