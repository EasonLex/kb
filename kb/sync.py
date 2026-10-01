"""
更新 kb 專用的 repo clone。VM 上 kb_ingest DAG 的第一步。

    python -m kb.sync            只印出會做什麼
    KB_SYNC_ENABLED=1 python -m kb.sync

★ 本機不要開 KB_SYNC_ENABLED：sources.yaml 預設指到你正在工作的三個 repo，
  pull 它們不是這個工具該做的事。★

安全閥：目標目錄有未 commit 的修改就拒絕 pull 並以非零結束——
那代表指錯目錄了（指到工作中的 checkout），不是該用 --force 蓋掉的情況。
"""

from __future__ import annotations

import os
import subprocess
import sys

from .config import load_sources
from .loaders.gitutil import GitError, is_dirty


def main() -> int:
    enabled = os.getenv("KB_SYNC_ENABLED", "0") == "1"
    rc = 0
    for s in load_sources():
        if not s.repo:
            print(f"[{s.name}] 沒有 repo URL，略過")
            continue
        if not s.path.exists():
            cmd = ["git", "clone", "--quiet", s.repo, str(s.path)]
        else:
            try:
                if is_dirty(s.path):
                    print(f"[{s.name}] {s.path} 有未 commit 的修改，拒絕 pull——這是不是你工作中的 checkout？")
                    rc = 1
                    continue
            except GitError as e:
                print(f"[{s.name}] {e}")
                rc = 1
                continue
            cmd = ["git", "-C", str(s.path), "pull", "--ff-only", "--quiet"]
        print(f"[{s.name}] {' '.join(cmd)}" + ("" if enabled else "   （未執行：KB_SYNC_ENABLED != 1）"))
        if enabled:
            cp = subprocess.run(cmd, capture_output=True, text=True)
            if cp.returncode != 0:
                print(f"[{s.name}] 失敗：{cp.stderr.strip()}")
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
