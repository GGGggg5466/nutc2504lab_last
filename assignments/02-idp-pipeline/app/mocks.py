def mock_ocr(text: str) -> dict:
    return {
        "engine": "ocr-mock",
        "extracted_text": text,
        "tables": [],
    }

def mock_vlm(text: str) -> dict:
    return {
        "engine": "vlm-mock",
        "caption": f"VLM mock caption: {text}",
    }
