"""
連線與 schema。

    python -m kb.db apply      套用 sql/schema.sql（全部 IF NOT EXISTS，重跑安全）
    python -m kb.db check      確認連得上、extension 在、表在

用 psycopg 直接執行整份 SQL 檔，不靠 psql CLI——跟 api/apply_sql.py 同一個理由：
CI 的 runner 和 Airflow 的映像檔都不一定有 psql。
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from .config import KB_ROOT, database_url

SCHEMA_FILE = KB_ROOT / "sql" / "schema.sql"
REQUIRED_TABLES = ("kb_document", "kb_chunk", "kb_ingest_run", "kb_source_state", "kb_query_log")


def connect(url: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    conn = psycopg.connect(url or database_url(), autocommit=autocommit)
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        # extension 還沒建（第一次 apply 之前）。apply 完再連一次就好。
        pass
    return conn


def apply_schema(conn: psycopg.Connection, path: Path = SCHEMA_FILE) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def check(conn: psycopg.Connection) -> list[str]:
    problems: list[str] = []
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if cur.fetchone() is None:
            problems.append("pgvector extension 不在：先跑 python -m kb.db apply")
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        have = {r[0] for r in cur.fetchall()}
        for t in REQUIRED_TABLES:
            if t not in have:
                problems.append(f"缺表 {t}")
    return problems


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    if cmd == "apply":
        with connect(autocommit=True) as conn:
            apply_schema(conn)
        with connect() as conn:
            problems = check(conn)
        print("schema 已套用" if not problems else "\n".join(problems))
        return 0 if not problems else 1
    if cmd == "check":
        with connect() as conn:
            problems = check(conn)
        print("OK" if not problems else "\n".join(problems))
        return 0 if not problems else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
