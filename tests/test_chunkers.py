from kb.chunkers import MAX_CHARS, chunk_document, kind_of
from kb.chunkers import commit as commit_chunker
from kb.chunkers import markdown, plain, python_ast, sql


def test_kind_of():
    assert kind_of("DEVLOG.md") == "doc"
    assert kind_of("src/trafficproject/compact.py") == "code"
    assert kind_of("sql/schema.sql") == "sql"
    assert kind_of("deploy/docker-compose.yml") == "config"
    assert kind_of("Dockerfile") == "config"
    assert kind_of("env.api.example") == "config"
    assert kind_of("requirements-dev.txt") == "config"
    assert kind_of("static/index.html") == "code"


# ---------------------------------------------------------------- markdown

MD = """# 開發日誌

前言一句。

## 四之一、資料品質

第一節內容。

### 細節

細節內容，提到 heavy_memory。

```python
# 這不是 heading
x = 1
```

## 四之二、Kafka 導入

第二節內容。
"""


def test_markdown_heading_path_and_lines():
    chunks = markdown.chunk("DEVLOG.md", MD)
    paths = [c.heading_path for c in chunks]
    assert any(p.endswith("四之一、資料品質 > 細節") for p in paths), paths
    assert any(p.endswith("四之二、Kafka 導入") for p in paths), paths
    # 每段 text 以 [heading 路徑] 開頭，embedding 才知道脈絡
    assert all(c.text.startswith("[DEVLOG.md") for c in chunks)
    # fenced code 裡的 # 沒有變成 heading
    assert not any("這不是 heading" in (c.heading_path or "") for c in chunks)
    # 行號對得上原文
    for c in chunks:
        assert c.line_start is not None and c.line_end >= c.line_start
    kafka = next(c for c in chunks if c.heading_path.endswith("Kafka 導入"))
    assert MD.splitlines()[kafka.line_start - 1].startswith("## 四之二")
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_markdown_long_section_is_windowed_with_same_heading():
    body = "\n".join(f"第 {i} 行，一些內容一些內容一些內容一些內容。" for i in range(400))
    text = f"# T\n\n## 長段落\n\n{body}\n"
    chunks = markdown.chunk("x.md", text)
    assert len(chunks) > 3
    assert all(c.heading_path.endswith("長段落") for c in chunks)
    assert all(len(c.text) <= MAX_CHARS + 200 for c in chunks)
    # 相鄰視窗要有重疊（下一段的起點在上一段終點之前）
    assert chunks[1].line_start <= chunks[0].line_end
    assert chunks[1].line_start > chunks[0].line_start


def test_markdown_small_sections_merge():
    text = "# T\n\n## A\n\n短。\n\n## B\n\n也短。\n\n## C\n\n還是短。\n"
    chunks = markdown.chunk("x.md", text)
    assert len(chunks) == 1
    assert "短。" in chunks[0].text and "還是短。" in chunks[0].text


# ---------------------------------------------------------------- python

PY = '''"""模組說明。"""

import os
from x import y

POOL = "heavy_memory"


@decorator
def compact(day):
    """壓實。"""
    return day


class Loader:
    def run(self):
        return 1


THRESHOLD = 3
'''


def test_python_symbols_and_lines():
    chunks = python_ast.chunk("pipe.py", PY)
    by_symbol = {c.symbol: c for c in chunks}
    assert set(by_symbol) == {"module docstring", "module header", "compact", "Loader"}
    assert by_symbol["module docstring"].line_start == 1
    # decorator 要算進函式的起點
    assert PY.splitlines()[by_symbol["compact"].line_start - 1] == "@decorator"
    assert PY.splitlines()[by_symbol["compact"].line_end - 1].strip() == "return day"
    assert by_symbol["compact"].text.startswith("[pipe.py :: compact]\n")
    # 類別後面的常數也歸到 module header（第二個 header 段）
    headers = [c for c in chunks if c.symbol == "module header"]
    assert len(headers) == 2 and "THRESHOLD" in headers[1].text
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_python_syntax_error_falls_back_to_plain():
    chunks = python_ast.chunk("bad.py", "def (:\n  pass\n")
    assert len(chunks) == 1 and chunks[0].text.startswith("[bad.py]")


# ---------------------------------------------------------------- sql

SQL = """-- 基準表的說明
CREATE TABLE baseline_segment (
    city VARCHAR(16),   -- 這裡有分號; 在註解裡
    note TEXT DEFAULT 'a;b'
);

-- 站序
CREATE TABLE IF NOT EXISTS route_stop (seq INT);
"""


def test_sql_statements_with_comments_attached():
    chunks = sql.chunk("schema.sql", SQL)
    # 兩個 statement 很小，會合併成一段；註解裡和字串裡的分號不切
    assert len(chunks) == 1
    c = chunks[0]
    assert c.symbol == "baseline_segment"
    assert c.line_start == 1 and c.line_end == 8
    assert "-- 基準表的說明" in c.text and "route_stop" in c.text


def test_sql_large_statements_split_per_statement():
    big = "\n".join([f"-- 表 {i}\nCREATE TABLE t{i} (" + ", ".join(f"c{j} INT" for j in range(60)) + ");" for i in range(6)])
    chunks = sql.chunk("x.sql", big)
    assert len(chunks) >= 3
    assert chunks[0].symbol == "t0"


# ---------------------------------------------------------------- plain / commit

def test_plain_small_file_single_chunk():
    chunks = plain.chunk("Dockerfile", "FROM python:3.12-slim\nRUN echo hi\n")
    assert len(chunks) == 1 and chunks[0].line_end == 2


def test_plain_large_file_windows_overlap():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    chunks = plain.chunk("big.yml", text)
    assert len(chunks) > 3
    for a, b in zip(chunks, chunks[1:]):
        assert b.line_start <= a.line_end and b.line_start > a.line_start


def test_commit_chunker_message_then_per_file_diff():
    text = ("commit abc123  2026-09-01  作者 誰\n修時區造成的 off-by-one\n\n不要用 ds。\n\n變更檔案：\n dags/common.py | 3 +-\n\n"
            "diff:\ndiff --git a/dags/common.py b/dags/common.py\n--- a\n+++ b\n+TAIPEI_DAY = 1\n"
            "diff --git a/README.md b/README.md\n+說明\n")
    chunks = commit_chunker.chunk("commits/abc123", text)
    assert chunks[0].symbol == "message" and "不要用 ds。" in chunks[0].text and "diff --git" not in chunks[0].text
    assert [c.symbol for c in chunks[1:]] == ["dags/common.py", "README.md"]
    assert chunks[1].text.startswith("[commits/abc123 :: 修時區造成的 off-by-one :: dags/common.py]")


def test_chunk_document_dispatch():
    assert chunk_document("a.md", "# x\n\nhi")[0].heading_path
    assert chunk_document("a.py", "def f():\n    pass\n")[0].symbol == "f"
    assert chunk_document("a.sql", "SELECT 1;")[0].text.startswith("[a.sql")
    assert chunk_document("a.yml", "a: 1")[0].text.startswith("[a.yml]")
