"""
chunk_store.py : 청크를 DB 에 넣고 빼는 일만 담당합니다. (evidence_store.py 와 같은 자리)

    from chunk_store import save_chunks, get_chunks, delete_chunks

    save_chunks(chunks)                  # 청킹 결과를 저장 (같은 증적의 옛 청크는 지우고 새로 넣음)
    get_chunks("E0001")                  # 그 증적의 청크 전부 (chunk_index 순서)
    get_chunks("E0001", version=1)       # 그 버전만
    delete_chunks("E0001")               # 그 증적의 청크 삭제
    count_chunks()                       # 전체 개수

청크 13칸은 chunking/chunk_format.py 의 CHUNK_KEYS 와 같습니다.
증적을 지우면 그 증적의 청크도 DB 가 알아서 지웁니다 (ON DELETE CASCADE).
"""

import json

from config import KST
from db import get_connection

COLUMNS = ["chunk_id", "evidence_id", "version", "chunk_index", "file_type", "source_file",
           "chunk_type", "page_start", "page_end", "heading", "text", "block_orders", "source"]


class ChunkError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _now():
    from datetime import datetime
    return datetime.now(KST).replace(tzinfo=None)


def _check(chunks):
    """넣기 전에 칸이 다 있는지만 확인합니다. (규칙 검사는 validate_chunks 가 담당)"""
    if not isinstance(chunks, list) or not chunks:
        raise ChunkError("EMPTY_CHUNKS", "저장할 청크가 없습니다.")
    ids = {c["evidence_id"] for c in chunks}
    if len(ids) != 1:
        raise ChunkError("MIXED_EVIDENCE", f"한 번에 한 증적만 저장할 수 있습니다: {sorted(ids)}")
    for c in chunks:
        missing = [k for k in COLUMNS if k not in c]
        if missing:
            raise ChunkError("MISSING_FIELD", f"{c.get('chunk_id')} 에 빠진 칸: {missing}")
    return ids.pop()


def save_chunks(chunks, replace=True):
    """
    청크를 저장합니다. 성공하면 넣은 개수를 돌려줍니다.

      replace=True  : 같은 증적의 **모든 버전** 청크를 지우고 새로 넣습니다 (기본).
                      새 버전이 올라왔을 때 옛 청크가 검색에 남지 않게 하려는 것입니다.
      replace=False : 지우지 않고 넣습니다. chunk_id 가 겹치면 오류가 납니다.
    """
    evidence_id = _check(chunks)
    now = _now()
    rows = [tuple(json.dumps(c[k], ensure_ascii=False) if k == "block_orders" else c[k]
                  for k in COLUMNS) + (now,) for c in chunks]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM evidence WHERE evidence_id = %s", (evidence_id,))
            if cur.fetchone() is None:
                raise ChunkError("EVIDENCE_NOT_FOUND", f"{evidence_id} 증적이 DB 에 없습니다.")
            if replace:
                cur.execute("DELETE FROM chunk WHERE evidence_id = %s", (evidence_id,))
            cur.executemany(
                f"INSERT INTO chunk ({', '.join(COLUMNS)}, created_at) "
                f"VALUES ({', '.join(['%s'] * (len(COLUMNS) + 1))})", rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(rows)


def get_chunks(evidence_id, version=None):
    """청크를 chunk_index 순서로 돌려줍니다. (청크 JSON 13칸 그대로)"""
    sql = f"SELECT {', '.join(COLUMNS)} FROM chunk WHERE evidence_id = %s"
    args = [evidence_id]
    if version is not None:
        sql += " AND version = %s"
        args.append(version)
    sql += " ORDER BY version, chunk_index"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(args))
            rows = cur.fetchall()
    finally:
        conn.close()
    for r in rows:
        r["block_orders"] = json.loads(r["block_orders"])
    return rows


def delete_chunks(evidence_id, version=None):
    """청크를 지웁니다. 지운 개수를 돌려줍니다."""
    sql = "DELETE FROM chunk WHERE evidence_id = %s"
    args = [evidence_id]
    if version is not None:
        sql += " AND version = %s"
        args.append(version)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(args))
            n = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return n


def count_chunks(evidence_id=None):
    """청크 개수. evidence_id 를 주면 그 증적만."""
    sql = "SELECT COUNT(*) AS n FROM chunk"
    args = ()
    if evidence_id is not None:
        sql += " WHERE evidence_id = %s"
        args = (evidence_id,)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()["n"]
    finally:
        conn.close()
