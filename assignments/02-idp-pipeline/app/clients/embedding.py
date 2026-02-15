# app/clients/embedding_client.py (示意)
from sentence_transformers import SentenceTransformer
import torch

_model = None

def get_embedder():
    global _model
    if _model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device=device)
    return _model

def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return vecs.tolist()