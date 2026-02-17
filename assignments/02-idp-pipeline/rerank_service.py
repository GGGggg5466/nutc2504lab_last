from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import time
import re

app = FastAPI(title="Mock Rerank Service", version="0.3.0")

# -------------------------
# Models
# -------------------------
class Candidate(BaseModel):
    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)

class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1)
    candidates: List[Candidate] = Field(..., min_length=1)

class ScoredItem(BaseModel):
    id: str
    score: float

class RerankResponse(BaseModel):
    scores: List[ScoredItem]
    latency_ms: int

# -------------------------
# Scoring
# -------------------------
def simple_score(q: str, t: str) -> float:
    q_tokens = set(re.findall(r"[A-Za-z0-9]+", q.lower()))
    t_tokens = set(re.findall(r"[A-Za-z0-9]+", t.lower()))
    if not q_tokens or not t_tokens:
        return 0.0
    inter = len(q_tokens & t_tokens)
    return inter / (len(q_tokens) ** 0.5)

# -------------------------
# API
# -------------------------
@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    t0 = time.perf_counter()

    out: List[ScoredItem] = []
    for c in req.candidates:
        out.append(ScoredItem(id=c.id, score=float(simple_score(req.query, c.text))))

    out.sort(key=lambda x: x.score, reverse=True)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return RerankResponse(scores=out, latency_ms=latency_ms)
