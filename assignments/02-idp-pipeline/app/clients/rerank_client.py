import os
import requests
from typing import Dict, List, Any, Optional, Tuple

RERANK_URL = os.getenv("RERANK_URL", "").strip()

class RerankError(RuntimeError):
    pass

def rerank_remote(
    query: str,
    candidates: List[Dict[str, str]],
    timeout_ms: int = 2000,
) -> Tuple[Dict[str, float], int]:
    """
    Call remote rerank service.
    Returns: (id->score, latency_ms)
    """
    if not RERANK_URL:
        raise RerankError("RERANK_URL is not set")

    payload = {"query": query, "candidates": candidates}

    try:
        resp = requests.post(
            RERANK_URL,
            json=payload,
            timeout=timeout_ms / 1000.0,
        )
        resp.raise_for_status()
        data = resp.json()

        scores_list = data.get("scores", [])
        out: Dict[str, float] = {}
        for item in scores_list:
            cid = str(item.get("id", ""))
            if not cid:
                continue
            out[cid] = float(item.get("score", 0.0))

        latency_ms = int(data.get("latency_ms", 0))  # optional from server
        return out, latency_ms

    except requests.exceptions.Timeout as e:
        raise RerankError(f"timeout after {timeout_ms}ms") from e
    except Exception as e:
        raise RerankError(f"remote rerank failed: {e}") from e
