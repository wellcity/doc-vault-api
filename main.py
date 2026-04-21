"""
DocVault FastAPI - 文件向量資料庫 API
PostgreSQL + pgvector
"""
import sys
sys.path.append(".")

import os
import io
import uuid
import time
import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import nest_asyncio
import psycopg2

try:
    nest_asyncio.apply()
except Exception:
    pass

from config import API_HOST, API_PORT, IMAGE_DIR, OUTPUT_DIR
from db import init_db, get_conn
from vector_store import insert_chunks, search as pg_search, get_chunks_by_ids, get_stats
from parsers.pdf_parser import parse_pdf
from parsers.word_parser import parse_docx
from parsers.ppt_parser import parse_pptx
from parsers.excel_parser import parse_xlsx
from ppt_generator import generate_ppt, generate_ppt_from_outline
from pdf_generator import generate_pdf_from_outline, generate_pdf_from_chunks
from excel_generator import generate_excel_from_data
from word_generator import generate_word_from_outline
from embeddings import get_embedding_provider
from scraper import scrape as do_scrape

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ======================== 生命週期 ========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("初始化資料庫...")
    try:
        init_db()
        logger.info("資料庫初始化完成")
    except Exception as e:
        logger.warning(f"資料庫初始化警告：{e}")
    yield
    logger.info("DocVault API 關閉")


# ======================== FastAPI 應用 ========================

