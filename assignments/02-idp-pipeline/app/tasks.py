import os
import time
import inspect
from typing import Any, Dict, Optional, Tuple

from rq import get_current_job

from .queue import redis_conn, set_status, set_result, set_error
from .state import JobStatus, Route
from .router import decide_route

# 你原本的 client / mock
from app.clients.model_api import call_ocr, call_vlm
from app.mocks import mock_ocr, mock_vlm

DEFAULT_TIMEOUT_SEC = int(os.getenv("DEFAULT_TIMEOUT_SEC", "20"))
USE_REAL_API = os.getenv("USE_REAL_API", "0") == "1"


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
        "text": f"[DOC FROM {pdf_path}]",
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
def _call_with_feedback(fn, *, route: str, text: str, timeout: int) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    統一包裝：回傳 (payload, api_feedback)

    - 自動判斷 fn 是否支援 timeout 參數，避免 TypeError
    - 捕捉例外，api_feedback 會帶 error
    """
    t0 = time.time()
    payload = None
    err = None

    try:
        sig = inspect.signature(fn)
        if "timeout" in sig.parameters:
            payload = fn(text=text, timeout=timeout)
        else:
            # 你的 call_ocr/call_vlm 若只吃 text，就走這條
            payload = fn(text=text)
    except Exception as e:
        err = str(e)

    latency_ms = int((time.time() - t0) * 1000)

    api_feedback = {
        "mode": "real",
        "route": route,
        "ok": payload is not None and err is None,
        "latency_ms": latency_ms,
        "timeout_sec": timeout,
        "error": err,
    }
    return payload, api_feedback


# =========================
#  RQ Worker Job
# =========================
def run_job(text: str, route: str):
    """
    RQ worker 執行的 job

    - route: "auto" | "ocr" | "vlm"
    - text: 目前同時拿來當文字輸入/或檔案路徑（pdf/image）用
      （更正式的做法：schema 增加 input_type / file_path）
    """
    job = get_current_job()
    job_id = job.id if job else None

    # ✅ set_status / set_result / set_error 都要用 (redis_conn, job_id, value)
    set_status(redis_conn, job_id, JobStatus.started.value)

    try:
        # 0) failure 測試（你要的失敗案例）
        if "please fail" in (text or "").lower():
            raise RuntimeError("Forced failure for testing")

        # 1) 決策：route_enum + chosen_route + route_hint
        route_enum = Route(route)  # 若非法會直接丟例外（API 已用 enum 擋過通常不會）
        if route_enum == Route.auto:
            route_hint = decide_route(text)  # e.g. {"route":"ocr","confidence":0.75,"reason":"..."}
            chosen_route = route_hint["route"]
        else:
            route_hint = {"route": route_enum.value, "confidence": 1.0, "reason": "Route forced by request"}
            chosen_route = route_enum.value

        # 2) 決定 input_type（image/pdf/text）
        input_type, path = infer_input_type(text)

        # 3) 執行：依 chosen_route + input_type 分流
        api_feedback: Dict[str, Any] = {"mode": "local", "ok": True, "error": None}
        payload: Any = None

        if chosen_route == "ocr":
            # OCR：image -> EasyOCR、pdf -> Docling、text -> OCR API/Mock
            if input_type == "image":
                payload = run_easyocr(path)  # 本地 OCR（先 stub）
                api_feedback = {"mode": "local", "route": "easyocr", "ok": True, "latency_ms": 0, "error": None}

            elif input_type == "pdf":
                payload = run_docling(path)  # 本地 Docling（先 stub）
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
            # VLM：目前先用 text/prompt 方式示範
            # 真正要把圖片丟給 VLM：你後面要把 call_vlm 改成支援 image input（base64/multipart）
            if USE_REAL_API:
                payload, api_feedback = _call_with_feedback(
                    call_vlm, route="vlm", text=text, timeout=DEFAULT_TIMEOUT_SEC
                )
                if payload is None:
                    raise RuntimeError(f"VLM API call failed: {api_feedback.get('error')}")
            else:
                payload = mock_vlm(text)
                api_feedback = {"mode": "mock", "route": "vlm", "ok": True, "latency_ms": 0, "error": None}

        else:
            raise ValueError(f"Unknown chosen_route: {chosen_route}")

        # 4) 組結果（你要的欄位：route_request/chosen_route/route_hint/api_feedback）
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
        # ✅ Redis 不能塞 dict，error 統一存字串
        err_msg = str(e)
        set_error(redis_conn, job_id, err_msg)
        set_status(redis_conn, job_id, JobStatus.failed.value)
        raise
