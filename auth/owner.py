"""
owner.py : "지금 이 요청은 어느 회사 것만 봐야 하는가" 를 한 군데에서 정한다.

격리 단위는 **회사**다 (IS_SO_UI 모델). 같은 회사 사람끼리는 서로 올린 증적을
본다. 다른 회사 것은 안 보인다.

    evidence.company_id   누가 볼 수 있나   ← 격리
    evidence.uploaded_by  누가 올렸나       ← 책임추적성

소유자는 evidence 한 표에만 있고, phase2_result·phase2_excluded·phase1_mapping·
chunk 는 evidence_id 로 거기 이어 붙는다. 여러 표에 복사하면 한쪽이 어긋난다.

쓰는 법 — 요청마다 main_api.py 의 미들웨어가 한 번 심는다.

    owner.use(company_id="C0001", actor="hong@acme.co.kr", role="admin")
    owner.where("e")       "e.company_id = 'C0001'"
    owner.owns(cur, eid)   이 증적이 우리 회사 것인가

★ 서비스 운영자(super)
   super 는 **고객 증적에 접근하지 않는다** (팀 결정 2026-10-11). 화면에도
   "운영 관리자는 회사의 증적 내용을 열람할 수 없습니다" 라고 적혀 있다.
   회사·사용자 관리만 한다 (/api/admin/*).

   super 로 들어온 요청에는 "아무것도 안 보인다" 는 조건이 붙는다. 회사가
   없다고 안 거르면 전부 보이므로, 거기를 막는 게 이 블록의 요지다.
   건드리려 한 자리는 audit_denied() 가 DENIED 로 남긴다.

터미널 도구(phase2_runner, phase2_report)는 아무도 안 심어서 거르지 않는다.
"""
from __future__ import annotations

import contextvars
import os
import re
from pathlib import Path

SPEC_VERSION = "owner-0.2"

# 진짜 owner 모듈이라는 표식. 대체품에는 False 로 들어 있다.
AVAILABLE = True

# 회사 번호 모양. 따옴표·공백이 들어올 수 없어야 SQL 에 바로 박을 수 있다.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")

_company: contextvars.ContextVar = contextvars.ContextVar("company", default=None)
_actor: contextvars.ContextVar = contextvars.ContextVar("actor", default=None)
_role: contextvars.ContextVar = contextvars.ContextVar("role", default=None)
_super: contextvars.ContextVar = contextvars.ContextVar("is_super", default=False)


class NotYours(Exception):
    """남의 회사 증적을 건드리려 할 때. API 층에서 404 로 바꾼다."""

    code = "EVIDENCE_NOT_FOUND"
    message = "증적을 찾을 수 없습니다."


# ── 심고 꺼내기 ───────────────────────────────────────────────────────────
def use(company_id=None, actor=None, role=None, is_super=False):
    """이 요청의 범위를 정한다. 미들웨어가 요청마다 부른다."""
    if company_id and not _SAFE_ID.match(company_id):
        raise ValueError(f"회사 번호 모양이 아닙니다: {company_id!r}")
    if not company_id and not is_super and (actor or role):
        # 사람은 있는데 회사가 없으면 거를 기준이 없다. 그대로 두면
        # 모든 회사가 보인다 — 열어두는 쪽으로 틀리지 않게 막는다.
        raise ValueError("회사 없는 사용자에게는 범위를 정할 수 없습니다.")
    _company.set(company_id or None)
    _actor.set(actor or None)
    _role.set(role or None)
    _super.set(bool(is_super))


def clear():
    _company.set(None)
    _actor.set(None)
    _role.set(None)
    _super.set(False)


def current():
    """지금 거를 회사 번호. None 이면 안 거른다 (터미널)."""
    return _company.get()


def blocked() -> bool:
    """
    아무것도 보여주지 않을 요청인가.

    super 가 그렇다. 회사가 없어서 current() 가 None 인데, 그걸 터미널처럼
    "안 거른다" 로 읽으면 모든 회사가 보인다. 그 반대로 둔다.
    """
    return _super.get()


def actor():
    return _actor.get()


def role():
    return _role.get()


def is_super() -> bool:
    return _super.get()


def company_of_actor():
    """super 라도 자기 회사 번호는 그대로 돌려준다. 기록에 쓴다."""
    return _company.get()


# ── SQL 조각 ──────────────────────────────────────────────────────────────
def _quoted() -> str:
    """
    회사 번호를 SQL 에 바로 박을 수 있는 모양으로.

    보통은 %s 로 값을 넘겨야 하지만, 이 조각이 끼어드는 자리가 전부 값 없는
    f-string 이라(summary() 의 질의 12개가 조각 하나를 돌려 쓴다) 질의마다
    인자를 더 넣으면 틀리기 쉽다. 대신 _SAFE_ID 를 두 번 통과시킨다 —
    use() 에서 한 번, 여기서 또 한 번. 따옴표가 들어올 수 없다.
    """
    cid = current()
    if not _SAFE_ID.match(cid):
        raise ValueError(f"회사 번호 모양이 아닙니다: {cid!r}")
    return "'" + cid + "'"


def where(alias: str = "evidence") -> str:
    """evidence 를 직접 거는 질의용. 거를 게 없으면 항상 참."""
    if blocked():
        return "1 = 0"
    if current() is None:
        return "1 = 1"
    return f"{alias}.company_id = {_quoted()}"


