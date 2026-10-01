"""
ingest —— 來源 repo → 遮蔽 → 切段 → hash 增量 → embedding → pgvector。

    python -m kb.ingest                      所有來源，增量
    python -m kb.ingest --source api         只跑一個來源
    python -m kb.ingest --full               忽略 hash，全部重切重算（換遮蔽規則、換切法時用）
    python -m kb.ingest --dry-run            只算不寫、不打 embedding API；印出會發生什麼

增量的兩層：
  1. 文件層：遮蔽後全文的 sha256 沒變 → 連切都不切，只更新 last_seen。
  2. 段落層：文件變了但某些段落的 text 沒變 → 沿用舊 embedding，只算新段落。
  第二次對同一份語料跑，stats.embedded_texts 應該是 0；測試會驗。

一份文件一個交易：中途炸掉（API 斷線、容器被砍）不會留下「文件有了但 chunk 是舊的」。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from dataclasses import dataclass, field

from . import db, store
from .chunkers import Chunk, chunk_document
from .chunkers import commit as commit_chunker
from .config import Source, load_sources
from .embed import Embedder, get_embedder
from .loaders import RawDoc, fs, git
from .mask import mask

log = logging.getLogger("kb.ingest")


@dataclass
class SourceStats:
    git_sha: str = ""
    documents: int = 0
    unchanged: int = 0
    added: int = 0
    changed: int = 0
    deleted: int = 0
    chunks: int = 0
    embedded_texts: int = 0
    masked: int = 0
    masked_by_rule: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunks_for(doc: RawDoc, text: str) -> list[Chunk]:
    if doc.kind == "commit":
        return commit_chunker.chunk(doc.path, text)
    return chunk_document(doc.path, text, doc.kind)


def ingest_document(conn, doc: RawDoc, embedder: Embedder, run_id: int, full: bool,
                    stats: SourceStats, dry_run: bool) -> None:
    masked = mask(doc.text)
    content_hash = _sha256(masked.text)
    stats.masked += masked.total
    for k, v in masked.counts.items():
        stats.masked_by_rule[k] = stats.masked_by_rule.get(k, 0) + v

    existing = None if dry_run else store.get_document(conn, doc.source, doc.path)
    if existing and existing.content_hash == content_hash and not full:
        store.touch_document(conn, existing.id, run_id, doc.git_sha)
        conn.commit()
        stats.unchanged += 1
        return

    chunks = _chunks_for(doc, masked.text)
    hashes = [_sha256(c.text) for c in chunks]
    stats.chunks += len(chunks)

    if dry_run:
        stats.added += 1
        stats.embedded_texts += len(chunks)
        log.info("  %-60s %3d 段  遮蔽 %d", doc.path, len(chunks), masked.total)
        return

    old = store.existing_embeddings(conn, existing.id) if (existing and not full) else {}
    embeddings: list[list[float] | None] = []
    todo_idx: list[int] = []
    for i, h in enumerate(hashes):
        if h in old and old[h][1] == embedder.name:
            embeddings.append(old[h][0])
        else:
            embeddings.append(None)
            todo_idx.append(i)
    if todo_idx:
        vecs = embedder.embed([chunks[i].text for i in todo_idx], "document")
        for i, v in zip(todo_idx, vecs):
            embeddings[i] = v
        stats.embedded_texts += len(todo_idx)

    doc_id = store.upsert_document(
        conn, source=doc.source, path=doc.path, kind=doc.kind, title=doc.title,
        acl_level=doc.acl_level, content_hash=content_hash, git_sha=doc.git_sha,
        content=masked.text, masked_count=masked.total, run_id=run_id)
    store.replace_chunks(conn, doc_id, chunks, hashes, embeddings, embedder.name)
    conn.commit()
    if existing:
        stats.changed += 1
    else:
        stats.added += 1


def ingest_source(conn, source: Source, embedder: Embedder, run_id: int, full: bool, dry_run: bool) -> SourceStats:
    stats = SourceStats()
    sha, docs = fs.load(source)
    docs += git.load(source)
    stats.git_sha = sha
    stats.documents = len(docs)
    log.info("[%s] HEAD %s，%d 份文件（檔案 %d、commit %d）", source.name, sha[:12], len(docs),
             sum(d.kind != "commit" for d in docs), sum(d.kind == "commit" for d in docs))
    for doc in docs:
        try:
            ingest_document(conn, doc, embedder, run_id, full, stats, dry_run)
        except Exception as e:                     # 一份壞掉不擋其他文件，但要留下紀錄並讓整次 run 標 failed
            conn.rollback()
            stats.errors.append(f"{doc.path}: {e}")
            log.exception("  %s 失敗", doc.path)
    if not dry_run:
        stats.deleted = store.delete_unseen(conn, source.name, run_id)
        n_doc, n_chunk = store.update_source_state(conn, source.name, sha, run_id)
        conn.commit()
        log.info("[%s] 完成：新增 %d、變更 %d、未變 %d、刪除 %d；共 %d 份／%d 段；embedding %d 段；遮蔽 %d 處",
                 source.name, stats.added, stats.changed, stats.unchanged, stats.deleted,
                 n_doc, n_chunk, stats.embedded_texts, stats.masked)
    else:
        log.info("[%s] dry-run：%d 份文件會產生 %d 段、遮蔽 %d 處", source.name, len(docs), stats.chunks, stats.masked)
    return stats


def run(sources: list[Source], embedder: Embedder, full: bool = False, dry_run: bool = False,
        conn=None) -> tuple[str, dict]:
    own_conn = conn is None
    conn = conn or db.connect()
    started = time.time()
    run_id = store.start_run(conn, full) if not dry_run else -1
    all_stats: dict[str, dict] = {}
    status = "ok"
    try:
        for source in sources:
            s = ingest_source(conn, source, embedder, run_id, full, dry_run)
            all_stats[source.name] = {k: v for k, v in s.__dict__.items()}
            if s.errors:
                status = "failed"
    except Exception as e:
        status = "failed"
        if not dry_run:
            store.finish_run(conn, run_id, status, {"sources": all_stats, "fatal": str(e)}, str(e))
        raise
    summary = {
        "sources": all_stats,
        "embedding": {"model": embedder.name, "calls": embedder.calls, "texts": embedder.texts, "tokens": embedder.tokens},
        "seconds": round(time.time() - started, 1),
    }
    if not dry_run:
        store.finish_run(conn, run_id, status, summary, "; ".join(
            e for s in all_stats.values() for e in s["errors"]) or None)
    if own_conn:
        conn.close()
    return status, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", help="只跑這個來源（可重複）")
    ap.add_argument("--full", action="store_true", help="忽略 hash，全部重算")
    ap.add_argument("--dry-run", action="store_true", help="只算不寫、不打 API")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    sources = load_sources()
    if args.source:
        unknown = set(args.source) - {s.name for s in sources}
        if unknown:
            print(f"不認得的來源：{sorted(unknown)}；sources.yaml 裡有：{[s.name for s in sources]}", file=sys.stderr)
            return 2
        sources = [s for s in sources if s.name in args.source]

    import os
    if args.dry_run:
        os.environ.setdefault("EMBEDDING_PROVIDER", "fake")   # dry-run 不打 API，不需要 key
    embedder = get_embedder()
    status, summary = run(sources, embedder, full=args.full, dry_run=args.dry_run,
                          conn=None if not args.dry_run else _NullConn())
    emb = summary["embedding"]
    log.info("總結：%s；embedding %s 呼叫 %d 次／%d 段／%d tokens；%.1f 秒",
             status, emb["model"], emb["calls"], emb["texts"], emb["tokens"], summary["seconds"])
    return 0 if status == "ok" else 1


class _NullConn:
    """dry-run 用：ingest 不應該碰 DB，但程式流程裡有 commit/rollback 呼叫。"""

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


if __name__ == "__main__":
    sys.exit(main())
