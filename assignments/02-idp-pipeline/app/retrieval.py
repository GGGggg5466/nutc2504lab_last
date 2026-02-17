from typing import Dict, List, Tuple, Optional, Any

def rrf_fuse(
    *,
    dense_rank: Dict[str, int],
    bm25_rank: Dict[str, int],
    rrf_k: int = 60,
) -> Dict[str, float]:
    """
    RRF score = sum(1/(k + rank)).
    rank starts from 1.
    """
    ids = set(dense_rank.keys()) | set(bm25_rank.keys())
    out: Dict[str, float] = {}
    for pid in ids:
        s = 0.0
        if pid in dense_rank:
            s += 1.0 / (rrf_k + dense_rank[pid])
        if pid in bm25_rank:
            s += 1.0 / (rrf_k + bm25_rank[pid])
        out[pid] = s
    return out
