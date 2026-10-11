"""
user_store.py : 회원가입 · 로그인 · 세션.

아이디는 메일 주소다. 회사·역할·초대는 company_store.py 가 맡고, 이 파일은
"이 사람이 본인인가" 만 본다. 가입은 보통 company_store.signup 으로 들어온다.

    token = login("hong@ace.co.kr", "비밀번호")   로그인 → 토큰
    me = current_user(token)                      토큰 → 사용자 (만료·정지 확인)
    change_password(user_id, 지금, 새것)           바꾸면 쓰던 토큰이 끊긴다
    logout(token)

비밀번호는 평문으로 저장하지 않는다. 표준 라이브러리 PBKDF2-HMAC-SHA256 을 쓴다.
설계 판단과 아직 안 한 것은 README.md 에 적어뒀다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 입력팀 database 폴더를 찾아 db.get_connection 과 KST 를 쓴다.
_HERE = Path(__file__).resolve().parent
for _base in [_HERE, *_HERE.parents]:
    _cand = _base / "phase1_입력" / "database"
    if _cand.is_dir():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

try:
    from config import KST
    from db import get_connection
except Exception:                                    # DB 없이 시험할 때
    from datetime import timezone
    KST = timezone(timedelta(hours=9))
    get_connection = None

SPEC_VERSION = "user-store-0.1"

# ── 정책값. 팀이 정하면 이 줄만 고친다 ────────────────────────────────────
ROLES = ("REVIEWER", "APPROVER")      # phase2_review_store.ROLES 와 같아야 한다
STATUSES = ("PENDING", "ACTIVE", "DISABLED")
AUTO_ACTIVATE = False                 # True 면 가입 즉시 ACTIVE (관리자 승인 생략)
SESSION_HOURS = 8
MAX_FAILED = 5                        # 연속 실패가 이만큼이면 잠근다
LOCK_MINUTES = 10
PBKDF2_ROUNDS = 600_000
MIN_LEN_2KIND = 10                    # 2종 조합이면 10자 이상
MIN_LEN_3KIND = 8                     # 3종 조합이면 8자 이상

# 아이디는 메일 주소다 (IS_SO_UI 모델). @ 와 길이를 허용하되 따옴표·공백은 막는다.
# owner.py 는 회사 번호만 SQL 에 박으므로 여기 글자는 질의에 안 들어간다.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
                         r"|^[A-Za-z][A-Za-z0-9_.-]{2,49}$")


class AuthError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ── 연결 ──────────────────────────────────────────────────────────────────
_conn_factory = None
_paramstyle = "format"


def use_connection(factory, paramstyle="format"):
    """시험할 때 다른 DB 를 끼우는 자리. 평소에는 부르지 않는다."""
    global _conn_factory, _paramstyle
    _conn_factory, _paramstyle = factory, paramstyle


def _connect():
    if _conn_factory is not None:
        return _conn_factory()
    if get_connection is None:
        raise AuthError("DB_NOT_CONFIGURED",
                        "phase1_입력/database/db.py 를 찾지 못했습니다.")
    return get_connection()


def _q(sql):
    return sql if _paramstyle == "format" else sql.replace("%s", "?")


def _rows(cur):
    # pymysql 은 DictCursor 라 이미 dict 를 준다. zip 으로 또 감싸면
    # dict 의 "키" 와 짝지어져 값이 통째로 망가진다.
    cols = [d[0] for d in cur.description]
    return [r if isinstance(r, dict) else dict(zip(cols, r))
            for r in cur.fetchall()]


def _now():
    return datetime.now(KST).replace(tzinfo=None)


def _dt(v):
    """sqlite 는 문자열로 돌려준다. 비교 전에 datetime 으로 맞춘다."""
    if v is None or isinstance(v, datetime):
        return v
    s = str(v)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _log(cur, user_id, event, detail=None):
    cur.execute(_q("INSERT INTO user_login_log (user_id, event, detail, at) "
                   "VALUES (%s, %s, %s, %s)"),
                (user_id, event, (detail or "")[:200] or None, _now()))


# ── 비밀번호 ──────────────────────────────────────────────────────────────
def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, rounds: int | None = None) -> str:
    # 기본값 자리에 PBKDF2_ROUNDS 를 쓰면 안 된다. 그 값은 함수를 만들 때
    # 한 번 박혀서, 나중에 상수를 올려도 예전 횟수로 계속 해시한다.
    rounds = PBKDF2_ROUNDS if rounds is None else rounds
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 _unb64(salt), int(rounds))
    except Exception:
        return False
    return hmac.compare_digest(_b64(dk), want)


def burn_time(password: str) -> None:
    """
    없는 아이디일 때도 맞춰 쓰는 시간.

    고정 더미 해시를 쓰면 반복 횟수가 달라 응답이 빨라진다. 그 차이로
    어떤 아이디가 있는지 알아낼 수 있어서, 실제와 같은 횟수를 돌린다.
    """
    hashlib.pbkdf2_hmac("sha256", password.encode(), b"no-such-user",
                        PBKDF2_ROUNDS)


def check_password_policy(password: str, user_id: str = "") -> None:
    """KISA 기준 — 2종 조합 10자 이상 또는 3종 조합 8자 이상."""
    kinds = sum([bool(re.search(r"[A-Za-z]", password)),
                 bool(re.search(r"\d", password)),
                 bool(re.search(r"[^A-Za-z0-9]", password))])
    if kinds < 2:
        raise AuthError("WEAK_PASSWORD",
                        "영문·숫자·특수문자 중 두 종류 이상을 섞어야 합니다.")
    need = MIN_LEN_3KIND if kinds >= 3 else MIN_LEN_2KIND
    if len(password) < need:
        raise AuthError("WEAK_PASSWORD",
                        f"{kinds}종 조합은 {need}자 이상이어야 합니다 "
                        f"(지금 {len(password)}자).")
    if user_id and user_id.lower() in password.lower():
        raise AuthError("WEAK_PASSWORD", "비밀번호에 아이디를 넣을 수 없습니다.")
    if re.search(r"(.)\1\1", password):
        raise AuthError("WEAK_PASSWORD", "같은 글자를 세 번 이어 쓸 수 없습니다.")


# ── 가입 ──────────────────────────────────────────────────────────────────
def signup(user_id, password, display_name, role="REVIEWER"):
    """
    가입 신청. 기본은 PENDING 이라 관리자가 activate() 해야 로그인된다.

    자가 가입으로 바로 APPROVER 가 되면 승인 통제가 의미를 잃는다.
    """
    user_id = (user_id or "").strip()
    display_name = (display_name or "").strip()
    if len(user_id) > 190 or not _USER_ID_RE.match(user_id):
        raise AuthError("INVALID_USER_ID",
                        "메일 주소를 입력하세요.")
    if not display_name:
        raise AuthError("DISPLAY_NAME_REQUIRED", "이름을 입력하세요.")
    if len(display_name) > 100:
        raise AuthError("DISPLAY_NAME_TOO_LONG", "이름은 100자까지입니다.")
    if role not in ROLES:
        raise AuthError("INVALID_ROLE", f"역할은 {', '.join(ROLES)} 중 하나입니다.")
    check_password_policy(password, user_id)

    now = _now()
    status = "ACTIVE" if AUTO_ACTIVATE else "PENDING"
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id FROM app_user WHERE user_id = %s"),
                        (user_id,))
            if _rows(cur):
                raise AuthError("USER_EXISTS", "이미 있는 아이디입니다.")
            cur.execute(_q(
                "INSERT INTO app_user (user_id, display_name, password_hash, role, "
                " is_admin, status, failed_count, created_at, password_changed_at, "
                " approved_at) "
                "VALUES (%s, %s, %s, %s, 0, %s, 0, %s, %s, %s)"),
                (user_id, display_name, hash_password(password), role, status,
                 now, now, now if AUTO_ACTIVATE else None))
            _log(cur, user_id, "SIGNUP", f"role={role} status={status}")
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "display_name": display_name,
            "role": role, "status": status}


def bootstrap_admin(user_id, password, display_name, role="APPROVER"):
    """
    첫 관리자 계정. 사용자가 하나도 없을 때만 된다.

    관리자가 없으면 아무도 가입을 승인할 수 없어서 열어둔 자리다.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT COUNT(*) AS n FROM app_user"))
            if _rows(cur)[0]["n"]:
                raise AuthError("ALREADY_INITIALIZED",
                                "이미 사용자가 있습니다. signup() 을 쓰세요.")
    finally:
        conn.close()

    signup(user_id, password, display_name, role=role)
    now = _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("UPDATE app_user SET is_admin = 1, status = 'ACTIVE', "
                           "approved_at = %s, approved_by = %s WHERE user_id = %s"),
                        (now, user_id, user_id))
            _log(cur, user_id, "ACTIVATE", "bootstrap_admin")
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "role": role, "is_admin": True, "status": "ACTIVE"}


