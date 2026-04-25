#!/usr/bin/env python3
"""
修复 admin_panel.py 中工具配置助手页面的缩进问题（版本3）

当前问题：
- with t1:、with t2: 等有8个空格缩进（正确，在with tab_manual内部）
- 但这些with块内的代码（如st.subheader）也只有8个空格缩进（错误）
- 这些代码应该有12个空格缩进（在with t1块内部）

需要修复的范围：
- with t1: 块内的代码（从st.subheader开始，到with t2:之前）
- with t2: 块内的代码
- with t3: 块内的代码
- with t4: 块内的代码
"""

def fix_indentation():
    file_path = '/Users/andy/Desktop/AIProxy/admin_panel.py'
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print("分析代码结构...")
    print("-" * 50)
    
    # 找到关键位置
    with_t1_idx = None
    with_t2_idx = None
    with_t3_idx = None
    with_t4_idx = None
    elif_menu_idx = None
    
    for i, line in enumerate(lines):
        if '        with t1:' in line:
            with_t1_idx = i
            print(f"找到 with t1: 在第 {i+1} 行")
        elif '        with t2:' in line:
            with_t2_idx = i
            print(f"找到 with t2: 在第 {i+1} 行")
        elif '        with t3:' in line:
            with_t3_idx = i
            print(f"找到 with t3: 在第 {i+1} 行")
        elif '        with t4:' in line:
            with_t4_idx = i
            print(f"找到 with t4: 在第 {i+1} 行")
        elif line.strip().startswith('elif menu =='):
            elif_menu_idx = i
            print(f"找到 elif menu == 在第 {i+1} 行")
            break
    
    print("-" * 50)
    
    # 确认所有位置都找到
    if None in [with_t1_idx, with_t2_idx, with_t3_idx, with_t4_idx, elif_menu_idx]:
        print("错误：无法找到所有关键位置")
        return
    
    # 定义各个块的范围
    # with t1: 块内代码：with_t1_idx + 1 到 with_t2_idx - 1
    # with t2: 块内代码：with_t2_idx + 1 到 with_t3_idx - 1
    # with t3: 块内代码：with_t3_idx + 1 到 with_t4_idx - 1
    # with t4: 块内代码：with_t4_idx + 1 到 elif_menu_idx - 1
    
    blocks = [
        ("with t1 块内", with_t1_idx + 1, with_t2_idx - 1),
        ("with t2 块内", with_t2_idx + 1, with_t3_idx - 1),
        ("with t3 块内", with_t3_idx + 1, with_t4_idx - 1),
        ("with t4 块内", with_t4_idx + 1, elif_menu_idx - 1),
    ]
    
    modified_count = 0
    
    for block_name, start_idx, end_idx in blocks:
        print(f"\n处理 {block_name}（行 {start_idx+1} 到 {end_idx+1}）...")
        
        for i in range(start_idx, end_idx + 1):
            if i >= len(lines):
                break
            
            line = lines[i]
            
            # 跳过空行
            if line == '\n' or line == '\r\n':
                continue
            
            # 跳过只有空格的行
            if line.strip() == '':
                continue
            
            # 检查当前缩进是否为8个空格
            # 如果是，增加到12个空格
            if line.startswith('        ') and not line.startswith('         '):
                # 将前8个空格替换为12个空格
                lines[i] = '    ' + line
                modified_count += 1
    
    print(f"\n{'=' * 50}")
    print(f"总共修改了 {modified_count} 行")
    
    # 写回文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print("修复完成！")

if __name__ == '__main__':
    fix_indentation()
