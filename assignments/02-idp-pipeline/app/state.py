from enum import Enum

class JobStatus(str, Enum):
    queued = "queued"
    started = "started"
    finished = "finished"
    failed = "failed"

class Route(str, Enum):
    auto = "auto"
    ocr = "ocr"
    vlm = "vlm"
    pipeline = "pipeline"

class InputType(str, Enum):
    text = "text"
    image = "image"
    pdf = "pdf"