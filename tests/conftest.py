"""
測試分兩種：
  不碰 DB 的（mask、chunker、斷詞、config、loader）永遠跑。
  要 DB 的（ingest、retrieve、schema contract）需要 KB_DATABASE_URL 指向一個 pgvector；
  沒設就整個 skip 並說明。CI 一定有設（.github/workflows/tests.yml 起 pgvector service container），
  所以 CI 不會因為 skip 而綠得莫名其妙——本機沒起容器才會 skip。

  不用 SQLite 或 mock 代替：hybrid 檢索的 SQL 用到 pgvector 的 <=> 與 tsvector，
  換個引擎測到的就不是我們跑的東西。

temp_repo fixture 造一個小 git repo 當來源，每個測試的 source 名字都不一樣，
測完刪掉自己的文件，所以可以對著開發用的 DB 跑而不弄髒它。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from kb.config import Source

DB_URL = os.getenv("KB_DATABASE_URL")


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "測試", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "測試", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text(
        "# 測試專案\n\n## 壓實\n\n壓實把 staging 的小檔合成 silver 日檔。heavy_memory pool 槽位 1。\n\n"
        "## 部署\n\n只綁 127.0.0.1 與 100.101.102.103。\n", encoding="utf-8")
    (repo / "pipe.py").write_text(
        '"""管線模組。"""\n\nimport os\n\nPOOL = "heavy_memory"\n\n\n'
        "def compact(day: str) -> int:\n    \"\"\"把一天的小檔壓成一個日檔。\"\"\"\n    return 1\n\n\n"
        "class Loader:\n    def run(self):\n        password = os.environ[\"MYSQL_PASSWORD\"]\n        return password\n",
        encoding="utf-8")
    (repo / "schema.sql").write_text(
        "-- 基準表\nCREATE TABLE baseline_segment (city TEXT, p50_sec INT);\n\n"
        "-- 站序\nCREATE TABLE route_stop (seq INT);\n", encoding="utf-8")
    (repo / ".env").write_text("MONGO_URI=mongodb://admin:realpassword@db:27017/\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    commit_all(repo, "初始：壓實與基準表")
    return repo


def make_source(repo: Path, name: str | None = None, **kw) -> Source:
    return Source(name=name or f"t-{uuid.uuid4().hex[:8]}", path=repo, github="example/repo",
                  include=["*.md", "*.py", "*.sql"], exclude=[], **kw)


@pytest.fixture
def db_conn():
    if not DB_URL:
        pytest.skip("KB_DATABASE_URL 沒設：起 docker compose -f docker-compose.dev.yml up -d 再 export")
    from kb import db
    conn = db.connect(DB_URL, autocommit=True)
    db.apply_schema(conn)
    conn.close()
    conn = db.connect(DB_URL)
    created: list[str] = []
    conn.test_sources = created            # type: ignore[attr-defined]
    yield conn
    with conn.cursor() as cur:
        for s in created:
            cur.execute("DELETE FROM kb_document WHERE source = %s", (s,))
            cur.execute("DELETE FROM kb_source_state WHERE source = %s", (s,))
    conn.commit()
    conn.close()
