import os, uuid
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

def get_qdrant() -> QdrantClient:
    host = os.getenv("QDRANT_HOST", "qdrant")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    return QdrantClient(host=host, port=port)

def ensure_collection(client: QdrantClient, collection: str, dim: int):
    exists = client.collection_exists(collection_name=collection)
    if not exists:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

def upsert_points(
    client: QdrantClient,
    collection: str,
    points: List[PointStruct],
):
    client.upsert(collection_name=collection, points=points)

def build_filter(
    *,
    doc_id: Optional[str] = None,
    pipeline_version: Optional[str] = None,
) -> Optional[Filter]:
    conditions = []
    if doc_id:
        conditions.append(FieldCondition(key="doc_id", match=MatchValue(value=doc_id)))
    if pipeline_version:
        conditions.append(
            FieldCondition(key="pipeline_version", match=MatchValue(value=pipeline_version))
        )
    if not conditions:
        return None
    return Filter(must=conditions)

def search_points(
    client: QdrantClient,
    collection: str,
    query_vector: List[float],
    *,
    limit: int = 10,
    qdrant_filter: Optional[Filter] = None,
    with_payload: bool = True,
):
    """
    Compatibility wrapper for different qdrant-client versions.
    Tries:
      1) client.search(...)          (newer)
      2) client.search_points(...)   (older)
      3) client.query_points(...)    (newer query API, returns .points)
    """

    # 1) Newer style: client.search
    if hasattr(client, "search"):
        try:
            return client.search(
                collection_name=collection,
                query_vector=query_vector,
                limit=limit,
                query_filter=qdrant_filter,
                with_payload=with_payload,
            )
        except TypeError:
            return client.search(
                collection_name=collection,
                query_vector=query_vector,
                limit=limit,
                filter=qdrant_filter,
                with_payload=with_payload,
            )

    # 2) Older style: client.search_points
    if hasattr(client, "search_points"):
        try:
            return client.search_points(
                collection_name=collection,
                query_vector=query_vector,
                limit=limit,
                query_filter=qdrant_filter,
                with_payload=with_payload,
            )
        except TypeError:
            return client.search_points(
                collection_name=collection,
                query_vector=query_vector,
                limit=limit,
                filter=qdrant_filter,
                with_payload=with_payload,
            )

    # 3) Query API: client.query_points (often returns an object with `.points`)
    if hasattr(client, "query_points"):
        try:
            resp = client.query_points(
                collection_name=collection,
                query=query_vector,
                limit=limit,
                query_filter=qdrant_filter,
                with_payload=with_payload,
            )
        except TypeError:
            resp = client.query_points(
                collection_name=collection,
                query=query_vector,
                limit=limit,
                filter=qdrant_filter,
                with_payload=with_payload,
            )
        return getattr(resp, "points", resp)

    raise AttributeError(
        "Unsupported qdrant-client: missing search/search_points/query_points methods"
    )

def normalize_point_id(value) -> str:
    """
    Qdrant point id must be unsigned int or UUID.
    We always return UUID string for safety.
    """
    # already UUID
    if isinstance(value, uuid.UUID):
        return str(value)

    s = str(value)

    # if it's a 32-hex string, convert to UUID with hyphens
    if len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
        return str(uuid.UUID(hex=s))

    # otherwise generate a UUID (or use uuid5 if you want deterministic)
    return str(uuid.uuid4())
