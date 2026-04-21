# DocVault API

文件向量資料庫 API。將文件（PDF、Word、PowerPoint、Excel）建置至 PostgreSQL 向量資料庫（pgvector），提供搜尋、爬蟲、與 Office 文件生成功能，支援文件機密等級與使用者權限控制。

## 系統需求

- Python 3.10+
- PostgreSQL 16+（含 pgvector extension）
- sentence-transformers / Ollama / LM Studio / OpenAI（向量化模型）

## 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 啟動 PostgreSQL

```bash
docker run -d --name docvault-postgres \
  -p 5432:5432 \
  -e POSTGRES_DB=docvault \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=docvault123 \
  pgvector/pgvector:pg16
```

### 3. 設定

建立 `.env` 檔案：

```bash
# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=***

# Embedding Provider（三選一）
EMBEDDING_PROVIDER=openai        # 建議（LM Studio 或 OpenAI API）
# EMBEDDING_PROVIDER=ollama
# EMBEDDING_PROVIDER=local

# LM Studio / OpenAI（EMBEDDING_PROVIDER=openai 時使用）
OPENAI_API_KEY=lm-studio         # LM Studio 可用任意值，Base URL 對即可
OPENAI_BASE_URL=http://26.26.26.1:1234/v1
OPENAI_EMBEDDING_MODEL=text-embedding-qwen3-8b-text-embedding

# Ollama（EMBEDDING_PROVIDER=ollama 時使用）
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# 本地模型（EMBEDDING_PROVIDER=local 時使用）
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
LOCAL_EMBEDDING_DIM=384

# 服務
API_HOST=0.0.0.0
API_PORT=5002
```

### 4. 啟動

```bash
python main.py
```

服務啟動於 `http://localhost:5002`

### 5. 驗證

```bash
curl http://localhost:5002/health
```

## API 端點總覽

|| 方法 | 路徑 | 說明 |
|------|------|------|------|
|| GET | `/health` | 健康檢查 |
|| POST | `/ingest` | 上傳檔案入庫 |
|| POST | `/search` | 向量搜尋文件 |
|| POST | `/permissions/sync` | 同步使用者權限 |
|| GET | `/permissions/{user_id}` | 查詢使用者權限 |
|| POST | `/export/ppt` | 將搜尋結果匯出為 PPT |
|| POST | `/scrape` | 爬取公開網頁 |
|| POST | `/generate/ppt` | 從大綱生成 PPT |
|| POST | `/generate/pdf` | 從大綱生成 PDF |
|| POST | `/export/pdf` | 將搜尋結果匯出為 PDF |
|| POST | `/generate/excel` | 從資料生成 Excel |
|| POST | `/generate/word` | 從大綱生成 Word |
|| GET | `/collections/stats` | 統計資訊 |
|| DELETE | `/collection/{file_id}` | 刪除檔案 |
|| GET | `/admin` | Web 管理介面 |

## 功能說明

### 入庫

```bash
# 單一檔案（公開文件）
curl -X POST http://localhost:5002/ingest \
  -F "file=@文件.pdf"

# 指定機密等級
curl -X POST http://localhost:5002/ingest \
  -F "file=@機密文件.pdf" \
  -F "metadata={\"confidentiality\":\"機密\",\"department\":\"HR\"}"
```

### 搜尋

```bash
# 所有人可見的搜尋
curl -X POST http://localhost:5002/search \
  -H "Content-Type: application/json" \
  -d '{"query": "特休假的規定", "top_k": 10}'

# 特定使用者的權限過濾搜尋
curl -X POST http://localhost:5002/search \
  -H "Content-Type: application/json" \
  -d '{"query": "特休假的規定", "user_id": "user_001", "top_k": 10}'
```

### 權限同步

```bash
curl -X POST http://localhost:5002/permissions/sync \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_001",
    "documents": [
      {"document_id": "doc_abc", "access_level": "read"},
      {"document_id": "doc_xyz", "access_level": "read"}
    ]
  }'
```

### 爬蟲

```bash
# 基本爬取
curl -X POST http://localhost:5002/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://news.ycombinator.com/"}'

# 指定 CSS 選擇器，只取特定區塊
curl -X POST http://localhost:5002/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "selector": "article.content"}'

# 同時回傳連結清單
curl -X POST http://localhost:5002/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://news.ycombinator.com/", "extract_links": true}'
```

回應格式：

```json
{
  "url": "https://...",
  "status_code": 200,
  "title": "頁面標題",
  "text": "純文字內容...",
  "links": [{"text": "連結文字", "href": "https://..."}]
}
```

### 生成 PPT（從大綱）

