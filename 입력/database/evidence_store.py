"""
evidence_store.py : B 담당의 핵심 파일 (장부 담당)

테이블 2개를 씁니다.
  evidence          : 지금 쓰는 증적. 증적 하나당 한 줄 (항상 최신본)
  evidence_history  : 교체되기 전 옛날 버전 보관함

이 파일에 들어 있는 함수들
  ─ compute_hash()      : 파일 지문(해시) 계산                      ← 할 일 2번
  ─ save_evidence()     : 새 증적 등록 (중복 확인·번호 발급·저장)       ← 할 일 3번 (A 파트가 호출)
  ─ replace_evidence()  : 수정된 파일로 교체 (옛날 것은 보관함으로)      ← 할 일 3번 버전 +1
  ─ get_file_path()     : 증적 파일이 어디 있는지 알려줌                ← 할 일 4번 (전처리 담당이 호출)
  ─ update_status()     : 처리 상태 바꾸기                            (각 단계 담당이 호출)
  ─ list_evidence()     : 증적 목록 조회                              ← 할 일 4번
  ─ get_evidence()      : 증적 하나 상세 + 버전 이력                   ← 할 일 4번
  ─ delete_evidence()   : 증적 삭제 (모든 버전)

JSON 칸 이름은 팀 규격을 따릅니다:
  evidence_id, version, file_name, file_type, file_size, file_hash,
  uploaded_at, status, error_code, error_message
"""

import hashlib
import os
import shutil
from datetime import datetime

from config import ALLOWED_FILE_TYPES, ID_DIGITS, ID_PREFIX, KST, STORAGE_DIR, VALID_STATUSES
from db import get_connection


# ─────────────────────────────────────────────
# 에러 : "무슨 문제인지"를 코드로 알려주기 위한 예외
# ─────────────────────────────────────────────
class EvidenceError(Exception):
    """
    예) raise EvidenceError("EVIDENCE_NOT_FOUND", "증적을 찾을 수 없습니다.")
    API는 이걸 잡아서 {"success": false, "error": {"code": ..., "message": ...}} 로 바꿔 보냅니다.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ─────────────────────────────────────────────
# 할 일 2번 : 파일 지문(해시) 계산
# ─────────────────────────────────────────────
def compute_hash(file_path):
    """
    파일 내용을 읽어서 sha256 지문(64글자)을 돌려줍니다.
    같은 파일이면 항상 같은 값, 한 글자라도 다르면 완전히 다른 값이 나옵니다.
    파일 이름은 지문에 영향을 주지 않습니다. (내용만 봄)
    """
    h = hashlib.sha256()
    with open(file_path, "rb") as f:  # "rb" = 바이트(원본 그대로)로 읽기
        # 8192바이트(8KB)씩 조금씩 읽어서 넣습니다. 큰 파일도 메모리를 거의 안 씀
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


# ─────────────────────────────────────────────
# 내부 도우미 함수들 (이름 앞에 _ 가 붙은 건 이 파일 안에서만 쓰는 함수)
# ─────────────────────────────────────────────
# 화면·API로 내보낼 칸 (SELECT * 는 나중에 칸이 늘면 원치 않는 정보가 샐 수 있어서 안 씀)
_COLUMNS = """
    evidence_id, version, file_name, file_type, file_size,
    file_hash, uploaded_at, status, error_code, error_message
