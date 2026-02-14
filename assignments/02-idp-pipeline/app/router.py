import re
from typing import Tuple
from .state import Route

TABLE_HINTS = [
    "表格", "欄位", "列", "行", "csv", "excel", "xlsx", "tsv",
    "header", "row", "column", "schema"
]
FIGURE_HINTS = [
    "圖", "圖表", "趨勢", "折線", "長條", "圓餅", "曲線", "scatter",
    "chart", "plot", "figure", "diagram"
]

def decide_route(text: str) -> Tuple[Route, float, str]:
    """
    Returns: (route, confidence, reason)
    """
    t = text.lower()

    # 1) 明顯表格/結構化描述 → OCR
    if any(k in text for k in TABLE_HINTS):
        return Route.ocr, 0.75, "Detected table/structured keywords"

    # 2) 圖表/視覺理解描述 → VLM
    if any(k in text for k in FIGURE_HINTS):
        return Route.vlm, 0.75, "Detected figure/chart keywords"

    # 3) 偵測大量分隔符（像表格）
    if re.search(r"[,|\t;]{3,}", text):
        return Route.ocr, 0.65, "Detected dense delimiters (csv-like)"

    # 4) 預設：VLM（偏視覺語意理解，之後可調）
    return Route.vlm, 0.55, "Default route (no strong table hints)"
