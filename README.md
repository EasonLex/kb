# kb · 內部知識庫（RAG）

把三個 repo 的文件、程式碼、SQL、設定與 commit 歷史索引進 pgvector，
提供 hybrid 檢索（向量 + 中文全文），之後接上多家 LLM 回答並附出處。

```
TrafficProject / api / bus-airflow（只讀 git HEAD）
   │  python -m kb.ingest          ← Airflow DAG kb_ingest 呼叫這一行（第三階段）
   ▼
遮蔽 → 切段 → hash 增量 → embedding（Voyage）→ Postgres 16 + pgvector
   │
   ▼
python -m kb.search "問題"         ← 第一階段到這裡
FastAPI /api/ask + 網頁            ← 第二階段
```

目前完成：**第一階段**（schema、ingest CLI、檢索 CLI、測試）。

---

## 跟其他 repo 的關係

| | 這裡（kb） | api、TrafficProject、bus-airflow |
|---|---|---|
| 角色 | 索引與問答的程式 | 知識庫的**來源**，只讀不寫 |
| 讀法 | `git ls-tree` + `git cat-file` 讀 HEAD | 工作目錄的未 commit 修改**不會**進來 |

只索引 commit 進去的內容，三個理由：出處連結要指到 GitHub 上正確的 sha；
`.gitignore` 蓋掉的 `.env`、`raw/`、`output/`、`*.ipynb` 根本不在 HEAD 裡，不用另外維護黑名單；
同一個 sha 跑兩次結果一樣，增量判斷才有意義。

第三階段會把這個 repo bind-mount 進 bus-airflow 的容器（`/opt/kb`），
做法跟 bus-airflow 掛主專案到 `/opt/project` 一樣——程式碼不 COPY 進映像檔。

---

## 本機快速開始

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
docker compose -f docker-compose.dev.yml up -d          # pgvector 在 127.0.0.1:5433

cp env.kb.example .env.kb                               # 填 VOYAGE_API_KEY；沒有的話 EMBEDDING_PROVIDER=fake 先試跑
set -a; source .env.kb; set +a

.venv/bin/python -m kb.db apply                         # 建 schema
.venv/bin/python -m kb.ingest --dry-run                 # 看會切出幾段、遮蔽幾處，不寫 DB
.venv/bin/python -m kb.ingest                           # 真的索引（第二次跑 embedding 應該是 0 段）
.venv/bin/python -m kb.search "為什麼 bus_serving 用 Asset 觸發而不是排時間"
.venv/bin/python -m pytest tests -q
```

來源路徑預設是 kb 的上一層（三個 repo 並排的 `TKR102/`），要指到別處用 `KB_SOURCES_DIR` 或 `KB_SRC_*`，見 `env.kb.example`。

---

## 幾個刻意的決定

### 遮蔽在 hash 之前

`kb/mask.py` 對整份文件先遮蔽（含帳密的 URI、Bearer token、常見金鑰格式、字面值的 `password=…`、
Tailscale 100.64/10 的 IP、email、私鑰區塊），然後才算 hash、切段、存 DB。
所以 DB 裡從頭到尾不會出現 secret；遮蔽規則改了，hash 跟著變，自然觸發重算。

規則只遮「看起來像字面值」的秘密：`password=os.environ["X"]`、`${FERNET_KEY}`、`MYSQL_PASSWORD=`（空值）
都保留——它們是設定的形狀，不是秘密本身。`tests/test_mask.py` 用三個 repo 裡真的看到的形狀當樣本。

遮蔽次數會寫進 `kb_ingest_run.stats`；一份文件突然多遮了很多處，值得看一眼。

### 兩層增量

文件層：遮蔽後全文的 sha256 沒變 → 連切都不切。
段落層：文件變了但某段 text 沒變 → 沿用舊 embedding。
`tests/test_ingest_db.py::test_second_run_embeds_nothing` 守著「第二次跑 embedding 是 0 段」。

一份文件一個交易：中途炸掉不會留下「文件更新了但 chunk 是舊的」。

### 中文全文檢索：jieba + bigram，不用 pg_jieba

Postgres 的 parser 把整句中文當一個 token。這裡在 Python 端切好詞、用空白接起來、以 `simple` 設定建 tsvector。
除了 jieba 的詞，還放相鄰兩字的 bigram——「槽位」「壓實」「回填」這類詞庫沒有的專案用語，
jieba 會切成單字，bigram 讓它們還是找得到。識別字保留原樣並把 snake_case 拆開多放一份。
索引端與查詢端用同一個 `segment()`，兩邊的 token 才對得上。

不裝 `pg_jieba`：pgvector 官方映像沒有它，自己編 extension 的維護成本遠高於在 Python 端切。

### Hybrid 用 RRF 合併，commit 乘 0.6

向量 top-20 與全文 top-20 以 Reciprocal Rank Fusion 合併，只看名次不看分數——
cosine 距離和 `ts_rank_cd` 的尺度不同，加權係數只能用猜的。

commit 的 diff 幾乎是檔案內容的複本，同一句話會同時命中「現在的檔案」和「加進那句話的 commit」。
commit 的分數乘 `KIND_WEIGHT["commit"] = 0.6` 往後排：問「現在怎麼做」檔案優先，
問「哪次改的」時 commit 的 message 段仍會浮上來。

### 權限在 SQL 的 WHERE

`acl_level`：1 = viewer（只有 `doc`），2 = engineer（全部）。過濾放在 CTE 的 WHERE，
不是撈回來再篩——篩掉之後補位的段落要重算名次，在 SQL 裡做一次就對了。

### Embedding 固定一家，跟回答用哪家 LLM 無關

向量只能由同一個模型產生才能比對。使用者在前端切換 Claude / OpenAI / Gemini（第二階段）動的是回答層，
不會碰到這一層。`VOYAGE_MODEL` 換了等於換向量空間，下次 ingest 全部重算（`embed_model` 欄位對不上就重算）。

Voyage 不裝官方 SDK：它會拖進 langchain-core、huggingface-hub、tokenizers、numpy、pillow 共 60 幾個套件，
而我們需要的只是一個 POST。`kb/embed.py` 用 `requests` 直接打。

### 切段帶脈絡前綴

每段 text 以 `[DEVLOG.md > 四之十二 … > …]` 或 `[main.py :: travel_time]` 開頭。
embedding 看的只有 text，沒有 metadata——「第 3 段提到 pool」在向量空間裡要能靠自己站住。

Markdown 依 heading 切（fenced code 裡的 `#` 不算）、小段合併、長段依行切視窗；
Python 以 top-level 函式／類別為單位（ast，解析失敗退回一般切法）；
SQL 以 statement 為單位，前導註解歸給該 statement；commit 的 message 自成一段、diff 每個檔案一段。

