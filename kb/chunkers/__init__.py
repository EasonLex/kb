"""
切段。每種檔案類型一個切法，對外只有 chunk_document()。

大小用「字元數」不用 token 數：三家 LLM 加一家 embedding 的 tokenizer 都不同，
沒有一個對的答案；中文大約 1 字 ≈ 1 token，程式碼約 3–4 字 ≈ 1 token，
所以字元上限訂在 embedding 模型的 token 上限之內很寬鬆。

每個 chunk 的 text 都帶著脈絡前綴（檔名、heading 路徑或符號名），
因為 embedding 看的只有 text，沒有 metadata——「第 3 段提到 pool」在
向量空間裡要能靠自己站住。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

TARGET_CHARS = 1200      # 想要的大小
MAX_CHARS = 2000         # 超過一定切
OVERLAP_CHARS = 150      # 連續切的時候，下一段從前一段尾巴往回帶多少


@dataclass
class Chunk:
    text: str
    ord: int
    line_start: int | None = None
    line_end: int | None = None
    heading_path: str | None = None
    symbol: str | None = None


KIND_BY_SUFFIX = {
    ".md": "doc", ".markdown": "doc", ".txt": "doc", ".rst": "doc",
    ".py": "code", ".sh": "code", ".js": "code", ".html": "code", ".ts": "code",
    ".sql": "sql",
    ".yml": "config", ".yaml": "config", ".toml": "config", ".json": "config",
    ".ini": "config", ".cfg": "config", ".env": "config",
}
KIND_BY_NAME = {"Dockerfile": "config", "dockerignore": "config", ".dockerignore": "config",
                "LICENSE": "doc"}


def kind_of(path: str) -> str:
    p = PurePosixPath(path)
    if p.name in KIND_BY_NAME:
        return KIND_BY_NAME[p.name]
    if p.name.startswith("env.") and p.name.endswith(".example"):
        return "config"
    if p.name.startswith("requirements") and p.suffix == ".txt":
        return "config"
    return KIND_BY_SUFFIX.get(p.suffix.lower(), "doc")


def chunk_document(path: str, text: str, kind: str | None = None) -> list[Chunk]:
    from . import markdown, plain, python_ast, sql

    kind = kind or kind_of(path)
    suffix = PurePosixPath(path).suffix.lower()
    if kind == "doc" and suffix in (".md", ".markdown"):
        return markdown.chunk(path, text)
    if kind == "code" and suffix == ".py":
        return python_ast.chunk(path, text)
    if kind == "sql":
        return sql.chunk(path, text)
    return plain.chunk(path, text)
