"""
DocVault API - Web Admin 管理介面
"""
import sys
sys.path.append(".")

import psutil
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from config import API_PORT
from vector_store import get_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def get_process_info() -> dict:
    """取得目前程序資訊"""
    try:
        process = psutil.Process()
        with process.oneshot():
            return {
                "pid": process.pid,
                "memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
                "cpu_percent": process.cpu_percent(),
                "create_time": datetime.fromtimestamp(process.create_time()).strftime("%Y-%m-%d %H:%M:%S"),
            }
    except Exception:
        return {"pid": None, "memory_mb": None, "cpu_percent": None, "create_time": None}


def get_db_status() -> dict:
    """檢查 PostgreSQL 連線"""
    try:
        from db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        stats = get_stats()
        return {"connected": True, "stats": stats}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def get_log_tail(lines: int = 100) -> list[str]:
    """取得 log 檔案最後幾行"""
    log_path = Path(__file__).parent / "docvault.log"
    if not log_path.exists():
        return ["(無 log 檔案)"]

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
        return [l.strip() for l in all_lines[-lines:] if l.strip()]
    except Exception:
        return ["(無法讀取 log)"]


# ======================== HTML 頁面 ========================

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DocVault Admin</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #e0e0e0; min-height: 100vh; padding: 20px; }
  .container { max-width: 900px; margin: 0 auto; }
  h1 { color: #00d4ff; margin-bottom: 20px; font-size: 1.8rem; }
  h2 { color: #7ec8e3; margin: 20px 0 10px; font-size: 1.1rem; border-bottom: 1px solid #333; padding-bottom: 5px; }

  .card { background: #16213e; border-radius: 8px; padding: 20px; margin-bottom: 15px; border: 1px solid #0f3460; }
  .card-title { font-size: 0.85rem; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }
  .card-value { font-size: 1.4rem; font-weight: 600; }
  .status-ok { color: #00e676; }
  .status-error { color: #ff5252; }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }

  .btn { display: inline-block; padding: 8px 16px; border-radius: 5px; cursor: pointer; border: none; font-size: 0.9rem; transition: opacity 0.2s; }
  .btn:hover { opacity: 0.85; }
  .btn-refresh { background: #2196f3; color: #fff; }

  .log-box { background: #0d1117; border-radius: 5px; padding: 15px; font-family: 'Consolas', 'Courier New', monospace; font-size: 0.8rem; color: #aaa; max-height: 350px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; line-height: 1.6; }

  .info-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1e2d4a; }
  .info-row:last-child { border-bottom: none; }
  .info-key { color: #7ec8e3; }
  .info-val { color: #e0e0e0; }

  .actions { margin-top: 15px; display: flex; gap: 10px; align-items: center; }
  .actions span { font-size: 0.8rem; color: #666; }

  .updated { font-size: 0.75rem; color: #555; margin-top: 15px; text-align: right; }
</style>
</head>
<body>
<div class="container">
  <h1>DocVault Admin</h1>

  <h2>服務狀態</h2>
  <div class="grid">
    <div class="card">
      <div class="card-title">狀態</div>
      <div class="card-value status-ok">執行中</div>
    </div>
    <div class="card">
      <div class="card-title">PID</div>
      <div class="card-value" id="pid">-</div>
    </div>
    <div class="card">
      <div class="card-title">記憶體</div>
      <div class="card-value" id="memory">-</div>
    </div>
    <div class="card">
      <div class="card-title">PostgreSQL</div>
      <div class="card-value" id="db">-</div>
    </div>
    <div class="card">
      <div class="card-title">文件 chunks</div>
      <div class="card-value" id="chunks">-</div>
    </div>
  </div>

  <h2>系統資訊</h2>
  <div class="card">
    <div class="info-row"><span class="info-key">啟動時間</span><span class="info-val" id="create_time">-</span></div>
    <div class="info-row"><span class="info-key">Python 版本</span><span class="info-val">3.x</span></div>
    <div class="info-row"><span class="info-key">API 連接埠</span><span class="info-val">%(port)s</span></div>
    <div class="info-row"><span class="info-key">資料庫</span><span class="info-val">PostgreSQL + pgvector</span></div>
  </div>

  <h2>最近 Log</h2>
  <div class="card">
    <div class="log-box" id="log">載入中...</div>
  </div>

  <div class="actions">
    <button class="btn btn-refresh" id="btnRefresh">🔄 重新整理</button>
    <span>自動更新每 10 秒</span>
  </div>
  <div class="updated">最後更新：<span id="updated">-</span></div>
</div>

<script>
async function loadData() {
  try {
    const [statusRes, logRes] = await Promise.all([
      fetch('/admin/status'),
      fetch('/admin/log')
    ]);
    const data = await statusRes.json();
    const logs = await logRes.json();

    document.getElementById('pid').textContent = data.process?.pid ?? '-';
    document.getElementById('memory').textContent = data.process?.memory_mb != null ? data.process.memory_mb + ' MB' : '-';

    const dbEl = document.getElementById('db');
    if (data.db?.connected) {
      dbEl.textContent = '✅ 已連線';
      dbEl.className = 'card-value status-ok';
    } else {
      dbEl.textContent = '❌ 未連線';
      dbEl.className = 'card-value status-error';
    }

    const stats = data.db?.stats;
    document.getElementById('chunks').textContent = stats?.chunks ?? '-';
    document.getElementById('create_time').textContent = data.process?.create_time ?? '-';

    document.getElementById('log').textContent = logs.lines?.join('\\n') || '(無)';
    document.getElementById('log').scrollTop = document.getElementById('log').scrollHeight;

    document.getElementById('updated').textContent = new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('log').textContent = '載入失敗: ' + e;
  }
}

document.getElementById('btnRefresh').addEventListener('click', loadData);
loadData();
setInterval(loadData, 10000);
</script>
</body>
</html>
""" % {"port": API_PORT}


# ======================== API 端點 ========================

@router.get("/", response_class=HTMLResponse)
async def admin_page():
    """管理頁面"""
    return ADMIN_HTML


@router.get("/status")
async def admin_status():
    """取得服務狀態 JSON"""
    process_info = get_process_info()
    db_info = get_db_status()
    return {
        "process": process_info,
        "db": db_info,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/log")
async def admin_log():
    """取得 log 內容"""
    return {"lines": get_log_tail(100)}
