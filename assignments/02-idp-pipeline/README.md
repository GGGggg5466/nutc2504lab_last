# 02 - IDP Pipeline MVP

## 一、專案目標

本作業實作一個最小可運作（MVP）的非同步任務處理 API，
建立完整 Intelligent Document Processing (IDP) 流水線基礎架構，
支援 OCR → VLM → Normalize → Chunk 的可擴充式處理流程。

功能包含：

- 建立任務（`POST /v1/jobs`）
- 背景非同步處理（`Redis + RQ worker`）
- 查詢任務狀態與結果（`GET /v1/jobs/{job_id}`）

- Pipeline 模式（`route=pipeline`）：OCR/Docling → VLM → Normalize → Chunk → Embed → Index
- input_type 支援（text / image / pdf）
- 向量化與索引（embed/index）：chunks 寫入 Qdrant（Vector DB）

- ✅ Day5：Semantic Search API（`POST /v1/search`）
  - dense / hybrid（FTS + Dense → RRF fusion）
  - rerank（TopN rerank → TopK）
  - 回傳 citations（doc_id/pipeline_version/chunk_index）+ latency debug

- ✅ Day5：Answer API（`POST /v1/answer`）
  - search →（optional rerank）→ LLM 生成
  - 回傳 answer + citations + debug（search_latency_ms / llm_latency_ms / rerank_used）

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

補充：RQ Worker 在處理流程的最後階段（embed/index）會把 chunk 向量寫入 Qdrant，供後續 RAG/檢索使用。
Day5 補充：

- `POST /v1/search`：直接從 Qdrant（dense）與 FTS（keyword）檢索候選 chunks，再做 RRF / rerank。
- `POST /v1/answer`：重用 `/v1/search` 的結果，組 prompt 並呼叫遠端 LLM（若 LLM_API_URL 未設定則 fallback）。
- 外部依賴：
  - rerank service（HTTP）
  - LLM service（HTTP）

服務包含：

- api
- worker
- redis
- qdrant（Vector DB：保存 chunk embeddings 與 payload）

---

## 三、技術選型

- FastAPI：建立高效能 REST API
- Redis：任務佇列與結果儲存
- RQ：輕量級非同步任務處理框架
- Docker Compose：多服務統一部署
- Qdrant：向量資料庫（保存 chunk embeddings 與檢索 payload）
- SentenceTransformers：產生 embeddings（供 Qdrant indexing）

- （Day5）FTS5 / BM25：keyword retrieval（SQLite FTS）
- （Day5）RRF fusion：Dense + BM25 結果融合
- （Day5）Rerank service：TopN rerank 提升排序品質
- （Day5）LLM API：Answer 生成（有設定 LLM_API_URL 時才會真的呼叫）

---

## 四、API 說明

### 1️⃣ 建立任務

POST `/v1/jobs`

Request:

```json
{
  "text": "hello rq",
  "input_type": "text",
  "route": "auto"
}
```

- input_type 可為：text | image | pdf
- route 可為：auto | ocr | vlm | pipeline
- 若不填，預設為 auto

Response（立即回傳；job 進入 queued）：

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

### 3️⃣ Pipeline 模式（IDP 完整流程）

當 route 設為 `pipeline` 時，會啟用完整文件處理流程：

- image → OCR → VLM → Normalize → Chunk
- pdf → Docling → VLM → Normalize → Chunk
- text → VLM → Normalize → Chunk

回傳結果將包含：

- stages：執行階段
- normalized：抽取後的 JSON object
- chunks：切塊結果（RAG-ready）

### 4️⃣ Semantic Search API（Day5）

POST `/v1/search`

Request（最小範例）：

```json
{
  "query": "test",
  "top_k": 3,
  "include_payload": true,
  "retrieval": { "mode": "dense" },
  "rerank": { "enabled": true, "top_n": 50, "timeout_ms": 2000 }
}
```

### 5️⃣ Answer API（Day5）

Request（最小範例）：

```json
{
  "query": "test",
  "top_k": 3,
  "retrieval": { "mode": "dense" },
  "rerank": { "enabled": true, "top_n": 50, "timeout_ms": 2000 }
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

> 注意：route 與 input_type 請一律使用小寫（auto/ocr/vlm/pipeline；text/image/pdf），避免 enum 驗證失敗。

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

### F. Image（OCR stub）

```bash
curl -s -X POST "http://localhost:8000/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"text":"/data/test.jpg","input_type":"image","route":"ocr"}'
```

### G. Pipeline 模式（完整 IDP）

```bash
curl -s -X POST "http://localhost:8000/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"text":"/data/test.jpg","input_type":"image","route":"pipeline"}'
```

### H. Semantic Search（Day5）

```bash
curl -s http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "test",
    "top_k": 3,
    "include_payload": true,
    "rerank": {"enabled": true, "top_n": 50, "timeout_ms": 2000},
    "retrieval": {"mode": "dense"}
  }'
```
### I. Answer API（Day5）

```bash
curl -s http://localhost:8000/v1/answer \
  -H "Content-Type: application/json" \
  -d '{
    "query": "test",
    "top_k": 3,
    "rerank": {"enabled": true, "top_n": 50, "timeout_ms": 2000},
    "retrieval": {"mode": "dense"}
  }' | jq '.answer, .debug, .citations[0]'
```

## 七、目前完成進度

已完成：

- 非同步架構（Redis + RQ worker）
- Job 狀態管理（queued / started / finished / failed）
- 路由機制（auto / ocr / vlm / pipeline）
- input_type 支援（text / image / pdf）
- ✅ Pipeline（IDP）處理鏈路：
  - EasyOCR / Docling stub 整合
  - 負責呼叫 VLM API（VLM_API_URL / VLM_MODEL）
  - JSON 正規化抽取（extract_json）
  - Chunking（RAG-ready 切塊）
  - Embedding（產生 chunk 向量）
  - Indexing（寫入 Qdrant collection=idp_chunks）

- ✅ Day5 M1：Semantic Search v1（POST /v1/search）
  - dense retrieval（Qdrant）
  - 支援 doc_id filter（若 request 有帶）
  - 回傳 results + citations(lineage) + latency debug

- ✅ Day5 M2：Hybrid Retrieval（品質升級）
  - FTS5 keyword retrieval
  - RRF fusion（Dense + BM25/FTS）
  - keyword query（數字/代碼）命中率提升

- ✅ Day5 M3：Rerank（TopK 排序更準）
  - rerank on topN → return topK
  - timeout fallback
  - debug：rerank_used / candidates_n / used_chunk_ids

- ✅ Day5 M4：Answer API（POST /v1/answer）
  - search →（optional rerank）→ LLM 生成答案
  - debug：llm_used / llm_latency_ms / search_latency_ms
  - citations 已補齊 doc_id / pipeline_version / chunk_index / text_snippet（可追溯）
  - answer 會引用 chunk_id（例如 [chunk_id]）

## 八、未來擴充

後續規劃：

- 支援檔案上傳（base64 / multipart）
- ✅（GraphRAG-ready）將 entities / relations 寫入知識圖譜（Neo4j）
- GraphRAG 整合（Chunk → Entity/Edge → Graph query → Answer）

- Docling 真實整合（layout/table parsing；目前為 stub）
- EasyOCR 真實整合（目前為 stub/簡化版）

- Horizontal worker scaling（多 worker 擴展）
- Gateway queue limit / rate limit（保護下游模型資源）
- metrics（Prometheus / logging / tracing）

- 補齊正式 Mermaid 架構圖 + OpenAPI schema / error codes（文件化驗收）


