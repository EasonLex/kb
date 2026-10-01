"""
從 repo 的 HEAD 讀檔案——不是工作目錄。

    git ls-tree -r --name-only HEAD      列出這個 commit 追蹤的檔案
    git cat-file --batch                 一個行程把所有內容吐出來

為什麼不直接 open() 工作目錄的檔案：
  1. 工作目錄可能有未 commit 的修改，出處連結指到 GitHub 上的 HEAD sha 會對不上。
  2. ls-tree 看的是 commit，所以 .gitignore 蓋掉的 .env、raw/、output/ 根本不在清單裡，
     不需要再維護一份「絕對不能讀」的名單。
  3. 同一個 sha 跑兩次結果一樣，增量判斷才有意義。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..chunkers import kind_of
from ..config import Source
from . import RawDoc
from .gitutil import GitError, glob_to_regex, head_sha, matches_any, run

MAX_FILE_BYTES = 2 * 1024 * 1024        # 超過就當作不是給人讀的


def tracked_paths(repo: Path) -> list[str]:
    out = run(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD")
    return [p for p in out.split("\0") if p]


def select_paths(source: Source, paths: list[str]) -> list[str]:
    inc = [glob_to_regex(p) for p in source.include]
    exc = [glob_to_regex(p) for p in source.exclude]
    return sorted(p for p in paths if matches_any(p, inc) and not matches_any(p, exc))


def read_blobs(repo: Path, paths: list[str]) -> dict[str, bytes]:
    """用 cat-file --batch 一次讀完。回傳 path → bytes；讀不到的（理論上不會）略過。"""
    if not paths:
        return {}
    stdin = "".join(f"HEAD:{p}\n" for p in paths).encode("utf-8")
    cp = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                        input=stdin, capture_output=True, check=False)
    if cp.returncode != 0:
        raise GitError(f"git cat-file 失敗：{cp.stderr.decode('utf-8', 'replace').strip()}")
    data = cp.stdout
    out: dict[str, bytes] = {}
    pos = 0
    for p in paths:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        out[p] = data[pos:pos + size]
        pos += size + 1                     # 內容後面跟一個 \n
    return out


def load(source: Source) -> tuple[str, list[RawDoc]]:
    """回傳 (HEAD sha, 文件清單)。"""
    sha = head_sha(source.path)
    chosen = select_paths(source, tracked_paths(source.path))
    blobs = read_blobs(source.path, chosen)
    docs: list[RawDoc] = []
    for path in chosen:
        raw = blobs.get(path)
        if raw is None or len(raw) > MAX_FILE_BYTES or b"\x00" in raw[:8192]:
            continue                        # 二進位或超大檔
        text = raw.decode("utf-8", "replace")
        if not text.strip():
            continue
        kind = kind_of(path)
        docs.append(RawDoc(
            source=source.name, path=path, kind=kind,
            title=path.rsplit("/", 1)[-1], text=text, git_sha=sha,
            acl_level=source.acl_doc if kind == "doc" else source.acl_default,
        ))
    return sha, docs