def _set_status(user_id, status, by, event):
    now = _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT status FROM app_user WHERE user_id = %s"),
                        (user_id,))
            rows = _rows(cur)
            if not rows:
                raise AuthError("USER_NOT_FOUND", f"없는 사용자입니다: {user_id}")
            cur.execute(_q("UPDATE app_user SET status = %s, approved_at = %s, "
                           "approved_by = %s, failed_count = 0, locked_until = NULL "
                           "WHERE user_id = %s"), (status, now, by, user_id))
            if status != "ACTIVE":
                # 계정을 막으면 이미 로그인된 세션도 끊는다.
                cur.execute(_q("UPDATE user_session SET revoked_at = %s "
                               "WHERE user_id = %s AND revoked_at IS NULL"),
                            (now, user_id))
            _log(cur, user_id, event, f"by={by}")
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "status": status}


def activate(user_id, by):
    """가입 승인. 관리자만 부르도록 호출하는 쪽에서 막는다."""
    return _set_status(user_id, "ACTIVE", by, "ACTIVATE")


def disable(user_id, by):
    """계정 정지. 세션도 같이 끊는다."""
    return _set_status(user_id, "DISABLED", by, "DISABLE")


def set_role(user_id, role, by):
    if role not in ROLES:
        raise AuthError("INVALID_ROLE", f"역할은 {', '.join(ROLES)} 중 하나입니다.")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("UPDATE app_user SET role = %s WHERE user_id = %s"),
                        (role, user_id))
            if not cur.rowcount:
                raise AuthError("USER_NOT_FOUND", f"없는 사용자입니다: {user_id}")
            _log(cur, user_id, "ACTIVATE", f"role={role} by={by}")
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "role": role}


