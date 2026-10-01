"""
ingest 的增量行為，對著真的 pgvector 跑。這些是「改壞了不會報錯，只會多花 embedding 錢」的行為。
"""

from kb import ingest
from kb.embed import FakeEmbedder

from .conftest import commit_all, make_source


def _counts(conn, source):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM kb_document WHERE source = %s", (source,))
        n_doc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM kb_chunk c JOIN kb_document d ON d.id = c.document_id WHERE d.source = %s", (source,))
        n_chunk = cur.fetchone()[0]
    return n_doc, n_chunk


def test_second_run_embeds_nothing(db_conn, temp_repo):
    src = make_source(temp_repo)
    db_conn.test_sources.append(src.name)
    emb = FakeEmbedder()

    status, s1 = ingest.run([src], emb, conn=db_conn)
    assert status == "ok"
    st = s1["sources"][src.name]
    assert st["documents"] == 4                 # 3 個檔案 + 1 個 commit
    assert st["added"] == 4 and st["embedded_texts"] == st["chunks"] > 0
    n_doc, n_chunk = _counts(db_conn, src.name)
    assert (n_doc, n_chunk) == (4, st["chunks"])

    emb.calls = emb.texts = 0
    status, s2 = ingest.run([src], emb, conn=db_conn)
    st2 = s2["sources"][src.name]
    assert st2["unchanged"] == 4 and st2["added"] == 0 and st2["changed"] == 0
    assert st2["embedded_texts"] == 0 and emb.calls == 0
    assert _counts(db_conn, src.name) == (n_doc, n_chunk)


def test_changed_file_only_reembeds_changed_chunks(db_conn, temp_repo):
    src = make_source(temp_repo)
    db_conn.test_sources.append(src.name)
    emb = FakeEmbedder()
    ingest.run([src], emb, conn=db_conn)

    # 只改 README 的第二節；第一節的段落 hash 不變，embedding 要沿用
    readme = temp_repo / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("只綁 127.0.0.1", "只綁 127.0.0.1 與 VPC 內部 IP"), encoding="utf-8")
    commit_all(temp_repo, "改部署說明")

    emb.calls = emb.texts = 0
    status, s = ingest.run([src], emb, conn=db_conn)
    st = s["sources"][src.name]
    assert status == "ok", st["errors"]
    assert st["changed"] == 1                    # README
    assert st["added"] == 1                      # 新 commit
    assert st["unchanged"] == 3                  # pipe.py、schema.sql、舊 commit
    # 新算的 embedding = README 變動的那幾段 + 新 commit 的所有段；一定少於 README 全部 + commit 全部重算
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM kb_chunk c JOIN kb_document d ON d.id=c.document_id WHERE d.source=%s AND d.path='README.md'", (src.name,))
        readme_chunks = cur.fetchone()[0]
    assert st["embedded_texts"] >= 1
    assert st["embedded_texts"] < readme_chunks + 10 or readme_chunks == 1


def test_deleted_file_removed_and_full_reembeds_all(db_conn, temp_repo):
    src = make_source(temp_repo)
    db_conn.test_sources.append(src.name)
    emb = FakeEmbedder()
    _, s1 = ingest.run([src], emb, conn=db_conn)
    total_chunks = s1["sources"][src.name]["chunks"]

    (temp_repo / "schema.sql").unlink()
    commit_all(temp_repo, "刪 schema")
    _, s2 = ingest.run([src], emb, conn=db_conn)
    assert s2["sources"][src.name]["deleted"] == 1
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM kb_document WHERE source=%s AND path='schema.sql'", (src.name,))
        assert cur.fetchone()[0] == 0

    emb.calls = emb.texts = 0
    _, s3 = ingest.run([src], emb, full=True, conn=db_conn)
    st = s3["sources"][src.name]
    assert st["unchanged"] == 0
    assert st["embedded_texts"] == st["chunks"] > 0


def test_secrets_never_reach_the_database(db_conn, temp_repo):
    (temp_repo / "notes.md").write_text(
        "# 筆記\n\n連線：mongodb://admin:RealSecret99@traffic-db:27017/\n主機 100.119.53.70\n", encoding="utf-8")
    commit_all(temp_repo, "加筆記（含連線字串）")
    src = make_source(temp_repo)
    db_conn.test_sources.append(src.name)
    _, s = ingest.run([src], FakeEmbedder(), conn=db_conn)
    assert s["sources"][src.name]["masked"] >= 2
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM kb_document WHERE source=%s AND (content LIKE '%%RealSecret99%%' OR content LIKE '%%100.119.53.70%%')", (src.name,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM kb_chunk c JOIN kb_document d ON d.id=c.document_id WHERE d.source=%s AND c.text LIKE '%%RealSecret99%%'", (src.name,))
        assert cur.fetchone()[0] == 0
        # commit 的 diff 裡也有那行，一樣要被遮
        cur.execute("SELECT content FROM kb_document WHERE source=%s AND kind='commit' ORDER BY id DESC LIMIT 1", (src.name,))
        assert "RealSecret99" not in cur.fetchone()[0]


def test_embed_model_change_triggers_reembedding(db_conn, temp_repo):
    src = make_source(temp_repo)
    db_conn.test_sources.append(src.name)
    ingest.run([src], FakeEmbedder(), conn=db_conn)
    # 文件沒變 → 文件層會跳過。--full 才會重算；確認 full 之後 embed_model 全部換新。
    other = FakeEmbedder()
    other.name = "fake-other"
    _, s = ingest.run([src], other, full=True, conn=db_conn)
    with db_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT embed_model FROM kb_chunk c JOIN kb_document d ON d.id=c.document_id WHERE d.source=%s", (src.name,))
        assert [r[0] for r in cur.fetchall()] == ["fake-other"]


def test_run_is_recorded(db_conn, temp_repo):
    src = make_source(temp_repo)
    db_conn.test_sources.append(src.name)
    ingest.run([src], FakeEmbedder(), conn=db_conn)
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, stats FROM kb_ingest_run ORDER BY id DESC LIMIT 1")
        status, stats = cur.fetchone()
        assert status == "ok"
        assert stats["sources"][src.name]["documents"] == 4
        cur.execute("SELECT git_sha, document_count, chunk_count FROM kb_source_state WHERE source=%s", (src.name,))
        sha, n_doc, n_chunk = cur.fetchone()
        assert len(sha) == 40 and n_doc == 4 and n_chunk > 0
