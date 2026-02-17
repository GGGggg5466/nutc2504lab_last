from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Literal
from .state import JobStatus, Route, InputType

class CreateJobRequest(BaseModel):
    text: str
    input_type: InputType = InputType.text
    route: Route = Route.auto   # auto/ocr/vlm

class CreateJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    queue: str = "default"

class JobResult(BaseModel):
    ok: bool
    echo: str
    len: int
    note: Optional[str] = None

class GetJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    result: Optional[Any] = None
    error: Optional[str] = None

# ----------------------------
# Day5: Semantic Search API v1
# ----------------------------

class RerankConfig(BaseModel):
    enabled: bool = False
    timeout_ms: int = Field(2000, ge=200, le=20000)
    top_n: int = Field(50, ge=5, le=200)  # candidates size

class SearchFilters(BaseModel):
    doc_id: Optional[str] = None
    pipeline_version: Optional[str] = None

class RetrievalConfig(BaseModel):
    mode: Literal["dense", "hybrid"] = "dense"
    dense_top_k: int = Field(50, ge=1, le=200)
    bm25_top_k: int = Field(50, ge=1, le=200)
    rrf_k: int = Field(60, ge=1, le=200)

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query text")
    top_k: int = Field(10, ge=1, le=50, description="Number of results to return")
    filters: Optional[SearchFilters] = None

    retrieval: RetrievalConfig = RetrievalConfig()

    include_payload: bool = True  # v1 先固定回 payload（含 text/lineage）
    rerank: RerankConfig = RerankConfig()

class SearchResultItem(BaseModel):
    score: float
    chunk_id: str

    doc_id: Optional[str] = None
    pipeline_version: Optional[str] = None
    chunk_index: Optional[int] = None
    text: Optional[str] = None

    payload: Optional[Dict[str, Any]] = None

class SearchDebug(BaseModel):
    latency_ms: int
    collection: str
    used_filter: bool
    top_k: int

    mode: str
    dense_hits: int
    bm25_hits: int
    dense_top_k: int
    bm25_top_k: int
    rrf_k: int
    rerank_used: bool
    rerank_latency_ms: int
    rerank_fallback_reason: Optional[str] = None
    candidates_n: int

class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    debug: SearchDebug

class AnswerGenConfig(BaseModel):
    max_context_chars: int = Field(6000, ge=500, le=30000)
    style: str = Field("normal", description="concise|normal")
    force_citations: bool = Field(True, description="If model returns no [chunk_id], append citations automatically")

class CitationItem(BaseModel):
    chunk_id: str
    score: float

    doc_id: Optional[str] = None
    pipeline_version: Optional[str] = None
    chunk_index: Optional[int] = None

    text_snippet: Optional[str] = None

class AnswerDebug(BaseModel):
    search_latency_ms: int
    llm_latency_ms: int
    rerank_used: bool
    candidates_n: int
    used_chunk_ids: List[str]

    # ✅ 新增：讓 llm_latency_ms=0 可解釋
    llm_used: bool = False
    llm_fallback_reason: Optional[str] = None

class AnswerRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=20)

    # 下面三個都沿用你 /v1/search 的結構
    filters: Optional[SearchFilters] = None
    retrieval: RetrievalConfig = RetrievalConfig()
    rerank: RerankConfig = RerankConfig()

    gen: AnswerGenConfig = AnswerGenConfig()

class AnswerResponse(BaseModel):
    query: str
    answer: str
    citations: List[CitationItem]
    debug: AnswerDebug