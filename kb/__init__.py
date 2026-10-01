"""
kb —— 內部知識庫的 ingest、檢索與問答。

    python -m kb.db apply          建 schema
    python -m kb.ingest            索引 sources.yaml 裡的來源（增量）
    python -m kb.search "問題"      hybrid 檢索，印出前幾段
    python -m kb.sync              更新 kb 專用的 repo clone（VM 上才開）
"""
