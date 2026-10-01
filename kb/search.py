"""
    python -m kb.search "為什麼 bus_serving 用 Asset 觸發而不是排時間"
    python -m kb.search "LEAD()" --kind code --kind sql --source api
    python -m kb.search "heavy_memory pool" --acl 1        # 用 viewer 的權限看，應該只剩文件

檢索品質的第一線檢查工具：不經過 LLM，直接看前幾段是不是對的來源。
"""

from __future__ import annotations

import argparse
import sys

from . import db
from .config import load_sources
from .embed import get_embedder
from .retrieve import search


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--acl", type=int, default=2, help="1=viewer 只看文件；2=engineer 全部")
    ap.add_argument("--source", action="append")
    ap.add_argument("--kind", action="append", help="doc / code / sql / config / commit")
    ap.add_argument("--prefix", help="路徑前綴")
    ap.add_argument("--full-text", action="store_true", help="印出整段而不是前三行")
    args = ap.parse_args(argv)

    github = {s.name: s.github for s in load_sources()}
    embedder = get_embedder()
    with db.connect() as conn:
        hits = search(conn, embedder, args.question, k=args.k, acl_level=args.acl,
                      sources=args.source, kinds=args.kind, path_prefix=args.prefix)
    if not hits:
        print("沒有結果")
        return 1
    for i, h in enumerate(hits, 1):
        where = h.heading_path or (f"{h.path} :: {h.symbol}" if h.symbol else h.path)
        lines = f" L{h.line_start}-{h.line_end}" if h.line_start else ""
        ranks = f"vec#{h.vec_rank or '-'} txt#{h.text_rank or '-'}"
        print(f"[{i}] {h.score:.4f}  {h.source}/{h.kind}  {where}{lines}  ({ranks})")
        url = h.github_url(github.get(h.source))
        if url:
            print(f"     {url}")
        body = h.text if args.full_text else "\n".join(h.text.splitlines()[1:4])
        for line in body.splitlines():
            print(f"     | {line[:160]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