---

## 測試

```bash
.venv/bin/python -m pytest tests -q            # 有 KB_DATABASE_URL：64 個全跑
env -u KB_DATABASE_URL .venv/bin/python -m pytest tests -q    # 沒 DB：44 跑、20 skip（會說明原因）
```

要 DB 的測試打真的 pgvector，不用 SQLite 或 mock（CI 起 `pgvector/pgvector:pg16` service container）。
每個測試造自己的小 git repo 當來源、用獨一無二的 source 名字、測完刪掉自己的文件，
所以可以對著開發用的 DB 跑而不弄髒它。

把設計決定寫成斷言的幾支：第二次 ingest 不打 embedding、改一個檔只重算變動的段、刪檔連 chunk 一起刪、
secret 不會出現在 DB 任何欄位（含 commit diff）、viewer 看不到程式碼、schema 的 vector 維度跟 `EMBEDDING_DIM` 一致。

---

## 目前索引的規模（2026-10-01，三個 repo 的 HEAD）

| 來源 | 文件 | 段落 | 遮蔽 |
|---|---|---|---|
| trafficproject | 83（檔案 34、commit 49） | 585 | 16 |
| api | 18（檔案 14、commit 4） | 114 | 15 |
| bus-airflow | 20（檔案 12、commit 8） | 139 | 10 |

全部 838 段，一次 full ingest 約 7 秒（不含 embedding API 時間）。

---

## 還沒做的

- **第二階段**：FastAPI `/api/ask`（SSE 串流）、`LLMProvider` 介面（Claude / OpenAI / Gemini 各用官方 SDK）、
  編號來源 `[n]` 的出處機制、vanilla JS 網頁、`kb-service.yml`。
- **第三階段**：bus-airflow 的 `kb_ingest` DAG、`requirements.txt` 加套件、compose 加 `kb-postgres` / `kb-api`、黃金問答集量 recall@5。
- **第四階段**：Claude 上的 agentic 工程模式（`search_kb` / `read_document` / `git_log`）、rerank、push 觸發、feedback。
- jieba 用的是預設（簡體）詞庫，繁體切分略差但可用；要換大詞庫在 `kb/textsearch.py` 一行 `set_dictionary`。
- 向量品質要有 `VOYAGE_API_KEY` 之後才看得出來；目前本機用 `fake` 跑通的是流程，不是召回率。

---

## 授權

MIT。來源 repo 的內容版權各自依其授權。
