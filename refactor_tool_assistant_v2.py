#!/usr/bin/env python3
"""
重构工具配置助手页面，将智能配置和手动配置改成Tab切换

重构目标：
1. 创建两个主要Tab：智能配置和手动配置
2. 默认模型配置移到智能配置Tab里
3. 手动配置Tab也有独立的默认模型配置

代码结构：
- 第1041-1070行：页面头部、基础配置读取（保留在Tab之外）
- 第1072-1104行：默认模型配置（移到智能配置Tab）
- 第1106-1132行：配置内容计算（移到智能配置Tab）
- 第1134-1148行：Docker环境检测（移到智能配置Tab）
- 第1150-1417行：智能配置内容（移到智能配置Tab）
- 第1419-1632行：手动配置内容（移到手动配置Tab）
"""

def refactor_tool_assistant():
    file_path = '/Users/andy/Desktop/AIProxy/admin_panel.py'
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print("分析代码结构...")
    print("-" * 60)
    
    # 找到关键行号（0-based）
    start_line = None  # elif menu == "工具配置助手"
    model_hint_line = None  # model_hint = "GLM5"...
    default_model_start = None  # st.subheader("🎯 默认模型配置")
    config_calc_start = None  # opencode_models_dict = {...}
    docker_check_start = None  # running_in_docker = is_running_in_docker()
    smart_config_start = None  # if not running_in_docker:
    manual_tab_start = None  # t1, t2, t3, t4 = st.tabs(...)
    next_menu_start = None  # elif menu == "模型体验":
    
    for i, line in enumerate(lines):
        if 'elif menu == "工具配置助手":' in line:
            start_line = i
            print(f"工具配置助手开始: 行 {i+1}")
        elif start_line is not None and 'model_hint = "GLM5" if "GLM5" in model_list' in line:
            model_hint_line = i
            print(f"model_hint定义: 行 {i+1}")
        elif start_line is not None and 'st.subheader("🎯 默认模型配置")' in line:
            default_model_start = i
            print(f"默认模型配置开始: 行 {i+1}")
        elif start_line is not None and 'opencode_models_dict = {' in line:
            config_calc_start = i
            print(f"配置内容计算开始: 行 {i+1}")
        elif start_line is not None and 'running_in_docker = is_running_in_docker()' in line:
            docker_check_start = i
            print(f"Docker检查开始: 行 {i+1}")
        elif start_line is not None and 'if not running_in_docker:' in line and i > 1100:
            smart_config_start = i
            print(f"智能配置开始: 行 {i+1}")
        elif start_line is not None and 't1, t2, t3, t4 = st.tabs(' in line:
            manual_tab_start = i
            print(f"手动配置Tab开始: 行 {i+1}")
        elif start_line is not None and 'elif menu == "模型体验":' in line:
            next_menu_start = i
            print(f"下一个菜单开始: 行 {i+1}")
            break
    
    print("-" * 60)
    
    if None in [start_line, model_hint_line, default_model_start, config_calc_start, 
                docker_check_start, smart_config_start, manual_tab_start, next_menu_start]:
        print("错误：无法找到所有关键位置")
        return
    
    # 现在我理解了代码结构，让我制定一个精确的重构计划
    # 由于代码结构复杂，我将采用另一种方法：生成新的代码结构
    
    print("\n代码结构确认：")
    print(f"  页面头部: 行 {start_line+1} - {model_hint_line+1}")
    print(f"  默认模型配置: 行 {default_model_start+1} - {config_calc_start}")
    print(f"  配置内容计算: 行 {config_calc_start+1} - {docker_check_start}")
    print(f"  Docker检查: 行 {docker_check_start+1} - {smart_config_start}")
    print(f"  智能配置: 行 {smart_config_start+1} - {manual_tab_start}")
    print(f"  手动配置: 行 {manual_tab_start+1} - {next_menu_start}")
    
    # 由于代码结构复杂，我将使用更简单的方法：
    # 1. 先备份原始文件
    # 2. 然后使用Edit工具进行精确的分段修改
    
    print("\n由于代码结构复杂，建议使用以下步骤进行重构：")
    print("1. 在model_hint定义之后插入Tab创建代码")
    print("2. 将默认模型配置、配置内容计算、Docker检测、智能配置内容移到智能配置Tab内")
    print("3. 在手动配置Tab内添加独立的默认模型配置和配置内容计算")
    print("4. 将手动配置内容移到手动配置Tab内")
    
    # 实际上，让我先查看关键代码段，然后生成精确的修改指令
    print("\n关键代码段：")
    print("-" * 60)
    
    # 查看model_hint定义之后的代码
    print(f"\n行 {model_hint_line+1} - {model_hint_line+5}:")
    for i in range(model_hint_line, min(model_hint_line+5, len(lines))):
        print(f"  {i+1}: {repr(lines[i])}")
    
    # 查看默认模型配置的开始
    print(f"\n行 {default_model_start+1} - {default_model_start+5}:")
    for i in range(default_model_start, min(default_model_start+5, len(lines))):
        print(f"  {i+1}: {repr(lines[i])}")
    
    # 查看手动配置Tab的开始
    print(f"\n行 {manual_tab_start+1} - {manual_tab_start+5}:")
    for i in range(manual_tab_start, min(manual_tab_start+5, len(lines))):
        print(f"  {i+1}: {repr(lines[i])}")

if __name__ == '__main__':
    refactor_tool_assistant()