# ── 로그인 ────────────────────────────────────────────────────────────────
def _token_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


def login(user_id, password):
    """
    맞으면 세션 토큰을 돌려준다. 이 토큰은 DB 에 원본으로 저장되지 않는다.

    아이디가 없든 비밀번호가 틀리든 같은 LOGIN_FAILED 를 낸다.
    어느 쪽인지 알려주면 아이디 목록을 캐낼 수 있다.
    """
    user_id = (user_id or "").strip()
    now = _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id, display_name, password_hash, role, "
                           " is_admin, status, failed_count, locked_until "
                           "FROM app_user WHERE user_id = %s"), (user_id,))
            rows = _rows(cur)
            u = rows[0] if rows else None

            if u is None:
                burn_time(password or "")                # 시간 차이를 없앤다
                _log(cur, user_id or "(빈 아이디)", "LOGIN_FAIL", "no such user")
                conn.commit()
                raise AuthError("LOGIN_FAILED", "아이디 또는 비밀번호가 틀렸습니다.")

            locked = _dt(u["locked_until"])
            if locked and locked > now:
                _log(cur, user_id, "LOGIN_FAIL", "locked")
                conn.commit()
                left = int((locked - now).total_seconds() // 60) + 1
                raise AuthError("ACCOUNT_LOCKED",
                                f"연속 실패로 잠겼습니다. {left}분 뒤에 다시 하세요.")

            if not verify_password(password, u["password_hash"]):
                failed = int(u["failed_count"]) + 1
                if failed >= MAX_FAILED:
                    cur.execute(_q("UPDATE app_user SET failed_count = %s, "
                                   "locked_until = %s WHERE user_id = %s"),
                                (failed, now + timedelta(minutes=LOCK_MINUTES),
                                 user_id))
                    _log(cur, user_id, "LOCKED", f"failed={failed}")
                else:
                    cur.execute(_q("UPDATE app_user SET failed_count = %s "
                                   "WHERE user_id = %s"), (failed, user_id))
                    _log(cur, user_id, "LOGIN_FAIL", f"failed={failed}")
                conn.commit()
                raise AuthError("LOGIN_FAILED", "아이디 또는 비밀번호가 틀렸습니다.")

            # 비밀번호는 맞다. 계정 상태는 그 다음에 본다.
            if u["status"] == "PENDING":
                _log(cur, user_id, "LOGIN_FAIL", "pending")
                conn.commit()
                raise AuthError("ACCOUNT_PENDING", "가입 승인을 기다리는 계정입니다.")
            if u["status"] != "ACTIVE":
                _log(cur, user_id, "LOGIN_FAIL", "disabled")
                conn.commit()
                raise AuthError("ACCOUNT_DISABLED", "사용할 수 없는 계정입니다.")

            token = secrets.token_urlsafe(32)
            cur.execute(_q("INSERT INTO user_session "
                           "(session_id, user_id, created_at, expires_at) "
                           "VALUES (%s, %s, %s, %s)"),
                        (_token_key(token), user_id, now,
                         now + timedelta(hours=SESSION_HOURS)))
            cur.execute(_q("UPDATE app_user SET failed_count = 0, "
                           "locked_until = NULL, last_login_at = %s "
                           "WHERE user_id = %s"), (now, user_id))
            _log(cur, user_id, "LOGIN_OK")
        conn.commit()
    finally:
        conn.close()
    return token


def current_user(token):
    """토큰이 살아 있으면 사용자, 아니면 None. 화면 보호는 이 값으로 한다."""
    if not token:
        return None
    now = _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "SELECT u.user_id, u.display_name, u.role, u.is_admin, u.status, "
                "       s.expires_at, s.revoked_at "
                "  FROM user_session s JOIN app_user u ON u.user_id = s.user_id "
                " WHERE s.session_id = %s"), (_token_key(token),))
            rows = _rows(cur)
    finally:
        conn.close()
    if not rows:
        return None
    r = rows[0]
    if r["revoked_at"] is not None or r["status"] != "ACTIVE":
        return None
    exp = _dt(r["expires_at"])
    if exp is None or exp <= now:
        return None
    return {"user_id": r["user_id"], "display_name": r["display_name"],
            "role": r["role"], "is_admin": bool(r["is_admin"]),
            "expires_at": exp}


