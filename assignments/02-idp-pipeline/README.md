# 02 - IDP Pipeline MVP

## 一、專案目標

本作業實作一個最小可運作（MVP）的非同步任務處理 API，
建立後續 AI 推論流程（OCR / VLM / NLP）的基礎架構。

功能包含：

- 建立任務（`POST /v1/jobs`）
- 背景非同步處理（`Redis + RQ worker`）
- 查詢任務狀態與結果（`GET /v1/jobs/{job_id}`）
- 路由策略（`route=auto|ocr|vlm`）：支援自動判斷與強制指定
- 失敗測試（輸入含 `please fail` → `failed`）
- 真 API 連線測試資訊（`api_feedback` 回傳延遲/錯誤/timeout 等）

---

## 二、系統架構

整體架構如下：

Client  
│  
▼  
FastAPI (API Service)  
│  
▼  
Redis (Queue + Job Metadata)  
│  
▼  
RQ Worker（背景處理任務）  
│  
▼  
Redis（儲存結果）  
│  
▼  
FastAPI 回傳任務狀態與結果  

服務包含：

- api
- worker
- redis

---

## 三、技術選型

- FastAPI：建立高效能 REST API
- Redis：任務佇列與結果儲存
- RQ：輕量級非同步任務處理框架
- Docker Compose：多服務統一部署

---

## 四、API 說明

### 1️⃣ 建立任務

POST `/v1/jobs`

Request:

```json
{
  "text": "hello rq",
  "route": "ocr"
}
```

- route 可為：auto | ocr | vlm
- 若不填，預設為 auto

Response（完成時）：

```json
{
  "job_id": "xxxx",
  "status": "queued",
  "queue": "default",
  "route_request": "ocr",
  "route_hint": {
    "route": "ocr",
    "confidence": 1.0,
    "reason": "Route forced by request"
  }
}
```

### 2️⃣ 查詢任務狀態

GET `/v1/jobs/{job_id}`

```json
{
  "job_id": "xxxx",
  "status": "finished",
  "result": {
    "ok": true,
    "job_id": "xxxx",
    "route_request": "auto",
    "chosen_route": "ocr",
    "route_hint": {
      "route": "ocr",
      "confidence": 0.75,
      "reason": "Detected table/structured keywords"
    },
    "api_feedback": {
      "mode": "real",
      "route": "ocr",
      "ok": true,
      "latency_ms": 664,
      "timeout_sec": 20,
      "error": null
    },
    "payload": {
      "engine": "ocr-api",
      "raw": "..."
    }
  },
  "error": null
}
```
失敗（failed）時範例：

```json
{
  "job_id": "xxxx",
  "status": "failed",
  "result": null,
  "error": "Forced failure for testing"
}
```

## 五、啟動方式

```bash
docker-compose up -d --build
docker-compose ps
```

啟動後可使用：

Swagger 文件：

```bash
http://localhost:8000/docs
```

## 六、測試流程

建立任務：

### A. OCR 路由（強制）
```bash
curl -s -X POST "http://localhost:8000/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"text":"hello rq","route":"ocr"}'
```

### B. VLM 路由（強制）
查詢任務：

```bash
curl -s -X POST "http://localhost:8000/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"text":"這張圖請描述重點","route":"vlm"}'
```
### C. AUTO（由系統判斷）

```bash
curl -s -X POST "http://localhost:8000/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"text":"表格欄位：name/age/score，請轉成JSON","route":"auto"}'
```

### D. 失敗測試（應回 failed）

```bash
curl -s -X POST "http://localhost:8000/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"text":"please fail","route":"ocr"}'
```

### E. 查詢 job

把 <job_id> 換成剛剛回傳的 job_id：
```bash
curl -s "http://localhost:8000/v1/jobs/<job_id>"
```

## 七、目前完成進度

已完成：

- 非同步架構（Redis + RQ）
- 路由機制（auto / ocr / vlm）
- chosen_route 回傳
- route_hint 說明
- api_feedback（真 API 測試資訊）
- 失敗測試機制

後續將整合：

- EasyOCR（image）
- Docling（PDF）
- Gemma VLM（圖像理解）
- 完整 IDP pipeline



