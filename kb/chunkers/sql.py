"""
SQL：以 statement 為單位（分號結尾），小的合併、大的照行切。

statement 前面的 -- 註解歸給那個 statement：schema.sql 的註解寫在 CREATE TABLE 上方，
說明的是那張表，拆開就沒意義了。
"""

from __future__ import annotations

import re

from . import MAX_CHARS, TARGET_CHARS, Chunk
from .plain import window_lines

_OBJECT = re.compile(r"(?is)\b(?:create\s+(?:or\s+replace\s+)?(?:table|view|index|user|database|schema|function)"
                     r"(?:\s+if\s+not\s+exists)?|alter\s+table|insert\s+into|grant\s+\w+\s+on|with|select)\s+"
                     r"[`\"]?([A-Za-z_][\w.$]*)")


def _statements(lines: list[str]) -> list[tuple[int, int]]:
    """回傳 (起始行, 結束行) 清單，1-based、含註解前導。"""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    in_block_comment = False
    quote: str | None = None
    for idx, line in enumerate(lines, start=1):
        if start is None and line.strip():
            start = idx
        i = 0
        while i < len(line):
            ch = line[i]
            two = line[i:i + 2]
            if in_block_comment:
                if two == "*/":
                    in_block_comment = False
                    i += 2
                    continue
            elif quote:
                if ch == quote:
                    quote = None
            elif two == "--":
                break
            elif two == "/*":
                in_block_comment = True
                i += 2
                continue
            elif ch in ("'", '"', "`"):
                quote = ch
            elif ch == ";":
                spans.append((start or idx, idx))
                start = None
            i += 1
    if start is not None and any(l.strip() for l in lines[start - 1:]):
        spans.append((start, len(lines)))
    return spans


def chunk(path: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    out: list[Chunk] = []
    ord_ = 0
    group: list[tuple[int, int]] = []
    group_size = 0

    def symbol_of(s: int, e: int) -> str | None:
        m = _OBJECT.search("\n".join(lines[s - 1:e]))
        return m.group(1) if m else None

    def flush() -> None:
        nonlocal ord_, group, group_size
        if not group:
            return
        s, e = group[0][0], group[-1][1]
        body = "\n".join(lines[s - 1:e])
        sym = symbol_of(s, e)
        prefix = f"[{path}" + (f" :: {sym}]" if sym else "]") + "\n"
        if len(body) <= MAX_CHARS:
            out.append(Chunk(text=prefix + body, ord=ord_, line_start=s, line_end=e, symbol=sym))
            ord_ += 1
        else:
            pieces = window_lines(path, lines[s - 1:e], start_line=s, prefix=prefix, symbol=sym, ord_start=ord_)
            out.extend(pieces)
            ord_ += len(pieces)
        group, group_size = [], 0

    for s, e in _statements(lines):
        size = sum(len(l) + 1 for l in lines[s - 1:e])
        if group and group_size + size > TARGET_CHARS:
            flush()
        group.append((s, e))
        group_size += size
        if group_size > TARGET_CHARS:
            flush()
    flush()
    return out