```bash
curl -X POST http://localhost:5002/generate/ppt \
  -H "Content-Type: application/json" \
  -d '{
    "outline": {
      "title": "Q2 業務報告",
      "subtitle": "2025 年第二季",
      "author": "業務部",
      "slides": [
        {"title": "季度回顧", "bullets": ["營收成長 20%", "新客戶 15 家"]},
        {"title": "下季目標", "bullets": ["營收目標 +30%", "拓展東區市場"]}
      ]
    },
    "output_name": "q2_report"
  }' \
  --output q2_report.pptx
```

outline 欄位：

|| 欄位 | 必填 | 說明 |
||------|------|------|
|| `title` | ✅ | 簡報標題 |
|| `subtitle` | | 副標題 |
|| `author` | | 作者 |
|| `slides` | ✅ | 投影片陣列 |
|| `slides[].title` | ✅ | 投影片標題 |
|| `slides[].bullets` | | 項目符號內容 |
|| `slides[].notes` | | 備註（Speaker Notes） |

### 生成 PDF（從大綱）

```bash
curl -X POST http://localhost:5002/generate/pdf \
  -H "Content-Type: application/json" \
  -d '{
    "outline": {
      "title": "人資年度報告",
      "subtitle": "2025 年度",
      "author": "人資部",
      "sections": [
        {
          "heading": "一、員工概況",
          "content": ["目前在職員工共 128 人", "新進員工 15 人", "離職人數 8 人"]
        },
        {
          "heading": "二、數據統計",
          "table": [["項目", "數值"], ["在職人數", "128"], ["新進", "15"]]
        }
      ]
    },
    "output_name": "hr_report"
  }' \
  --output hr_report.pdf
```

outline 欄位：

|| 欄位 | 必填 | 說明 |
||------|------|------|
|| `title` | ✅ | 報告標題 |
|| `subtitle` | | 副標題 |
|| `author` | | 作者 |
|| `date` | | 日期（預設今天） |
|| `sections` | ✅ | 章節陣列 |
|| `sections[].heading` | ✅ | 章節標題 |
|| `sections[].content` | | 內文（str 或 list of str） |
|| `sections[].table` | | 表格（list of list） |

### 匯出 PDF（從文件 chunks）

```bash
curl -X POST http://localhost:5002/export/pdf \
  -H "Content-Type: application/json" \
  -d '{"result_ids": ["uuid1", "uuid2"], "output_name": "document_export"}' \
  --output document_export.pdf
```

### 生成 Excel

```bash
curl -X POST http://localhost:5002/generate/excel \
  -H "Content-Type: application/json" \
  -d '{
    "sheets": [
      {
        "name": "員工統計",
        "headers": ["姓名", "部門", "在職年資"],
        "data": [
          ["王小明", "業務部", 3],
          ["李小華", "人資部", 5]
        ]
      },
      {
        "name": "費用明細",
        "headers": ["項目", "金額", "日期"],
        "data": [
          ["交通費", 1200, "2025/01/05"],
          ["交際費", 3500, "2025/01/08"]
        ]
      }
    ],
    "output_name": "monthly_report",
    "title": "2025 年 1 月份統計報告"
  }' \
  --output monthly_report.xlsx
```

sheets 欄位：

|| 欄位 | 必填 | 說明 |
||------|------|------|
|| `name` | ✅ | 工作表名稱 |
|| `headers` | ✅ | 欄位標題（str 或 dict：label/width/align） |
|| `data` | ✅ | 資料列（list 或 dict 以 headers 為 key） |

### 生成 Word（從大綱）

```bash
curl -X POST http://localhost:5002/generate/word \
  -H "Content-Type: application/json" \
  -d '{
    "outline": {
      "title": "人資年度報告",
      "subtitle": "2025 年度",
      "author": "人資部",
      "sections": [
        {
          "heading": "一、員工概況",
          "content": "目前在職員工共 128 人，新進員工 15 人，離職人數 8 人。"
        },
        {
          "heading": "二、各部門人數",
          "table": [["部門", "人數"], ["業務部", "45"], ["人資部", "8"]]
        }
      ]
    },
    "output_name": "hr_report"
  }' \
  --output hr_report.docx
```

outline 欄位：

|| 欄位 | 必填 | 說明 |
||------|------|------|
|| `title` | ✅ | 文件標題 |
|| `subtitle` | | 副標題 |
|| `author` | | 作者 |
|| `date` | | 日期（預設今天） |
|| `sections` | ✅ | 章節陣列 |
|| `sections[].heading` | ✅ | 章節標題 |
|| `sections[].content` | | 內文（str 或 list of str） |
|| `sections[].table` | | 表格（list of list） |

