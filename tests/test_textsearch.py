from kb.textsearch import quote_term, segment, to_tsquery_terms


def test_identifiers_kept_and_split():
    toks = segment("baseline_segment 用 LEAD() 不用 seq+1")
    assert "baseline_segment" in toks and "baseline" in toks and "segment" in toks
    assert "lead" in toks and "seq" in toks


def test_cjk_words_and_bigrams():
    toks = segment("heavy_memory pool 槽位只有一個")
    # 「槽位」不在 jieba 詞庫，靠 bigram 還是找得到
    assert "槽位" in toks
    assert "一個" in toks


def test_query_terms_drop_stopwords_and_dedupe():
    terms = to_tsquery_terms("為什麼 bus_serving 要用 Asset 觸發，而不是排時間？bus_serving")
    assert "為什麼" not in terms and "而" not in terms
    assert terms.count("bus_serving") == 1
    assert "asset" in terms and "觸發" in terms


def test_quote_term_escapes():
    assert quote_term("it's") == "'it''s'"
    assert quote_term("a\\b") == "'a\\\\b'"


def test_index_and_query_use_same_tokens():
    doc = "壓實把 staging 的小檔合成 silver 日檔"
    q = "壓實 staging"
    assert set(to_tsquery_terms(q)) <= set(segment(doc))
