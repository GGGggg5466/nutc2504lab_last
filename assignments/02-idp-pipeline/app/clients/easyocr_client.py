import easyocr

_reader = None

def get_reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(['en', 'ch_tra'])
    return _reader

def run_easyocr(image_path: str):
    reader = get_reader()
    results = reader.readtext(image_path)
    texts = [r[1] for r in results]
    return {
        "engine": "easyocr",
        "text_blocks": texts,
    }