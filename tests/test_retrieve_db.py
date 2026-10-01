"""
hybrid 檢索，對著真的 pgvector 跑。用 FakeEmbedder，所以向量那半邊沒有語意——
這裡驗的是全文那半邊的召回、過濾條件、以及兩邊合併不會炸。
"""

from kb import ingest
from kb.embed import FakeEmbedder
from kb.retrieve import search

from .conftest import make_source


def _indexed(db_conn, temp_repo):
    src = make_source(temp_repo)
    db_conn.test_sources.append(src.name)
    emb = FakeEmbedder()
    ingest.run([src], emb, conn=db_conn)
    return src, emb


def test_fulltext_finds_project_jargon(db_conn, temp_repo):
    src, emb = _indexed(db_conn, temp_repo)
    hits = search(db_conn, emb, "heavy_memory 槽位", sources=[src.name], k=5)
    assert hits
    top_text = [h for h in hits if h.text_rank is not None]
    assert top_text, "全文那半邊一個都沒命中"
    assert any(h.path == "README.md" for h in top_text)


def test_identifier_search_hits_code_and_sql(db_conn, temp_repo):
    src, emb = _indexed(db_conn, temp_repo)
    hits = search(db_conn, emb, "baseline_segment", sources=[src.name], kinds=["sql"], k=5)
    assert hits and all(h.kind == "sql" for h in hits)
    assert hits[0].path == "schema.sql" and hits[0].symbol == "baseline_segment"


def test_acl_viewer_only_sees_docs(db_conn, temp_repo):
    src, emb = _indexed(db_conn, temp_repo)
    eng = search(db_conn, emb, "heavy_memory", sources=[src.name], acl_level=2, k=10)
    viewer = search(db_conn, emb, "heavy_memory", sources=[src.name], acl_level=1, k=10)
    assert any(h.kind in ("code", "commit") for h in eng)
    assert viewer and all(h.kind == "doc" for h in viewer)


def test_filters_source_kind_prefix(db_conn, temp_repo):
    src, emb = _indexed(db_conn, temp_repo)
    assert all(h.source == src.name for h in search(db_conn, emb, "壓實", sources=[src.name], k=10))
    assert all(h.kind == "commit" for h in search(db_conn, emb, "壓實", sources=[src.name], kinds=["commit"], k=10))
    assert all(h.path.startswith("commits/") for h in search(db_conn, emb, "壓實", sources=[src.name], path_prefix="commits/", k=10))
    assert search(db_conn, emb, "壓實", sources=["no-such-source"], k=10) == []


def test_query_without_text_terms_still_works(db_conn, temp_repo):
    src, emb = _indexed(db_conn, temp_repo)
    hits = search(db_conn, emb, "的 了 是", sources=[src.name], k=3)      # 全是停用詞 → 只剩向量那半邊
    assert len(hits) == 3 and all(h.text_rank is None for h in hits)


def test_github_url(db_conn, temp_repo):
    src, emb = _indexed(db_conn, temp_repo)
    hits = search(db_conn, emb, "compact 壓實 日檔", sources=[src.name], kinds=["code"], k=3)
    h = next(x for x in hits if x.path == "pipe.py")
    url = h.github_url("example/repo")
    assert url.startswith(f"https://github.com/example/repo/blob/{h.git_sha}/pipe.py#L")
    commit_hit = search(db_conn, emb, "初始", sources=[src.name], kinds=["commit"], k=1)[0]
    assert commit_hit.github_url("example/repo") == f"https://github.com/example/repo/commit/{commit_hit.git_sha}"
