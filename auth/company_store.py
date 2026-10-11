"""
company_store.py : 회사 · 멤버 · 초대.

IS_SO_UI 모델에 맞춘다.

    회사 = 이메일 도메인       acme.co.kr 로 가입하면 그게 회사가 된다
    초대 = 같은 도메인만        gmail 같은 공용 메일로는 회사를 못 만든다
    메일 = 보내지 않는다        화면이 초대 링크를 띄우고 사람이 전달한다

user_store 의 비밀번호·세션은 그대로 쓴다. 여기는 그 위에 회사를 얹는다.
"""
from __future__ import annotations

import re
import secrets
import sys
from datetime import timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import user_store as us                              # noqa: E402

SPEC_VERSION = "company-store-0.1"

# UI 의 역할. super 는 서비스 운영자라 회사에 속하지 않는다.
ROLES = ("admin", "reviewer", "member")

# UI 권한표와 같아야 한다 (IS_SO_UI/dist/app.js 의 perms).
PERMS = {
    "upload": ("admin", "reviewer", "member"),
    "inspect": ("admin", "reviewer", "member"),
    "review": ("admin", "reviewer"),
    "delete": ("admin",),
    "team": ("admin",),
}

# 이 도메인으로는 회사를 못 만든다. UI 와 같은 목록.
PUBLIC_DOMAINS = {"gmail.com", "naver.com", "daum.net", "hanmail.net",
                  "outlook.com", "hotmail.com", "yahoo.com", "kakao.com",
                  "nate.com", "icloud.com"}

INVITE_DAYS = 7

_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class CompanyError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _conn():
    return us._connect()


def _q(sql):
    return us._q(sql)


def _rows(cur):
    return us._rows(cur)


def _now():
    return us._now()


# ── 이메일 · 도메인 ───────────────────────────────────────────────────────
def check_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 190:
        raise CompanyError("INVALID_EMAIL", "메일 주소 모양이 아닙니다.")
    return email


def domain_of(email: str) -> str:
    return check_email(email).split("@", 1)[1]


def check_company_email(email: str) -> str:
    """회사를 만들 수 있는 메일인가. 공용 메일은 막는다."""
    email = check_email(email)
    if domain_of(email) in PUBLIC_DOMAINS:
        raise CompanyError(
            "PUBLIC_EMAIL",
            "공용 메일로는 회사 가입을 할 수 없습니다. 회사 메일을 쓰세요.")
    return email


def can(role: str, action: str) -> bool:
    """UI 권한표 그대로. 모르는 작업은 막는다."""
    return role in PERMS.get(action, ())


# ── 회사 ──────────────────────────────────────────────────────────────────
def _next_company_id(cur) -> str:
    cur.execute(_q("SELECT company_id FROM company "
                   "ORDER BY LENGTH(company_id) DESC, company_id DESC LIMIT 1"))
    rows = _rows(cur)
    n = int(rows[0]["company_id"][1:]) + 1 if rows else 1
    return "C" + str(n).zfill(4)


def get_company(company_id):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT company_id, name, domain, status, created_at, "
                           " created_by FROM company WHERE company_id = %s"),
                        (company_id,))
            rows = _rows(cur)
    finally:
        conn.close()
    return rows[0] if rows else None


def company_by_domain(domain):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT company_id, name, domain, status FROM company "
                           "WHERE domain = %s"), (domain,))
            rows = _rows(cur)
    finally:
        conn.close()
    return rows[0] if rows else None


