"""
kb_document / kb_chunk 的讀寫。全部手寫 SQL，一個函式一件事。
交易由呼叫端（ingest）控制：一份文件一個 commit，中途失敗不會留下半份。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg
from pgvector import Vector

from .chunkers import Chunk
from .textsearch import to_tsv_text


@dataclass
class ExistingDoc:
    id: int
    content_hash: str


def start_run(conn: psycopg.Connection, full: bool) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO kb_ingest_run (full_rebuild) VALUES (%s) RETURNING id", (full,))
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int, status: str, stats: dict, error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE kb_ingest_run SET finished_at = now(), status = %s, stats = %s::jsonb, error = %s WHERE id = %s",
                    (status, json.dumps(stats, ensure_ascii=False), error, run_id))
    conn.commit()


def get_document(conn: psycopg.Connection, source: str, path: str) -> ExistingDoc | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, content_hash FROM kb_document WHERE source = %s AND path = %s", (source, path))
        row = cur.fetchone()
    return ExistingDoc(*row) if row else None


def touch_document(conn: psycopg.Connection, doc_id: int, run_id: int, git_sha: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE kb_document SET last_seen_run_id = %s, git_sha = %s WHERE id = %s", (run_id, git_sha, doc_id))


def existing_embeddings(conn: psycopg.Connection, doc_id: int) -> dict[str, tuple[list[float], str]]:
    """chunk_hash → (embedding, embed_model)。重切之後，內容沒變的段落直接沿用。"""
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_hash, embedding, embed_model FROM kb_chunk WHERE document_id = %s AND embedding IS NOT NULL",
                    (doc_id,))
        return {h: (e.to_list() if hasattr(e, "to_list") else list(e), m) for h, e, m in cur.fetchall()}


def upsert_document(conn: psycopg.Connection, *, source: str, path: str, kind: str, title: str,
                    acl_level: int, content_hash: str, git_sha: str, content: str,
                    masked_count: int, run_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO kb_document (source, path, kind, title, acl_level, content_hash, git_sha,
                                     size_chars, masked_count, content, last_seen_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, path) DO UPDATE SET
                kind = EXCLUDED.kind, title = EXCLUDED.title, acl_level = EXCLUDED.acl_level,
                content_hash = EXCLUDED.content_hash, git_sha = EXCLUDED.git_sha,
                size_chars = EXCLUDED.size_chars, masked_count = EXCLUDED.masked_count,
                content = EXCLUDED.content, updated_at = now(), last_seen_run_id = EXCLUDED.last_seen_run_id
            RETURNING id
        """, (source, path, kind, title, acl_level, content_hash, git_sha,
              len(content), masked_count, content, run_id))
        return cur.fetchone()[0]


def replace_chunks(conn: psycopg.Connection, doc_id: int, chunks: list[Chunk],
                   hashes: list[str], embeddings: list[list[float] | None], embed_model: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM kb_chunk WHERE document_id = %s", (doc_id,))
        rows = []
        for c, h, e in zip(chunks, hashes, embeddings):
            rows.append((doc_id, c.ord, c.heading_path, c.symbol, c.line_start, c.line_end,
                         c.text, h, len(c.text), Vector(e) if e is not None else None,
                         embed_model if e is not None else None, to_tsv_text(c.text)))
        cur.executemany("""
            INSERT INTO kb_chunk (document_id, ord, heading_path, symbol, line_start, line_end,
                                  text, chunk_hash, n_chars, embedding, embed_model, tsv)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('simple', %s))
        """, rows)


def delete_unseen(conn: psycopg.Connection, source: str, run_id: int) -> int:
    """這次 ingest 沒看到的文件 = 已從 repo 消失（或被 exclude 掉），連 chunk 一起刪。"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM kb_document WHERE source = %s AND (last_seen_run_id IS DISTINCT FROM %s)",
                    (source, run_id))
        return cur.rowcount


def update_source_state(conn: psycopg.Connection, source: str, git_sha: str, run_id: int) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM kb_document WHERE source = %s", (source,))
        n_doc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM kb_chunk c JOIN kb_document d ON d.id = c.document_id WHERE d.source = %s",
                    (source,))
        n_chunk = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO kb_source_state (source, git_sha, document_count, chunk_count, indexed_at, run_id)
            VALUES (%s, %s, %s, %s, now(), %s)
            ON CONFLICT (source) DO UPDATE SET git_sha = EXCLUDED.git_sha, document_count = EXCLUDED.document_count,
                chunk_count = EXCLUDED.chunk_count, indexed_at = now(), run_id = EXCLUDED.run_id
        """, (source, git_sha, n_doc, n_chunk, run_id))
    return n_doc, n_chunk
