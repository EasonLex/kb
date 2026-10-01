from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        cp = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    except FileNotFoundError as e:
        raise GitError("找不到 git 指令") from e
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {' '.join(args)} 在 {repo} 失敗：{e.stderr.decode('utf-8', 'replace').strip()}") from e
    return cp.stdout if binary else cp.stdout.decode("utf-8", "replace")


def head_sha(repo: Path) -> str:
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        raise GitError(f"{repo} 不是 git repo（或路徑不存在）。檢查 KB_SOURCES_DIR / KB_SRC_* 的設定。")
    return run(repo, "rev-parse", "HEAD").strip()


def is_dirty(repo: Path) -> bool:
    return bool(run(repo, "status", "--porcelain").strip())


def glob_to_regex(pattern: str) -> re.Pattern:
    """
    gitignore 風格的 glob：
      *   不跨 /        ?  單一字元（不含 /）
      **/ 任意層目錄    ** 任意字元（含 /）
    """
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(path: str, patterns: list[re.Pattern]) -> bool:
    return any(p.match(path) for p in patterns)