def list_companies():
    """서비스 운영자(super) 화면용. 회사별 멤버 수까지."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "SELECT c.company_id, c.name, c.domain, c.status, c.created_at, "
                "       c.created_by, COUNT(u.user_id) AS members, "
                "       (SELECT MIN(a.email) FROM app_user a "
                "         WHERE a.company_id = c.company_id "
                "           AND a.role = 'admin' AND a.status = 'ACTIVE') AS admin "
                "  FROM company c LEFT JOIN app_user u ON u.company_id = c.company_id "
                " GROUP BY c.company_id, c.name, c.domain, c.status, "
                "          c.created_at, c.created_by "
                " ORDER BY c.created_at"))
            return _rows(cur)
    finally:
        conn.close()


def set_company_status(company_id, status, by):
    # 화면은 소문자로 보낸다 (active / suspended).
    status = str(status or "").upper()
    if status not in ("ACTIVE", "SUSPENDED"):
        raise CompanyError(
            "INVALID_STATUS",
            "이용 중(active) 또는 정지(suspended) 만 바꿀 수 있습니다. "
            "가입 승인·거절은 아직 지원하지 않습니다.")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("UPDATE company SET status = %s WHERE company_id = %s"),
                        (status, company_id))
            if not cur.rowcount:
                raise CompanyError("COMPANY_NOT_FOUND", "없는 회사입니다.")
            if status != "ACTIVE":
                # 회사를 막으면 그 회사 사람들 세션도 끊는다.
                cur.execute(_q("UPDATE user_session SET revoked_at = %s "
                               " WHERE revoked_at IS NULL AND user_id IN "
                               " (SELECT user_id FROM app_user WHERE company_id = %s)"),
                            (_now(), company_id))
            us._log(cur, by, "COMPANY_" + status, company_id)
        conn.commit()
    finally:
        conn.close()
    return {"company_id": company_id, "status": status}


# ── 가입 ──────────────────────────────────────────────────────────────────
def signup(company: str, name: str, email: str, password: str):
    """
    회사를 만들면서 첫 사람(admin)이 가입한다. UI 의 /api/auth/signup.

    메일을 안 보내므로 verified 를 바로 1 로 둔다. 화면의 인증 단계는
    표시만 하고 서버는 통과시킨다 (팀 결정, 2026-10-11).
    """
    email = check_company_email(email)
    name = (name or "").strip()
    company = (company or "").strip()
    if not name:
        raise CompanyError("NAME_REQUIRED", "이름을 입력하세요.")
    if not company:
        raise CompanyError("COMPANY_REQUIRED", "회사 이름을 입력하세요.")
    us.check_password_policy(password, email.split("@", 1)[0])

    domain = domain_of(email)
    now = _now()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id FROM app_user WHERE email = %s"), (email,))
            if _rows(cur):
                raise CompanyError("USER_EXISTS", "이미 가입된 메일 주소입니다.")
            cur.execute(_q("SELECT company_id FROM company WHERE domain = %s"),
                        (domain,))
            if _rows(cur):
                raise CompanyError(
                    "COMPANY_EXISTS",
                    f"{domain} 는 이미 등록된 회사입니다. "
                    f"관리자에게 초대를 요청하세요.")
            cid = _next_company_id(cur)
            cur.execute(_q("INSERT INTO company (company_id, name, domain, status, "
                           " created_at, created_by) VALUES (%s,%s,%s,'ACTIVE',%s,%s)"),
                        (cid, company, domain, now, email))
            cur.execute(_q(
                "INSERT INTO app_user (user_id, display_name, password_hash, role, "
                " is_admin, is_super, status, failed_count, created_at, "
                " password_changed_at, approved_at, email, company_id, verified) "
                "VALUES (%s,%s,%s,'admin',1,0,'ACTIVE',0,%s,%s,%s,%s,%s,1)"),
                (email, name, us.hash_password(password), now, now, now, email, cid))
            us._log(cur, email, "SIGNUP", f"company={cid} role=admin")
        conn.commit()
    finally:
        conn.close()
    return {"company_id": cid, "company": company, "domain": domain,
            "email": email, "name": name, "role": "admin"}


def login(email: str, password: str):
    """UI 의 /api/auth/login. user_store.login 을 쓰고 회사 정보를 붙인다."""
    email = check_email(email)
    token = us.login(email, password)
    me = us.current_user(token)
    info = profile(me["user_id"])
    if info.get("company_status") == "SUSPENDED":
        us.logout(token)
        raise CompanyError("COMPANY_SUSPENDED", "사용이 중지된 회사입니다.")
    return token, info


def profile(user_id):
    """세션에서 꺼낸 사용자에 회사를 붙인 화면용 모양."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "SELECT u.user_id, u.display_name, u.email, u.role, u.is_admin, "
                "       u.is_super, u.status, u.company_id, u.verified, "
                "       c.name AS company_name, c.domain, c.status AS company_status "
                "  FROM app_user u LEFT JOIN company c ON c.company_id = u.company_id "
                " WHERE u.user_id = %s"), (user_id,))
            rows = _rows(cur)
    finally:
        conn.close()
    if not rows:
        return None
    r = rows[0]
    role = "super" if r["is_super"] else r["role"]
    return {"name": r["display_name"], "email": r["email"] or r["user_id"],
            "role": role, "company": r["company_name"], "companyId": r["company_id"],
            "domain": r["domain"], "verified": bool(r["verified"]),
            "company_status": r["company_status"], "user_id": r["user_id"]}


