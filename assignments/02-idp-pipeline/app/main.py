from fastapi import FastAPI, HTTPException, Body
from rq import Retry
import os, re, requests
import time
from time import perf_counter
import traceback

from .schemas import AnswerRequest, AnswerResponse, CitationItem, AnswerDebug

from app.queue import queue, redis_conn
from app.router import decide_route
from app.state import Route

from .schemas import (
    CreateJobRequest,
    GetJobResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SearchDebug,
)
from .state import JobStatus
from .queue import get_status, get_result, get_error

from app.clients.embedding import embed_texts
from app.clients.qdrant_client import get_qdrant, build_filter, search_points
from app.clients.fts_client import reset_fts, bulk_upsert, search_keyword
from app.retrieval import rrf_fuse
from app.clients.rerank_client import rerank_remote, RerankError
from app.clients.model_api import call_llm

app = FastAPI(title="IDP Pipeline MVP", version="0.3.0")
DEFAULT_JOB_TIMEOUT_SEC = int(os.getenv("DEFAULT_TIMEOUT_SEC", "20"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "idp_chunks")

@app.get("/")
def root():
    return {"message": "OK. Try /health, POST /v1/jobs, or POST /v1/search"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/v1/jobs")
def create_job(req: CreateJobRequest):
    # 使用者要求的 route（auto/ocr/vlm）
    route_request = req.route.value

    # route_hint：只有展示用（A 先保留），真正 chosen_route 以 worker 結果為準
    if req.route == Route.auto:
        hint_route, hint_conf, hint_reason = decide_route(req.text)
        route_hint = {"route": hint_route.value, "confidence": hint_conf, "reason": hint_reason}
        route_for_worker = Route.auto.value   # ✅ 建議：仍傳 auto，讓 worker 統一決策
    else:
        route_hint = {"route": route_request, "confidence": 1.0, "reason": "Route forced by request"}
        route_for_worker = route_request

    job = queue.enqueue(
        "app.tasks.run_job",
        req.text,
        route_for_worker,
        req.input_type.value,                 # ✅ 新增：傳 input_type
        job_timeout=DEFAULT_JOB_TIMEOUT_SEC,  # ✅ 新增：RQ timeout
        retry=Retry(max=2, interval=[1, 3])   # ✅ 新增：最小 retry
    )

    return {
        "job_id": job.id,
        "status": "queued",
        "queue": job.origin,
        "route_request": route_request,
        "route_hint": route_hint,
        "input_type": req.input_type.value,
    }

@app.get("/v1/jobs/{job_id}", response_model=GetJobResponse)
def get_job(job_id: str):
    status = get_status(redis_conn, job_id)
    if not status:
        # 代表 job_id 根本不存在/過期/被清掉
        raise HTTPException(status_code=404, detail="Job not found")

    result = get_result(redis_conn, job_id) if status == JobStatus.finished.value else None
    error = get_error(redis_conn, job_id) if status == JobStatus.failed.value else None

    return GetJobResponse(job_id=job_id, status=status, result=result, error=error)

# ----------------------------
# Day5: Semantic Search API v1
# ----------------------------

@app.post("/v1/search", response_model=SearchResponse)
def semantic_search(req: SearchRequest):
    t0 = time.perf_counter()

    # ----------------
    # 0) helpers
    # ----------------
    def _item_from_dense_hit(h, score: float) -> SearchResultItem:
        payload = h.payload or {}
        return SearchResultItem(
            score=float(score),
            chunk_id=str(getattr(h, "id", "")),
            doc_id=payload.get("doc_id"),
            pipeline_version=payload.get("pipeline_version"),
            chunk_index=payload.get("chunk_index"),
            text=payload.get("text"),
            payload=payload if req.include_payload else None,
        )

    def _item_from_bm25_row(pid: str, r: dict, score: float) -> SearchResultItem:
        payload = {
            "doc_id": r.get("doc_id"),
            "pipeline_version": r.get("pipeline_version"),
            "chunk_index": r.get("chunk_index"),
            "text": r.get("text"),
            "snippet": r.get("snippet"),
            "bm25_score": r.get("bm25_score"),
            "source": "fts",
        }
        return SearchResultItem(
            score=float(score),
            chunk_id=pid,
            doc_id=r.get("doc_id"),
            pipeline_version=r.get("pipeline_version"),
            chunk_index=r.get("chunk_index"),
            text=r.get("text"),
            payload=payload if req.include_payload else None,
        )

    def _apply_rerank_inplace(items: list[SearchResultItem]) -> tuple[bool, int, str | None]:
        """
        Rerank items in-place using remote reranker.
        Returns: (rerank_used, rerank_latency_ms, fallback_reason)
        """
        if not getattr(req, "rerank", None) or not req.rerank.enabled:
            return False, 0, None

        # build candidates
        candidates = []
        for it in items[: req.rerank.top_n]:
            text = it.text
            if (not text) and it.payload:
                text = it.payload.get("text")
            if not text:
                continue
            candidates.append({"id": it.chunk_id, "text": text})

        if not candidates:
            return False, 0, "no candidates with text"

        rr_t0 = time.perf_counter()
        try:
            score_map, rr_lat = rerank_remote(
                req.query,
                candidates,
                timeout_ms=req.rerank.timeout_ms,
            )
            rr_t1 = time.perf_counter()

            # prefer server-reported latency if present, else local measured
            rerank_latency_ms = int(rr_lat) if rr_lat else int((rr_t1 - rr_t0) * 1000)

            # sort: has score first, then score desc
            def _key(it: SearchResultItem):
                has = 1 if it.chunk_id in score_map else 0
                sc = score_map.get(it.chunk_id, -1e9)
                return (has, sc)

            items.sort(key=_key, reverse=True)

            # (optional) overwrite item.score with rerank score if available
            for it in items:
                if it.chunk_id in score_map:
                    it.score = float(score_map[it.chunk_id])

            return True, rerank_latency_ms, None

        except RerankError as e:
            return False, 0, str(e)

    # ----------------
    # 1) embed query
    # ----------------
    try:
        qvec = embed_texts([req.query])[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    # ----------------
    # 2) filters
    # ----------------
    doc_id = req.filters.doc_id if req.filters else None
    pipeline_version = req.filters.pipeline_version if req.filters else None
    qfilter = build_filter(doc_id=doc_id, pipeline_version=pipeline_version)

    mode = req.retrieval.mode
    dense_top_k = req.retrieval.dense_top_k
    bm25_top_k = req.retrieval.bm25_top_k
    rrf_k = req.retrieval.rrf_k

    # ----------------
    # 3) dense search
    # ----------------
    dense_hits = []
    dense_by_id = {}
    try:
        qdrant = get_qdrant()

        # if rerank enabled, we need more candidates than top_k
        dense_limit = dense_top_k if mode == "hybrid" else (req.rerank.top_n if req.rerank.enabled else req.top_k)

        dense_hits = search_points(
            qdrant,
            QDRANT_COLLECTION,
            qvec,
            limit=dense_limit,
            qdrant_filter=qfilter,
            with_payload=req.include_payload,
        )
        for i, h in enumerate(dense_hits, start=1):
            dense_by_id[str(getattr(h, "id", ""))] = (i, h)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Qdrant search failed: {e}")

    # ----------------------------
    # 4) dense-only return (+rerank)
    # ----------------------------
    if mode != "hybrid":
        candidates_items = [_item_from_dense_hit(h, float(getattr(h, "score", 0.0))) for h in dense_hits]

        # rerank (optional)
        rerank_used, rerank_latency_ms, rerank_reason = _apply_rerank_inplace(candidates_items)

        # final slice
        results = candidates_items[: req.top_k]

        latency_ms = int((time.perf_counter() - t0) * 1000)
        return SearchResponse(
            query=req.query,
            results=results,
            debug=SearchDebug(
                latency_ms=latency_ms,
                collection=QDRANT_COLLECTION,
                used_filter=bool(qfilter),
                top_k=req.top_k,
                mode="dense",
                dense_hits=len(dense_hits),
                bm25_hits=0,
                dense_top_k=len(dense_hits),
                bm25_top_k=0,
                rrf_k=rrf_k,
                # M3 debug
                rerank_used=rerank_used,
                rerank_latency_ms=rerank_latency_ms,
                rerank_fallback_reason=rerank_reason,
                candidates_n=len(candidates_items),
            ),
        )

    # ----------------
    # 5) keyword search (FTS) for hybrid
    # ----------------
    bm25_rows = []
    bm25_by_id = {}
    try:
        bm25_rows = search_keyword(
            req.query,
            limit=bm25_top_k,
            doc_id=doc_id,
            pipeline_version=pipeline_version,
        )
        for i, r in enumerate(bm25_rows, start=1):
            bm25_by_id[str(r["chunk_id"])] = (i, r)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FTS keyword search failed: {e}")

    # ----------------
    # 6) hybrid: RRF fusion
    # ----------------
    dense_rank = {pid: rank for pid, (rank, _) in dense_by_id.items()}
    bm25_rank = {pid: rank for pid, (rank, _) in bm25_by_id.items()}
    fused = rrf_fuse(dense_rank=dense_rank, bm25_rank=bm25_rank, rrf_k=rrf_k)

    # candidates: take top_n if rerank enabled, else top_k
    candidate_n = req.rerank.top_n if req.rerank.enabled else req.top_k
    fused_sorted = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:candidate_n]

    candidates_items: list[SearchResultItem] = []
    for pid, fused_score in fused_sorted:
        if pid in dense_by_id:
            _, h = dense_by_id[pid]
            it = _item_from_dense_hit(h, float(fused_score))
        else:
            _, r = bm25_by_id[pid]
            it = _item_from_bm25_row(pid, r, float(fused_score))

        # keep fused score in payload for debugging if you want (only when include_payload)
        if req.include_payload and it.payload is not None:
            it.payload["fused_score"] = float(fused_score)

        candidates_items.append(it)

    # ----------------
    # 7) rerank (optional) then slice top_k
    # ----------------
    rerank_used, rerank_latency_ms, rerank_reason = _apply_rerank_inplace(candidates_items)
    results = candidates_items[: req.top_k]

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return SearchResponse(
        query=req.query,
        results=results,
        debug=SearchDebug(
            latency_ms=latency_ms,
            collection=QDRANT_COLLECTION,
            used_filter=bool(qfilter),
            top_k=req.top_k,
            mode="hybrid",
            dense_hits=len(dense_hits),
            bm25_hits=len(bm25_rows),
            dense_top_k=dense_top_k,
            bm25_top_k=bm25_top_k,
            rrf_k=rrf_k,
            # M3 debug
            rerank_used=rerank_used,
            rerank_latency_ms=rerank_latency_ms,
            rerank_fallback_reason=rerank_reason,
            candidates_n=len(candidates_items),
        ),
    )

def call_llm_for_answer(prompt: str, *, return_meta: bool = False):
    llm_url = os.getenv("LLM_API_URL", "").strip()

    # fallback：不呼叫遠端 LLM
    if not llm_url:
        answer = f"(fallback) LLM_API_URL not set\n\n{prompt}"
        if return_meta:
            return answer, False, "LLM_API_URL not set"
        return answer

    try:
        # 你原本呼叫 model_api 的邏輯放這裡（保留你原本的實作）
        answer, err = call_llm(prompt, timeout=60)
        if err:
            raise RuntimeError(err)
        if return_meta:
            return answer, True, None
        return answer
    except Exception as e:
        # 遠端 LLM 失敗 → fallback
        answer = f"(fallback) LLM call failed: {e}\n\n{prompt}"
        if return_meta:
            return answer, False, f"LLM call failed: {e}"
        return answer


@app.post("/v1/answer", response_model=AnswerResponse)
def answer_v1(req: AnswerRequest):
    t0 = time.perf_counter()

    # 1) run search (reuse /v1/search logic by direct function call)
    try:
        search_req = SearchRequest(
            query=req.query,
            top_k=req.top_k,
            filters=req.filters,
            retrieval=req.retrieval,
            rerank=req.rerank,
            include_payload=True,
        )
        search_resp = semantic_search(search_req)  # <-- your /v1/search handler in same file
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Answer search stage failed: {e}")

    search_latency_ms = int((time.perf_counter() - t0) * 1000)

    # 2) build context (LLM 用) + citations (回傳用)
    hits = getattr(search_resp, "results", None) or []
    citations = []
    used_chunk_ids = []
    contexts = []

    # context 長度上限：優先用 req.gen.max_context_chars，沒給就用 4000
    max_ctx = 4000
    if getattr(req, "gen", None) and getattr(req.gen, "max_context_chars", None):
        max_ctx = int(req.gen.max_context_chars)

    cur_len = 0

    for h in hits[: req.top_k]:
        used_chunk_ids.append(h.chunk_id)

        # ✅ citations：回傳給 reviewer 的可追溯資訊（保持不變）
        snippet = (h.text or "")[:220] if getattr(h, "text", None) else None
        citations.append(
            CitationItem(
                chunk_id=h.chunk_id,
                score=float(h.score),
                doc_id=getattr(h, "doc_id", None),
                pipeline_version=getattr(h, "pipeline_version", None),
                chunk_index=getattr(h, "chunk_index", None),
                text_snippet=snippet,
            )
        )

        # ✅ contexts：只餵「純文字 chunk」給 LLM（避免把 citations dict 印進 prompt）
        chunk_text = (getattr(h, "text", None) or "").strip()
        if not chunk_text:
            continue

        block = f"[{h.chunk_id}]\n{chunk_text}\n"
        if cur_len + len(block) > max_ctx:
            # 還可以塞一點尾巴（可選）：把剩餘空間塞滿後 break
            remain = max_ctx - cur_len
            if remain > 80:  # 留一點空間，避免切太碎
                contexts.append(block[:remain])
            break

        contexts.append(block)
        cur_len += len(block)

    # 3) prompt：乾淨、可追溯、RAG 友善
    chunks_section = "\n".join(contexts) if contexts else "(no chunks)"

    prompt = (
        "你是一個嚴謹的資料工程/文件理解助理。\n"
        "請嚴格根據提供的 Chunks 回答問題；若資訊不足，請明確說不足。\n"
        "回答時請在關鍵句後面用 [chunk_id] 標註依據（可多個）。\n\n"
        f"問題：{req.query}\n\n"
        "Chunks:\n"
        f"{chunks_section}\n"
    )
    # 3) LLM generate
    t1 = perf_counter()
    answer_text, llm_used, llm_reason = call_llm_for_answer(prompt, return_meta=True)
    llm_latency_ms = int((perf_counter() - t1) * 1000)

    # 4) debug
    debug = getattr(search_resp, "debug", None)
    rerank_used = bool(getattr(debug, "rerank_used", False)) if debug else False
    candidates_n = int(getattr(debug, "candidates_n", len(hits))) if debug else len(hits)

    return AnswerResponse(
        query=req.query,
        answer=answer_text,
        citations=citations,
        debug=AnswerDebug(
            search_latency_ms=search_latency_ms,
            llm_latency_ms=llm_latency_ms,
            rerank_used=rerank_used,
            candidates_n=candidates_n,
            used_chunk_ids=used_chunk_ids,
            llm_used=llm_used,
            llm_fallback_reason=llm_reason,
        ),
    )

@app.post("/v1/reindex/fts")
def reindex_fts(batch_size: int = 256):
    """
    Build SQLite FTS index from Qdrant payloads.
    v1: batch reindex for stability (no sync write during ingest).

    Key goals:
    - never crash the API process (return 500 with detail instead of connection reset)
    - compatible with qdrant-client API differences (scroll return types & param names)
    - index both chunk text and selected metadata into one searchable content field
    """
    t0 = time.perf_counter()

    try:
        # 0) init fts schema
        reset_fts()

        qdrant = get_qdrant()

        total_indexed = 0
        batches = 0
        next_offset = None

        def _normalize_scroll_response(resp):
            """
            qdrant-client versions differ:
            - may return (points, next_offset)
            - may return an object with .points / .result and .next_page_offset
            """
            if isinstance(resp, tuple) and len(resp) == 2:
                pts, noff = resp
                return pts or [], noff

            pts = getattr(resp, "points", None)
            if pts is None:
                pts = getattr(resp, "result", None)
            pts = pts or []

            noff = getattr(resp, "next_page_offset", None)
            return pts, noff

        while True:
            # 1) scroll a batch (compat layer)
            try:
                resp = qdrant.scroll(
                    collection_name=QDRANT_COLLECTION,
                    limit=batch_size,
                    offset=next_offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except TypeError:
                # older variants: try without with_vectors
                resp = qdrant.scroll(
                    collection_name=QDRANT_COLLECTION,
                    limit=batch_size,
                    offset=next_offset,
                    with_payload=True,
                )
            except Exception:
                # last resort: minimal args
                resp = qdrant.scroll(
                    collection_name=QDRANT_COLLECTION,
                    limit=batch_size,
                    offset=next_offset,
                )

            points, next_offset = _normalize_scroll_response(resp)

            if not points:
                break

            # 2) transform points -> fts chunks
            chunks = []
            for p in points:
                pid = str(getattr(p, "id", ""))

                payload = getattr(p, "payload", None) or {}
                doc_id = payload.get("doc_id")
                pv = payload.get("pipeline_version")
                chunk_index = payload.get("chunk_index")

                # these might or might not exist in payload depending on your pipeline
                source_file = payload.get("source_file") or payload.get("source")  # fallback
                job_id = payload.get("job_id")

                text = payload.get("text") or ""
                if not isinstance(text, str):
                    text = str(text)

                # searchable content = text + selected metadata (keyword-friendly)
                # NOTE: avoid ':' tokenization pitfalls by also adding space-separated variants
                parts = [text]
                if doc_id:
                    parts.append(f"doc_id {doc_id}")
                if source_file:
                    parts.append(f"source_file {source_file}")
                if job_id:
                    parts.append(f"job_id {job_id}")
                if pv:
                    parts.append(f"pipeline_version {pv}")

                content = "\n".join([x for x in parts if x])

                chunks.append(
                    {
                        "chunk_id": pid,
                        "doc_id": doc_id,
                        "pipeline_version": pv,
                        "chunk_index": chunk_index,
                        "source_file": source_file,
                        "job_id": job_id,
                        "content": content,
                        # keep raw text too (optional)
                        "text": text,
                    }
                )

            # 3) write to sqlite
            try:
                bulk_upsert(chunks)
            except Exception as e:
                raise RuntimeError(
                    f"bulk_upsert failed (batch={batches}, size={len(chunks)}): {e}"
                )

            total_indexed += len(chunks)
            batches += 1

            # scroll termination condition
            if next_offset is None:
                break

        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "indexed_chunks": total_indexed,
            "batches": batches,
            "collection": QDRANT_COLLECTION,
            "db_path": os.getenv("FTS_DB_PATH", "/app/data/fts.db"),
            "latency_ms": latency_ms,
        }

    except Exception as e:
        # IMPORTANT: never crash uvicorn; return 500 JSON with full trace
        tb = traceback.format_exc()
        print("reindex_fts failed:", tb, flush=True)
        raise HTTPException(status_code=500, detail=f"reindex failed: {e}")