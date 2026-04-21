"""
將舊版 .doc 檔案透過 LibreOffice 轉換為 .docx
僅支援 Windows 環境（soffice.exe 路徑固定）
"""
import subprocess
import tempfile
import shutil
import os
from pathlib import Path

# LibreOffice Windows 安裝路徑
SOFFICE_PATH = r"C:\Program Files\LibreOffice\program\soffice.exe"


def convert_doc_to_docx(doc_path: str) -> str:
    """
    將 .doc 檔案轉換為 .docx，並回傳 .docx 的暫存路徑。

    Args:
        doc_path: .doc 檔案的絕對路徑

    Returns:
        轉換後 .docx 檔案的暫存路徑（需自行刪除）

    Raises:
        FileNotFoundError: soffice.exe 不存在
        RuntimeError: 轉換失敗
    """
    if not os.path.exists(SOFFICE_PATH):
        raise FileNotFoundError(
            f"LibreOffice soffice.exe 不存在：{SOFFICE_PATH}"
            "，請先安裝 LibreOffice：https://www.libreoffice.org/download/download/"
        )

    doc_path = Path(doc_path).resolve()
    if not doc_path.exists():
        raise FileNotFoundError(f"檔案不存在：{doc_path}")

    # 建立暫存目錄（轉換輸出的目的地）
    temp_dir = tempfile.mkdtemp(prefix="docvault_")
    temp_docx = Path(temp_dir) / f"{doc_path.stem}.docx"

    # LibreOffice headless 模式：--headless --convert-to docx --outdir <output_dir> <file>
    cmd = [
        SOFFICE_PATH,
        "--headless",
        "--convert-to", "docx",
        "--outdir", temp_dir,
        str(doc_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice 轉換失敗：{result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("LibreOffice 轉換逾時（60秒）")
    except FileNotFoundError:
        raise

    # soffice 可能輸出到不同位置，找一下
    if temp_docx.exists():
        return str(temp_docx)

    # 可能直接覆蓋原檔案目錄
    alt_path = doc_path.with_suffix(".docx")
    if alt_path.exists():
        # 搬到暫存目錄，保持一致的路徑邏輯
        shutil.move(str(alt_path), str(temp_docx))
        return str(temp_docx)

    raise RuntimeError(
        f"LibreOffice 轉換完成，但找不到輸出檔案：{result.stdout.strip()} {result.stderr.strip()}"
    )