def logout(token):
    if not token:
        return False
    now = _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("UPDATE user_session SET revoked_at = %s "
                           "WHERE session_id = %s AND revoked_at IS NULL"),
                        (now, _token_key(token)))
            done = bool(cur.rowcount)
            if done:
                cur.execute(_q("SELECT user_id FROM user_session "
                               "WHERE session_id = %s"), (_token_key(token),))
                rows = _rows(cur)
                if rows:
                    _log(cur, rows[0]["user_id"], "LOGOUT")
        conn.commit()
    finally:
        conn.close()
    return done


def change_password(user_id, old_password, new_password):
    """비밀번호를 바꾸면 그 사람의 다른 세션을 전부 끊는다."""
    check_password_policy(new_password, user_id)
    now = _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT password_hash FROM app_user WHERE user_id = %s"),
                        (user_id,))
            rows = _rows(cur)
            if not rows:
                raise AuthError("USER_NOT_FOUND", f"없는 사용자입니다: {user_id}")
            if not verify_password(old_password, rows[0]["password_hash"]):
                raise AuthError("LOGIN_FAILED", "지금 비밀번호가 틀렸습니다.")
            if verify_password(new_password, rows[0]["password_hash"]):
                raise AuthError("SAME_PASSWORD", "전과 다른 비밀번호를 쓰세요.")
            cur.execute(_q("UPDATE app_user SET password_hash = %s, "
                           "password_changed_at = %s WHERE user_id = %s"),
                        (hash_password(new_password), now, user_id))
            cur.execute(_q("UPDATE user_session SET revoked_at = %s "
                           "WHERE user_id = %s AND revoked_at IS NULL"),
                        (now, user_id))
            _log(cur, user_id, "PW_CHANGE")
        conn.commit()
    finally:
        conn.close()
    return True


# ── 조회 ──────────────────────────────────────────────────────────────────
def get_user(user_id):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id, display_name, role, is_admin, status, "
                           " failed_count, locked_until, created_at, approved_at, "
                           " approved_by, last_login_at "
                           "FROM app_user WHERE user_id = %s"), (user_id,))
            rows = _rows(cur)
    finally:
        conn.close()
    return rows[0] if rows else None


def list_users(status=None):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            sql = ("SELECT user_id, display_name, role, is_admin, status, "
                   " created_at, last_login_at FROM app_user")
            args = ()
            if status:
                sql += " WHERE status = %s"
                args = (status,)
            cur.execute(_q(sql + " ORDER BY created_at"), args)
            return _rows(cur)
    finally:
        conn.close()


def pending_users():
    """가입 승인 대기 목록. 관리자 화면이 쓴다."""
    return list_users(status="PENDING")


def log_event(user_id, event, detail=None):
    """
    계정 기록에 한 줄 남긴다. 증적 삭제처럼 다른 모듈에서 부르는 자리.

    기록이 실패해도 본 작업을 되돌리지 않는다. 부르는 쪽에서 감싸서 쓴다.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _log(cur, user_id, event, detail)
        conn.commit()
    finally:
        conn.close()
    return True


def login_log(user_id=None, limit=100):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            sql = "SELECT log_id, user_id, event, detail, at FROM user_login_log"
            args = []
            if user_id:
                sql += " WHERE user_id = %s"
                args.append(user_id)
            sql += " ORDER BY log_id DESC LIMIT %s"
            args.append(int(limit))
            cur.execute(_q(sql), tuple(args))
            return _rows(cur)
    finally:
        conn.close()


def purge_sessions(before=None):
    """만료·차단된 세션을 치운다. 가끔 돌리면 된다."""
    cutoff = before or _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("DELETE FROM user_session "
                           "WHERE expires_at < %s OR revoked_at IS NOT NULL"),
                        (cutoff,))
            n = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return n
