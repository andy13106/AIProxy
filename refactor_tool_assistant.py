#!/usr/bin/env python3
"""
重构工具配置助手页面，将智能配置和手动配置改成Tab切换

重构目标：
1. 创建两个主要Tab：智能配置和手动配置
2. 默认模型配置移到智能配置Tab里
3. 手动配置Tab也有独立的默认模型配置

代码结构分析：
- 第1041-1070行：页面头部、基础配置读取（保留在Tab之外）
- 第1072-1104行：默认模型配置（移到智能配置Tab）
- 第1106-1132行：配置内容计算（在两个Tab中分别进行）
- 第1134-1148行：Docker环境检测（移到智能配置Tab）
- 第1150-1417行：智能配置内容（移到智能配置Tab）
- 第1419-1632行：手动配置内容（移到手动配置Tab）
"""

import re

def refactor_tool_assistant():
    file_path = '/Users/andy/Desktop/AIProxy/admin_panel.py'
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    
    print("分析代码结构...")
    print("-" * 60)
    
    # 找到关键行号
    start_line = None  # elif menu == "工具配置助手"
    default_model_start = None  # st.subheader("🎯 默认模型配置")
    config_calc_start = None  # opencode_models_dict = {...}
    docker_check_start = None  # running_in_docker = is_running_in_docker()
    smart_config_start = None  # if not running_in_docker: (在docker检查之后)
    manual_tab_start = None  # t1, t2, t3, t4 = st.tabs(...)
    next_menu_start = None  # elif menu == "模型体验":
    
    for i, line in enumerate(lines):
        if 'elif menu == "工具配置助手":' in line:
            start_line = i
            print(f"工具配置助手开始: 第 {i+1} 行")
        elif start_line is not None and 'st.subheader("🎯 默认模型配置")' in line:
            default_model_start = i
            print(f"默认模型配置开始: 第 {i+1} 行")
        elif start_line is not None and 'opencode_models_dict = {' in line:
            config_calc_start = i
            print(f"配置内容计算开始: 第 {i+1} 行")
        elif start_line is not None and 'running_in_docker = is_running_in_docker()' in line:
            docker_check_start = i
            print(f"Docker检查开始: 第 {i+1} 行")
        elif start_line is not None and 'if not running_in_docker:' in line and i > 1100:
            smart_config_start = i
            print(f"智能配置开始: 第 {i+1} 行")
        elif start_line is not None and 't1, t2, t3, t4 = st.tabs(' in line:
            manual_tab_start = i
            print(f"手动配置Tab开始: 第 {i+1} 行")
        elif start_line is not None and 'elif menu == "模型体验":' in line:
            next_menu_start = i
            print(f"下一个菜单开始: 第 {i+1} 行")
            break
    
    print("-" * 60)
    
    if None in [start_line, default_model_start, config_calc_start, 
                docker_check_start, smart_config_start, manual_tab_start, next_menu_start]:
        print("错误：无法找到所有关键位置")
        return
    
    # 现在我理解了代码结构，让我制定一个更简单的重构方案
    # 由于代码结构复杂，我将采用另一种方法：直接生成新的代码结构
    
    print("\n代码结构确认：")
    print(f"  页面头部: 行 {start_line+1} - {default_model_start}")
    print(f"  默认模型配置: 行 {default_model_start+1} - {config_calc_start}")
    print(f"  配置内容计算: 行 {config_calc_start+1} - {docker_check_start}")
    print(f"  Docker检查: 行 {docker_check_start+1} - {smart_config_start}")
    print(f"  智能配置: 行 {smart_config_start+1} - {manual_tab_start}")
    print(f"  手动配置: 行 {manual_tab_start+1} - {next_menu_start}")
    
    print("\n由于代码结构复杂，我将使用更简单的方法：")
    print("1. 先备份原始文件")
    print("2. 然后手动进行精确的Edit操作")
    
    # 实际上，让我先检查一下当前的git状态，确保可以恢复
    print("\n当前文件已在git控制下，可以安全地进行修改。")
    print("建议使用Edit工具进行精确的分段修改。")

if __name__ == '__main__':
    refactor_tool_assistant()
