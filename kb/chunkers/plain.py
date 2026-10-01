"""
一般文字：YAML、Dockerfile、shell、requirements、HTML。

小檔整檔一段；大檔用行數滑動視窗。這類檔案的結構不值得各寫一個 parser——
compose 檔的註解比結構重要，而註解是散文。
"""

from __future__ import annotations

from . import MAX_CHARS, OVERLAP_CHARS, TARGET_CHARS, Chunk


def chunk(path: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    prefix = f"[{path}]\n"
    if len(text) <= MAX_CHARS:
        if not text.strip():
            return []
        return [Chunk(text=prefix + text, ord=0, line_start=1, line_end=max(1, len(lines)))]
    return window_lines(path, lines, start_line=1, prefix=prefix)


def window_lines(path: str, lines: list[str], start_line: int, prefix: str,
                 symbol: str | None = None, heading_path: str | None = None,
                 ord_start: int = 0) -> list[Chunk]:
    """
    把一串行切成大約 TARGET_CHARS 的視窗，相鄰視窗重疊約 OVERLAP_CHARS。
    行是最小單位——不會從一行中間切開。
    """
    out: list[Chunk] = []
    i, ord_ = 0, ord_start
    n = len(lines)
    while i < n:
        size, j = 0, i
        while j < n and (size + len(lines[j]) + 1 <= TARGET_CHARS or j == i):
            size += len(lines[j]) + 1
            j += 1
        body = "\n".join(lines[i:j])
        if body.strip():
            out.append(Chunk(text=prefix + body, ord=ord_,
                             line_start=start_line + i, line_end=start_line + j - 1,
                             symbol=symbol, heading_path=heading_path))
            ord_ += 1
        if j >= n:
            break
        # 往回帶 overlap，但至少前進一行，避免原地打轉
        back, k = 0, j
        while k > i + 1 and back + len(lines[k - 1]) + 1 <= OVERLAP_CHARS:
            k -= 1
            back += len(lines[k]) + 1
        i = max(k, i + 1)
    return out
