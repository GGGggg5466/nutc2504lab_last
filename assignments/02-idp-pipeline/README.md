# 02 - IDP Pipeline MVP

## 一、專案目標

本作業實作一個最小可運作（MVP）的非同步任務處理 API，
建立後續 AI 推論流程（OCR / VLM / NLP）的基礎架構。

功能包含：

- 建立任務（POST /v1/jobs）
- 背景非同步處理（Redis + RQ Worker）
- 查詢任務狀態與結果（GET /v1/jobs/{job_id}）
- 使用 Docker Compose 部署多服務架構

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
  "text": "hello rq"
}
```

### 2️⃣ 查詢任務狀態

GET `/v1/jobs/{job_id}`
Response（完成時）：

```json
{
  "job_id": "...",
  "status": "finished",
  "result": {
    "ok": true,
    "echo": "hello rq",
    "len": 8
  }
}
```
狀態可能包含：

- queued
- started
- finished
- failed

## 五、啟動方式

```bash
docker-compose up --build
```

啟動後可使用：

Swagger 文件：

```bash
http://localhost:8000/docs
```

## 六、測試流程

建立任務：

```bash
curl -X POST "http://localhost:8000/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"text":"hello rq"}'
```
查詢任務：

```bash
curl "http://localhost:8000/v1/jobs/<job_id>"
```

## 七、未來延伸方向

本架構可擴充為：

- 將背景任務替換為 OCR / LLM 推論流程
- 增加任務分類（多模型 routing）
- 加入 retry / timeout 機制
- 接入資料庫儲存歷史任務
- Kubernetes 部署升級

## 八、總結

本專案成功建立：

- 非同步任務 API
- Redis Queue 架構
- Worker 背景處理機制
- Docker 多服務部署流程