import os, re
import sqlite3
from typing import List, Optional, Dict, Any, Tuple

DEFAULT_DB_PATH = os.getenv("FTS_DB_PATH", "/app/data/fts.db")

def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_fts(db_path: str = DEFAULT_DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(
                chunk_id UNINDEXED,
                doc_id UNINDEXED,
                pipeline_version UNINDEXED,
                chunk_index UNINDEXED,
                source_file UNINDEXED,
                job_id UNINDEXED,
                content
            );
        """)
        conn.commit()
    finally:
        conn.close()

def reset_fts(db_path: str = DEFAULT_DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS chunks_fts;")
        conn.commit()
    finally:
        conn.close()
    init_fts(db_path)

def upsert_chunk(
    chunk_id: str,
    doc_id: Optional[str],
    pipeline_version: Optional[str],
    chunk_index: Optional[int],
    text: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """
    FTS virtual table doesn't reliably support INSERT OR REPLACE across versions,
    so we do delete + insert.
    """
    if not text:
        return

    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
        conn.execute(
            "INSERT INTO chunks_fts(chunk_id, doc_id, pipeline_version, chunk_index, text) VALUES (?, ?, ?, ?, ?)",
            (chunk_id, doc_id, pipeline_version, chunk_index, text),
        )
        conn.commit()
    finally:
        conn.close()

def bulk_upsert(chunks: List[Dict[str, Any]], db_path: str = DEFAULT_DB_PATH) -> int:
    """
    chunks: [{chunk_id, doc_id, pipeline_version, chunk_index, text}, ...]
    """
    if not chunks:
        return 0

    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        # speed: single transaction
        for c in chunks:
            text = c.get("text") or ""
            if not text:
                continue
            chunk_id = str(c.get("chunk_id") or "")
            if not chunk_id:
                continue
            cur.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
            cur.execute(
                "INSERT INTO chunks_fts(chunk_id, doc_id, pipeline_version, chunk_index, source_file, job_id, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    c.get("doc_id"),
                    c.get("pipeline_version"),
                    c.get("chunk_index"),
                    c.get("source_file"),
                    c.get("job_id"),
                    c.get("content"),
                ),
            )
        conn.commit()
        return len(chunks)
    finally:
        conn.close()

def _auto_prefix(q: str) -> str:
    q = q.strip()
    if not q:
        return q

    # 如果使用者已經寫了 FTS 語法（有空白/引號/OR/AND/*/()），就不要動
    if any(x in q for x in ['"', "'", " OR ", " AND ", "(", ")", "*"]):
        return q

    # 純代碼/數字/英數混合，且長度>=3：自動做 prefix match
    if re.fullmatch(r"[A-Za-z0-9_.-]{3,}", q):
        return q + "*"

    return q

def search_keyword(
    query: str,
    *,
    limit: int = 50,
    doc_id: Optional[str] = None,
    pipeline_version: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """
    FTS5 bm25(): smaller is better, so ORDER BY score ASC.
    """
    init_fts(db_path)

    query2 = _auto_prefix(query)
    where = ["chunks_fts MATCH ?"]
    params = [query2]

    if doc_id:
        where.append("doc_id = ?")
        params.append(doc_id)

    if pipeline_version:
        where.append("pipeline_version = ?")
        params.append(pipeline_version)

    sql = f"""
        SELECT
            chunk_id,
            doc_id,
            pipeline_version,
            chunk_index,
            source_file,
            job_id,
            bm25(chunks_fts) AS bm25_score,
            snippet(chunks_fts, 6, '[', ']', '…', 12) AS snippet,
            content
        FROM chunks_fts
        WHERE {" AND ".join(where)}
        ORDER BY bm25_score ASC
        LIMIT ?
    """
    params.append(limit)

    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [ {**dict(r), "text": dict(r).get("content")} for r in rows ]
    finally:
        conn.close()
