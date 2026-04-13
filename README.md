# DocVault API

文件向量資料庫 API。將文件（PDF、Word、PowerPoint、Excel）建置至 Milvus 向量資料庫，提供搜尋與 PPT 匯出功能。

## 系統需求

- Python 3.10+
- Milvus（建議 v2.4+，運行於 localhost:19530）
- Ollama / OpenAI API / sentence-transformers（向量化的 embedding 模型）

## 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 設定

建立 `.env` 檔案：

```bash
# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530

# Embedding Provider（三選一）
EMBEDDING_PROVIDER=ollama        # 建議
# EMBEDDING_PROVIDER=openai
# EMBEDDING_PROVIDER=local

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

### 3. 啟動

```bash
python main.py
```

服務啟動於 `http://localhost:5002`

### 4. 驗證

```bash
curl http://localhost:5002/health
```

## API 端點

### 入庫

```bash
# 單一檔案
curl -X POST http://localhost:5002/ingest \
  -F "file=@文件.pdf" \
  -F "metadata={\"source\":\"HR部門\",\"tags\":[\"法規\"]}"
```

### 搜尋

```bash
curl -X POST http://localhost:5002/search \
  -H "Content-Type: application/json" \
  -d '{"query": "特休假的規定", "top_k": 10}'
```

### 匯出 PPT

```bash
curl -X POST http://localhost:5002/export/ppt \
  -H "Content-Type: application/json" \
  -d '{"result_ids": ["uuid1", "uuid2"], "output_name": "我的報告", "include_images": true}' \
  --output report.pptx
```

### 管理

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/collections/stats` | 統計資訊 |
| DELETE | `/collection/{file_id}` | 刪除檔案所有 chunks |
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
```

啟動時確保 DocVault API 先啟動，McpServerIIS 啟動時會自動註冊以下 Tools：
- `doc_ingest` — 上傳檔案入庫
- `doc_search` — 搜尋文件
- `doc_export_ppt` — 匯出 PPT

## 目錄結構

```
doc-vault-api/
├── main.py              # FastAPI 應用程式
├── config.py            # 設定讀取
├── embeddings.py        # Embedding Provider（工廠模式）
├── vector_store.py      # Milvus 操作
├── ppt_generator.py     # PPT 生成
├── parsers/             # 文件解析器
│   ├── pdf_parser.py
│   ├── word_parser.py
│   ├── ppt_parser.py
│   └── excel_parser.py
├── processed/           # 處理過的資料
│   └── images/          # 萃取的圖片
├── output/              # 輸出檔案
└── requirements.txt
```

## Embedding Provider

支援三種模式，透過 `EMBEDDING_PROVIDER` 設定切換：

### Ollama（推薦）

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
# 設定 API Key
OPENAI_API_KEY=sk-xxx
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

### 本地模型

```bash
pip install sentence-transformers
# 模型會自動下載
```

## Milvus 安裝

```bash
mkdir -p milvus && cd milvus
curl -fsSL https://github.com/milvus-io/milvus/releases/download/v2.4.9/milvus-standalone-docker-compose.yml -o docker-compose.yml
docker compose up -d
```

Milvus 啟動於 `localhost:19530`。

可搭配 [Attu](https://github.com/zilliztech/attu)（Milvus GUI）管理。