"""


def _stored_file_path(evidence_id, version, file_type):
    """
    서버에 저장되는 실제 파일 경로 규칙.
    원래 파일명은 절대 쓰지 않습니다. (겹침, 한글 문제, ../ 같은 경로 조작 공격 방지)
    버전마다 파일이 따로 남습니다.  예) data/evidence/000001_v1.pdf, 000001_v2.pdf
    """
    return STORAGE_DIR / f"{evidence_id}_v{version}.{file_type}"


def _next_evidence_id(cur):
    """
    다음 증적 번호를 만듭니다. 가장 큰 번호를 찾아 +1 (한 명만 쓰는 도구라는 전제)
    번호 모양은 config.py 의 ID_PREFIX, ID_DIGITS 로 정합니다.
    """
    cur.execute(
        "SELECT MAX(evidence_id) AS last_id FROM evidence WHERE evidence_id LIKE %s",
        (ID_PREFIX + "%",),
    )
    last_id = cur.fetchone()["last_id"]  # 표가 비어 있으면 None
    if last_id is None:
        next_num = 1
    else:
        next_num = int(last_id[len(ID_PREFIX):]) + 1  # "000007" → 7 → 8
    return ID_PREFIX + str(next_num).zfill(ID_DIGITS)  # 8 → "000008"


def _now():
    """지금 한국 시간 (DB에는 +09:00 표시 없이 저장)"""
    return datetime.now(KST).replace(tzinfo=None)


def _iso(dt):
    """DB 시간 → "2026-09-19T20:25:21+09:00" 모양"""
    return dt.replace(tzinfo=KST).isoformat(timespec="seconds") if dt else None


def _to_json(row):
    """DB에서 꺼낸 한 줄을 API로 내보내기 좋은 모양으로 바꿉니다."""
    if row is None:
        return None
    result = dict(row)
    if "uploaded_at" in result:
        result["uploaded_at"] = _iso(result["uploaded_at"])
    return result


def _normalize_type(file_type):
    file_type = file_type.lower().lstrip(".")  # "PDF", ".pdf" → "pdf"
    if file_type not in ALLOWED_FILE_TYPES:
        raise EvidenceError("INVALID_FILE_TYPE", f"지원하지 않는 파일 형식입니다: {file_type}")
    return file_type


def _cleanup(conn, final_path, temp_file_path):
    """저장 도중 실패했을 때 전부 되돌리기"""
    if conn is not None:
        conn.rollback()  # DB 기록 취소
    if final_path is not None and os.path.exists(final_path):
        os.remove(final_path)  # 옮겨진 파일 삭제
    if os.path.exists(temp_file_path):
        os.remove(temp_file_path)  # 임시 파일 삭제 (쓰레기 남기지 않기)


def _find_same_file(cur, file_hash):
    """같은 지문의 파일이 이미 있으면 그 증적 번호를 돌려줌 (현재 증적 → 보관함 순서로 찾음)"""
    cur.execute("SELECT evidence_id FROM evidence WHERE file_hash = %s LIMIT 1", (file_hash,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "SELECT evidence_id FROM evidence_history WHERE file_hash = %s LIMIT 1", (file_hash,)
        )
        row = cur.fetchone()
    return row["evidence_id"] if row else None


# ─────────────────────────────────────────────
# 할 일 3번 : 새 증적 등록 (A 파트가 파일 검사를 끝낸 뒤 이 함수를 부릅니다)
# ─────────────────────────────────────────────
def save_evidence(temp_file_path, file_name, file_type):
    """
    임시 폴더에 있는 파일을 새 증적으로 등록합니다. (version 1)

    받는 값
      temp_file_path : A 파트가 임시로 저장해둔 파일 경로
      file_name      : 사용자가 올린 원래 파일명 (예: 계정관리_절차서.pdf)
      file_type      : pdf, docx, xlsx, pptx, png, jpg 중 하나

    돌려주는 값 (딕셔너리)
      evidence_id, version, file_name, file_type, file_size, file_hash,
      uploaded_at, status, is_duplicate, duplicate_of

    주의: 성공하면 임시 파일은 정식 폴더로 "이동"되어 원래 자리에서 사라집니다.
    """
    # ① 입력값 정리
    file_type = _normalize_type(file_type)
    if not os.path.exists(temp_file_path):
        raise EvidenceError("FILE_UPLOAD_FAILED", "업로드된 임시 파일을 찾을 수 없습니다.")

    # ② 파일 크기와 지문 계산 (할 일 2번)
    file_size = os.path.getsize(temp_file_path)
    file_hash = compute_hash(temp_file_path)

    conn = None
    final_path = None
    try:
        # DB 연결도 try 안에서 합니다. (연결 실패 시에도 임시 파일을 정리하기 위해)
        conn = get_connection()
        with conn.cursor() as cur:
            # ③ 중복 확인 : 같은 지문이 이미 있나? (있어도 막지 않고 알려주기만 함)
            duplicate_of = _find_same_file(cur, file_hash)

            # ④ 새 번호 발급
            evidence_id = _next_evidence_id(cur)
            version = 1

            # ⑤ DB에 한 줄 기록 (아직 commit 안 했으니 "임시 기록" 상태)
            uploaded_at = _now()
            cur.execute(
                """
                INSERT INTO evidence
                  (evidence_id, version, file_name, file_type,
                   file_size, file_hash, uploaded_at, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'UPLOADED')
                """,
                (evidence_id, version, file_name, file_type, file_size, file_hash, uploaded_at),
            )

        # ⑥ 파일을 정식 폴더로 이동
        final_path = _stored_file_path(evidence_id, version, file_type)
        os.makedirs(STORAGE_DIR, exist_ok=True)
        shutil.move(str(temp_file_path), str(final_path))

        # ⑦ 여기까지 다 성공했을 때만 DB에 "진짜 저장"
        conn.commit()

    except Exception as e:
        _cleanup(conn, final_path, temp_file_path)
        raise EvidenceError("FILE_UPLOAD_FAILED", f"파일 저장에 실패했습니다: {e}") from e
    finally:
        if conn is not None:
            conn.close()  # 성공하든 실패하든 연결은 항상 닫기

    return {
        "evidence_id": evidence_id,
        "version": version,
        "file_name": file_name,
        "file_type": file_type,
        "file_size": file_size,
        "file_hash": file_hash,
        "uploaded_at": _iso(uploaded_at),
        "status": "UPLOADED",
        "is_duplicate": duplicate_of is not None,
        "duplicate_of": duplicate_of,
    }


# ─────────────────────────────────────────────
# 할 일 3번 (버전) : 수정된 파일로 교체
# ─────────────────────────────────────────────
def replace_evidence(evidence_id, temp_file_path, file_name, file_type):
    """
    이미 있는 증적(evidence_id)을 수정된 파일로 바꿉니다.

      1) evidence 의 지금 줄을 evidence_history(보관함)에 복사
         (언제 교체됐는지는 다음 버전의 uploaded_at 으로 알 수 있음)
      2) evidence 의 그 줄을 새 파일 정보로 덮어씀 (version +1, 상태는 UPLOADED 로 초기화)
      3) 새 파일은 000001_v2.pdf 처럼 저장. 옛날 파일(000001_v1.pdf)도 지우지 않고 남겨둠

    증적 번호는 그대로라서, 화면과 다른 단계에서는 "같은 증적이 새 파일로 바뀐 것"으로 보입니다.
    """
    file_type = _normalize_type(file_type)
    if not os.path.exists(temp_file_path):
        raise EvidenceError("FILE_UPLOAD_FAILED", "업로드된 임시 파일을 찾을 수 없습니다.")

    file_size = os.path.getsize(temp_file_path)
    file_hash = compute_hash(temp_file_path)

    conn = None
    final_path = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # ① 지금 줄 가져오기 (FOR UPDATE : 교체하는 동안 다른 곳에서 못 건드리게 잠금)
            cur.execute(
                f"SELECT {_COLUMNS} FROM evidence WHERE evidence_id = %s FOR UPDATE",
                (evidence_id,),
            )
            current = cur.fetchone()
            if current is None:
                raise EvidenceError("EVIDENCE_NOT_FOUND", f"{evidence_id} 증적을 찾을 수 없습니다.")
            if current["file_hash"] == file_hash:
                raise EvidenceError("FILE_UPLOAD_FAILED", "지금 파일과 내용이 똑같아서 교체할 필요가 없습니다.")

            # ② 지금 줄을 보관함으로 복사
            now = _now()
            cur.execute(
                """
                INSERT INTO evidence_history
                  (evidence_id, version, file_name, file_type, file_size, file_hash,
                   uploaded_at, status, error_code, error_message)
                SELECT evidence_id, version, file_name, file_type, file_size, file_hash,
                       uploaded_at, status, error_code, error_message
                  FROM evidence WHERE evidence_id = %s
                """,
                (evidence_id,),
            )

            # ③ 지금 줄을 새 파일 정보로 덮어쓰기
            version = current["version"] + 1
            cur.execute(
                """
                UPDATE evidence
                   SET version = %s, file_name = %s, file_type = %s, file_size = %s,
                       file_hash = %s, uploaded_at = %s,
                       status = 'UPLOADED', error_code = NULL, error_message = NULL
                 WHERE evidence_id = %s
                """,
                (version, file_name, file_type, file_size, file_hash, now, evidence_id),
            )

        # ④ 새 파일 저장 (옛날 파일은 그대로 둠)
        final_path = _stored_file_path(evidence_id, version, file_type)
        os.makedirs(STORAGE_DIR, exist_ok=True)
        shutil.move(str(temp_file_path), str(final_path))

        conn.commit()

    except EvidenceError:
        _cleanup(conn, final_path, temp_file_path)
        raise
    except Exception as e:
        _cleanup(conn, final_path, temp_file_path)
        raise EvidenceError("FILE_UPLOAD_FAILED", f"파일 교체에 실패했습니다: {e}") from e
    finally:
        if conn is not None:
            conn.close()

    return {
        "evidence_id": evidence_id,
        "version": version,
        "previous_version": version - 1,
        "file_name": file_name,
        "file_type": file_type,
        "file_size": file_size,
        "file_hash": file_hash,
        "uploaded_at": _iso(now),
        "status": "UPLOADED",
    }


# ─────────────────────────────────────────────
# 할 일 4번 : 파일 위치 알려주기 (전처리 담당이 씀)
# ─────────────────────────────────────────────
def get_file_path(evidence_id, version=None):
    """
    증적 번호를 주면 실제 파일 경로를 돌려줍니다.
    version 을 안 주면 지금 파일(최신), 주면 그 버전 파일 (보관함에서도 찾음)
    예) get_file_path("000001")    → ".../data/evidence/000001_v2.pdf"
        get_file_path("000001", 1) → ".../data/evidence/000001_v1.pdf"
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version, file_type FROM evidence WHERE evidence_id = %s", (evidence_id,)
            )
            row = cur.fetchone()
            if row is not None and version is not None and row["version"] != version:
                cur.execute(
                    """SELECT version, file_type FROM evidence_history
                        WHERE evidence_id = %s AND version = %s""",
                    (evidence_id, version),
                )
                row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        what = evidence_id if version is None else f"{evidence_id} v{version}"
        raise EvidenceError("EVIDENCE_NOT_FOUND", f"{what} 증적을 찾을 수 없습니다.")

    path = _stored_file_path(evidence_id, row["version"], row["file_type"])
    if not os.path.exists(path):
        raise EvidenceError("FILE_NOT_FOUND", f"DB에는 있지만 파일이 없습니다: {path.name}")
    return str(path)