def mine(column: str = "evidence_id", table: str | None = None) -> str:
    """evidence 를 안 거는 표(phase2_result 등)용. 하위 질의로 본다."""
    if blocked():
        return "1 = 0"
    if current() is None:
        return "1 = 1"
    left = f"{table}.{column}" if table else column
    return (f"{left} IN (SELECT o_ev.evidence_id FROM evidence o_ev "
            f"WHERE o_ev.company_id = {_quoted()})")


def _no_filter() -> bool:
    return not blocked() and current() is None


def applies() -> bool:
    """
    이 요청에 조건을 붙여야 하나. WHERE 를 손으로 조립하는 자리에서 쓴다.

    current() is not None 으로 묻지 말 것 — super 는 회사가 없어서 그 질문에
    False 가 나오고, 그러면 아무 조건도 안 붙어 전부 보인다.
    """
    return not _no_filter()


def and_mine(column: str = "evidence_id", table: str | None = None) -> str:
    return "" if _no_filter() else " AND " + mine(column, table)


def and_where(alias: str = "evidence") -> str:
    return "" if _no_filter() else " AND " + where(alias)


# ── 한 건 확인 ────────────────────────────────────────────────────────────
def owns(cur, evidence_id: str, q=lambda s: s) -> bool:
    """
    이 증적이 우리 회사 것인가. 한 건을 손대기 전에 부른다.

    super 면 항상 거짓 — 고객 증적에 접근하지 않는다. 거를 게 없으면
    (터미널) 항상 참. 증적이 아예 없어도 참 —
    "없는 증적" 과 "남의 증적" 은 같은 404 로 답해야 한다. 다르게 답하면
    번호를 훑어서 남의 증적이 몇 건인지 셀 수 있다.

    회사가 안 적힌 증적(company_id NULL)은 웹에서 아무에게도 안 보인다.
    예전 자료나 터미널로 넣은 자료가 번호만 알면 열리는 자리가 된다.
    """
    if blocked():
        return False
    if current() is None:
        return True
    cur.execute(q("SELECT company_id FROM evidence WHERE evidence_id = %s"),
                (evidence_id,))
    row = cur.fetchone()
    if row is None:
        return True
    got = row["company_id"] if isinstance(row, dict) else row[0]
    return got == current()


def check(cur, evidence_id: str, q=lambda s: s):
    if not owns(cur, evidence_id, q):
        raise NotYours(f"{evidence_id} 증적을 찾을 수 없습니다.")


# ── super 가 본 것을 남긴다 ───────────────────────────────────────────────
# 감사 저장소. 웹에서 못 내려받는 자리에 둔다.
AUDIT_DB = os.getenv("ACCESS_AUDIT_DB") or str(
    Path(__file__).resolve().parent / "access_audit.sqlite3")

# access_audit.AccessAudit 의 grants. 역할 -> 허용 작업.
# super 는 비어 있다 — 고객 증적에 아무 작업도 허용하지 않는다. 그래서
# execute() 가 DENIED 를 남기고 AccessDenied 를 던진다.
GRANTS = {
    "super": set(),
    "admin": {"VIEW_ORIGINAL", "DOWNLOAD_ORIGINAL", "REVIEW"},
    "reviewer": {"VIEW_ORIGINAL", "REVIEW"},
    "member": {"VIEW_ORIGINAL"},
}

_guard = None


def guard():
    """팀원이 만든 access_audit.AccessAudit 를 한 번만 만들어 쓴다."""
    global _guard
    if _guard is None:
        import sys
        here = Path(__file__).resolve().parent
        for base in [here, *here.parents]:
            cand = base / "phase2_통합"
            if (cand / "access_audit.py").is_file():
                if str(cand) not in sys.path:
                    sys.path.insert(0, str(cand))
                break
        from access_audit import AccessAudit
        _guard = AccessAudit(AUDIT_DB, GRANTS)
    return _guard


def audit(action: str, evidence_id: str, version: int, operation):
    """
    원본·결과를 실제로 읽는 자리를 감싼다. 기록이 안 되면 작업을 안 돌린다.

        owner.audit("VIEW_ORIGINAL", "E0002", 1, lambda: load_result("E0002"))

    super 가 남의 회사 것을 볼 때 특히 중요하다. 그게 이 함수를 만든 이유다.
    같은 회사 안의 조회는 기록할 필요가 적지만, 같은 경로로 지나가게 두면
    "어떤 건 기록되고 어떤 건 아닌" 구멍이 안 생긴다.
    """
    return guard().execute(actor=actor() or "(미로그인)", role=role() or "",
                           action=action, evidence_id=evidence_id,
                           version=int(version), operation=operation)


def audit_super(action: str, evidence_id: str, version: int, operation):
    """
    super 일 때만 감싸고, 아니면 그냥 돌린다.

    지금 정책에서는 super 가 여기까지 오지 못한다 (owns() 가 먼저 막는다).
    정책이 "볼 수 있다" 로 바뀌면 이 자리가 다시 쓰인다.
    """
    if not is_super():
        return operation()
    return audit(action, evidence_id, version, operation)


def audit_denied(action: str, evidence_id: str, version: int = 1) -> None:
    """
    막은 것을 남긴다. super 가 고객 증적을 건드리려 한 자리.

    GRANTS["super"] 가 비어 있어서 execute() 가 DENIED 를 적고 AccessDenied 를
    던진다. 그 예외는 여기서 삼킨다 — 부르는 쪽은 이미 404 로 답할 참이다.
    """
    if not is_super():
        return
    try:
        audit(action, evidence_id, int(version), lambda: None)
    except Exception:
        pass