### 匯出 PPT（從文件 chunks）

```bash
curl -X POST http://localhost:5002/export/ppt \
  -H "Content-Type: application/json" \
  -d '{"result_ids": ["uuid1", "uuid2"], "output_name": "我的報告", "include_images": true}' \
  --output report.pptx
```

## 支援格式

|| 格式 | 副檔名 | 說明 |
||------|--------|------|
|| PDF | `.pdf` | 每段或每頁為一個 chunk |
|| Word | `.docx` | 段落 + 表格萃取 |
|| PowerPoint | `.pptx` | 每張投影片為一個 chunk |
|| Excel | `.xlsx` | 每個工作表為一個 chunk |

## 與 McpServerIIS 整合

```
AI Agent
    │  MCP Protocol
    ▼
McpServerIIS（.NET 8，IIS 部署）
    │  HTTP（port 5002）
    ▼
DocVault API（本專案）
    │
    ├── 文件解析（PDF / Word / PPT / Excel）
    ├── 向量儲存（PostgreSQL + pgvector）
    ├── 爬蟲（httpx + BeautifulSoup）
    └── 文件生成（PPT / PDF / Excel / Word）
```

## 目錄結構

```
doc-vault-api/
├── main.py              # FastAPI 應用程式
├── config.py            # 設定讀取
├── db.py                # PostgreSQL 連線與 schema
├── embeddings.py        # Embedding Provider（工廠模式）
├── vector_store.py      # pgvector 操作
├── scraper.py          # 網頁爬蟲
├── ppt_generator.py    # PPT 生成
├── pdf_generator.py    # PDF 生成
├── excel_generator.py  # Excel 生成
├── word_generator.py   # Word 生成
├── admin_routes.py      # Web Admin 管理介面
├── parsers/             # 文件解析器
│   ├── pdf_parser.py
│   ├── word_parser.py
│   ├── ppt_parser.py
│   └── excel_parser.py
├── processed/           # 處理過的資料
│   └── images/         # 萃取的圖片
├── output/             # 輸出檔案
├── requirements.txt
└── windows-service-setup.md
```

## Embedding Provider

支援三種模式，透過 `EMBEDDING_PROVIDER` 設定切換。向量維度自動偵測，支援任意維度的 embedding 模型。

### LM Studio / OpenAI（預設）

```bash
# LM Studio（建議，本地 GPU 加速）
# 下載模型後，在 LM Studio 中載入並啟動 Server（預設 port 1234）
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=lm-studio         # LM Studio 不驗證 API key，任意值即可
OPENAI_BASE_URL=http://26.26.26.1:1234/v1
OPENAI_EMBEDDING_MODEL=text-embedding-qwen3-8b-text-embedding

# OpenAI API
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-***
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

### 本地模型（離線可用）

```bash
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
LOCAL_EMBEDDING_DIM=384
```

```bash
# 安裝 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 下載 embedding 模型
ollama pull nomic-embed-text

# 啟動
ollama serve
```

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

### OpenAI

```bash
OPENAI_API_KEY=***
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

## 資料庫 Schema

```sql
-- 文件主表
CREATE TABLE documents (
    file_id         VARCHAR(64) PRIMARY KEY,
    filename        VARCHAR(256),
    file_type       VARCHAR(16),
    confidentiality VARCHAR(32),  -- 公開 / 內部 / 機密 / 極機密
    department      VARCHAR(64),
    metadata_json   TEXT,
    created_at      TIMESTAMP
);

-- Chunks 表（含向量）
-- 向量維度由首次 init_db 時自動偵測（可用任何維度的 embedding 模型）
CREATE TABLE document_chunks (
    chunk_id      VARCHAR(64) PRIMARY KEY,
    file_id       VARCHAR(64) REFERENCES documents(file_id) ON DELETE CASCADE,
    page          INTEGER,
    chunk_index   INTEGER,
    text          TEXT,
    image_paths   TEXT[],
    text_vector   VECTOR,       -- 維度自動偵測（支援 HNSW / IVFFlat 索引）
    created_at    TIMESTAMP
);

-- 使用者權限表
CREATE TABLE document_permissions (
    id           SERIAL PRIMARY KEY,
    user_id      VARCHAR(64),
    document_id  VARCHAR(64) REFERENCES documents(file_id) ON DELETE CASCADE,
    access_level VARCHAR(16),   -- read / write
    granted_at   TIMESTAMP,
    UNIQUE(user_id, document_id)
);
```

## Windows Service 安裝

使用 NSSM 將 DocVault API 安裝為 Windows Service。詳見 `windows-service-setup.md`。