# ── 멤버 ──────────────────────────────────────────────────────────────────
def members(company_id):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT display_name AS name, email, role, verified, "
                           " status, last_login_at FROM app_user "
                           " WHERE company_id = %s ORDER BY created_at"),
                        (company_id,))
            return [{**r, "verified": bool(r["verified"]),
                     "active": r["status"] == "ACTIVE"} for r in _rows(cur)]
    finally:
        conn.close()


def set_member_role(company_id, email, role, by):
    if role not in ROLES:
        raise CompanyError("INVALID_ROLE", f"역할은 {', '.join(ROLES)} 중 하나입니다.")
    email = check_email(email)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT role FROM app_user "
                           " WHERE email = %s AND company_id = %s"),
                        (email, company_id))
            rows = _rows(cur)
            if not rows:
                raise CompanyError("MEMBER_NOT_FOUND", "그 회사의 멤버가 아닙니다.")
            if rows[0]["role"] == "admin" and role != "admin":
                _require_other_admin(cur, company_id, email)
            cur.execute(_q("UPDATE app_user SET role = %s, is_admin = %s "
                           " WHERE email = %s AND company_id = %s"),
                        (role, 1 if role == "admin" else 0, email, company_id))
            us._log(cur, email, "ROLE_CHANGE", f"{role} by={by}")
        conn.commit()
    finally:
        conn.close()
    return {"email": email, "role": role}


def remove_member(company_id, email, by):
    """회사에서 뺀다. 계정은 DISABLED 로 두고 지우지 않는다 (기록 보존)."""
    email = check_email(email)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id, role FROM app_user "
                           " WHERE email = %s AND company_id = %s"),
                        (email, company_id))
            rows = _rows(cur)
            if not rows:
                raise CompanyError("MEMBER_NOT_FOUND", "그 회사의 멤버가 아닙니다.")
            if rows[0]["role"] == "admin":
                _require_other_admin(cur, company_id, email)
            cur.execute(_q("UPDATE app_user SET status = 'DISABLED' "
                           " WHERE user_id = %s"), (rows[0]["user_id"],))
            cur.execute(_q("UPDATE user_session SET revoked_at = %s "
                           " WHERE user_id = %s AND revoked_at IS NULL"),
                        (_now(), rows[0]["user_id"]))
            us._log(cur, email, "MEMBER_REMOVE", f"by={by}")
        conn.commit()
    finally:
        conn.close()
    return {"email": email, "removed": True}


def _require_other_admin(cur, company_id, email):
    """마지막 관리자를 빼거나 강등하면 그 회사는 아무도 관리할 수 없게 된다."""
    cur.execute(_q("SELECT COUNT(*) AS n FROM app_user "
                   " WHERE company_id = %s AND role = 'admin' "
                   "   AND status = 'ACTIVE' AND email <> %s"),
                (company_id, email))
    if not int(_rows(cur)[0]["n"]):
        raise CompanyError("LAST_ADMIN",
                           "마지막 관리자입니다. 다른 사람을 먼저 관리자로 지정하세요.")


