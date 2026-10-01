"""
Hybrid 檢索：向量 top-N ∪ 全文 top-N → Reciprocal Rank Fusion → 前 k 段。

RRF：score = Σ 1 / (60 + rank)。不用把兩種分數正規化後加權——
cosine 距離和 ts_rank_cd 的尺度完全不同，加權係數只能用猜的；
RRF 只看名次，兩邊都排前面的段落自然浮上來。60 是論文裡的常數，不用調。

權限過濾放在 SQL 的 WHERE，不是撈回來再篩：篩掉之後補位的段落要重算名次，
在 SQL 裡做一次就對了。

commit 的 diff 幾乎是檔案內容的複本（README 改一段，diff 裡就有那一段），
所以同一句話會同時命中「現在的檔案」和「加進那句話的 commit」。
兩個都留，但 commit 乘 KIND_WEIGHT 往後排——問「現在怎麼做」時檔案優先，
問「哪次改的」時 commit 的 message 段落仍會因為 diff 以外的詞而浮上來。
"""


from __future__ import annotations

from dataclasses import dataclass

import psycopg
from pgvector import Vector

from .embed import Embedder
from .textsearch import quote_term, to_tsquery_terms

RRF_K = 60
KIND_WEIGHT = {"commit": 0.6}


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    source: str
    path: str
    kind: str
    title: str | None
    heading_path: str | None
    symbol: str | None
    line_start: int | None
    line_end: int | None
    text: str
    git_sha: str | None
    score: float
    vec_rank: int | None
    text_rank: int | None

    def github_url(self, github_repo: str | None) -> str | None:
        if not github_repo or not self.git_sha:
            return None
        if self.kind == "commit":
            return f"https://github.com/{github_repo}/commit/{self.git_sha}"
        url = f"https://github.com/{github_repo}/blob/{self.git_sha}/{self.path}"
        if self.line_start:
            url += f"#L{self.line_start}" + (f"-L{self.line_end}" if self.line_end and self.line_end != self.line_start else "")
        return url


def _filters(acl_level: int, sources: list[str] | None, kinds: list[str] | None, path_prefix: str | None):
    where = ["d.acl_level <= %(acl)s"]
    params: dict = {"acl": acl_level}
    if sources:
        where.append("d.source = ANY(%(sources)s)")
        params["sources"] = sources
    if kinds:
        where.append("d.kind = ANY(%(kinds)s)")
        params["kinds"] = kinds
    if path_prefix:
        where.append("d.path LIKE %(prefix)s")
        params["prefix"] = path_prefix.replace("%", "\\%") + "%"
    return " AND ".join(where), params


def search(conn: psycopg.Connection, embedder: Embedder, question: str, *, k: int = 8,
           acl_level: int = 2, sources: list[str] | None = None, kinds: list[str] | None = None,
           path_prefix: str | None = None, k_each: int = 20) -> list[Hit]:
    where, params = _filters(acl_level, sources, kinds, path_prefix)
    qvec = embedder.embed([question], "query")[0]
    terms = to_tsquery_terms(question)
    params.update({"qvec": Vector(qvec), "k_each": k_each, "k": k, "rrf": RRF_K})

    vec_cte = f"""
        vec AS (
            SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> %(qvec)s) AS rnk
            FROM kb_chunk c JOIN kb_document d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL AND {where}
            ORDER BY c.embedding <=> %(qvec)s
            LIMIT %(k_each)s
        )"""
    if terms:
        params["tsq"] = " | ".join(quote_term(t) for t in terms)
        txt_cte = f""",
        txt AS (
            SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC) AS rnk
            FROM kb_chunk c JOIN kb_document d ON d.id = c.document_id,
                 to_tsquery('simple', %(tsq)s) q
            WHERE c.tsv @@ q AND {where}
            ORDER BY ts_rank_cd(c.tsv, q) DESC
            LIMIT %(k_each)s
        )"""
        union = "SELECT id, rnk, 'vec' AS src FROM vec UNION ALL SELECT id, rnk, 'txt' FROM txt"
    else:
        txt_cte = ""
        union = "SELECT id, rnk, 'vec' AS src FROM vec"

    params["commit_w"] = KIND_WEIGHT["commit"]
    sql = f"""
        WITH {vec_cte}{txt_cte},
        fused AS (
            SELECT id,
                   SUM(1.0 / (%(rrf)s + rnk)) AS score,
                   MIN(CASE WHEN src = 'vec' THEN rnk END) AS vec_rank,
                   MIN(CASE WHEN src = 'txt' THEN rnk END) AS text_rank
            FROM ({union}) u
            GROUP BY id
        ),
        weighted AS (
            SELECT f.id, f.vec_rank, f.text_rank,
                   f.score * CASE WHEN d.kind = 'commit' THEN %(commit_w)s ELSE 1.0 END AS score
            FROM fused f JOIN kb_chunk c ON c.id = f.id JOIN kb_document d ON d.id = c.document_id
            ORDER BY score DESC
            LIMIT %(k)s
        )
        SELECT c.id, c.document_id, d.source, d.path, d.kind, d.title, c.heading_path, c.symbol,
               c.line_start, c.line_end, c.text, d.git_sha, w.score, w.vec_rank, w.text_rank
        FROM weighted w JOIN kb_chunk c ON c.id = w.id JOIN kb_document d ON d.id = c.document_id
        ORDER BY w.score DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [Hit(chunk_id=r[0], document_id=r[1], source=r[2], path=r[3], kind=r[4], title=r[5],
                heading_path=r[6], symbol=r[7], line_start=r[8], line_end=r[9], text=r[10],
                git_sha=r[11], score=float(r[12]), vec_rank=r[13], text_rank=r[14]) for r in rows]
