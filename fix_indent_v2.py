#!/usr/bin/env python3
"""
修复 admin_panel.py 中工具配置助手页面的缩进问题（版本2）

问题分析：
1. 第1509-1720行的代码需要整体增加4个空格缩进
2. 原来的代码结构：
   - with tab_manual: (4个空格)
     - t1, t2, t3, t4 = st.tabs([...]) (8个空格)
   - with t1: (4个空格 - 错误，应该在with tab_manual内部)
     - st.subheader(...) (8个空格)

3. 目标结构：
   - with tab_manual: (4个空格)
     - t1, t2, t3, t4 = st.tabs([...]) (8个空格)
     - with t1: (8个空格 - 现在在with tab_manual内部)
       - st.subheader(...) (12个空格)

所以需要将第1509-1720行的所有代码增加4个空格缩进。
"""

def fix_indentation():
    file_path = '/Users/andy/Desktop/AIProxy/admin_panel.py'
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 需要修改的行范围：第1509行到第1720行（注意：Python是0-based，文件行号是1-based）
    # 所以索引是 1508 到 1719
    start_idx = 1508
    end_idx = 1719
    
    print(f"原始代码（第1509-1515行）:")
    for i in range(start_idx, min(start_idx + 10, len(lines))):
        print(f"  行{i+1}: {repr(lines[i])}")
    
    # 检查当前状态
    # 第1509行 (index 1508) 应该是 "    with t1:" 或 "        with t1:"
    # 第1510行 (index 1509) 应该是 "        st.subheader(...)" 或 "            st.subheader(...)"
    
    modified_count = 0
    
    # 我们需要将第1509-1720行的所有代码增加4个空格缩进
    # 但要注意：空行和只有空格的行不应该修改
    for i in range(start_idx, end_idx + 1):
        if i >= len(lines):
            break
        
        line = lines[i]
        
        # 跳过空行（只包含换行符）
        if line == '\n' or line == '\r\n':
            continue
        
        # 跳过只有空格和换行的行
        if line.strip() == '':
            continue
        
        # 给这行增加4个空格缩进
        lines[i] = '    ' + line
        modified_count += 1
    
    print(f"\n修改后代码（第1509-1515行）:")
    for i in range(start_idx, min(start_idx + 10, len(lines))):
        print(f"  行{i+1}: {repr(lines[i])}")
    
    # 写回文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print(f"\n已修改 {modified_count} 行的缩进")
    print("修复完成！")

if __name__ == '__main__':
    fix_indentation()
