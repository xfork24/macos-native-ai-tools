"""Markdown 代码区域保护；所有转换阶段共用，避免把代码解释成标题或公式。"""
from __future__ import annotations

import re


def code_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    offset = 0
    opened = None
    for line in text.splitlines(keepends=True):
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n"))
        if opened:
            start, char, length = opened
            if fence and fence[1][0] == char and len(fence[1]) >= length and not fence[2].strip():
                spans.append((start, offset + len(line)))
                opened = None
        elif fence:
            opened = (offset, fence[1][0], len(fence[1]))
        elif line.startswith(("    ", "\t")) and line.strip():
            spans.append((offset, offset + len(line)))
        offset += len(line)
    if opened:
        spans.append((opened[0], len(text)))
    # Inline code only outside block regions; arbitrary backtick delimiter length.
    mask = list(text)
    for a, b in spans:
        mask[a:b] = " " * (b - a)
    for m in re.finditer(r"(?<!`)(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", "".join(mask)):
        spans.append(m.span())
    return sorted(spans)


def mask_code(text: str) -> str:
    chars = list(text)
    for a, b in code_spans(text):
        chars[a:b] = ["\n" if c == "\n" else " " for c in text[a:b]]
    return "".join(chars)


def protect_code(text: str) -> tuple[str, dict[str, str]]:
    saved = {}
    # Source-dependent collision avoidance without changing repeatability.
    prefix = "MD2EPUBCODE"
    while prefix in text:
        prefix += "X"
    for i, (a, b) in reversed(list(enumerate(code_spans(text)))):
        key = f"{prefix}{i:06d}END"
        saved[key] = text[a:b]
        text = text[:a] + key + ("\n" if text[a:b].endswith("\n") else "") + text[b:]
    return text, saved


def restore_code(text: str, saved: dict[str, str]) -> str:
    for key, value in saved.items():
        text = text.replace(key + ("\n" if value.endswith("\n") else ""), value)
    return text


def map_prose(text: str, fn) -> str:
    protected, saved = protect_code(text)
    return restore_code(fn(protected), saved)
