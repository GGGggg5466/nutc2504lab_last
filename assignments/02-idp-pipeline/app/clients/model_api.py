import os
import requests
from typing import Any, Dict, Tuple, Optional


def _post_json(url: str, payload: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def call_ocr(text: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Return: (payload, error)
    payload: normalized OCR result
    """
    url = os.getenv("OCR_API_URL", "").strip()
    model = os.getenv("OCR_MODEL", "").strip()

    if not url:
        return {}, "OCR_API_URL is empty"

    # 依你們的 OpenAI-compatible /v1/chat/completions 形式去包
    req = {
        "model": model or "unknown",
        "messages": [
            {"role": "system", "content": "You are an OCR extraction engine. Return structured JSON."},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
    }

    try:
        raw = _post_json(url, req, timeout=90)
        # 你可以先用最保守方式抽文字（依常見格式）
        content = (
            raw.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return {
            "engine": "ocr-api",
            "raw": raw,
            "text": content,
            "tables": [],  # 之後你再把表格解析補上
        }, None
    except Exception as e:
        return {}, f"OCR call failed: {e}"


def call_vlm(text: str) -> Tuple[Dict[str, Any], Optional[str]]:
    url = os.getenv("VLM_API_URL", "").strip()
    model = os.getenv("VLM_MODEL", "").strip()

    if not url:
        return {}, "VLM_API_URL is empty"

    req = {
        "model": model or "unknown",
        "messages": [
            {"role": "system", "content": "You are a vision-language model. Return structured JSON."},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
    }

    try:
        raw = _post_json(url, req, timeout=90)
        content = (
            raw.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return {
            "engine": "vlm-api",
            "raw": raw,
            "caption": content,
        }, None
    except Exception as e:
        return {}, f"VLM call failed: {e}"