# ── 초대 ──────────────────────────────────────────────────────────────────
def invite(company_id, email, role, by):
    """
    초대장을 만든다. **메일을 보내지 않는다** — 토큰을 돌려주면 화면이
    '초대 링크 열기' 로 띄우고 사람이 직접 전달한다.
    """
    if role not in ROLES:
        raise CompanyError("INVALID_ROLE", f"역할은 {', '.join(ROLES)} 중 하나입니다.")
    email = check_email(email)
    comp = get_company(company_id)
    if comp is None:
        raise CompanyError("COMPANY_NOT_FOUND", "없는 회사입니다.")
    if domain_of(email) != comp["domain"]:
        raise CompanyError(
            "DOMAIN_MISMATCH",
            f"@{comp['domain']} 메일 주소로만 초대할 수 있습니다.")

    now = _now()
    token = secrets.token_urlsafe(32)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id FROM app_user WHERE email = %s"), (email,))
            if _rows(cur):
                raise CompanyError("USER_EXISTS", "이미 가입된 메일 주소입니다.")
            # 같은 사람에게 살아 있는 초대가 있으면 그걸 접는다.
            cur.execute(_q("UPDATE invite SET revoked_at = %s "
                           " WHERE email = %s AND accepted_at IS NULL "
                           "   AND revoked_at IS NULL"), (now, email))
            cur.execute(_q("INSERT INTO invite (token, company_id, email, role, "
                           " invited_by, created_at, expires_at) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s)"),
                        (token, company_id, email, role, by, now,
                         now + timedelta(days=INVITE_DAYS)))
            us._log(cur, by, "INVITE", f"{email} role={role}")
        conn.commit()
    finally:
        conn.close()
    return {"token": token, "email": email, "role": role,
            "company_id": company_id, "company": comp["name"],
            "expires_at": (now + timedelta(days=INVITE_DAYS)).isoformat(),
            "note": "메일을 보내지 않습니다. 이 링크를 직접 전달하세요."}


def list_invites(company_id):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT token, email, role, invited_by, created_at, "
                           " expires_at, accepted_at, revoked_at FROM invite "
                           " WHERE company_id = %s ORDER BY created_at DESC"),
                        (company_id,))
            out = []
            for r in _rows(cur):
                r["status"] = ("accepted" if r["accepted_at"] else
                               "revoked" if r["revoked_at"] else
                               "expired" if us._dt(r["expires_at"]) and
                               us._dt(r["expires_at"]) <= _now() else "pending")
                out.append(r)
            return out
    finally:
        conn.close()


def revoke_invite(company_id, token, by):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("UPDATE invite SET revoked_at = %s "
                           " WHERE token = %s AND company_id = %s "
                           "   AND accepted_at IS NULL AND revoked_at IS NULL"),
                        (_now(), token, company_id))
            if not cur.rowcount:
                raise CompanyError("INVITE_NOT_FOUND", "취소할 초대가 없습니다.")
            us._log(cur, by, "INVITE_REVOKE", token[:12])
        conn.commit()
    finally:
        conn.close()
    return {"revoked": True}


