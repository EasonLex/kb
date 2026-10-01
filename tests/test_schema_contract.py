"""
schema 的契約：程式假設的欄位與索引存在、向量維度對、擴充套件在。
跟 api 的 test_schema_contract 同一個想法——這支也可以對著生產 DB 跑。
"""

import pytest

REQUIRED = {
    "kb_document": {"id", "source", "path", "kind", "acl_level", "content_hash", "git_sha", "content", "last_seen_run_id"},
    "kb_chunk": {"id", "document_id", "ord", "heading_path", "symbol", "line_start", "line_end", "text",
                 "chunk_hash", "embedding", "embed_model", "tsv"},
    "kb_ingest_run": {"id", "status", "stats", "full_rebuild"},
    "kb_source_state": {"source", "git_sha", "run_id"},
    "kb_query_log": {"id", "question", "provider", "model", "retrieved_chunk_ids", "feedback"},
}


@pytest.fixture(scope="module")
def columns(db_conn_module):
    with db_conn_module.cursor() as cur:
        cur.execute("SELECT table_name, column_name, data_type, udt_name FROM information_schema.columns WHERE table_schema='public'")
        out: dict[str, dict[str, tuple[str, str]]] = {}
        for t, c, dt, udt in cur.fetchall():
            out.setdefault(t, {})[c] = (dt, udt)
    return out


@pytest.fixture(scope="module")
def db_conn_module():
    import os
    if not os.getenv("KB_DATABASE_URL"):
        pytest.skip("KB_DATABASE_URL 沒設")
    from kb import db
    conn = db.connect(autocommit=True)
    db.apply_schema(conn)
    yield conn
    conn.close()


@pytest.mark.parametrize("table", sorted(REQUIRED))
def test_required_columns_present(columns, table):
    assert table in columns, f"缺表 {table}"
    missing = REQUIRED[table] - set(columns[table])
    assert not missing, f"{table} 缺欄位 {sorted(missing)}"


def test_vector_extension_and_dimension(db_conn_module):
    with db_conn_module.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
        assert cur.fetchone() is not None
        cur.execute("""SELECT atttypmod FROM pg_attribute
                       WHERE attrelid = 'kb_chunk'::regclass AND attname = 'embedding'""")
        dim = cur.fetchone()[0]
    import os
    assert dim == int(os.getenv("EMBEDDING_DIM", "1024")), "schema 的 vector(N) 跟 EMBEDDING_DIM 不一致"


def test_indexes_exist(db_conn_module):
    with db_conn_module.cursor() as cur:
        cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='kb_chunk'")
        defs = {name: d for name, d in cur.fetchall()}
    assert any("hnsw" in d and "vector_cosine_ops" in d for d in defs.values()), "沒有 HNSW cosine 索引"
    assert any("gin" in d.lower() and "tsv" in d for d in defs.values()), "沒有 tsv 的 GIN 索引"


def test_schema_is_rerunnable(db_conn_module):
    from kb import db
    db.apply_schema(db_conn_module)        # 第二次套用不該炸
    db.apply_schema(db_conn_module)
