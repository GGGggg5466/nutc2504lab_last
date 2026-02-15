import os, requests, uuid
import time
import inspect
import json
import re
from typing import Any, Dict, Optional, Tuple
from hashlib import sha256
from rq import get_current_job

from .queue import redis_conn, set_status, set_result, set_error
from .state import JobStatus, Route
from .router import decide_route

# 你原本的 client / mock
from app.clients.model_api import call_ocr, call_vlm
from app.clients.embedding import embed_texts
from app.clients.qdrant_client import get_qdrant, ensure_collection, upsert_points
from app.mocks import mock_ocr, mock_vlm

from qdrant_client.http.models import PointStruct

DEFAULT_TIMEOUT_SEC = int(os.getenv("DEFAULT_TIMEOUT_SEC", "20"))
USE_REAL_API = os.getenv("USE_REAL_API", "0") == "1"
COLLECTION = os.getenv("QDRANT_COLLECTION", "idp_chunks")


# =========================
#  Input Type Inference
# =========================
def infer_input_type(text: str) -> Tuple[str, Optional[str]]:
    """
    回傳 (input_type, path_or_none)

    規則（先用最簡單可工作的版本）：
    - 若 text 看起來像檔案路徑且結尾是 .pdf -> ("pdf", path)
    - 若結尾是 .png/.jpg/.jpeg/.webp -> ("image", path)
    - 否則 -> ("text", None)

    你之後若要更正式，建議把 input_type/file_path 放到 CreateJobRequest schema 裡。
    """
    t = (text or "").strip()

    lower = t.lower()
    if lower.endswith(".pdf"):
        return "pdf", t
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return "image", t

    return "text", None


# =========================
#  Docling / EasyOCR (stub)
# =========================
def run_docling(pdf_path: str) -> Dict[str, Any]:
    """
    這裡先做 stub：回傳你後續 pipeline 需要的結構。
    你真的要接 Docling 時，就在這裡 import docling 並實作解析。
    """
    # TODO: integrate real Docling here
    return {
        "engine": "docling-stub",
        "pdf_path": pdf_path,
        "text": f"[DOC FROM {pdf_path}]\nThis is a PDF test document. ...",
        "tables": [],
    }


def run_easyocr(image_path: str) -> Dict[str, Any]:
    """
    stub：之後換成 EasyOCR 真實推論
    """
    # TODO: integrate real EasyOCR here
    return {
        "engine": "easyocr-stub",
        "image_path": image_path,
        "extracted_text": f"[OCR FROM {image_path}]",
        "boxes": [],
    }


# =========================
#  API Call With Feedback
# =========================
def _call_with_feedback(fn, route: str, text: str, timeout: int):
    """
    统一封装：调用外部 API，并回传 (payload, api_feedback)

    兼容两种 fn 回传格式：
    1) payload
    2) (payload, error_str)   <-- model_api.call_vlm/call_ocr
    """
    t0 = time.time()
    payload = None
    err = None

    try:
        result = fn(text=text, timeout=timeout)

        # ✅ 兼容 (payload, err) 形式
        if isinstance(result, tuple) and len(result) == 2:
            payload, err = result
        else:
            payload = result
            err = None

    except requests.Timeout:
        payload = None
        err = "timeout"
    except Exception as e:
        payload = None
        err = str(e)

    latency_ms = int((time.time() - t0) * 1000)

    api_feedback = {
        "mode": "real" if USE_REAL_API else "mock",
        "route": route,
        "ok": (payload is not None) and (not err),
        "latency_ms": latency_ms,
        "error": err,
    }

    return payload, api_feedback

def get_vlm_text(v: dict) -> str:
    if not isinstance(v, dict):
        return ""
    return v.get("caption") or v.get("text") or ""

