#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re

def is_chinese_dominant(line):
    """判断一行是否以中文为主"""
    # 统计中文字符数量（CJK统一汉字范围）
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', line)
    # 统计非空白字符数量
    non_space_chars = re.sub(r'\s', '', line)
    # 如果中文字符数量 >= 非空白字符数量的一半，认为以中文为主
    return len(chinese_chars) >= len(non_space_chars) / 2

def process_file(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    processed_lines = []
    for line in lines:
        # 去除行尾换行符以便处理
        original_line = line
        content = line.rstrip('\n\r')

        # 如果该行是空行（只有空白或完全为空），则删除
        if not content or content.isspace():
            continue

        # 如果该行以空白符开头，则删除空白符
        content = content.lstrip()

        # 根据是否以中文为主，在行首加cn/或en/
        if is_chinese_dominant(content):
            content = 'cn/' + content
        else:
            content = 'en/' + content

        processed_lines.append(content + '\n')

    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(processed_lines)

    print(f"处理完成！共处理了 {len(processed_lines)} 行")

if __name__ == '__main__':
    input_file = '/Users/hengliu/workspace/mlx/固定搭配.md'
    output_file = '/Users/hengliu/workspace/mlx/固定搭配_processed.md'
    process_file(input_file, output_file)