def read_invite(token):
    """초대 링크를 열었을 때. 비밀번호 없이 볼 수 있어야 해서 공개 주소다."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "SELECT i.token, i.email, i.role, i.expires_at, i.accepted_at, "
                "       i.revoked_at, c.name AS company, c.company_id "
                "  FROM invite i JOIN company c ON c.company_id = i.company_id "
                " WHERE i.token = %s"), (token,))
            rows = _rows(cur)
    finally:
        conn.close()
    if not rows:
        raise CompanyError("INVITE_NOT_FOUND", "초대 링크를 확인할 수 없습니다.")
    r = rows[0]
    if r["accepted_at"]:
        raise CompanyError("INVITE_USED", "이미 사용된 초대입니다.")
    if r["revoked_at"]:
        raise CompanyError("INVITE_REVOKED", "취소된 초대입니다.")
    if us._dt(r["expires_at"]) and us._dt(r["expires_at"]) <= _now():
        raise CompanyError("INVITE_EXPIRED", "기한이 지난 초대입니다.")
    return {"email": r["email"], "role": r["role"], "company": r["company"],
            "company_id": r["company_id"], "expires_at": r["expires_at"]}


def accept_invite(token, name, password):
    """초대를 받아 계정을 만든다. 초대 링크가 메일 인증을 대신한다."""
    info = read_invite(token)
    name = (name or "").strip()
    if not name:
        raise CompanyError("NAME_REQUIRED", "이름을 입력하세요.")
    us.check_password_policy(password, info["email"].split("@", 1)[0])

    now = _now()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id FROM app_user WHERE email = %s"),
                        (info["email"],))
            if _rows(cur):
                raise CompanyError("USER_EXISTS", "이미 가입된 메일 주소입니다.")
            cur.execute(_q(
                "INSERT INTO app_user (user_id, display_name, password_hash, role, "
                " is_admin, is_super, status, failed_count, created_at, "
                " password_changed_at, approved_at, email, company_id, verified) "
                "VALUES (%s,%s,%s,%s,%s,0,'ACTIVE',0,%s,%s,%s,%s,%s,1)"),
                (info["email"], name, us.hash_password(password), info["role"],
                 1 if info["role"] == "admin" else 0, now, now, now,
                 info["email"], info["company_id"]))
            cur.execute(_q("UPDATE invite SET accepted_at = %s WHERE token = %s"),
                        (now, token))
            us._log(cur, info["email"], "INVITE_ACCEPT",
                    f"company={info['company_id']} role={info['role']}")
        conn.commit()
    finally:
        conn.close()
    return {"email": info["email"], "role": info["role"],
            "company_id": info["company_id"]}



# ── 화면용 모양 ───────────────────────────────────────────────────────────
# IS_SO_UI 는 상태를 소문자로 비교한다 (app.js 의 statusLabel · companyStatus).
# DB 는 대문자를 그대로 쓰고, 밖으로 나갈 때만 바꾼다.
def member_view(r):
    """app.js 의 team() 이 읽는 모양. status 는 소문자 한 칸으로 합친다."""
    status = ("disabled" if r.get("status") != "ACTIVE"
              else "active" if r.get("verified") else "unverified")
    return {"name": r.get("name"), "email": r.get("email"),
            "role": r.get("role"), "status": status,
            "verified": bool(r.get("verified")),
            "active": r.get("status") == "ACTIVE",
            "last_login_at": _iso(r.get("last_login_at"))}


def user_view(r):
    """app.js 의 ops-users 가 읽는 모양."""
    status = ("disabled" if r.get("status") != "ACTIVE"
              else "active" if r.get("verified", 1) else "unverified")
    return {"name": r.get("name"), "email": r.get("email"),
            "company": r.get("company"), "role": r.get("role"),
            "status": status, "last_login_at": _iso(r.get("last_login_at"))}


def company_view(r):
    """app.js 의 ops-companies 가 읽는 모양. id · createdAt · admin 까지."""
    return {"id": r.get("company_id"), "company_id": r.get("company_id"),
            "name": r.get("name"), "domain": r.get("domain"),
            "admin": r.get("admin") or r.get("created_by"),
            "members": int(r.get("members") or 0),
            "createdAt": _day(r.get("created_at")),
            "created_at": _iso(r.get("created_at")),
            "status": str(r.get("status") or "").lower()}


def invite_view(r, base=""):
    """
    초대 한 줄. 화면이 링크를 띄울 수 있게 전체 주소도 같이 준다.

    app.js 는 지금(2026-10-11) 이 주소를 화면에 안 그린다 — demo 모드에만
    "초대 링크 열기" 가 있다. UI 에 한 줄 들어가면 바로 쓰인다.
    """
    token = r.get("token")
    return {"token": token, "id": token,
            "email": r.get("email"), "role": r.get("role"),
            "status": r.get("status"),
            "invited_by": r.get("invited_by"),
            "expires_at": _iso(r.get("expires_at")),
            "invite_url": f"{base.rstrip('/')}/?invite={token}" if base
                          else f"/?invite={token}"}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _day(v):
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v or "")[:10]


_TEAM_EVENT_KO = {
    "SIGNUP": "회사 가입", "INVITE": "초대", "INVITE_ACCEPT": "초대 수락",
    "INVITE_REVOKE": "초대 취소", "INVITE_RESEND": "초대 기한 연장",
    "ROLE_CHANGE": "역할 변경", "MEMBER_REMOVE": "구성원 내보내기",
    "ACTIVATE": "사용 재개", "DISABLE": "사용 중지",
    "PW_CHANGE": "비밀번호 변경",
}


def team_audit(company_id, limit=50):
    """
    팀 화면의 "권한 변경 기록". user_login_log 에서 그 회사 사람들 것만.

    화면은 text · actor · at 세 칸을 읽는다 (app.js 의 audit-log).
    로그인 성공·실패는 뺀다 — 권한 기록을 보는 자리라 묻히기만 한다.
    """
    names = tuple(_TEAM_EVENT_KO)
    marks = ",".join(["%s"] * len(names))
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "SELECT l.user_id, l.event, l.detail, l.at FROM user_login_log l "
                " WHERE l.event IN (" + marks + ") "
                "   AND (l.user_id IN (SELECT email FROM app_user "
                "                       WHERE company_id = %s) "
                "     OR l.detail LIKE %s) "
                " ORDER BY l.log_id DESC LIMIT %s"),
                names + (company_id, f"%company={company_id}%", int(limit)))
            out = []
            for r in _rows(cur):
                what = _TEAM_EVENT_KO.get(r["event"], r["event"])
                by = ""
                for part in str(r.get("detail") or "").split():
                    if part.startswith("by="):
                        by = part[3:]
                out.append({"text": f"{r['user_id']} · {what}",
                            "actor": by or r["user_id"],
                            "at": _iso(r["at"])})
            return out
    finally:
        conn.close()


def set_member_active(company_id, email, active, by):
    """회사 관리자가 구성원을 쓰게/못 쓰게 한다. 계정은 지우지 않는다."""
    email = check_email(email)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT user_id, role FROM app_user "
                           " WHERE email = %s AND company_id = %s"),
                        (email, company_id))
            rows = _rows(cur)
            if not rows:
                raise CompanyError("MEMBER_NOT_FOUND", "그 회사의 멤버가 아닙니다.")
            if not active and rows[0]["role"] == "admin":
                _require_other_admin(cur, company_id, email)
            cur.execute(_q("UPDATE app_user SET status = %s WHERE user_id = %s"),
                        ("ACTIVE" if active else "DISABLED", rows[0]["user_id"]))
            if not active:
                cur.execute(_q("UPDATE user_session SET revoked_at = %s "
                               " WHERE user_id = %s AND revoked_at IS NULL"),
                            (_now(), rows[0]["user_id"]))
            us._log(cur, email, "ACTIVATE" if active else "DISABLE", f"by={by}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "email": email, "active": bool(active)}


def resend_invite(company_id, token, by):
    """
    같은 초대의 기한을 다시 7일로 늘린다. 메일은 안 보낸다.

    토큰을 바꾸지 않는다 — 이미 링크를 전달했을 수 있다.
    """
    now = _now()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("UPDATE invite SET expires_at = %s "
                           " WHERE token = %s AND company_id = %s "
                           "   AND accepted_at IS NULL AND revoked_at IS NULL"),
                        (now + timedelta(days=INVITE_DAYS), token, company_id))
            if not cur.rowcount:
                raise CompanyError("INVITE_NOT_FOUND", "다시 보낼 초대가 없습니다.")
            us._log(cur, by, "INVITE_RESEND", token[:12])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "token": token,
            "expires_at": (now + timedelta(days=INVITE_DAYS)).isoformat(),
            "sent": False,
            "note": "메일을 보내지 않습니다. 기한만 다시 7일로 늘렸습니다."}

# ── 서비스 운영자 ─────────────────────────────────────────────────────────
def bootstrap_super(email, password, name="서비스 운영자"):
    """첫 운영자. 회사에 속하지 않고 모든 회사를 본다."""
    email = check_email(email)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT COUNT(*) AS n FROM app_user WHERE is_super = 1"))
            if int(_rows(cur)[0]["n"]):
                raise CompanyError("ALREADY_INITIALIZED", "이미 운영자가 있습니다.")
    finally:
        conn.close()
    us.check_password_policy(password, email.split("@", 1)[0])
    now = _now()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "INSERT INTO app_user (user_id, display_name, password_hash, role, "
                " is_admin, is_super, status, failed_count, created_at, "
                " password_changed_at, approved_at, email, company_id, verified) "
                "VALUES (%s,%s,%s,'admin',1,1,'ACTIVE',0,%s,%s,%s,%s,NULL,1)"),
                (email, name, us.hash_password(password), now, now, now, email))
            us._log(cur, email, "SIGNUP", "super")
        conn.commit()
    finally:
        conn.close()
    return {"email": email, "role": "super"}


def all_users():
    """운영자 화면용. 전체 사용자와 소속 회사."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "SELECT u.display_name AS name, u.email, u.role, u.is_super, "
                "       u.status, u.verified, u.last_login_at, c.name AS company "
                "  FROM app_user u LEFT JOIN company c ON c.company_id = u.company_id "
                " ORDER BY u.created_at"))
            return [{**r, "role": "super" if r["is_super"] else r["role"]}
                    for r in _rows(cur)]
    finally:
        conn.close()
