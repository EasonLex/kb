"""
全文檢索這半邊的斷詞。

Postgres 的 parser 把一整句中文當成一個 token，所以中文先用 jieba 切，
切出來的詞用空白接起來存進 tsvector（'simple' 設定：不做詞幹、不濾英文停用詞）。
查詢端用同一個函式切，再用 OR 接起來——問句通常很長，AND 會什麼都查不到。

程式識別字（baseline_segment、LEAD、heavy_memory）保留原樣，另外把
snake_case 拆開多放一份（baseline、segment），問「segment 那張表」也找得到。

jieba 預設是簡體詞庫，對繁體的切分略差但可用；之後要換繁體大詞庫
在這裡 jieba.set_dictionary 就好，不影響其他地方。
"""

from __future__ import annotations

import re

import jieba

jieba.setLogLevel(60)          # 關掉「Building prefix dict」那行雜訊

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_CJK = re.compile(r"[一-鿿]")

# 問句裡的虛詞。只放最常見的，寧少勿多——濾錯一個關鍵詞比留一個虛詞糟。
STOPWORDS = set("""
的 了 是 在 我 有 和 就 不 都 一 也 很 到 說 要 去 你 會 著 沒有 看 好 自己 這 那 嗎 什麼 怎麼 為什麼 如何
哪 哪裡 哪個 可以 能 請 問 一下 關於 以及 或 與 跟 把 被 讓 從 對 向 給 但 而 因為 所以 如果 還是 怎樣 多少 誰
the a an is are was were be of to in on for and or not with by at from as this that it its
""".split())


_CJK_RUN = re.compile(r"[一-鿿]+")


def segment(text: str) -> list[str]:
    """
    切詞，給 tsvector 用。索引端與查詢端用同一個函式，兩邊的 token 才對得上。

    CJK 的部分給兩種 token：
      jieba 搜尋模式切出的詞（≥2 字）——有詞庫的詞切得準；
      相鄰兩字的 bigram——詞庫沒有的詞（「槽位」「壓實」「回填」這類專案用語）
      jieba 會切成單字，單字太雜不能用，bigram 讓它們還是找得到。
    """
    tokens: list[str] = []
    for ident in _IDENT.findall(text):
        tokens.append(ident.lower())
        parts = [p for p in ident.lower().split("_") if len(p) > 1]
        if len(parts) > 1:
            tokens.extend(parts)
    for run in _CJK_RUN.findall(text):
        for w in jieba.lcut_for_search(run):
            if len(w) >= 2:
                tokens.append(w)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def to_tsv_text(text: str) -> str:
    return " ".join(segment(text))


def to_tsquery_terms(question: str) -> list[str]:
    """查詢端：切詞後去停用詞、去重，回傳給 to_tsquery 用 OR 接的詞。"""
    seen: set[str] = set()
    out: list[str] = []
    for tok in segment(question):
        if tok in STOPWORDS or len(tok) < 2:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def quote_term(term: str) -> str:
    """to_tsquery 的詞要加單引號，裡面的單引號和反斜線要跳脫。"""
    return "'" + term.replace("\\", "\\\\").replace("'", "''") + "'"