app = FastAPI(
    title="DocVault API",
    description="文件向量資料庫 — 入庫、搜尋、PPT 匯出（PostgreSQL + pgvector）",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Admin web 管理介面
from admin_routes import router as admin_router
app.include_router(admin_router)


# ======================== 模型 ========================

class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    file_types: list[str] | None = None
    confidentiality: list[str] | None = None
    user_id: str | None = None  # JWT user_id


class PermissionSyncRequest(BaseModel):
    user_id: str
    documents: list[dict]  # [{"document_id": "xxx", "access_level": "read"}]


class ExportPptRequest(BaseModel):
    result_ids: list[str]
    output_name: str = "report"
    include_images: bool = True


class GeneratePptFromOutlineRequest(BaseModel):
    outline: dict
    output_name: str = "presentation"


class GeneratePdfFromOutlineRequest(BaseModel):
    outline: dict
    output_name: str = "report"


class GeneratePdfFromChunksRequest(BaseModel):
    result_ids: list[str]
    output_name: str = "document"


class GenerateExcelRequest(BaseModel):
    sheets: list[dict]
    output_name: str = "report"
    title: str | None = None


class GenerateWordRequest(BaseModel):
    outline: dict
    output_name: str = "document"


class ScrapeRequest(BaseModel):
    url: str
    selector: str | None = None  # CSS 選擇器，只取符合的元素
    extract_links: bool = False   # 是否一併回傳連結清單
    timeout: float = 30.0        # 請求逾時（秒）


# ======================== 工具函式 ========================

def parse_file(file_path: str, file_id: str, file_ext: str, metadata: dict):
    parsers = {
        ".pdf": parse_pdf,
        ".docx": parse_docx,
        ".pptx": parse_pptx,
        ".xlsx": parse_xlsx,
    }
    parser = parsers.get(file_ext.lower())
    if not parser:
        raise ValueError(f"不支援的格式：{file_ext}")
    return parser(file_path, file_id, metadata)


def get_file_ext(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lower()


# ======================== API 端點 ========================

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    metadata: str | None = Form(default=None),
):
    """上傳檔案入庫（content_hash 去重）"""
    start = time.time()
    import hashlib

    # 解析 metadata（提前到副檔名檢查之前）
    meta = {}
    if metadata:
        try:
            meta = json.loads(metadata)
        except Exception:
            pass

    # 副檔名檢查（包含 .doc 明確拒絕指引）
    file_ext = get_file_ext(file.filename or "unknown")
    if file_ext == ".doc":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Unsupported file type",
                "message": "不支援 .doc 格式，請將文件另存為 .docx 後再上傳。",
                "supported": [".pdf", ".docx", ".pptx", ".xlsx"],
            },
        )
    if file_ext not in {".pdf", ".docx", ".pptx", ".xlsx"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Unsupported file type",
                "supported": [".pdf", ".docx", ".pptx", ".xlsx"],
            },
        )

    # 讀取內容並計算 content_hash（用於去重）
    content = await file.read()
    content_hash = hashlib.sha256(content).hexdigest()[:32]
    file_id = str(uuid.uuid4())
    confidentiality = meta.get("confidentiality", "公開")
    department = meta.get("department", "")

    # 先查是否已有相同 content_hash 的檔案（去重）
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_id FROM documents WHERE content_hash = %s LIMIT 1",
                (content_hash,),
            )
            row = cur.fetchone()
            if row:
                file_id = row[0]
                logger.info(f"發現重複檔案，複用 file_id={file_id}")
            else:
                # 寫入 documents 表
                cur.execute("""
                    INSERT INTO documents (file_id, content_hash, filename, file_type, confidentiality, department, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (file_id) DO UPDATE
                        SET filename = EXCLUDED.filename,
                            confidentiality = EXCLUDED.confidentiality,
                            department = EXCLUDED.department,
                            metadata_json = EXCLUDED.metadata_json,
                            content_hash = EXCLUDED.content_hash
                """, (file_id, content_hash, file.filename or "unknown", file_ext.lstrip("."), confidentiality, department, json.dumps(meta)))

                # 舊格式相容：document_id
                doc_id = meta.get("document_id")
                if doc_id and doc_id != file_id:
                    cur.execute("UPDATE documents SET file_id = %s WHERE file_id = %s", (doc_id, file_id))
                    file_id = doc_id

                conn.commit()

    # 儲存暫存檔
    temp_dir = Path("/tmp/docvault")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{file_id}{file_ext}"

    try:
        with open(temp_path, "wb") as f:
            f.write(content)

        # 解析
        chunks, image_paths = parse_file(str(temp_path), file_id, file_ext, meta)
        chunk_count = len(chunks)
        image_count = len(image_paths)

        # 向量化（批次一次送出，大幅減少 HTTP 往返）
        embed = get_embedding_provider()
        texts_for_embed = [c["text"][:8000] for c in chunks]
        vectors = embed.embed(texts_for_embed)
        for chunk, vec in zip(chunks, vectors):
            chunk["embedding"] = vec

        # 寫入 PostgreSQL
        try:
            insert_chunks(chunks)
        except psycopg2.errors.UndefinedTable:
            logger.warning("document_chunks 表不存在，嘗試自動 init_db 重建後再寫入...")
            init_db()
            insert_chunks(chunks)

        elapsed_ms = int((time.time() - start) * 1000)

        return JSONResponse({
            "file_id": file_id,
            "filename": file.filename,
            "file_type": file_ext.lstrip("."),
            "confidentiality": confidentiality,
            "status": "completed",
            "chunk_count": chunk_count,
            "image_count": image_count,
            "processing_time_ms": elapsed_ms,
        })

    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        logger.exception("入庫錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})
    finally:
        if temp_path.exists():
            os.remove(temp_path)


@app.post("/search")
async def search_endpoint(req: SearchRequest):
    """搜尋文件（可指定 user_id 做權限過濾）"""
    try:
        embed = get_embedding_provider()
        vectors = embed.embed([req.query])
        query_vector = vectors[0]

        results = pg_search(
            query_vector=query_vector,
            top_k=req.top_k,
            user_id=req.user_id,
            file_types=req.file_types,
            confidentiality=req.confidentiality,
        )

        return JSONResponse({
            "query": req.query,
            "total": len(results),
            "results": results,
        })

    except Exception as e:
        logger.exception("搜尋錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/permissions/sync")
async def sync_permissions(req: PermissionSyncRequest):
    """
    同步使用者權限
    原系統呼叫此端點更新每位使用者的文件訪問權限
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 刪除該 user 的舊權限
                cur.execute(
                    "DELETE FROM document_permissions WHERE user_id = %s",
                    (req.user_id,)
                )

                # 寫入新權限
                records = [
                    (req.user_id, doc["document_id"], doc.get("access_level", "read"))
                    for doc in req.documents
                ]
                if records:
                    execute_values = __import__("psycopg2.extras", fromlist=["execute_values"]).execute_values
                    execute_values(
                        cur,
                        "INSERT INTO document_permissions (user_id, document_id, access_level) VALUES %s",
                        records,
                        template="(%s, %s, %s)",
                    )
                conn.commit()

        return JSONResponse({
            "status": "ok",
            "user_id": req.user_id,
            "document_count": len(req.documents),
        })

    except Exception as e:
        logger.exception("權限同步錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.get("/permissions/{user_id}")
async def get_user_permissions(user_id: str):
    """查詢使用者的文件權限"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT dp.document_id, dp.access_level, dp.granted_at,
                           d.filename, d.confidentiality
                    FROM document_permissions dp
                    JOIN documents d ON dp.document_id = d.file_id
                    WHERE dp.user_id = %s
                """, (user_id,))
                rows = cur.fetchall()

        return JSONResponse({
            "user_id": user_id,
            "documents": [
                {
                    "document_id": r[0],
                    "access_level": r[1],
                    "granted_at": r[2],
                    "filename": r[3],
                    "confidentiality": r[4],
                }
                for r in rows
            ],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/export/ppt")
async def export_ppt(req: ExportPptRequest):
    """匯出 PPT"""
    try:
        chunks = get_chunks_by_ids(req.result_ids)
        if not chunks:
            raise HTTPException(status_code=404, detail={"error": "找不到指定的 chunks"})

        ppt_bytes = generate_ppt(
            chunks=chunks,
            output_name=req.output_name,
            include_images=req.include_images,
        )

        output_filename = f"{req.output_name}.pptx"
        return StreamingResponse(
            io.BytesIO(ppt_bytes),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("匯出錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/scrape")
async def scrape_endpoint(req: ScrapeRequest):
    """
    爬取公開網頁內容。

    - url：目標網址
    - selector：CSS 選擇器，只取符合的元素（選填）
    - extract_links：是否回傳連結清單（預設 False）
    - timeout：請求逾時，預設 30 秒
    """
    try:
        result = await do_scrape(
            url=req.url,
            selector=req.selector,
            extract_links_flag=req.extract_links,
            timeout=req.timeout,
        )
        return JSONResponse(result)
    except Exception as e:
        logger.exception("爬蟲錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/ppt")
async def generate_ppt_from_outline_endpoint(req: GeneratePptFromOutlineRequest):
    """
    根據大綱結構生成 PPT。

    outline 格式：
    {
        "title": "報告標題",
        "subtitle": "副標題（選填）",
        "author": "作者（選填）",
        "slides": [
            {"title": "第一頁標題", "bullets": ["項目一", "項目二"], "notes": "備註（選填）"},
            ...
        ]
    }
    """
    try:
        ppt_bytes = generate_ppt_from_outline(
            outline=req.outline,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.pptx"
        return StreamingResponse(
            io.BytesIO(ppt_bytes),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("PPT 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/pdf")
async def generate_pdf_from_outline_endpoint(req: GeneratePdfFromOutlineRequest):
    """
    根據大綱結構生成 PDF 報告。

    outline 格式：
    {
        "title": "報告標題",
        "subtitle": "副標題（選填）",
        "author": "作者（選填）",
        "date": "2025/01/01（選填，預設今天）",
        "sections": [
            {
                "heading": "章節標題",
                "content": "內文" or ["項目一", "項目二"],
                "table": [["標題", "內容"], ...]（選填）
            }
        ]
    }
    """
    try:
        pdf_bytes = generate_pdf_from_outline(
            outline=req.outline,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("PDF 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/export/pdf")
async def export_pdf_from_chunks_endpoint(req: GeneratePdfFromChunksRequest):
    """
    將向量搜尋結果（chunks）匯出為 PDF 文件。
    """
    try:
        chunks = get_chunks_by_ids(req.result_ids)
        if not chunks:
            raise HTTPException(status_code=404, detail={"error": "找不到指定的 chunks"})

        pdf_bytes = generate_pdf_from_chunks(
            chunks=chunks,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("PDF 匯出錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/excel")
async def generate_excel_endpoint(req: GenerateExcelRequest):
    """
    根據資料結構生成 Excel 報表。

    sheets 格式：
    [{
        "name": "工作表名稱",
        "headers": ["欄位A", "欄位B"],
        "data": [
            ["值1", "值2"],
            ["值3", "值4"]
        ]
    }]

    headers 可為 dict 設定寬度與對齊：
    ["欄位A", {"label": "欄位B", "width": 20, "align": "center"}]

    data 每列可為 list 或 dict（以 headers 為 key）。
    """
    try:
        xlsx_bytes = generate_excel_from_data(
            sheets=req.sheets,
            output_name=req.output_name,
            title=req.title,
        )

        output_filename = f"{req.output_name}.xlsx"
        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("Excel 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/word")
async def generate_word_endpoint(req: GenerateWordRequest):
    """
    根據大綱結構生成 Word 文件。

    outline 格式：
    {
        "title": "文件標題",
        "subtitle": "副標題（選填）",
        "author": "作者（選填）",
        "date": "2025/01/01（選填，預設今天）",
        "sections": [
            {
                "heading": "章節標題",
                "content": "內文" or ["項目一", "項目二"],
                "table": [["標題1", "標題2"], ["內容1", "內容2"], ...]（選填）
            }
        ]
    }
    """
    try:
        docx_bytes = generate_word_from_outline(
            outline=req.outline,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.docx"
        return StreamingResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("Word 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.get("/collections/stats")
async def collections_stats():
    """統計資訊"""
    try:
        stats = get_stats()
        return JSONResponse(stats)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.delete("/collection/{file_id}")
async def delete_file_chunks(file_id: str):
    """刪除檔案（會 Cascade 刪除 chunks 和權限）"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE file_id = %s", (file_id,))
                conn.commit()
        return JSONResponse({"status": "deleted", "file_id": file_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)})


# ======================== 啟動 ========================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
