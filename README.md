# DocVault API

文件向量資料庫 API。將文件（PDF、Word、PowerPoint、Excel）建置至 PostgreSQL 向量資料庫（pgvector），提供搜尋與 PPT 匯出功能，支援文件機密等級與使用者權限控制。

## 系統需求

- Python 3.10+
- PostgreSQL 16+（含 pgvector extension）
- Ollama / OpenAI API / sentence-transformers（向量化的 embedding 模型）

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
POSTGRES_DB=docvault
POSTGRES_USER=postgres
POSTGRES_PASSWORD=docvault123

# Embedding Provider（三選一）
EMBEDDING_PROVIDER=local        # 建議（已內建sentence-transformers）
# EMBEDDING_PROVIDER=ollama
# EMBEDDING_PROVIDER=openai

# Ollama（EMBEDDING_PROVIDER=ollama 時使用）
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# OpenAI（EMBEDDING_PROVIDER=openai 時使用）
OPENAI_API_KEY=sk-xxx
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

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

## API 端點

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
# 原系統同步使用者權限
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

### 匯出 PPT

```bash
curl -X POST http://localhost:5002/export/ppt \
  -H "Content-Type: application/json" \
  -d '{"result_ids": ["uuid1", "uuid2"], "output_name": "我的報告", "include_images": true}' \
  --output report.pptx
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

Outline 格式欄位：

| 欄位 | 必填 | 說明 |
|------|------|------|
| `title` | ✅ | 簡報標題 |
| `subtitle` | | 副標題 |
| `author` | | 作者 |
| `slides` | ✅ | 投影片陣列 |
| `slides[].title` | ✅ | 投影片標題 |
| `slides[].bullets` | | 項目符號內容 |
| `slides[].notes` | | 備註（Speaker Notes） |

### 管理
|------|------|------|
| GET | `/collections/stats` | 文件/chunks/使用者統計 |
| GET | `/permissions/{user_id}` | 查詢使用者的文件權限 |
| DELETE | `/collection/{file_id}` | 刪除檔案所有 chunks |
| GET | `/admin` | Web 管理介面 |
| GET | `/health` | 健康檢查 |

## 支援格式

| 格式 | 副檔名 | 說明 |
|------|--------|------|
| PDF | `.pdf` | 每段或每頁為一個 chunk |
| Word | `.docx` | 段落 + 表格萃取 |
| PowerPoint | `.pptx` | 每張投影片為一個 chunk |
| Excel | `.xlsx` | 每個工作表為一個 chunk |

## 與 McpServerIIS 整合

McpServerIIS（.NET MCP 伺服器）可透過 HTTP 呼叫本 API：

```
AI Agent
    │  MCP Protocol
    ▼
McpServerIIS（.NET 8）
    │  HTTP（port 5002）
    ▼
DocVault API（本專案）
    │
    └── PostgreSQL（向量 + 權限 + metadata）
```

## 目錄結構

```
doc-vault-api/
├── main.py              # FastAPI 應用程式
├── config.py            # 設定讀取
├── db.py                # PostgreSQL 連線與 schema
├── embeddings.py        # Embedding Provider（工廠模式）
├── vector_store.py      # pgvector 操作
├── ppt_generator.py     # PPT 生成
├── scraper.py           # 網頁爬蟲
├── admin_routes.py      # Web Admin 管理介面
├── parsers/             # 文件解析器
│   ├── pdf_parser.py
│   ├── word_parser.py
│   ├── ppt_parser.py
│   └── excel_parser.py
├── processed/           # 處理過的資料
│   └── images/          # 萃取的圖片
├── output/              # 輸出檔案
├── requirements.txt
└── windows-service-setup.md
```

## Embedding Provider

支援三種模式，透過 `EMBEDDING_PROVIDER` 設定切換：

### 本地模型（預設，離線可用）

```bash
# sentence-transformers 會自動下載模型
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
LOCAL_EMBEDDING_DIM=384
```

### Ollama

```bash
# 安裝 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 下載 embedding 模型
ollama pull nomic-embed-text

# 啟動
ollama serve
```

### OpenAI

```bash
OPENAI_API_KEY=sk-xxx
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
CREATE TABLE document_chunks (
    chunk_id      VARCHAR(64) PRIMARY KEY,
    file_id       VARCHAR(64) REFERENCES documents(file_id) ON DELETE CASCADE,
    page          INTEGER,
    chunk_index   INTEGER,
    text          TEXT,
    image_paths   TEXT[],
    text_vector   VECTOR(384),   -- pgvector 向量
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
