"""
DocVault FastAPI - 文件向量資料庫 API
"""
import sys
sys.path.append(".")

import os
import io
import uuid
import time
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import nest_asyncio

# 允許在已有事件迴圈的環境執行（WSL/Colab 相容）
try:
    nest_asyncio.apply()
except Exception:
    pass

from config import API_HOST, API_PORT, IMAGE_DIR, OUTPUT_DIR
from vector_store import ensure_collection, insert_chunks, search as milvus_search, get_chunks_by_ids, delete_by_file_id, get_stats
from parsers.pdf_parser import parse_pdf
from parsers.word_parser import parse_docx
from parsers.ppt_parser import parse_pptx
from parsers.excel_parser import parse_xlsx
from ppt_generator import generate_ppt
from embeddings import get_embedding_provider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ======================== 事件生命週期 ========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時確保 Milvus Collection 存在"""
    logger.info("正在初始化 Milvus Collection...")
    try:
        ensure_collection()
        logger.info("Milvus Collection 初始化完成")
    except Exception as e:
        logger.warning(f"Milvus 初始化警告：{e}")
    yield
    logger.info("DocVault API 關閉")


# ======================== FastAPI 應用 ========================

app = FastAPI(
    title="DocVault API",
    description="文件向量資料庫 — 入庫、搜尋、PPT 匯出",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================== 模型 ========================

class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    file_types: list[str] | None = None
    tags: list[str] | None = None


class ExportPptRequest(BaseModel):
    result_ids: list[str]
    output_name: str = "report"
    include_images: bool = True


# ======================== 工具函式 ========================

def parse_file(file_path: str, file_id: str, file_ext: str, metadata: dict) -> tuple[list[dict], list[str]]:
    """根據副檔名分派 Parser"""
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
    """上傳檔案入庫"""
    start = time.time()
    file_id = str(uuid.uuid4())
    file_ext = get_file_ext(file.filename or "unknown")

    if file_ext not in {".pdf", ".docx", ".pptx", ".xlsx"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Unsupported file type",
                "supported": [".pdf", ".docx", ".pptx", ".xlsx"],
            },
        )

    # 解析 metadata
    import json
    meta = {}
    if metadata:
        try:
            meta = json.loads(metadata)
        except Exception:
            pass

    # 儲存暫存檔
    temp_dir = Path("/tmp/docvault")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{file_id}{file_ext}"

    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

        # 解析
        chunks, image_paths = parse_file(str(temp_path), file_id, file_ext, meta)
        chunk_count = len(chunks)
        image_count = len(image_paths)

        # 向量化
        embed = get_embedding_provider()

        for chunk in chunks:
            vectors = embed.embed([chunk["text"][:8000]])
            chunk["embedding"] = vectors[0]

        # 寫入 Milvus
        insert_chunks(chunks)

        elapsed_ms = int((time.time() - start) * 1000)

        return JSONResponse({
            "file_id": file_id,
            "filename": file.filename,
            "file_type": file_ext.lstrip("."),
            "status": "completed",
            "chunk_count": chunk_count,
            "image_count": image_count,
            "processing_time_ms": elapsed_ms,
            "created_at": chunks[0]["created_at"] if chunks else None,
        })

    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        logger.exception("入庫錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})
    finally:
        # 刪除暫存檔
        if temp_path.exists():
            os.remove(temp_path)


@app.post("/search")
async def search_endpoint(req: SearchRequest):
    """搜尋文件"""
    try:
        embed = get_embedding_provider()

        # 向量化查詢語句
        vectors = embed.embed([req.query])
        query_vector = vectors[0]

        # 搜尋
        results = milvus_search(
            query_vector=query_vector,
            top_k=req.top_k,
            file_types=req.file_types,
        )

        return JSONResponse({
            "query": req.query,
            "total": len(results),
            "results": results,
        })

    except Exception as e:
        logger.exception("搜尋錯誤")
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
    """刪除指定檔案的所有 chunks"""
    try:
        delete_by_file_id(file_id)
        return JSONResponse({"status": "deleted", "file_id": file_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)})


# ======================== 啟動 ========================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
