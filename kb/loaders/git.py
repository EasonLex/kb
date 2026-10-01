"""
把 git log 變成文件：一個 commit 一份，內容是 message、變更檔案統計、以及截斷過的 diff。

commit 的 message 在這幾個 repo 裡是真正的設計紀錄
（「修時區造成的 off-by-one：不要用 ds 算要壓的日期」），
diff 則回答「那次到底改了哪幾行」。diff 有上限（diff_max_lines），
超過就截掉並註明——大量資料檔或 lock 檔的 diff 沒有知識價值。

commit 的內容永遠不變，所以 hash 增量對它特別有效：第二次 ingest 全部跳過。
"""

from __future__ import annotations

from pathlib import Path

from ..config import Source
from . import RawDoc
from .gitutil import run

_SEP_FIELD = "\x1f"
_SEP_RECORD = "\x1e"

SKIP_DIFF_SUFFIXES = (".lock", ".ipynb", ".png", ".jpg", ".zip", ".parquet", ".csv")


def list_commits(repo: Path) -> list[dict]:
    fmt = _SEP_FIELD.join(["%H", "%aI", "%an", "%s", "%b"]) + _SEP_RECORD
    out = run(repo, "log", f"--format={fmt}", "--no-merges")
    commits = []
    for rec in out.split(_SEP_RECORD):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, date, author, subject, body = (rec.split(_SEP_FIELD) + [""] * 5)[:5]
        commits.append({"sha": sha.strip(), "date": date, "author": author,
                        "subject": subject.strip(), "body": body.strip()})
    return commits


def commit_stat(repo: Path, sha: str) -> str:
    return run(repo, "show", "--format=", "--stat=120", "--no-color", sha).strip()


def commit_diff(repo: Path, sha: str, max_lines: int) -> str:
    raw = run(repo, "show", "--format=", "-p", "--no-color", "-U2", sha)
    kept: list[str] = []
    skipping = False
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            target = line.split(" b/", 1)[-1] if " b/" in line else ""
            skipping = target.endswith(SKIP_DIFF_SUFFIXES)
            if skipping:
                kept.append(f"{line}\n（略過：{target} 的 diff 沒有索引價值）")
                continue
        if skipping:
            continue
        if line.startswith("Binary files"):
            continue
        kept.append(line)
        if len(kept) >= max_lines:
            kept.append(f"…（diff 超過 {max_lines} 行，其餘截斷）")
            break
    return "\n".join(kept)


def build_text(c: dict, stat: str, diff: str) -> str:
    parts = [
        f"commit {c['sha'][:12]}  {c['date'][:10]}  作者 {c['author']}",
        c["subject"],
    ]
    if c["body"]:
        parts += ["", c["body"]]
    if stat:
        parts += ["", "變更檔案：", stat]
    if diff:
        parts += ["", "diff:", diff]
    return "\n".join(parts)


def load(source: Source) -> list[RawDoc]:
    if not source.commits:
        return []
    docs: list[RawDoc] = []
    for c in list_commits(source.path):
        stat = commit_stat(source.path, c["sha"])
        diff = commit_diff(source.path, c["sha"], source.diff_max_lines) if source.diff_max_lines > 0 else ""
        docs.append(RawDoc(
            source=source.name, path=f"commits/{c['sha']}", kind="commit",
            title=c["subject"] or c["sha"][:12],
            text=build_text(c, stat, diff), git_sha=c["sha"],
            acl_level=source.acl_default,
        ))
    return docs