# =========================
#  RQ Worker Job
# =========================
def run_job(text: str, route: str, input_type: str = "text"):
    """
    RQ worker 執行的 job

    - route: "auto" | "ocr" | "vlm"
    - input_type: "text" | "image" | "pdf"  （由 API 明確傳入；若沒傳或不合法會 fallback）
    - text: 目前仍沿用：
        - text=input文字
        - image/pdf 時 text 放檔案路徑（例如 /data/a.jpg, /data/a.pdf）
    """
    job = get_current_job()
    job_id = job.id if job else None

    set_status(redis_conn, job_id, JobStatus.started.value)

    try:
        # 0) failure 測試
        if "please fail" in (text or "").lower():
            raise RuntimeError("Forced failure for testing")
        
        # 1) route enum
        route_enum = Route(route)  # "auto"/"ocr"/"vlm"/"pipeline"

        # 2) input_type：以 API 傳入為準，不合法就 fallback
        input_type = (input_type or "text").strip().lower()
        if input_type not in ("text", "image", "pdf"):
            input_type = "text"

        # （可選備援：如果你想保留自動推測）
        # inferred_type, inferred_path = infer_input_type(text)
        # if input_type == "text" and inferred_type in ("image", "pdf"):
        #     input_type = inferred_type

        # 3) route 決策（修正 decide_route：它回傳 tuple，不是 dict）
        if route_enum == Route.auto:
            hint_route, hint_conf, hint_reason = decide_route(text)
            route_hint = {
                "route": hint_route.value,
                "confidence": hint_conf,
                "reason": hint_reason,
            }

            # ✅ Day3 MVP：若是 image/pdf，auto 先強制走 OCR（最穩交付）
            if input_type in ("image", "pdf"):
                chosen_route = "ocr"
                route_hint = {
                    "route": "ocr",
                    "confidence": 0.90,
                    "reason": f"Forced OCR for {input_type} in Day3 MVP",
                }
            else:
                chosen_route = hint_route.value
        else:
            route_hint = {
                "route": route_enum.value,
                "confidence": 1.0,
                "reason": "Route forced by request",
            }
            chosen_route = route_enum.value

        # 4) 依 chosen_route + input_type 分流
        api_feedback: Dict[str, Any] = {"mode": "local", "ok": True, "error": None}
        payload: Any = None

        # A 方案：image/pdf 的路徑仍放在 text
        path = (text or "").strip()

        if chosen_route == "ocr":

            if input_type == "image":
                if not path:
                    raise RuntimeError("input_type=image but empty path/text")
                payload = run_easyocr(path)  # stub (之後換真 EasyOCR)
                api_feedback = {"mode": "local", "route": "easyocr", "ok": True, "latency_ms": 0, "error": None}

            elif input_type == "pdf":
                if not path:
                    raise RuntimeError("input_type=pdf but empty path/text")
                payload = run_docling(path)  # stub (之後換真 Docling)
                api_feedback = {"mode": "local", "route": "docling", "ok": True, "latency_ms": 0, "error": None}

            else:
                # text 型：走 OCR API（或 mock）
                if USE_REAL_API:
                    payload, api_feedback = _call_with_feedback(
                        call_ocr, route="ocr", text=text, timeout=DEFAULT_TIMEOUT_SEC
                    )
                    if payload is None:
                        raise RuntimeError(f"OCR API call failed: {api_feedback.get('error')}")
                else:
                    payload = mock_ocr(text)
                    api_feedback = {"mode": "mock", "route": "ocr", "ok": True, "latency_ms": 0, "error": None}

        elif chosen_route == "vlm":
            # ✅ Day3/Day4: VLM route (text prompt) + normalize JSON
            if USE_REAL_API:
                vlm_payload, api_feedback = _call_with_feedback(
                    call_vlm, route="vlm", text=text, timeout=DEFAULT_TIMEOUT_SEC
                )
                if vlm_payload is None:
                    raise RuntimeError(f"VLM API call failed: {api_feedback.get('error')}")
            else:
                vlm_payload = mock_vlm(text)
                api_feedback = {"mode": "mock", "route": "vlm", "ok": True, "latency_ms": 0, "error": None}

            raw_caption = (vlm_payload or {}).get("caption", "")
            normalized = extract_json_obj(raw_caption)

            payload = {
                "engine": "vlm-api",
                "raw": (vlm_payload or {}).get("raw") if isinstance(vlm_payload, dict) else vlm_payload,
                "raw_caption": raw_caption,
                "normalized": normalized,  # dict 或 None
            }
        
        elif chosen_route == "pipeline":

            stages = []
            intermediate_text = text

            extracted_text = None
            ocr_text = None
            
            if input_type in ("image", "pdf") and not path:
                raise RuntimeError(f"input_type={input_type} but empty path/text")
            

            # 1️⃣ OCR / Docling 階段
            if input_type == "image":
                stages.append("ocr")
                ocr_payload = run_easyocr(path)
                ocr_text = ocr_payload.get("extracted_text", "")
                extracted_text = ocr_text
                intermediate_text = extracted_text

            elif input_type == "pdf":
                stages.append("ocr")
                doc_payload = run_docling(path)
                extracted_text = doc_payload.get("text", "")
                intermediate_text = extracted_text

            # 2️⃣ VLM 階段
            stages.append("vlm")

            if USE_REAL_API:
                vlm_payload, fb = _call_with_feedback(call_vlm, route="vlm", text=intermediate_text, timeout=DEFAULT_TIMEOUT_SEC)
                if vlm_payload is None:
                    raise RuntimeError(f"VLM API call failed: {fb.get('error')}")
            else:
                vlm_payload = mock_vlm(intermediate_text)

            raw_text = get_vlm_text(vlm_payload)

            # 3️⃣ 正規化
            stages.append("normalize")

            normalized = {
                "content_text": raw_text,
                "content_json": extract_json_obj(raw_text),
            }

            # 4️⃣ Chunk
            stages.append("chunk")

            chunk_size = 300
            overlap = 50

            chunks = []
            start = 0
            idx = 0
            while start < len(raw_text):
                end = start + chunk_size
                chunk_text = raw_text[start:end]
                chunks.append({"i": idx, "text": chunk_text})
                idx += 1
                start = max(0, end - overlap)
                if start >= len(raw_text):
                    break

            # 5️⃣ Embed + Index (Day4)
            stages.append("embed")

            texts = [c["text"] for c in chunks]
            vectors = embed_texts(texts)  # List[List[float]]
            dim = len(vectors[0]) if vectors else 0

            stages.append("index")

            qdrant = get_qdrant()
            ensure_collection(qdrant, COLLECTION, dim=dim)

            # doc_id：用 input（例如檔案路徑）+ job_id 生成一個可追溯 id（最小 lineage）
            doc_seed = f"{job_id}:{path}:{input_type}"
            doc_id = sha256(doc_seed.encode("utf-8")).hexdigest()[:16]
            DOC_NS = uuid.UUID("12345678-1234-5678-1234-567812345678")

            points = []
            for c, v in zip(chunks, vectors):
                chunk_id = str(uuid.uuid5(DOC_NS, f"{doc_id}:{c['i']}"))
                points.append(
                    PointStruct(
                        id=chunk_id,
                        vector=v,
                        payload={
                            "doc_id": doc_id,
                            "job_id": job_id,
                            "chunk_index": c["i"],
                            "input_type": input_type,
                            "source": path,
                            "text": c["text"],
                            "pipeline_version": "v1",
                        },
                    )
                )

            upsert_points(qdrant, COLLECTION, points)

            payload = {
                "stages": stages,
                "ocr_text": ocr_text,
                "extracted_text": extracted_text,
                "normalized": normalized,
                "chunks": chunks,
                "raw": vlm_payload,
                "lineage": {
                    "doc_id": doc_id,
                    "pipeline_version": "v1",
                    "qdrant": {"collection": COLLECTION, "points": len(points), "dim": dim},
                },
            }

            api_feedback = {
                "mode": "pipeline",
                "route": "pipeline",
                "ok": True,
                "error": None,
            }

        else:
            raise ValueError(f"Unknown chosen_route: {chosen_route}")

        # 5) 組結果
        result = {
            "ok": True,
            "job_id": job_id,
            "route_request": route_enum.value,
            "chosen_route": chosen_route,
            "route_hint": route_hint,
            "input_type": input_type,
            "api_feedback": api_feedback,
            "payload": payload,
            "error": None,
        }

        set_result(redis_conn, job_id, result)
        set_status(redis_conn, job_id, JobStatus.finished.value)
        return result

    except Exception as e:
        err_msg = str(e)
        set_error(redis_conn, job_id, err_msg)
        set_status(redis_conn, job_id, JobStatus.failed.value)
        raise

def extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    """
    嘗試從 LLM/VLM 回傳的 content 中抽出「第一個可 parse 的 JSON object」。

    支援情境：
    1) ```json ... ``` code fence
    2) 文字中夾雜 JSON：抓第一個 '{' 到最後一個 '}'（並做簡單清理）
    3) content 本身就是 JSON

    成功回傳 dict，失敗回 None
    """
    if not text:
        return None

    s = text.strip()

    # Case 1: ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # Case 2: 直接就是 JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # Case 3: 文字中夾雜 JSON，抓最大包圍
    l = s.find("{")
    r = s.rfind("}")
    if l != -1 and r != -1 and r > l:
        candidate = s[l : r + 1].strip()

        # 簡單清理：去掉可能的前綴 "json\n{...}"
        candidate = re.sub(r"^\s*json\s*", "", candidate, flags=re.IGNORECASE)

        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None

    return None

