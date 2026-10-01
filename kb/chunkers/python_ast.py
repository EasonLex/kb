"""
Python：以 top-level 函式／類別為單位，模組 docstring 自成一段，
import 與常數合成一段「模組開頭」。

這些 repo 的程式碼最有價值的是 docstring 和註解裡的「為什麼」，
而那些都黏在函式或類別上。照函式切，一段就是一個完整的決定。

ast 解析失敗（語法錯、Python 2）就退回一般文字切法，不要讓一個壞檔案擋住整次 ingest。
"""

from __future__ import annotations

import ast

from . import MAX_CHARS, Chunk
from .plain import window_lines


def _node_span(node: ast.AST) -> tuple[int, int]:
    start = node.lineno
    for d in getattr(node, "decorator_list", []):
        start = min(start, d.lineno)
    return start, node.end_lineno or node.lineno


def chunk(path: str, text: str) -> list[Chunk]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        from .plain import chunk as plain_chunk
        return plain_chunk(path, text)

    lines = text.splitlines()
    out: list[Chunk] = []
    ord_ = 0

    def emit(start: int, end: int, symbol: str | None) -> None:
        nonlocal ord_
        body_lines = lines[start - 1:end]
        body = "\n".join(body_lines)
        if not body.strip():
            return
        label = f"[{path}" + (f" :: {symbol}]" if symbol else "]")
        prefix = label + "\n"
        if len(body) <= MAX_CHARS:
            out.append(Chunk(text=prefix + body, ord=ord_, line_start=start, line_end=end, symbol=symbol))
            ord_ += 1
            return
        pieces = window_lines(path, body_lines, start_line=start, prefix=prefix, symbol=symbol, ord_start=ord_)
        out.extend(pieces)
        ord_ += len(pieces)

    header_start: int | None = None
    header_end: int | None = None

    def flush_header() -> None:
        nonlocal header_start, header_end
        if header_start is not None:
            emit(header_start, header_end, "module header")
        header_start = header_end = None

    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        s, e = _node_span(body[0])
        emit(s, e, "module docstring")
        body = body[1:]

    for node in body:
        s, e = _node_span(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            flush_header()
            emit(s, e, node.name)
        else:
            if header_start is None:
                header_start = s
            header_end = e
    flush_header()
    return out
