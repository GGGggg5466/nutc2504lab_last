import os, uuid
from typing import Any, Dict, List, Optional
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct

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
