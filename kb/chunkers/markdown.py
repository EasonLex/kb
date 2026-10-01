"""
Markdown：依 heading 切，再把過長的段落細切。

DEVLOG.md 有 4000 多行、約 30 個「## 四之N」日期段落，heading 就是
最自然的邊界；每一段 text 前面帶完整 heading 路徑
（「DEVLOG.md > 四之12 Kafka 導入 > 為什麼」），所以單看一段也知道它在講什麼時期的哪件事。

fenced code block 裡的 # 不是 heading——DEVLOG 貼了很多 shell 指令與 Python，
不判斷 fence 的話，一行 `# 建表` 註解就會把段落切爛。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import MAX_CHARS, TARGET_CHARS, Chunk
from .plain import window_lines

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class _Section:
    level: int
    titles: list[str]            # 從最上層到這一層的標題
    start_line: int              # 1-based，heading 那一行
    lines: list[str] = field(default_factory=list)   # 不含 heading 本身

    @property
    def body_start(self) -> int:
        return self.start_line + 1


def _sections(text: str) -> list[_Section]:
    lines = text.splitlines()
    stack: list[str] = []
    levels: list[int] = []
    sections: list[_Section] = [_Section(level=0, titles=[], start_line=0)]
    in_fence = False
    fence_mark = ""
    for idx, line in enumerate(lines, start=1):
        m_fence = _FENCE.match(line)
        if m_fence:
            mark = m_fence.group(1)
            if not in_fence:
                in_fence, fence_mark = True, mark
            elif mark == fence_mark:
                in_fence = False
            sections[-1].lines.append(line)
            continue
        if in_fence:
            sections[-1].lines.append(line)
            continue
        m = _HEADING.match(line)
        if not m:
            sections[-1].lines.append(line)
            continue
        level, title = len(m.group(1)), m.group(2).strip()
        while levels and levels[-1] >= level:
            levels.pop()
            stack.pop()
        levels.append(level)
        stack.append(title)
        sections.append(_Section(level=level, titles=list(stack), start_line=idx))
    return sections


def chunk(path: str, text: str) -> list[Chunk]:
    out: list[Chunk] = []
    ord_ = 0
    pending: list[tuple[_Section, str]] = []     # 小段落合併用

    def heading_path(sec: _Section) -> str:
        return " > ".join([path, *sec.titles]) if sec.titles else path

    def flush_pending() -> None:
        nonlocal ord_
        if not pending:
            return
        first_sec = pending[0][0]
        last_sec = pending[-1][0]
        body = "\n\n".join(f"{'#' * s.level + ' ' if s.level else ''}{s.titles[-1] if s.titles else ''}\n{b}".strip()
                           for s, b in pending)
        hp = heading_path(first_sec) if len(pending) == 1 else " > ".join([path, *first_sec.titles[:-1]]) or path
        out.append(Chunk(text=f"[{hp}]\n{body}", ord=ord_, heading_path=hp,
                         line_start=first_sec.start_line or 1,
                         line_end=last_sec.start_line + len(last_sec.lines)))
        ord_ += 1
        pending.clear()

    for sec in _sections(text):
        body = "\n".join(sec.lines).strip("\n")
        if not body.strip():
            # 只有標題沒內文的段（例如 H1 底下直接接 H2）：標題已經在子段的 heading 路徑裡，不用自己成段
            continue
        hp = heading_path(sec)
        size = len(body)
        if size <= TARGET_CHARS:
            # 小段落先攬著，跟同層鄰居合併到接近 TARGET 再出
            pending_size = sum(len(b) for _, b in pending)
            same_parent = (not pending) or pending[0][0].titles[:-1] == sec.titles[:-1]
            if pending and (not same_parent or pending_size + size > TARGET_CHARS):
                flush_pending()
            pending.append((sec, body))
            continue
        flush_pending()
        if size <= MAX_CHARS:
            out.append(Chunk(text=f"[{hp}]\n{body}", ord=ord_, heading_path=hp,
                             line_start=sec.start_line or 1,
                             line_end=sec.start_line + len(sec.lines)))
            ord_ += 1
            continue
        # 超長段落：依行切視窗，每個視窗都帶同一個 heading 路徑
        pieces = window_lines(path, sec.lines, start_line=sec.body_start,
                              prefix=f"[{hp}]\n", heading_path=hp, ord_start=ord_)
        out.extend(pieces)
        ord_ += len(pieces)
    flush_pending()
    return out
