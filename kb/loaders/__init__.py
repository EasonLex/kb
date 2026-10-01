from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RawDoc:
    source: str
    path: str                 # repo 內相對路徑；commit 是 commits/<sha>
    kind: str
    title: str
    text: str                 # 尚未遮蔽
    git_sha: str              # 檔案：repo HEAD；commit：該 commit 自己
    acl_level: int
