# DocVault API — Windows Service 安裝指南

本文說明如何將 DocVault API 以 Windows Service 方式執行，開機自動啟動，透過 Services.msc 管理。

---

## 前置需求

1. **NSSM**（Non-Sucking Service Manager）
2. **Python 3.10+** 已安裝，且 python 已加入 PATH

---

## Step 1：下載 NSSM

### 方法 A：手動下載

1. 前往 https://nssm.cc/download
2. 下載最新版本（建議 zip）
3. 解壓縮到 `C:\Tools\nssm\`

### 方法 B：使用管理員 PowerShell

```powershell
# 建立目錄
New-Item -ItemType Directory -Force -Path C:\Tools\nssm

# 下載 NSSM
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.51.zip" -OutFile C:\Tools\nssm.zip

# 解壓縮（需要 PowerShell 5+ 或 7zip）
Expand-Archive C:\Tools\nssm.zip -DestinationPath C:\Tools\nssm -Force

# 複製 nssm.exe（根據系統選擇）
# 64-bit Windows
Copy-Item C:\Tools\nssm\nssm-2.51\win64\nssm.exe C:\Tools\nssm\n```

---

## Step 2：確認 Python 路徑

在終端機執行：

```powershell
where python
```

會顯示类似：
```
C:\Users\wellc\AppData\Local\Programs\Python\Python310\python.exe
```

記下這個路徑，之後會用到。

---

## Step 3：安裝 DocVault API 為 Service

以**系統管理員身份**開啟 PowerShell，執行：

```powershell
cd C:\Tools\nssm

# 安裝 Service
.\nssm.exe install DocVaultAPI
```

會彈出 NSSM 的 GUI 設定視窗，填入以下資訊：

### Application 分頁

| 欄位 | 填入 |
|------|------|
| Path | `C:\Users\wellc\AppData\Local\Programs\Python\Python310\python.exe` |
| Startup directory | `C:\WORK\openclaw_projects\doc-vault-api` |
| Arguments | `main.py` |

### Details 分頁

| 欄位 | 填入 |
|------|------|
| Display name | `DocVault API` |
| Description | `文件向量資料庫 API — 入庫、搜尋、PPT 匯出` |
| Startup type | `Automatic`（自動開機啟動）|

### Dependencies 分頁（選填）

DocVault API 依賴 PostgreSQL，確保 PostgreSQL Docker container 先行啟動後再啟動 DocVault Service。

---

## Step 4：啟動服務

```powershell
# 啟動
Start-Service DocVaultAPI

# 查看狀態
Get-Service DocVaultAPI

# 確認正在執行
Status     : Running
StartType  : Automatic
```

---

## Step 5：驗證

開啟瀏覽器：

- Admin 管理介面：http://localhost:5002/admin
- API 健康檢查：http://localhost:5002/health

---

## 管理命令

```powershell
# 啟動
Start-Service DocVaultAPI

# 停止
Stop-Service DocVaultAPI

# 重新啟動
Restart-Service DocVaultAPI

# 解除安裝（如果未來需要）
sc.exe delete DocVaultAPI
```

---

## 修改 NSSM 設定

```powershell
# 開啟 NSSM GUI 編輯
nssm.exe edit DocVaultAPI
```

---

---

## 疑難排解

### Service 啟動失敗

```powershell
# 查看 Event Log
Get-EventLog -LogName Application -Source "DocVaultAPI" -Newest 20
```

### 查看 NSSM Log

NSSM 預設 log 位於 `C:\Windows\System32\winevt\Logs\Application`，可以透過「事件檢視器」查看。

### 修改 Python 路徑

如果 Python 路徑變了，需要重新設定：

```powershell
nssm.exe set DocVaultAPI AppDirectory "C:\WORK\openclaw_projects\doc-vault-api"
nssm.exe set DocVaultAPI AppExecutable "C:\Users\wellc\AppData\Local\Programs\Python\Python310\python.exe"
nssm.exe set DocVaultAPI AppParameters "main.py"
```

---

## 完整流程指令碼

```powershell
# === 以系統管理員身份執行 ===

# 1. 建立目錄
New-Item -ItemType Directory -Force -Path C:\Tools\nssm

# 2. 下載並解壓縮 NSSM
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.51.zip" -OutFile C:\Tools\nssm.zip
Expand-Archive C:\Tools\nssm.zip -DestinationPath C:\Tools\nssm -Force
Copy-Item C:\Tools\nssm\nssm-2.51\win64\nssm.exe C:\Tools\nssm\n

# 3. 安裝 DocVault Service
#（需手動填入 GUI 中的路徑）
cd C:\Tools\nssm
.\nssm.exe install DocVaultAPI

# 4. 啟動
Start-Service DocVaultAPI
```
