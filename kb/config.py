"""
設定只從環境變數來（跟 api 一樣），來源清單從 sources.yaml 來。

sources.yaml 裡的路徑可以寫 ${VAR} 或 ${VAR:-預設值}；os.path.expandvars
不支援 :- 語法，所以自己展開。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KB_ROOT = Path(__file__).resolve().parent.parent          # kb repo 的根目錄
DEFAULT_SOURCES_FILE = KB_ROOT / "kb" / "sources.yaml"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def expand(value: str, env: dict | None = None) -> str:
    """
    展開 ${VAR} 與 ${VAR:-default}。沒設（或設成空字串）且沒預設值就展開成空字串。
    預設值裡可以再有 ${...}（例如 ${KB_SRC_API:-${KB_SOURCES_DIR}/api}），所以不用 regex，
    自己數大括號深度找到配對的 }。
    """
    env = os.environ if env is None else env
    out: list[str] = []
    i = 0
    while i < len(value):
        if not value.startswith("${", i):
            out.append(value[i])
            i += 1
            continue
        depth, j = 0, i
        while j < len(value):
            if value.startswith("${", j):
                depth += 1
                j += 2
                continue
            if value[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            raise ValueError(f"大括號沒配對：{value!r}")
        inner = value[i + 2:j]
        m = _NAME.match(inner)
        if not m:
            raise ValueError(f"不合法的變數引用：{value[i:j + 1]!r}")
        name = m.group(0)
        rest = inner[m.end():]
        default: str | None
        if rest == "":
            default = None
        elif rest.startswith(":-"):
            default = rest[2:]
        else:
            raise ValueError(f"不合法的變數引用：{value[i:j + 1]!r}")
        val = env.get(name)
        if val:
            out.append(val)
        elif default is not None:
            out.append(expand(default, env))
        i = j + 1
    return "".join(out)


def database_url() -> str:
    url = os.getenv("KB_DATABASE_URL")
    if not url:
        raise RuntimeError("KB_DATABASE_URL 沒設。本機：docker compose -f docker-compose.dev.yml up -d，"
                           "然後 export KB_DATABASE_URL=postgresql://kb:kb-dev-only@127.0.0.1:5433/kb")
    return url


def embedding_dim() -> int:
    return int(os.getenv("EMBEDDING_DIM", "1024"))


@dataclass
class Source:
    name: str
    path: Path
    repo: str | None = None             # sync 用的 clone URL
    github: str | None = None           # owner/repo，做出處連結用
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    commits: bool = True
    diff_max_lines: int = 400           # 每個 commit 的 diff 最多索引幾行
    acl_doc: int = 1
    acl_default: int = 2


def load_sources(path: Path | None = None) -> list[Source]:
    path = path or Path(os.getenv("KB_SOURCES_FILE") or DEFAULT_SOURCES_FILE)
    env = dict(os.environ)
    # KB_SOURCES_DIR 留空 → kb 的上一層（本機三個 repo 並排的那個目錄）
    env.setdefault("KB_SOURCES_DIR", "")
    if not env["KB_SOURCES_DIR"]:
        env["KB_SOURCES_DIR"] = str(KB_ROOT.parent)

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[Source] = []
    for item in raw.get("sources", []):
        for key in ("name", "path", "include"):
            if key not in item:
                raise ValueError(f"sources.yaml：來源缺少 {key!r}：{item}")
        commits_cfg = item.get("commits", True)
        acl = item.get("acl", {}) or {}
        out.append(Source(
            name=item["name"],
            path=Path(expand(str(item["path"]), env)).expanduser(),
            repo=item.get("repo"),
            github=item.get("github"),
            include=list(item["include"]),
            exclude=list(item.get("exclude", [])),
            commits=bool(commits_cfg) if not isinstance(commits_cfg, dict) else True,
            diff_max_lines=int(commits_cfg.get("diff_max_lines", 400)) if isinstance(commits_cfg, dict) else 400,
            acl_doc=int(acl.get("doc", 1)),
            acl_default=int(acl.get("default", 2)),
        ))
    names = [s.name for s in out]
    if len(names) != len(set(names)):
        raise ValueError(f"sources.yaml：來源名稱重複：{names}")
    return out