# ─────────────────────────────────────────────
# 상태 바꾸기 (각 단계 담당이 씀) — 항상 지금 버전의 상태를 바꿉니다
# ─────────────────────────────────────────────
def update_status(evidence_id, status, error_code=None, error_message=None):
    """
    예) update_status("000001", "PREPROCESSING")
        update_status("000001", "FAILED", error_code="FILE_PARSE_FAILED",
                      error_message="PDF에서 텍스트를 추출하지 못했습니다.")
    """
    # 정해진 8개 상태값이 아니면 거부 (오타·소문자 방지)
    if status not in VALID_STATUSES:
        raise ValueError(f"허용되지 않은 상태값입니다: {status} (허용: {sorted(VALID_STATUSES)})")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM evidence WHERE evidence_id = %s", (evidence_id,))
            if cur.fetchone() is None:
                raise EvidenceError("EVIDENCE_NOT_FOUND", f"{evidence_id} 증적을 찾을 수 없습니다.")
            cur.execute(
                """
                UPDATE evidence
                   SET status = %s, error_code = %s, error_message = %s
                 WHERE evidence_id = %s
                """,
                (status, error_code, error_message, evidence_id),
            )
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────
# 할 일 4번 : 조회 (API·화면이 씀)
# ─────────────────────────────────────────────
def list_evidence(status=None, page=1, size=20):
    """
    지금 증적 목록을 최신 업로드 순으로 돌려줍니다. (교체된 옛날 버전은 안 나옴)
    status 를 주면 그 상태인 것만, page/size 로 나눠서 가져옵니다.
    """
    page = max(1, int(page))
    size = min(max(1, int(size)), 100)  # 한 번에 최대 100개
    where, params = "", []
    if status:
        where, params = "WHERE status = %s", [status]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM evidence {where}", params)
            total = cur.fetchone()["n"]
            cur.execute(
                f"SELECT {_COLUMNS} FROM evidence {where} "
                "ORDER BY uploaded_at DESC LIMIT %s OFFSET %s",
                params + [size, (page - 1) * size],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return {"items": [_to_json(r) for r in rows], "page": page, "size": size, "total": total}


def get_evidence(evidence_id):
    """증적 하나의 지금 정보 + 버전 이력(옛날 버전 → 지금 버전 순서)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM evidence WHERE evidence_id = %s", (evidence_id,))
            current = cur.fetchone()
            cur.execute(
                """SELECT version, file_name, file_size, file_hash, uploaded_at, status
                     FROM evidence_history WHERE evidence_id = %s ORDER BY version""",
                (evidence_id,),
            )
            history = cur.fetchall()
    finally:
        conn.close()

    if current is None:
        raise EvidenceError("EVIDENCE_NOT_FOUND", f"{evidence_id} 증적을 찾을 수 없습니다.")

    result = _to_json(current)
    result["versions"] = [_to_json(h) for h in history] + [
        {
            "version": current["version"],
            "file_name": current["file_name"],
            "file_size": current["file_size"],
            "file_hash": current["file_hash"],
            "uploaded_at": _iso(current["uploaded_at"]),
            "status": current["status"],
        }
    ]
    return result


def delete_evidence(evidence_id):
    """
    증적 하나를 지웁니다. (지금 버전 + 보관함의 옛날 버전 + 저장된 파일 전부)
    A 파트 화면의 ✕ 버튼이 이 함수를 부릅니다.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT version, file_type FROM evidence WHERE evidence_id = %s
                   UNION ALL
                   SELECT version, file_type FROM evidence_history WHERE evidence_id = %s""",
                (evidence_id, evidence_id),
            )
            rows = cur.fetchall()
            if not rows:
                raise EvidenceError("EVIDENCE_NOT_FOUND", f"{evidence_id} 증적을 찾을 수 없습니다.")
            cur.execute("DELETE FROM evidence_history WHERE evidence_id = %s", (evidence_id,))
            cur.execute("DELETE FROM evidence WHERE evidence_id = %s", (evidence_id,))
        conn.commit()  # DB에서 먼저 지우고
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for r in rows:  # 그다음 파일 삭제 (파일이 이미 없으면 그냥 넘어감)
        path = _stored_file_path(evidence_id, r["version"], r["file_type"])
        if os.path.exists(path):
            os.remove(path)
