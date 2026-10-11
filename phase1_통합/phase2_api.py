"""
phase2_api.py : Phase 2 판정 조회 · 승인 · 수정 · 반려 엔드포인트.

이 파일은 라우터만 만든다. 서버는 저장소 뿌리의 main_api.py 가 띄운다.
로그인 검사와 오류 번역은 auth/auth_api.py 것을 그대로 쓴다.

    uvicorn phase2_api:app --port 8100        혼자 띄울 때

actor 는 요청 본문에서 받지 않는다. 항상 세션에서 꺼낸다.
요청 모델이 extra="forbid" 라 본문에 actor 를 넣으면 422 로 거부한다.

화면 쪽 규격은 API_auth_spec.md · API_response_spec.md 에 있다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from pydantic import Field

_HERE = Path(__file__).resolve().parent


def _find_root(start: Path) -> Path:
    def has(base, prefix):
        return any(p.is_dir() for p in base.glob(prefix))
    for base in [start, *start.parents]:
        if has(base, "phase1_*") and has(base, "phase2_*"):
            return base
    raise RuntimeError(f"저장소 뿌리를 찾지 못했습니다: {start}")


ROOT = _find_root(_HERE)
for _p in (ROOT / "phase2_통합", ROOT / "phase2_인터페이스", ROOT / "auth",
           ROOT / "phase1_입력" / "database", _HERE):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import phase1_mapping_store as ms          # noqa: E402
import phase2_result_store as rs           # noqa: E402
import phase2_review_store as rv           # noqa: E402
from auth_api import Body, _fail, current_user, require   # noqa: E402

router = APIRouter()


# ── 조회 ──────────────────────────────────────────────────────────────────
@router.get("/api/review/summary")
def summary(unknown_policy: str = "separate", _=Depends(current_user)):
    try:
        return rs.summary_response(unknown_policy=unknown_policy)
    except Exception as e:
        _fail(e)


@router.get("/api/review/coverage")
def coverage(include_controls: bool = True, _=Depends(current_user)):
    try:
        return rs.coverage_response(include_controls=include_controls)
    except Exception as e:
        _fail(e)


@router.get("/api/review/control/{control_id}")
def control(control_id: str, _=Depends(current_user)):
    try:
        return rs.control_response(control_id)
    except Exception as e:
        _fail(e)


@router.get("/api/review/control/{control_id}/candidates")
def candidates(control_id: str, include_history: bool = False,
               _=Depends(current_user)):
    try:
        return {"control_id": control_id,
                "candidates": ms.candidates(control_id, include_history)}
    except Exception as e:
        _fail(e)


@router.get("/api/review/evidence/{evidence_id}")
def evidence(evidence_id: str, _=Depends(current_user)):
    try:
        return rs.evidence_response(evidence_id)
    except Exception as e:
        _fail(e)


@router.get("/api/review/queue")
def queue(_=Depends(current_user)):
    try:
        return {"queue": rs.review_queue()}
    except Exception as e:
        _fail(e)


@router.get("/api/review/excluded")
def excluded(include_history: bool = False, _=Depends(current_user)):
    try:
        return {"excluded": rs.excluded(include_history=include_history)}
    except Exception as e:
        _fail(e)


@router.get("/api/review/history/{result_id}")
def history(result_id: int, _=Depends(current_user)):
    try:
        return {"result_id": result_id, "history": rv.history(result_id)}
    except Exception as e:
        _fail(e)


# ── 승인 · 수정 · 반려 ────────────────────────────────────────────────────
class ApproveIn(Body):
    reason: str | None = None


class ModifyIn(Body):
    target: str
    value_after: str
    reason: str = Field(min_length=1)


class RejectIn(Body):
    reason: str = Field(min_length=1)


@router.post("/api/review/{result_id}/approve")
def approve(result_id: int, body: ApproveIn, me=Depends(require("review"))):
    try:
        return rv.approve(result_id, actor=me["name"],
                          reason=body.reason, role=me["role"])
    except Exception as e:
        _fail(e)


@router.post("/api/review/{result_id}/modify")
def modify(result_id: int, body: ModifyIn, me=Depends(require("review"))):
    try:
        return rv.modify(result_id, actor=me["name"],
                         target=body.target, value_after=body.value_after,
                         reason=body.reason, role=me["role"])
    except Exception as e:
        _fail(e)


@router.post("/api/review/{result_id}/reject")
def reject(result_id: int, body: RejectIn, me=Depends(require("review"))):
    try:
        return rv.reject(result_id, actor=me["name"],
                         reason=body.reason, role=me["role"])
    except Exception as e:
        _fail(e)


@router.post("/api/review/evidence/{evidence_id}/approve")
def approve_evidence(evidence_id: str, body: ApproveIn,
                     me=Depends(require("review"))):
    """
    증적 하나에 걸린 판정을 한 번에 승인한다.

    화면이 증적 단위로 만들어져 있어서 열어둔 자리다. 한 건이라도 막히면
    그 건만 skipped 에 담고 나머지는 그대로 승인한다.
    """
    try:
        rows = rs.by_evidence(evidence_id)
    except Exception as e:
        _fail(e)
    targets = [r for r in rows
               if r.get("review_state") in ("PENDING", "REVALIDATING")]
    if not targets:
        return {"evidence_id": evidence_id, "approved": 0, "skipped": [],
                "message": "승인할 판정이 없습니다."}
    done, skipped = [], []
    for r in targets:
        try:
            rv.approve(r["result_id"], actor=me["name"],
                       reason=body.reason, role=me["role"])
            done.append(r["result_id"])
        except Exception as e:                                  # noqa: BLE001
            skipped.append({"result_id": r["result_id"],
                            "control_id": r.get("control_id"),
                            "error_code": getattr(e, "code", "INTERNAL"),
                            "message": getattr(e, "message", str(e))})
    return {"evidence_id": evidence_id, "approved": len(done),
            "result_ids": done, "skipped": skipped}


# ── 혼자 띄울 때 ──────────────────────────────────────────────────────────
app = FastAPI(title="ISMS-P Phase2 Review API", version="0.1.0")
app.include_router(router)
