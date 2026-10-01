-- =====================================================================
-- 知識庫 schema（Postgres 16 + pgvector）
--
--   python -m kb.db apply        ← 用 psycopg 套用，不依賴 psql CLI（跟 api/apply_sql.py 同一個理由）
--
-- 全部 IF NOT EXISTS，重跑安全。改結構用新增 ALTER，不改既有 CREATE。
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------
-- 文件 = 來源 repo 裡的一個檔案，或一個 commit。
--
-- content 存的是「遮蔽後」的全文。遮蔽在 hash 之前做，所以 DB 裡從頭到尾
-- 不會出現 secret；遮蔽規則改了，hash 跟著變，自然觸發重算。
-- 全文留在這裡是給第四階段工程模式的 read_document 工具用的：
-- 它讀 DB 裡的快照，不碰即時檔案系統。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_document (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT        NOT NULL,           -- sources.yaml 的 name
    path            TEXT        NOT NULL,           -- repo 內相對路徑；commit 是 commits/<sha>
    kind            TEXT        NOT NULL,           -- doc / code / sql / config / commit
    title           TEXT,
    acl_level       SMALLINT    NOT NULL,           -- 1 viewer（文件）、2 engineer（程式、SQL、設定、commit）
    content_hash    TEXT        NOT NULL,           -- 遮蔽後全文的 sha256
    git_sha         TEXT,                           -- 索引當下 repo 的 HEAD；做 GitHub 連結用
    size_chars      INTEGER     NOT NULL,
    masked_count    INTEGER     NOT NULL DEFAULT 0, -- 遮蔽了幾處；ingest 的 verify 會看它
    content         TEXT        NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_run_id BIGINT,
    UNIQUE (source, path)
);

-- ---------------------------------------------------------------------
-- 段落。embedding 與 tsv 都是「應用程式算好寫進來」，不用 GENERATED：
--   embedding 來自外部 API；
--   tsv 要先用 jieba 斷詞——Postgres 內建 parser 不會切中文，直接
--   to_tsvector 等於沒有關鍵字檢索。斷好的詞用空白接起來、以 'simple' 設定建索引。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_chunk (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT      NOT NULL REFERENCES kb_document(id) ON DELETE CASCADE,
    ord             INTEGER     NOT NULL,           -- 在文件內的順序
    heading_path    TEXT,                           -- Markdown：「DEVLOG.md > 四之12 … > …」
    symbol          TEXT,                           -- Python：函式／類別名；SQL：第一個物件名
    line_start      INTEGER,
    line_end        INTEGER,
    text            TEXT        NOT NULL,           -- 送去 embedding 的內容（含 heading 前綴）
    chunk_hash      TEXT        NOT NULL,           -- text 的 sha256；相同 hash 直接沿用舊 embedding
    n_chars         INTEGER     NOT NULL,
    embedding       vector(1024),                   -- 跟 EMBEDDING_DIM 一致；換維度要重建這張表
    embed_model     TEXT,                           -- 產生這個向量的模型；模型換了就重算
    tsv             tsvector,
    UNIQUE (document_id, ord)
);

-- HNSW 不用 IVFFlat：語料只有幾千段，HNSW 建索引快、查詢不用先訓練、
-- 而且資料增減時召回率不會像 IVFFlat 那樣悄悄變差。
CREATE INDEX IF NOT EXISTS kb_chunk_embedding_hnsw ON kb_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunk_tsv_gin        ON kb_chunk USING gin (tsv);
CREATE INDEX IF NOT EXISTS kb_chunk_document_idx   ON kb_chunk (document_id);
CREATE INDEX IF NOT EXISTS kb_document_source_idx  ON kb_document (source, kind, acl_level);

-- ---------------------------------------------------------------------
-- 每次 ingest 一列。stats 是 JSON：各來源的文件數、新增／變更／刪除、
-- embedding 呼叫次數、遮蔽次數。第二次跑同一份語料，embedded 應該是 0。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_ingest_run (
    id              BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running',  -- running / ok / failed
    full_rebuild    BOOLEAN     NOT NULL DEFAULT false,
    stats           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT
);

-- 每個來源最後一次成功索引到哪個 commit
CREATE TABLE IF NOT EXISTS kb_source_state (
    source          TEXT        PRIMARY KEY,
    git_sha         TEXT,
    document_count  INTEGER,
    chunk_count     INTEGER,
    indexed_at      TIMESTAMPTZ,
    run_id          BIGINT
);

-- ---------------------------------------------------------------------
-- 問答記錄（第二階段 /api/ask 開始寫）。留在這裡是因為黃金問答集和
-- 「同一題三家模型答得怎樣」的比較都要靠它。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_query_log (
    id              BIGSERIAL PRIMARY KEY,
    asked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    role            TEXT,
    mode            TEXT,                           -- kb / eng
    provider        TEXT,
    model           TEXT,
    question        TEXT        NOT NULL,
    retrieved_chunk_ids BIGINT[],
    cited_chunk_ids BIGINT[],
    answer          TEXT,
    latency_ms      INTEGER,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    feedback        SMALLINT                        -- +1 / -1 / NULL
);
