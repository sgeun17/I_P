"""
phase2_review_store.py : 승인·수정·반려와 검토 이력

    approve(result_id, actor, reason=None)
    modify (result_id, actor, target, value_after, reason)
    reject (result_id, actor, reason)
    history(result_id)

검토 상태는 판정 하나(result_id)마다 따로 있다. 한 증적이 통제항목 여러 개에
걸리면 항목마다 승인한다. evidence.status 는 그 증적의 모든 현재 판정을
종합해서 다시 계산한다.

    수정  그 판정은 PENDING 그대로. 여러 군데를 고치는 동안 유지
    승인  고친 게 있으면 REVALIDATING, 없으면 APPROVED
    반려  REJECTED

승인할 때 판정값과 사유 코드가 맞는지 검사한다 (MET 은 코드 없음,
NOT_MET·UNKNOWN 은 1개 이상). 어긋나면 거부한다 — 그대로 내보내면 출력 스키마
검증에서 떨어진다. 종합 판정이 문항 계산값과 다른 것은 막지 않고 경고로 남긴다.

이력 INSERT 와 상태 UPDATE 는 한 트랜잭션이다. 중간에 끊기면 둘 다 롤백된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:                                  # 패키지로 import 할 때
    from .phase2_result_store import (_connect, _q, _rows, _now, _lock, _sync_status,
                                      apply_history, split_target, TARGET_FIELDS,
                                      check_consistency,
                                      OVERALL_VALUES, ITEM_VALUES)
except ImportError:                    # 폴더에서 바로 돌릴 때
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from phase2_result_store import (_connect, _q, _rows, _now, _lock, _sync_status,
                                     apply_history, split_target, TARGET_FIELDS,
                                     check_consistency,
                                     OVERALL_VALUES, ITEM_VALUES)

# phase2_status.py 를 찾는다. 같은 폴더 → 형제 폴더 → 저장소 뿌리 순으로 본다.
_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "phase2_인터페이스", *(
        [p / "phase2_인터페이스" for p in _HERE.parents])):
    if (_p / "phase2_status.py").is_file():
        sys.path.insert(0, str(_p))
        break
import phase2_status as st   # noqa: E402

ACTIONS = ("APPROVE", "MODIFY", "REJECT")
ROLES = ("REVIEWER", "APPROVER")

# 반려한 판정을 어느 상태로 둘지. 합의 전 기본값.
REJECT_STATE = "REJECTED"

# 권한 분리를 켤지. 로그인이 아직 없어 기본은 끔.
ENFORCE_ROLE = False

# 기준팀 사유 코드. reason_codes 를 고칠 때 이 목록으로 검사한다.
# 비워두면 검사하지 않는다. load_reason_codes() 로 카탈로그에서 채운다.
REASON_CODES: set[str] = set()


class ReviewError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def load_reason_codes(path):
    """기준팀 chapter2_reason_codes_draft.json 에서 사유 코드를 읽어둔다."""
    d = json.load(open(path, encoding="utf-8"))
    REASON_CODES.clear()
    REASON_CODES.update(c["code"] for c in d.get("codes", []) if c.get("code"))
    return sorted(REASON_CODES)


def _check_role(action, role):
    if not ENFORCE_ROLE:
        return
    if role not in ROLES:
        raise ReviewError("ROLE_REQUIRED", f"권한이 필요합니다 (허용: {', '.join(ROLES)}).")
    if action == "APPROVE" and role != "APPROVER":
        raise ReviewError("ROLE_NOT_ALLOWED", "승인은 APPROVER 만 할 수 있습니다.")
    if action == "MODIFY" and role != "REVIEWER":
        raise ReviewError("ROLE_NOT_ALLOWED", "수정은 REVIEWER 만 할 수 있습니다.")


def _validate_target(effective, target, value_after):
    """
    무엇을 어떤 값으로 고치는지 검사한다.
    없는 문항이나 허용되지 않은 값은 받지 않는다.
    """
    if target == "overall_result":
        if value_after not in OVERALL_VALUES:
            raise ReviewError("VALUE_INVALID",
                              f"종합 판정은 {', '.join(OVERALL_VALUES)} 중 하나여야 합니다 "
                              f"(받은 값: {value_after!r}).")
        return effective.get("overall_result")

    item_id, field = split_target(target)
    item = next((i for i in (effective.get("items") or [])
                 if i.get("item_id") == item_id), None)
    if item is None:
        raise ReviewError(
            "TARGET_NOT_FOUND",
            f"그런 문항이 없습니다: {item_id}. 고칠 수 있는 것은 overall_result, "
            f"<문항ID>, <문항ID>.{'/'.join(TARGET_FIELDS)} 입니다.")

    if not field:
        if value_after not in ITEM_VALUES:
            raise ReviewError("VALUE_INVALID",
                              f"문항 판정은 {', '.join(ITEM_VALUES)} 중 하나여야 합니다 "
                              f"(받은 값: {value_after!r}).")
        return item.get("result")

    # field 는 TARGET_FIELDS 안의 값만 올 수 있다 (split_target 이 보장한다)
    if not REASON_CODES:
        # 카탈로그가 없으면 아무 문자열이나 사유 코드로 들어간다. 그래서 막는다.
        raise ReviewError(
            "REASON_CODE_CATALOG_NOT_LOADED",
            "사유 코드 카탈로그를 먼저 읽어야 합니다. "
            "load_reason_codes('phase2_기준/chapter2_reason_codes_draft.json')")
    codes = [c.strip() for c in (value_after or "").split(",") if c.strip()]
    bad = [c for c in codes if c not in REASON_CODES]
    if bad:
        raise ReviewError("REASON_CODE_INVALID",
                          f"기준팀 사유 코드가 아닙니다: {', '.join(bad)}")
    return ",".join(item.get("reason_codes") or [])


def _record(action, result_id, actor, *, role=None, target=None,
            value_after=None, reason=None):
    if action not in ACTIONS:
        raise ReviewError("ACTION_INVALID", f"모르는 동작: {action}")
    if not actor:
        raise ReviewError("ACTOR_REQUIRED", "누가 처리했는지 적어야 합니다.")
    _check_role(action, role)

    now = _now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # 이 판정 줄을 먼저 잠근다. 두 사람이 동시에 처리해도 seq 가 겹치지 않는다.
            cur.execute(_q("SELECT evidence_id, control_id, review_state, payload "
                           "FROM phase2_result WHERE result_id = %s" + _lock()),
                        (result_id,))
            rows = _rows(cur)
            if not rows:
                raise ReviewError("RESULT_NOT_FOUND", f"판정 결과가 없습니다: {result_id}")
            evidence_id = rows[0]["evidence_id"]
            state_before = rows[0]["review_state"]
            payload = rows[0]["payload"]
            payload = json.loads(payload) if isinstance(payload, str) else payload

            if state_before not in ("PENDING", "REVALIDATING"):
                raise ReviewError(
                    "NOT_IN_REVIEW",
                    f"검토 대기 상태가 아닙니다 (지금 {state_before}). "
                    "승인·수정·반려는 PENDING 인 판정에만 할 수 있습니다.")

            cur.execute(_q("SELECT status FROM evidence WHERE evidence_id = %s"),
                        (evidence_id,))
            srows = _rows(cur)
            if not srows:
                raise ReviewError("EVIDENCE_NOT_FOUND", f"증적이 없습니다: {evidence_id}")
            status_before = srows[0]["status"]

            cur.execute(_q("SELECT seq, action, target, value_after FROM review_history "
                           "WHERE result_id = %s ORDER BY seq"), (result_id,))
            hist = _rows(cur)
            seq = (max((int(h["seq"]) for h in hist), default=0)) + 1
            effective = apply_history(payload, hist)

            value_before = None
            if action == "MODIFY":
                # 고치기 전 값은 호출자 말을 믿지 않고 저장된 결과에서 읽는다.
                value_before = _validate_target(effective, target, value_after)
                state_after = state_before          # 수정은 상태를 옮기지 않는다
            elif action == "APPROVE":
                # 승인 직전에 판정값과 사유 코드가 맞는지 본다.
                # 어긋난 채로 승인하면 그 결과는 출력 스키마 검증에서 떨어진다.
                con = check_consistency(effective)
                if con["errors"]:
                    raise ReviewError(
                        "INCONSISTENT_RESULT",
                        "판정값과 사유 코드가 맞지 않아 승인할 수 없습니다. "
                        + " / ".join(e["message"] for e in con["errors"]))
                modified = any(h["action"] == "MODIFY" for h in hist)
                state_after = "REVALIDATING" if modified else "APPROVED"
            else:
                state_after = REJECT_STATE

            cur.execute(_q("UPDATE phase2_result SET review_state = %s WHERE result_id = %s"),
                        (state_after, result_id))
            status_after = _sync_status(cur, evidence_id) or status_before
            if status_after != status_before:
                st.check(status_before, status_after)

            cur.execute(_q(
                "INSERT INTO review_history "
                "(result_id, seq, action, actor, actor_role, acted_at, target, "
                " value_before, value_after, reason, state_before, state_after, "
                " status_before, status_after) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                (result_id, seq, action, actor, role, now, target,
                 value_before, value_after, reason,
                 state_before, state_after, status_before, status_after))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"result_id": result_id, "seq": seq, "action": action,
            "state_before": state_before, "state_after": state_after,
            "status_before": status_before, "status_after": status_after,
            "value_before": value_before, "acted_at": now.isoformat()}


def approve(result_id, actor, reason=None, role=None):
    """
    그대로 승인. 앞서 고친 게 있으면 다시 판정해야 하므로 REVALIDATING 이 된다.
    판정값과 사유 코드가 어긋나 있으면 INCONSISTENT_RESULT 로 거부한다.
    """
    return _record("APPROVE", result_id, actor, role=role, reason=reason)


def modify(result_id, actor, target, value_after, reason, role=None):
    """
    사람이 값을 고쳤다. 원본 payload 는 안 바뀌고 이력에만 쌓인다.
    조회하면 payload + 이력을 합친 effective 로 나온다.

        target  'overall_result'
                '2.5.1-Q03'                 문항 판정
                '2.5.1-Q03.reason_codes'    사유 코드 (쉼표로 구분)
    """
    if not target:
        raise ReviewError("TARGET_REQUIRED", "무엇을 고쳤는지 적어야 합니다.")
    if value_after is None:
        raise ReviewError("VALUE_REQUIRED", "바꾼 값을 적어야 합니다.")
    if not reason:
        raise ReviewError("REASON_REQUIRED", "수정 사유를 적어야 합니다.")
    return _record("MODIFY", result_id, actor, role=role, target=target,
                   value_after=value_after, reason=reason)


def reject(result_id, actor, reason, role=None):
    """반려. 사유가 반드시 있어야 한다."""
    if not reason:
        raise ReviewError("REASON_REQUIRED", "반려 사유를 적어야 합니다.")
    return _record("REJECT", result_id, actor, role=role, reason=reason)


def history(result_id):
    """그 결과에 대한 처리 이력. 순서대로."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(
                "SELECT history_id, seq, action, actor, actor_role, acted_at, target, "
                "value_before, value_after, reason, state_before, state_after, "
                "status_before, status_after "
                "FROM review_history WHERE result_id = %s ORDER BY seq"), (result_id,))
            return _rows(cur)
    finally:
        conn.close()
