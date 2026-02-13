import time

def run_job(payload: dict) -> dict:
    """
    背景任務：模擬耗時工作
    """
    time.sleep(2)  # 模擬耗時處理（例如 OCR/VLM/LLM）
    text = payload.get("text", "")
    return {
        "ok": True,
        "echo": text,
        "len": len(text),
        "note": "Replace this with real pipeline later."
    }
