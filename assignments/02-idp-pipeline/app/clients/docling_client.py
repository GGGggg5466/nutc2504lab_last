from docling.document_converter import DocumentConverter

def run_docling(file_path: str):
    converter = DocumentConverter()
    doc = converter.convert(file_path)

    return {
        "engine": "docling",
        "content": doc.export_to_markdown(),
    }
