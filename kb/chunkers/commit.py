"""
commit 文件：第 0 段是 message + 變更檔案清單，之後每個檔案的 diff 各自一段（太長再切）。

message 要單獨成段，因為它是最常被問到的部分（「為什麼要改成 X」），
不該被 diff 的雜訊稀釋掉 embedding。
"""

from __future__ import annotations

from . import MAX_CHARS, Chunk
from .plain import window_lines


def chunk(path: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    try:
        diff_at = lines.index("diff:")
    except ValueError:
        diff_at = len(lines)

    head = "\n".join(lines[:diff_at]).rstrip()
    subject = lines[1] if len(lines) > 1 else path
    out: list[Chunk] = [Chunk(text=f"[{path} :: {subject}]\n{head}", ord=0,
                              line_start=1, line_end=max(1, diff_at), symbol="message")]
    ord_ = 1
    # 依 diff --git 切成每個檔案一塊
    blocks: list[tuple[int, list[str]]] = []
    cur_start = 0
    cur: list[str] | None = None
    for idx in range(diff_at + 1, len(lines)):
        line = lines[idx]
        if line.startswith("diff --git "):
            if cur:
                blocks.append((cur_start, cur))
            cur_start, cur = idx + 1, [line]
        elif cur is not None:
            cur.append(line)
    if cur:
        blocks.append((cur_start, cur))

    for start, block in blocks:
        target = block[0].split(" b/", 1)[-1] if " b/" in block[0] else block[0]
        prefix = f"[{path} :: {subject} :: {target}]\n"
        body = "\n".join(block)
        if len(body) <= MAX_CHARS:
            out.append(Chunk(text=prefix + body, ord=ord_, line_start=start,
                             line_end=start + len(block) - 1, symbol=target))
            ord_ += 1
        else:
            pieces = window_lines(path, block, start_line=start, prefix=prefix, symbol=target, ord_start=ord_)
            out.extend(pieces)
            ord_ += len(pieces)
    return out
