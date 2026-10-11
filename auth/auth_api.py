"""
auth_api.py : IS_SO_UI 가 부르는 로그인 · 회사 · 초대 · 운영자 엔드포인트.

UI(IS_SO_UI/dist/app.js) 가 실제로 부르는 주소에 그대로 맞췄다.
메일은 보내지 않는다 — 초대 토큰을 응답으로 돌려주고 화면이 '초대 링크 열기'
로 띄운다 (팀 결정, 2026-10-11).

actor·role 은 요청 본문에서 받지 않는다. 항상 세션에서 꺼낸다.
요청 모델이 extra="forbid" 라 본문에 넣어 보내면 422 로 떨어진다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import (APIRouter, Depends, FastAPI, Header, HTTPException,
                     Request)
from pydantic import BaseModel, ConfigDict

_HERE = Path(__file__).resolve().parent
for _b in [_HERE, *_HERE.parents]:
    if (_b / "phase1_입력" / "database").is_dir():
        for _p in (_b / "auth", _b / "phase1_입력" / "database", _b / "phase2_통합"):
            if _p.is_dir() and str(_p) not in sys.path:
                sys.path.insert(0, str(_p))
        break

import company_store as cs                  # noqa: E402
import owner                                # noqa: E402
import user_store as us                     # noqa: E402

router = APIRouter()

# ── 오류를 HTTP 로 ────────────────────────────────────────────────────────
STATUS = {
    "LOGIN_FAILED": 401, "ACCOUNT_LOCKED": 423, "ACCOUNT_PENDING": 403,
    "ACCOUNT_DISABLED": 403, "COMPANY_SUSPENDED": 403, "ROLE_REQUIRED": 403,
    "ROLE_NOT_ALLOWED": 403, "NOT_ALLOWED": 403,
    "USER_EXISTS": 409, "COMPANY_EXISTS": 409, "ALREADY_INITIALIZED": 409,
    "INVITE_USED": 409, "RESULT_NOT_CURRENT": 409, "VERSION_NOT_CURRENT": 409,
    "NOT_IN_REVIEW": 409,
    "USER_NOT_FOUND": 404, "MEMBER_NOT_FOUND": 404, "COMPANY_NOT_FOUND": 404,
    "INVITE_NOT_FOUND": 404, "RESULT_NOT_FOUND": 404, "EVIDENCE_NOT_FOUND": 404,
    "DB_NOT_CONFIGURED": 503, "AUDIT_UNAVAILABLE": 503,
}


def fail(exc):
    code = getattr(exc, "code", "INTERNAL")
    raise HTTPException(status_code=STATUS.get(code, 400),
                        detail={"error_code": code,
                                "message": getattr(exc, "message", str(exc))})


_fail = fail
AuthErrors = (us.AuthError, cs.CompanyError)


# ── 로그인 확인 ───────────────────────────────────────────────────────────
def _token(authorization):
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None


def current_user(authorization: str | None = Header(default=None)):
    """로그인한 사람 + 회사. 없으면 401."""
    me = us.current_user(_token(authorization))
    if me is None:
        raise HTTPException(status_code=401,
                            detail={"error_code": "NOT_LOGGED_IN",
                                    "message": "로그인이 필요합니다."})
    info = cs.profile(me["user_id"])
    if info is None:
        raise HTTPException(status_code=401,
                            detail={"error_code": "NOT_LOGGED_IN",
                                    "message": "로그인이 필요합니다."})
    return info


def require(action: str):
    """
    UI 권한표(app.js 의 perms)와 같은 규칙으로 막는다.

        upload/inspect  admin · reviewer · member
        review          admin · reviewer
        delete · team   admin

    super(서비스 운영자)는 여기를 통과하지 못한다. 화면에도 "운영 관리자는
    회사의 증적 내용을 열람할 수 없습니다" 라고 적혀 있고, 남의 회사 판정을
    고치는 것은 회사·사용자 관리와 다른 일이다. 운영자 화면은
    require_super 로 따로 막는다.
    """
    def dep(me=Depends(current_user)):
        if cs.can(me["role"], action):
            return me
        raise HTTPException(status_code=403,
                            detail={"error_code": "NOT_ALLOWED",
                                    "message": "권한이 없습니다."})
    return dep


def require_super(me=Depends(current_user)):
    if me["role"] != "super":
        raise HTTPException(status_code=403,
                            detail={"error_code": "NOT_ALLOWED",
                                    "message": "서비스 운영자만 할 수 있습니다."})
    return me


class Body(BaseModel):
    # actor·role 을 본문으로 받지 않겠다는 뜻. 넣어 보내면 422.
    model_config = ConfigDict(extra="forbid")


# ── 가입 · 로그인 ─────────────────────────────────────────────────────────
class SignupIn(Body):
    company: str
    name: str
    email: str
    password: str


class LoginIn(Body):
    email: str
    password: str


class EmailIn(Body):
    email: str


class TokenIn(Body):
    token: str


class AcceptIn(Body):
    token: str
    name: str
    password: str


class PasswordIn(Body):
    old_password: str
    new_password: str


@router.post("/api/auth/signup")
def signup(body: SignupIn):
    """회사를 만들면서 첫 사람(admin)이 가입한다."""
    try:
        return cs.signup(body.company, body.name, body.email, body.password)
    except AuthErrors as e:
        fail(e)


@router.post("/api/auth/login")
def login(body: LoginIn):
    try:
        token, info = cs.login(body.email, body.password)
    except AuthErrors as e:
        fail(e)
    return {"token": token, "user": info}


@router.post("/api/auth/verify")
def verify(body: TokenIn):
    """
    화면의 메일 인증 단계. **메일을 안 보내므로 서버는 통과시킨다.**
    가입할 때 이미 verified=1 로 들어간다 (팀 결정, 2026-10-11).
    """
    return {"verified": True,
            "note": "메일 발송은 구현하지 않았습니다. 가입 즉시 인증된 상태입니다."}


@router.post("/api/auth/resend")
def resend(body: EmailIn):
    return {"sent": False,
            "note": "메일 발송은 구현하지 않았습니다. 바로 로그인하세요."}


@router.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None),
           me=Depends(current_user)):
    us.logout(_token(authorization))
    return {"ok": True}


@router.get("/api/auth/me")
def me(me=Depends(current_user)):
    return me


@router.post("/api/auth/password")
def change_password(body: PasswordIn, me=Depends(current_user)):
    try:
        us.change_password(me["user_id"], body.old_password, body.new_password)
    except AuthErrors as e:
        fail(e)
    return {"ok": True, "next": "다시 로그인하세요."}


# ── 초대 ──────────────────────────────────────────────────────────────────
@router.get("/api/invites/{token}")
def invite_info(token: str):
    """초대 링크를 연 화면. 아직 계정이 없으니 로그인 없이 볼 수 있어야 한다."""
    try:
        return cs.read_invite(token)
    except AuthErrors as e:
        fail(e)


@router.post("/api/invites/accept")
def invite_accept(body: AcceptIn):
    try:
        return cs.accept_invite(body.token, body.name, body.password)
    except AuthErrors as e:
        fail(e)


# ── 회사 (팀 관리) ────────────────────────────────────────────────────────
class InviteIn(Body):
    email: str
    role: str


class RoleIn(Body):
    role: str


class MemberPatchIn(Body):
    """화면은 PATCH 로 {role} 또는 {active} 중 하나만 보낸다."""
    role: str | None = None
    active: bool | None = None


class UserPatchIn(Body):
    blocked: bool | None = None


def _base(request: Request) -> str:
    """초대 링크에 붙일 주소. 프록시 뒤면 X-Forwarded-* 를 본다."""
    h = request.headers
    host = h.get("x-forwarded-host") or h.get("host") or ""
    scheme = h.get("x-forwarded-proto") or request.url.scheme
    return f"{scheme}://{host}" if host else ""


@router.get("/api/company/members")
def company_members(request: Request, me=Depends(current_user)):
    """
    화면의 팀 관리는 이 주소 하나만 부른다 (app.js loadTeam).
    그래서 초대 목록과 기록도 같이 넣는다. 따로 안 부르기 때문이다.
    """
    out = {"members": [cs.member_view(m) for m in cs.members(me["companyId"])],
           "company": me["company"], "domain": me["domain"],
           "invites": [], "audit": []}
    if cs.can(me["role"], "team"):
        base = _base(request)
        out["invites"] = [cs.invite_view(i, base)
                          for i in cs.list_invites(me["companyId"])
                          if i.get("status") == "pending"]
        out["audit"] = cs.team_audit(me["companyId"])
    return out


@router.get("/api/company/invites")
def company_invites(request: Request, me=Depends(require("team"))):
    rows = [cs.invite_view(i, _base(request))
            for i in cs.list_invites(me["companyId"])]
    return {"items": rows, "invites": rows}


@router.post("/api/company/invites")
def company_invite(body: InviteIn, me=Depends(require("team"))):
    """메일을 안 보낸다. token 을 돌려주면 화면이 링크로 띄운다."""
    try:
        return cs.invite(me["companyId"], body.email, body.role, me["email"])
    except AuthErrors as e:
        fail(e)


@router.post("/api/company/invites/{token}/resend")
def company_invite_resend(token: str, request: Request,
                          me=Depends(require("team"))):
    """화면의 "다시 보내기". 메일을 안 보내므로 기한만 다시 늘린다."""
    try:
        out = cs.resend_invite(me["companyId"], token, me["email"])
    except AuthErrors as e:
        fail(e)
    out["invite_url"] = f"{_base(request).rstrip('/')}/?invite={token}"
    return out


@router.delete("/api/company/invites/{token}")
def company_invite_revoke(token: str, me=Depends(require("team"))):
    try:
        return cs.revoke_invite(me["companyId"], token, me["email"])
    except AuthErrors as e:
        fail(e)


@router.patch("/api/company/members/{email}")
def company_member_patch(email: str, body: MemberPatchIn,
                         me=Depends(require("team"))):
    """화면이 쓰는 자리. {role} 이면 역할 변경, {active} 면 사용 중지·해제."""
    if body.role is None and body.active is None:
        raise HTTPException(status_code=400,
                            detail={"error_code": "NOTHING_TO_CHANGE",
                                    "message": "role 또는 active 를 보내세요."})
    try:
        if body.role is not None:
            return cs.set_member_role(me["companyId"], email, body.role,
                                      me["email"])
        return cs.set_member_active(me["companyId"], email, body.active,
                                    me["email"])
    except AuthErrors as e:
        fail(e)


@router.post("/api/company/members/{email}")
def company_member_role(email: str, body: RoleIn, me=Depends(require("team"))):
    """PATCH 와 같은 일. 먼저 만든 자리라 남겨 둔다."""
    try:
        return cs.set_member_role(me["companyId"], email, body.role, me["email"])
    except AuthErrors as e:
        fail(e)


@router.delete("/api/company/members/{email}")
def company_member_remove(email: str, me=Depends(require("team"))):
    try:
        return cs.remove_member(me["companyId"], email, me["email"])
    except AuthErrors as e:
        fail(e)


# ── 서비스 운영자 ─────────────────────────────────────────────────────────
class CompanyStatusIn(Body):
    status: str


@router.get("/api/admin/companies")
def admin_companies(_=Depends(require_super)):
    # 화면은 items 를 먼저 본다 (app.js: c.items || c).
    rows = [cs.company_view(c) for c in cs.list_companies()]
    return {"items": rows, "companies": rows}


@router.patch("/api/admin/companies/{company_id}")
def admin_company_patch(company_id: str, body: CompanyStatusIn,
                        me=Depends(require_super)):
    try:
        return cs.set_company_status(company_id, body.status, me["email"])
    except AuthErrors as e:
        fail(e)


@router.post("/api/admin/companies/{company_id}")
def admin_company_status(company_id: str, body: CompanyStatusIn,
                         me=Depends(require_super)):
    """PATCH 와 같은 일. 먼저 만든 자리라 남겨 둔다."""
    try:
        return cs.set_company_status(company_id, body.status, me["email"])
    except AuthErrors as e:
        fail(e)


@router.get("/api/admin/users")
def admin_users(_=Depends(require_super)):
    rows = [cs.user_view(u) for u in cs.all_users()]
    return {"items": rows, "users": rows}


@router.patch("/api/admin/users/{email}")
def admin_user_patch(email: str, body: UserPatchIn, _=Depends(require_super)):
    """
    화면의 "로그인 차단". 아직 안 만들었다.

    detail 을 글자로 돌려준다 — app.js 의 api() 가 detail 이 객체면
    JSON 을 그대로 toast 에 찍어서, 사람이 읽을 문장이 안 된다.
    """
    raise HTTPException(
        status_code=501,
        detail="운영자의 사용자 차단은 아직 지원하지 않습니다. "
               "회사 관리자가 구성원 관리에서 사용 중지할 수 있습니다.")


_ACTION_KO = {"VIEW_ORIGINAL": "원본 조회",
              "DOWNLOAD_ORIGINAL": "원본 내려받기",
              "REVIEW": "검토"}
_OUTCOME_KO = {"STARTED": "시작", "SUCCEEDED": "성공",
               "FAILED": "실패", "DENIED": "거부"}


def _audit_view(r):
    """화면은 text · actor · at 세 칸을 읽는다 (app.js 의 audit-log)."""
    act = _ACTION_KO.get(r.get("action"), r.get("action") or "")
    out = _OUTCOME_KO.get(r.get("outcome"), r.get("outcome") or "")
    where = r.get("evidence_id") or ""
    ver = r.get("evidence_version")
    if ver:
        where = f"{where} v{ver}"
    return {**r, "text": f"{where} {act} · {out}".strip(),
            "at": r.get("occurred_at")}


@router.get("/api/admin/audit")
def admin_audit(limit: int = 200, _=Depends(require_super)):
    """
    운영자가 남의 회사 증적을 본 기록. access_audit.py 의 SQLite 를 읽는다.

    운영자 자신이 보는 화면이라 변조 방지가 아니다. 그건 외부 보관이 필요하고
    아직 안 했다 (handoff/access_audit_integration.md 참고).
    """
    import sqlite3
    from contextlib import closing
    try:
        with closing(sqlite3.connect(owner.AUDIT_DB)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT occurred_at, actor, role, action, evidence_id, "
                "       evidence_version, outcome FROM access_event "
                " ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 1000)),)
            ).fetchall()
        out = [_audit_view(dict(r)) for r in rows]
        return {"items": out, "events": out}
    except sqlite3.Error:
        return {"items": [], "events": [],
                "note": "감사 기록이 아직 없습니다."}


@router.get("/api/admin/settings")
def admin_settings(_=Depends(require_super)):
    # 화면은 응답을 그대로 settings 로 쓴다 (app.js: settings: s).
    # 그래서 평평하게 주고, settings 키도 같이 남긴다.
    flat = {
        "requireApproval": False,       # 가입 승인 흐름은 아직 없다
        "blockedDomains": sorted(cs.PUBLIC_DOMAINS),
        "mail_enabled": False,
        "invite_days": cs.INVITE_DAYS,
        "session_hours": us.SESSION_HOURS,
        "max_failed": us.MAX_FAILED,
        "note": "메일 발송과 가입 정책 저장은 구현하지 않았습니다.",
    }
    return {**flat, "settings": flat}


@router.put("/api/admin/settings")
def admin_settings_save(_=Depends(require_super)):
    """화면의 "가입 정책 변경". 아직 안 만들었다."""
    raise HTTPException(
        status_code=501,
        detail="가입 정책 저장은 아직 지원하지 않습니다. "
               "지금은 가입하면 바로 쓸 수 있고, 공용 메일 도메인만 막습니다.")


# ── 혼자 띄울 때 ──────────────────────────────────────────────────────────
app = FastAPI(title="ISMS-P Auth API", version="0.2.0")
app.include_router(router)


@app.get("/health")
def health():
    return {"ok": True, "spec": {"user": us.SPEC_VERSION,
                                 "company": cs.SPEC_VERSION,
                                 "owner": owner.SPEC_VERSION}}
