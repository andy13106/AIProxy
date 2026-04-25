#!/usr/bin/env python3
"""
修复 admin_panel.py 中工具配置助手页面的缩进问题
将第1509行到第1720行的代码缩进从4个空格增加到8个空格
"""

import re

def fix_indentation():
    file_path = '/Users/andy/Desktop/AIProxy/admin_panel.py'
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 需要修改的行范围：第1509行到第1720行（注意：Python是0-based，文件行号是1-based）
    # 所以索引是 1508 到 1719
    start_line = 1508
    end_line = 1719
    
    modified_count = 0
    
    for i in range(start_line, end_line + 1):
        if i >= len(lines):
            break
        
        line = lines[i]
        
        # 检查行是否以4个空格开头
        if line.startswith('    ') and not line.startswith('     '):
            # 将前4个空格替换为8个空格
            lines[i] = '    ' + line
            modified_count += 1
    
    # 写回文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print(f"已修改 {modified_count} 行的缩进")
    print("修复完成！")

if __name__ == '__main__':
    fix_indentation()
