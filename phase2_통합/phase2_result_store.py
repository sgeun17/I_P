"""
phase2_result_store.py : Phase 2 판정 결과 저장·조회

    save(output, row=None, audit=None)   판정 결과 한 건 저장
    load(evidence_id, control_id)        한 건 꺼내기
    by_control(control_id)               그 통제항목에 걸린 증적 전부   ← 항목별 조회
    by_evidence(evidence_id)             그 증적이 걸친 통제항목 전부
    exclude(evidence_id, code, ...)      Phase 2 를 못 돌린 대상 기록
    summary()                            종합 — 집계 + 상태 + 승인 보류 사유

output 은 phase2_output.schema.json 을 따르는 dict 다.
row·audit 은 실행기가 같이 내는 파일이며, 없으면 그만큼 칸이 빈다.

사람이 고친 값은 payload 를 덮지 않는다. review_history 에 쌓이고,
조회할 때 payload + 이력을 합쳐 effective 로 만들어 돌려준다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# overall_result.py 를 찾는다. 같은 폴더 → 형제 폴더 → 상위 폴더 순.
_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "phase2_인터페이스",
           *[p / "phase2_인터페이스" for p in _HERE.parents]):
    if (_p / "overall_result.py").is_file():
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
        break
try:
    import overall_result as _orule
except ImportError:                      # 못 찾으면 자동 재계산만 못 한다
    _orule = None

# 입력팀 DB 설정(phase1_입력/database) 을 찾는다. 이걸 안 하면
# phase2_runner 를 거치지 않고 이 파일만 불러 쓸 때 (run_db_check.py 등)
# DB_NOT_CONFIGURED 가 난다.
for _p in (_HERE.parent / "phase1_입력" / "database",
           *[p / "phase1_입력" / "database" for p in _HERE.parents]):
    if (_p / "db.py").is_file():
        if str(_p) not in sys.path:
            sys.path.append(str(_p))
        break

try:
    from config import KST
    from db import get_connection
except ImportError:                      # 저장소 밖에서 단독 시험할 때
    from datetime import timedelta, timezone
    KST = timezone(timedelta(hours=9))
    get_connection = None

# 종합 판정 5값. overall_result.py 와 같은 순서를 쓴다.
OVERALL_VALUES = ("충족", "충족(보완 권고)", "확인 필요", "미충족", "증적 없음")

# 문항 판정 3값.
ITEM_VALUES = ("MET", "NOT_MET", "UNKNOWN")

# WBS 가 쓰는 4값과의 대응. 화면·보고서에서 WBS 용어가 필요할 때만 쓴다.
WBS_LABEL = {
    "충족": "적정",
    "충족(보완 권고)": "부분적정",
    "확인 필요": "판단불가",
    "미충족": "부적정",
    "증적 없음": "판단불가",
}

# 판정 하나의 검토 상태
REVIEW_STATES = ("NOT_REQUIRED", "PENDING", "APPROVED", "REVALIDATING", "REJECTED")

PENDING_STATUSES = ("UPLOADED", "PREPROCESSING", "PREPROCESSED", "MAPPING", "VALIDATING")

# Phase 2 를 돌리지 못한 사유
EXCLUDE_REASONS = {
    "SKIP_NO_MATCH": "Phase 1 이 통제항목을 찾지 못했다 (NO_MATCH)",
    "HOLD_PHASE1_INVALID": "Phase 1 결과가 규격에 안 맞아 보류했다 (E502)",
    "NO_CHECKLIST": "해당 통제항목의 질문지가 아직 없다",
    "FAILED": "앞 단계에서 실패했다",
    "ALL_TARGETS_UNRESOLVED": "판정 대상이 전부 제외·실패해서 남은 결과가 없다",
}

# 고치면 다시 될 수 있는 제외. run_pending(retry_failed=True) 가 다시 집는다.
RETRYABLE_REASONS = ("FAILED", "ALL_TARGETS_UNRESOLVED")


# ── "현재" 의 정의. 여기서만 정한다 ────────────────────────────────────
# 전에는 이 조건이 열한 군데에 흩어져 있었다. superseded_at 만 보는 곳,
# 버전까지 보는 곳이 섞여서 한 곳을 고칠 때마다 다음 곳이 드러났다.
# 조회·집계는 전부 이 함수를 지나간다.
#
#   현재 = 과거로 안 내려갔고 + 그 증적의 지금 버전인 것
#
# 버전을 안 보면 재업로드한 증적에서 v1 결과가 v2 와 같이 현재로 남는다.
def _is_current(alias: str = "phase2_result") -> str:
    return (f"{alias}.superseded_at IS NULL AND {alias}.evidence_version = "
            f"(SELECT e_cur.version FROM evidence e_cur "
            f" WHERE e_cur.evidence_id = {alias}.evidence_id)")


class ResultStoreError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ─────────────────────────────────────────────
# 연결
# ─────────────────────────────────────────────
_conn_factory = None
_paramstyle = "format"          # pymysql = %s, sqlite3 = ?
_supports_lock = True           # SELECT ... FOR UPDATE


def use_connection(factory, paramstyle="format", supports_lock=True):
    """시험할 때 다른 DB 를 끼우는 자리. 평소에는 부르지 않는다."""
    global _conn_factory, _paramstyle, _supports_lock
    _conn_factory, _paramstyle, _supports_lock = factory, paramstyle, supports_lock


def _connect():
    if _conn_factory is not None:
        return _conn_factory()
    if get_connection is None:
        raise ResultStoreError("DB_NOT_CONFIGURED", "DB 접속 설정을 찾을 수 없습니다.")
    return get_connection()


def _q(sql):
    return sql if _paramstyle == "format" else sql.replace("%s", "?")


def _lock():
    return " FOR UPDATE" if _supports_lock else ""


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else r for r in cur.fetchall()]


def _now():
    return datetime.now(KST).replace(tzinfo=None)


# ─────────────────────────────────────────────
# 저장
# ─────────────────────────────────────────────
def save(output: dict, row: dict, audit: dict | None = None,
         checklist_approved: bool = False, historical: bool = False,
         allow_non_primary: bool = False) -> int:
    """
    판정 결과 한 건을 저장하고 result_id 를 돌려준다.

    같은 증적·같은 통제항목의 이전 결과는 지우지 않고 superseded_at 을 찍는다.
    현재 유효한 줄을 먼저 잠그므로 두 번 동시에 들어와도 두 줄이 생기지 않는다.

    row 는 필수다. phase1_review_required 가 없으면 거부한다.
    안 넘기면 0 으로 저장돼서 Phase 1 검토가 열려 있는데도 COMPLETED 로 가버린다.
    정말 모르면 row={"phase1_review_required": None} 로 넘긴다 — 모름으로 저장되고
    승인 보류 사유가 붙는다.

    output["version"] 이 그 증적의 **지금 버전**이 아니면 거부한다. 현재 줄은
    (evidence_id, control_id) 하나뿐이라, v2 를 올린 뒤에 실수로 v1 을 다시
    판정하면 v1 결과가 현재가 되고 v2 가 과거로 밀린다.
    옛 버전을 정말 다시 판정해야 하면 historical=True 로 명시한다 — 그때는
    과거 줄로만 쌓이고 현재 줄은 건드리지 않는다.

    그 통제항목이 지금도 PRIMARY 가 아니면 거부한다. 판정이 도는 동안 검토자가
    매핑을 2.5.1 → 2.5.2 로 고치면, 늦게 끝난 2.5.1 결과가 다시 현재가 되어
    새 판정이 영영 안 돌 수 있다. 일부러 넣어야 하면 allow_non_primary=True.
    """
    for key in ("evidence_id", "version", "control_id", "overall_result",
                "schema_version", "rule_version"):
        if key not in output:
            raise ResultStoreError("OUTPUT_FIELD_MISSING", f"출력에 {key} 가 없습니다.")
    if output["overall_result"] not in OVERALL_VALUES:
        raise ResultStoreError("OVERALL_RESULT_INVALID",
                               f"모르는 판정값: {output['overall_result']}")

    if row is None or "phase1_review_required" not in row:
        raise ResultStoreError(
            "PHASE1_REVIEW_UNKNOWN",
            "row 에 phase1_review_required 가 있어야 합니다. 모르면 None 으로 넘기세요. "
            "빠뜨리면 Phase 1 검토가 열려 있어도 COMPLETED 로 넘어갑니다.")
    review = output.get("human_review") or {}
    extra = _extract(output, row, audit)
    state = "PENDING" if review.get("required") else "NOT_REQUIRED"
    now = _now()

    conn = _connect()
    try:
        with conn.cursor() as cur:
            # 증적 줄을 먼저 잠근다. 매핑 변경과 겹쳐도 한쪽이 기다린다.
            cur_ver = _evidence_version(cur, output["evidence_id"], lock=True)
            if int(output["version"]) != cur_ver and not historical:
                raise ResultStoreError(
                    "VERSION_NOT_CURRENT",
                    f"{output['evidence_id']} 의 지금 버전은 v{cur_ver} 인데 "
                    f"v{output['version']} 결과를 저장하려 합니다. 그대로 두면 "
                    f"옛 버전이 현재 값이 됩니다. 정말 과거 버전을 다시 판정하는 "
                    f"것이면 historical=True 로 넘기세요.")
            # 과거 버전을 다시 판정하는 경우에는 현재 줄을 건드리지 않고
            # 과거 줄로만 쌓는다 (처음부터 superseded_at 을 찍어 넣는다).
            old_version = historical and int(output["version"]) != cur_ver
            if not old_version:
                # 현재 줄은 (증적, 통제항목) 하나뿐이라 통제항목이 다르면
                # 옛 버전 판정도 현재로 남는다. v1/2.5.1 과 v2/2.5.2 가 같이
                # 현재가 되어 집계가 두 배로 잡힌다. 옛 버전은 과거로 내린다.
                # current-raw: 일부러 버전을 안 본다 — 옛 버전을 찾아 내리는 중
                cur.execute(_q("SELECT result_id FROM phase2_result "
                               "WHERE evidence_id = %s AND superseded_at IS NULL "
                               "AND evidence_version <> %s"),
                            (output["evidence_id"], cur_ver))
                for stale in _rows(cur):
                    cur.execute(_q("UPDATE phase2_result SET superseded_at = %s "
                                   "WHERE result_id = %s"), (now, stale["result_id"]))
            if (not old_version and not allow_non_primary
                    and not _is_current_primary(cur, output["evidence_id"],
                                                cur_ver, output["control_id"])):
                raise ResultStoreError(
                    "CONTROL_NOT_CURRENT_PRIMARY",
                    f"{output['control_id']} 은(는) {output['evidence_id']} "
                    f"v{cur_ver} 의 지금 PRIMARY 가 아닙니다. 판정이 도는 사이에 "
                    f"Phase 1 매핑이 바뀐 것으로 보입니다. 이 결과를 현재로 두면 "
                    f"새 매핑 판정이 영영 안 돕니다. 그래도 넣으려면 "
                    f"allow_non_primary=True.")
            if not old_version:
                # current-raw: 바로 위에서 옛 버전을 다 내렸다. 남은 건 지금 버전뿐
                cur.execute(_q(
                    "SELECT result_id FROM phase2_result "
                    "WHERE evidence_id = %s AND control_id = %s "
                    "AND superseded_at IS NULL" + _lock()),
                    (output["evidence_id"], output["control_id"]))
                for old in _rows(cur):
                    cur.execute(_q("UPDATE phase2_result SET superseded_at = %s "
                                   "WHERE result_id = %s"), (now, old["result_id"]))
            cur.execute(_q(
                "INSERT INTO phase2_result "
                "(evidence_id, evidence_version, control_id, control_name, overall_result, "
                " review_required, review_state, provisional, provisional_reason, "
                " phase1_review_required, phase1_review_reasons, error_codes, "
                " freshness_status, format_status, citation_count, citation_with_source, "
                " schema_version, rule_version, checklist_version, checklist_approved, "
                " payload, created_at, superseded_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                (output["evidence_id"], output["version"], output["control_id"],
                 output.get("control_name", ""), output["overall_result"],
                 1 if review.get("required") else 0, state,
                 1 if output.get("provisional") else 0,
                 output.get("provisional_reason"),
                 extra["phase1_review_required"], extra["phase1_review_reasons"],
                 extra["error_codes"], extra["freshness_status"], extra["format_status"],
                 extra["citation_count"], extra["citation_with_source"],
                 output["schema_version"], output["rule_version"],
                 output.get("checklist_version"), 1 if checklist_approved else 0,
                 json.dumps(output, ensure_ascii=False), now,
                 now if old_version else None))
            result_id = cur.lastrowid
            if not old_version:
                _sync_status(cur, output["evidence_id"])
        conn.commit()
        return result_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 정책 상태는 '나쁜 쪽이 이긴다'. 문항 하나라도 미주입이면 결과 전체가 미주입이다.
# audit 가 아예 없으면 '검사 자체를 안 했다'(NOT_EVALUATED)로 둔다.
# NULL 로 두면 "정책 문제 없음" 과 구분되지 않아 운영 승인으로 새어 나간다.
_POLICY_RANK = {"POLICY_MISSING": 3, "NOT_EVALUATED": 2, "UNKNOWN": 1}
POLICY_OK = "OK"
POLICY_NOT_EVALUATED = "NOT_EVALUATED"


def _worst(values):
    vals = [v for v in values if v]
    if not vals:
        return POLICY_NOT_EVALUATED
    best, rank = None, -1
    for v in vals:
        r = _POLICY_RANK.get(v, 0)
        if r > rank:
            best, rank = v, r
    return best


def _extract(output, row, audit):
    """승인 전에 같이 보여야 하는 값들을 출력·row·audit 에서 뽑는다."""
    review = output.get("human_review") or {}
    codes = review.get("error_codes") or []
    cits = [c for i in (output.get("items") or []) for c in (i.get("citations") or [])]
    items = (audit or {}).get("items") or []

    reasons = row.get("phase1_review_reasons") or []
    p1 = row.get("phase1_review_required")
    if p1 is None:                       # 모름 → 안전한 쪽(검토 남음)으로 둔다
        p1_flag, reasons = 1, (reasons or ["UNKNOWN"])
    else:
        p1_flag = 1 if p1 else 0
    return {
        "phase1_review_required": p1_flag,
        "phase1_review_reasons": ",".join(reasons) if reasons else None,
        "error_codes": ",".join(codes) if codes else None,
        "freshness_status": _worst((i.get("freshness") or {}).get("status") for i in items),
        "format_status": _worst((i.get("evidence_format") or {}).get("status")
                                for i in items),
        "citation_count": len(cits),
        "citation_with_source": sum(1 for c in cits if c.get("source")),
    }


# ─────────────────────────────────────────────
# 증적 상태 = 그 증적의 모든 현재 판정을 종합한 값
# ─────────────────────────────────────────────
def _sync_status(cur, evidence_id):
    """
    한 증적이 통제항목 여러 개에 걸리므로 evidence.status 는 항목 하나로 정하지 않는다.
        하나라도 Phase 1 검토 미완료 → REVIEW_REQUIRED
        하나라도 PENDING             → REVIEW_REQUIRED
        하나라도 REVALIDATING / REJECTED → VALIDATING
        그 외 (전부 APPROVED·NOT_REQUIRED) → COMPLETED
    판정이 하나도 없으면 건드리지 않는다.
    """
    cur.execute(_q("SELECT review_state, COUNT(*) AS n FROM phase2_result "
                   "WHERE evidence_id = %s AND " + _is_current() +
                   " GROUP BY review_state"), (evidence_id,))
    states = {r["review_state"]: int(r["n"]) for r in _rows(cur)}
    if not states:
        return None
    cur.execute(_q("SELECT COUNT(*) AS n FROM phase2_result "
                   "WHERE evidence_id = %s AND " + _is_current() +
                   " AND phase1_review_required = 1"), (evidence_id,))
    p1_open = int(_rows(cur)[0]["n"])
    if p1_open:
        # Phase 2 가 깨끗해도 Phase 1 검토가 남아 있으면 COMPLETED 로 가지 않는다.
        nxt = "REVIEW_REQUIRED"
    elif states.get("PENDING"):
        nxt = "REVIEW_REQUIRED"
    elif states.get("REVALIDATING") or states.get("REJECTED"):
        nxt = "VALIDATING"
    else:
        nxt = "COMPLETED"
    cur.execute(_q("UPDATE evidence SET status = %s WHERE evidence_id = %s"),
                (nxt, evidence_id))
    return nxt


def refresh_phase1_review(evidence_id: str, required: bool | None,
                          reasons=None) -> str | None:
    """
    Phase 1 사람 검토가 끝났을 때(또는 새로 열렸을 때) 불러준다.

    저장된 판정에는 '저장 시점의' Phase 1 상태가 복사돼 있다. 나중에 그 검토가
    끝나도 이 함수를 안 부르면 값이 1 로 남아 증적이 영원히 REVIEW_REQUIRED 다.
    증적 상태도 같이 다시 계산한다.
    """
    flag = 1 if (required or required is None) else 0
    rs = list(reasons or ([] if not flag else ["UNKNOWN"]))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("UPDATE phase2_result SET phase1_review_required = %s, "
                           "phase1_review_reasons = %s "
                           "WHERE evidence_id = %s AND " + _is_current()),
                        (flag, ",".join(rs) if rs else None, evidence_id))
            nxt = _sync_status(cur, evidence_id)
        conn.commit()
        return nxt
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# 판정값과 사유 코드가 맞는지
# ─────────────────────────────────────────────
def check_consistency(effective: dict) -> dict:
    """
    사람이 고친 뒤의 결과가 출력 계약과 맞는지 본다.

    errors    계약 위반. 이대로 내보내면 출력 스키마 검증에서 떨어진다
    warnings  사람의 판단이 규칙 계산값과 다르다. 막지는 않는다

        MET              reason_codes 가 비어야 한다
        NOT_MET·UNKNOWN  reason_codes 가 1개 이상이어야 한다
        종합 판정         문항으로 계산한 값과 같아야 한다 (다르면 경고)
    """
    errors, warnings = [], []
    for it in effective.get("items") or []:
        codes = it.get("reason_codes") or []
        if it.get("result") == "MET" and codes:
            errors.append({"code": "REASON_CODE_ON_MET", "item_id": it.get("item_id"),
                           "message": f"{it.get('item_id')} 는 MET 인데 사유 코드가 "
                                      f"남아 있습니다: {', '.join(codes)}"})
        if it.get("result") in ("NOT_MET", "UNKNOWN") and not codes:
            errors.append({"code": "REASON_CODE_MISSING", "item_id": it.get("item_id"),
                           "message": f"{it.get('item_id')} 는 {it.get('result')} 인데 "
                                      f"사유 코드가 없습니다"})
    if _orule is not None and (effective.get("items") or []):
        computed = _orule.compute(effective["items"],
                                  effective.get("critical_policy"))["overall_result"]
        if computed != effective.get("overall_result"):
            warnings.append({
                "code": "OVERALL_CONFLICTS_WITH_ITEMS",
                "message": f"문항으로 계산하면 '{computed}' 인데 "
                           f"'{effective.get('overall_result')}' 로 돼 있습니다",
                "computed": computed})
    return {"ok": not errors, "errors": errors, "warnings": warnings}


# ─────────────────────────────────────────────
# Phase 2 를 못 돌린 대상
# ─────────────────────────────────────────────
def _evidence_version(cur, evidence_id, lock: bool = False):
    """
    그 증적의 지금 버전. lock=True 면 증적 줄을 잠근다.

    Phase 1 매핑 변경과 Phase 2 결과 저장이 같은 증적에서 겹치면,
    "지금도 PRIMARY 인가" 를 확인한 직후 매핑이 바뀌어 옛 판정이 다시
    현재가 될 수 있다. 두 흐름이 같은 줄을 먼저 잠그면 그 틈이 없어진다.
    """
    cur.execute(_q("SELECT version FROM evidence WHERE evidence_id = %s"
                   + (_lock() if lock else "")), (evidence_id,))
    rows = _rows(cur)
    if not rows:
        raise ResultStoreError("EVIDENCE_NOT_FOUND", f"증적이 없습니다: {evidence_id}")
    return int(rows[0]["version"])


def exclude(evidence_id: str, reason_code: str, reason: str | None = None,
            control_id: str | None = None, version: int | None = None):
    """
    제외·보류를 기록한다. 안 적으면 올린 증적 중 일부가 화면에서 사라진다.

    control_id 를 주면 그 통제항목만 제외한 것이고, 안 주면 증적 전체다.
    version 을 안 주면 그 증적의 지금 버전으로 적는다. 버전을 같이 적어야
    v1 에서 제외한 것이 v2 재업로드까지 막지 않는다.
    """
    if reason_code not in EXCLUDE_REASONS:
        raise ResultStoreError("EXCLUDE_REASON_INVALID",
                               f"모르는 사유: {reason_code} "
                               f"(허용: {', '.join(EXCLUDE_REASONS)})")
    cid = control_id or ""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            ver = version if version is not None else _evidence_version(cur, evidence_id)
            cur.execute(_q("DELETE FROM phase2_excluded WHERE evidence_id = %s "
                           "AND evidence_version = %s AND control_id = %s"),
                        (evidence_id, ver, cid))
            cur.execute(_q("INSERT INTO phase2_excluded "
                           "(evidence_id, evidence_version, control_id, reason_code, "
                           " reason, noted_at) VALUES (%s,%s,%s,%s,%s,%s)"),
                        (evidence_id, ver, cid, reason_code,
                         reason or EXCLUDE_REASONS[reason_code], _now()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 지난 매핑에서 생긴 증적 단위 보류. 매핑이 바뀌면 이것들이 새 판정을 막으면 안 된다.
STALE_ON_REMAP = ("SKIP_NO_MATCH", "HOLD_PHASE1_INVALID", "FAILED",
                  "ALL_TARGETS_UNRESOLVED")


def _is_current_primary(cur, evidence_id, version, control_id):
    """
    그 통제항목이 지금도 PRIMARY 인가.
    phase1_mapping 이 없거나 그 증적 매핑이 하나도 없으면 '모름' 으로 보고 True.
    """
    try:
        cur.execute(_q("SELECT relation FROM phase1_mapping "
                       "WHERE evidence_id = %s AND evidence_version = %s"),
                    (evidence_id, version))
        rows = _rows(cur)
    except Exception:
        return True                      # 매핑 표가 아직 없다
    if not rows:
        # 매핑 줄이 하나도 없다. 두 가지를 구분해야 한다.
        #   아직 매핑을 저장 안 했다     → 모름. 막지 않는다
        #   PRIMARY 를 전부 없앴다       → 판정 대상이 아니다. 막는다
        # 후자는 SKIP_NO_MATCH 가 그 버전에 적혀 있다.
        cur.execute(_q("SELECT 1 AS hit FROM phase2_excluded "
                       "WHERE evidence_id = %s AND evidence_version = %s "
                       "AND control_id = '' AND reason_code = 'SKIP_NO_MATCH'"),
                    (evidence_id, version))
        return not _rows(cur)
    cur.execute(_q("SELECT relation FROM phase1_mapping "
                   "WHERE evidence_id = %s AND evidence_version = %s "
                   "AND control_id = %s"), (evidence_id, version, control_id))
    mine = _rows(cur)
    return bool(mine) and mine[0]["relation"] == "PRIMARY"


def _reopen_in_tx(cur, evidence_id, version, review_required=False):
    """
    매핑이 바뀌어 다시 판정해야 하는 증적을 대기 상태로 되돌린다.
    바깥 트랜잭션 안에서 부른다.

    두 가지를 한다.
      ① 지난 매핑에서 생긴 증적 단위 보류를 지운다.
         안 지우면 ALL_TARGETS_UNRESOLVED 같은 게 남아 run_pending 이
         이 증적을 영영 안 집는다 — supersede 해놓고 재판정이 안 된다.
      ② evidence.status 를 다시 연다.
         현재 판정이 하나도 안 남으면 _sync_status 는 아무것도 안 하므로
         COMPLETED 가 그대로 남는다. 여기서 명시적으로 바꾼다.

    돌려주는 값 {cleared: [사유코드], status: 새 상태}
    """
    marks = ", ".join(["%s"] * len(STALE_ON_REMAP))
    cur.execute(_q(f"SELECT reason_code FROM phase2_excluded "
                   f"WHERE evidence_id = %s AND evidence_version = %s "
                   f"AND control_id = '' AND reason_code IN ({marks})"),
                (evidence_id, version, *STALE_ON_REMAP))
    cleared = [r["reason_code"] for r in _rows(cur)]
    if cleared:
        cur.execute(_q(f"DELETE FROM phase2_excluded "
                       f"WHERE evidence_id = %s AND evidence_version = %s "
                       f"AND control_id = '' AND reason_code IN ({marks})"),
                    (evidence_id, version, *STALE_ON_REMAP))

    nxt = "REVIEW_REQUIRED" if review_required else "VALIDATING"
    cur.execute(_q("UPDATE evidence SET status = %s WHERE evidence_id = %s"),
                (nxt, evidence_id))
    return {"cleared": cleared, "status": nxt}


def missing_primary(cur, evidence_id, version):
    """
    지금 PRIMARY 인데 현재 판정이 없는 통제항목.
    매핑 표가 없거나 그 증적 매핑을 모르면 None (판단 불가).
    """
    try:
        cur.execute(_q("SELECT control_id FROM phase1_mapping "
                       "WHERE evidence_id = %s AND evidence_version = %s "
                       "AND relation = 'PRIMARY'"), (evidence_id, version))
        prim = [r["control_id"] for r in _rows(cur)]
    except Exception:
        return None
    if not prim:
        return None
    # current-raw: 버전을 인자로 받아 직접 지정한다
    cur.execute(_q("SELECT control_id FROM phase2_result "
                   "WHERE evidence_id = %s AND evidence_version = %s "
                   "AND superseded_at IS NULL"), (evidence_id, version))
    done = {r["control_id"] for r in _rows(cur)}
    return [c for c in prim if c not in done]


def _supersede_in_tx(cur, evidence_id, version, control_ids=None):
    """supersede 의 알맹이. 바깥 트랜잭션 안에서 부른다 (commit 안 한다)."""
    now = _now()
    # current-raw: 버전을 인자로 받아 직접 지정한다
    sql = ("SELECT result_id, control_id FROM phase2_result "
           "WHERE evidence_id = %s AND evidence_version = %s "
           "AND superseded_at IS NULL")
    args = [evidence_id, version]
    if control_ids:
        sql += " AND control_id IN (" + ", ".join(["%s"] * len(control_ids)) + ")"
        args += list(control_ids)
    cur.execute(_q(sql), tuple(args))
    rows = [(r["result_id"], r["control_id"]) for r in _rows(cur)]
    for rid, _cid in rows:
        cur.execute(_q("UPDATE phase2_result SET superseded_at = %s "
                       "WHERE result_id = %s"), (now, rid))
    if rows:
        _sync_status(cur, evidence_id)
    return rows


def supersede(evidence_id: str, version: int | None = None,
              control_ids=None, reason: str | None = None) -> list:
    """
    현재 판정을 과거로 돌린다. 지우지 않고 superseded_at 만 찍는다.

    Phase 1 매핑이 바뀌어 지금 판정이 더 이상 맞지 않을 때 쓴다. 과거가 되면
    run_pending 이 그 증적을 다시 집어 새 매핑 기준으로 다시 판정한다.

    control_ids 를 주면 그 통제항목만, 안 주면 그 증적의 현재 판정 전부.
    돌려주는 값은 과거로 돌린 (result_id, control_id) 목록.

    주의 — 사람이 그 판정에 달아둔 수정 이력도 같이 과거가 된다.
    그래서 매핑이 **실제로 바뀌었을 때만** 부른다.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            ver = version if version is not None else _evidence_version(cur, evidence_id)
            rows = _supersede_in_tx(cur, evidence_id, ver, control_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return rows


def _unexclude_in_tx(cur, evidence_id, version, control_ids):
    """제외 기록 지우기의 알맹이. 바깥 트랜잭션 안에서 부른다."""
    n = 0
    for cid in control_ids:
        cur.execute(_q("DELETE FROM phase2_excluded WHERE evidence_id = %s "
                       "AND evidence_version = %s AND control_id = %s"),
                    (evidence_id, version, cid or ""))
        n += 1
    return n


def unexclude(evidence_id: str, control_id: str | None = None,
              version: int | None = None) -> int:
    """제외 기록을 지운다. 다시 돌려서 성공했을 때 쓴다."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            ver = version if version is not None else _evidence_version(cur, evidence_id)
            cur.execute(_q("DELETE FROM phase2_excluded WHERE evidence_id = %s "
                           "AND evidence_version = %s AND control_id = %s"),
                        (evidence_id, ver, control_id or ""))
            n = cur.rowcount if hasattr(cur, "rowcount") else 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return n or 0


def excluded(include_history: bool = False):
    """
    제외·보류 기록. 기본은 **각 증적의 지금 버전**만.
    옛 버전 기록까지 섞이면 v1 의 NO_CHECKLIST 가 v2 대시보드에 계속 뜬다.
    """
    where = ("" if include_history else
             " WHERE evidence_version = (SELECT e_cur.version FROM evidence e_cur "
             " WHERE e_cur.evidence_id = phase2_excluded.evidence_id)")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q("SELECT evidence_id, evidence_version, control_id, "
                           "reason_code, reason, noted_at FROM phase2_excluded"
                           + where +
                           " ORDER BY evidence_id, evidence_version, control_id"))
            out = _rows(cur)
    finally:
        conn.close()
    for r in out:
        r["control_id"] = r["control_id"] or None
        r["scope"] = "control" if r["control_id"] else "evidence"
    return out


# ─────────────────────────────────────────────
# 조회
# ─────────────────────────────────────────────
_COLS = ("result_id, evidence_id, evidence_version, control_id, control_name, "
         "overall_result, review_required, review_state, provisional, provisional_reason, "
         "phase1_review_required, phase1_review_reasons, error_codes, "
         "freshness_status, format_status, citation_count, citation_with_source, "
         "checklist_version, checklist_approved, payload, created_at, superseded_at")


def _select(where, params, include_superseded=False, version_scoped=True):
    """
    version_scoped=False 면 지금 버전 조건을 빼고 과거로 안 내려간 줄을 다 준다.
    옛 버전 이력을 보여줄 때만 쓴다.
    """
    sql = f"SELECT {_COLS} FROM phase2_result WHERE " + where
    if not include_superseded:
        # current-raw: version_scoped=False 는 이력 조회용이라 일부러 뺀다
        sql += " AND " + (_is_current() if version_scoped
                          else "superseded_at IS NULL")
    sql += " ORDER BY control_id, evidence_id"
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(sql), params)
            out = _rows(cur)
            for r in out:
                cur.execute(_q("SELECT seq, action, actor, acted_at, target, "
                               "value_before, value_after, reason "
                               "FROM review_history WHERE result_id = %s ORDER BY seq"),
                            (r["result_id"],))
                r["history"] = _rows(cur)
    finally:
        conn.close()
    for r in out:
        _hydrate(r)
    return out


def _hydrate(r):
    r["payload"] = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
    r["review_required"] = bool(r["review_required"])
    r["provisional"] = bool(r["provisional"])
    r["phase1_review_required"] = bool(r["phase1_review_required"])
    r["checklist_approved"] = bool(r["checklist_approved"])
    r["phase1_review_reasons"] = r["phase1_review_reasons"].split(",") \
        if r["phase1_review_reasons"] else []
    r["error_codes"] = r["error_codes"].split(",") if r["error_codes"] else []
    r["effective"] = apply_history(r["payload"], r.get("history") or [])
    r["overall_result_effective"] = r["effective"]["overall_result"]
    r["modified_by_human"] = r["effective"] != r["payload"]
    r["consistency"] = check_consistency(r["effective"])
    r["approval_blockers"] = _blockers(r)
    return r


# 문항 ID 에 점이 들어간다 (2.5.1-Q03). 그래서 첫 점으로 자르면 안 된다.
# 고칠 수 있는 하위 칸만 꼬리로 인정한다.
TARGET_FIELDS = ("reason_codes",)


def split_target(target: str):
    """'2.5.1-Q03.reason_codes' → ('2.5.1-Q03', 'reason_codes')"""
    for f in TARGET_FIELDS:
        if target.endswith("." + f):
            return target[: -len(f) - 1], f
    return target, ""


def apply_history(payload: dict, history) -> dict:
    """
    원본 출력에 사람이 고친 값을 순서대로 적용한 결과.
    payload 자체는 건드리지 않는다. 화면이 보여줄 값은 이쪽이다.

    target 모양
        overall_result              종합 판정
        2.5.1-Q03                   그 문항의 result
        2.5.1-Q03.reason_codes      그 문항의 사유 코드 (쉼표로 구분)
    """
    eff = json.loads(json.dumps(payload, ensure_ascii=False))
    last_overall = last_item = -1
    for h in sorted(history, key=lambda x: x["seq"]):
        if h["action"] != "MODIFY" or not h["target"]:
            continue
        t, after, seq = h["target"], h["value_after"], int(h["seq"])
        if t == "overall_result":
            eff["overall_result"] = after
            last_overall = seq
            continue
        item_id, field = split_target(t)
        for item in eff.get("items") or []:
            if item.get("item_id") != item_id:
                continue
            if field == "reason_codes":
                item["reason_codes"] = [c.strip() for c in (after or "").split(",")
                                        if c.strip()]
            elif not field:
                item["result"] = after
                last_item = seq

    # 문항을 고친 뒤 종합을 안 고쳤으면 규칙으로 다시 계산한다.
    # 안 하면 '문항은 미충족인데 종합은 충족' 인 모순이 남는다.
    if last_item > last_overall and _orule is not None:
        eff["overall_result"] = _orule.compute(
            eff.get("items") or [], eff.get("critical_policy"))["overall_result"]
        eff["overall_recomputed"] = True
    return eff


def _blockers(r):
    """이 결과를 '운영 승인' 으로 볼 수 없게 만드는 사유들."""
    b = []
    if r["phase1_review_required"]:
        b.append({"code": "PHASE1_REVIEW_OPEN",
                  "message": "Phase 1 사람 검토가 끝나지 않았다",
                  "detail": r["phase1_review_reasons"]})
    if r["review_state"] in ("PENDING", "REVALIDATING", "REJECTED"):
        b.append({"code": "PHASE2_REVIEW_" + r["review_state"],
                  "message": {"PENDING": "Phase 2 사람 검토가 필요하다",
                              "REVALIDATING": "사람이 고쳐서 다시 판정해야 한다",
                              "REJECTED": "반려됐다"}[r["review_state"]],
                  "detail": (r["payload"].get("human_review") or {}).get("reasons", [])})
    if "P2E006" in r["error_codes"] or not r["checklist_approved"]:
        b.append({"code": "CHECKLIST_NOT_APPROVED",
                  "message": "미승인 체크리스트로 판정했다", "detail": []})
    if r["freshness_status"] != POLICY_OK or r["format_status"] != POLICY_OK:
        # 'OK' 가 아니면 전부 보류다. NULL·NOT_EVALUATED 를 '문제 없음' 으로 보면
        # 정책 검사를 아예 안 한 결과가 운영 승인으로 새어 나간다.
        b.append({"code": "POLICY_MISSING",
                  "message": "최신성·인정 증적 형식 정책이 확인되지 않았다",
                  "detail": [x or "NOT_EVALUATED"
                             for x in (r["freshness_status"], r["format_status"])]})
    con = r.get("consistency") or {}
    if con.get("errors"):
        b.append({"code": "INCONSISTENT_RESULT",
                  "message": "판정값과 사유 코드가 맞지 않는다",
                  "detail": [e["message"] for e in con["errors"]]})
    if con.get("warnings"):
        b.append({"code": "OVERALL_OVERRIDDEN",
                  "message": "사람이 정한 종합 판정이 문항 계산값과 다르다",
                  "detail": [w["message"] for w in con["warnings"]]})
    if r["provisional"]:
        b.append({"code": "PROVISIONAL",
                  "message": "아직 최종 결과가 아니다",
                  "detail": [r["provisional_reason"]] if r["provisional_reason"] else []})
    return b


def load(evidence_id: str, control_id: str, include_superseded=False):
    rows = _select("evidence_id = %s AND control_id = %s",
                   (evidence_id, control_id), include_superseded)
    if include_superseded:
        return rows or None
    return rows[0] if rows else None


def by_control(control_id: str):
    """그 통제항목에 걸린 증적 전부. 증적이 여러 개면 여러 줄이다."""
    return _select("control_id = %s", (control_id,))


def by_evidence(evidence_id: str, version: int | None = None,
                include_history: bool = False):
    """
    그 증적이 걸친 통제항목.

    기본은 **지금 버전**만. 옛 버전 결과까지 섞이면 재업로드한 증적에서
    v1 결과를 보고 "이미 판정했다" 고 착각해 v2 를 안 돌리게 된다.
    """
    if version is not None:
        return _select("evidence_id = %s AND evidence_version = %s",
                       (evidence_id, version))
    if include_history:
        return _select("evidence_id = %s", (evidence_id,),
                       include_superseded=True)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            ver = _evidence_version(cur, evidence_id)
    finally:
        conn.close()
    return _select("evidence_id = %s AND evidence_version = %s", (evidence_id, ver))


def history_of(evidence_id: str, control_id: str):
    """같은 증적·항목의 지난 판정까지 전부. 최신이 먼저."""
    rows = _select("evidence_id = %s AND control_id = %s",
                   (evidence_id, control_id), include_superseded=True)
    return sorted(rows, key=lambda r: r["result_id"], reverse=True)


def all_results():
    """현재 판정 전부."""
    return _select("1 = 1", ())


def review_queue(states=("PENDING", "REVALIDATING", "REJECTED")):
    """
    아직 안 끝난 검토. 검토 화면의 목록이다.

        PENDING       아직 아무도 안 봤다
        REVALIDATING  사람이 고쳤고 그 값을 한 번 더 승인해야 한다
        REJECTED      반려됐다. 증적을 다시 올려야 한다

    REVALIDATING 은 LLM 을 다시 돌리라는 뜻이 아니다. 같은 값으로 한 번 더
    approve() 하면 APPROVED 가 된다.
    """
    marks = ", ".join(["%s"] * len(states))
    rows = _select(f"review_state IN ({marks})", tuple(states))
    return [_one(r) for r in rows]


# ─────────────────────────────────────────────
# 커버리지 — 통제항목 101개를 분모로
# ─────────────────────────────────────────────
# 통제항목 하나에 증적이 여러 건 걸렸을 때의 대표 판정. 나쁜 쪽을 쓴다.
# 사전점검이라 하나라도 미충족이면 그 통제항목은 미충족으로 본다.
ROLLUP_RANK = {"미충족": 0, "증적 없음": 1, "확인 필요": 2,
               "충족(보완 권고)": 3, "충족": 4}

_CATALOG_CACHE: dict = {}


def load_control_catalog(path=None) -> dict:
    """
    통제항목 표 (control_id → control_name). 커버리지의 분모다.
    path 를 안 주면 phase1_검색/controls.json 을 거슬러 올라가며 찾는다.
    """
    if path is None:
        for base in [_HERE, *_HERE.parents]:
            cand = base / "phase1_검색" / "controls.json"
            if cand.is_file():
                path = cand
                break
        else:
            raise ResultStoreError(
                "CONTROL_CATALOG_NOT_FOUND",
                "controls.json 을 찾지 못했습니다. "
                "load_control_catalog('phase1_검색/controls.json') 로 경로를 주세요.")
    key = str(path)
    if key not in _CATALOG_CACHE:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        rows = raw if isinstance(raw, list) else raw["controls"]
        _CATALOG_CACHE[key] = {r["control_id"]: r["control_name"] for r in rows}
    return dict(_CATALOG_CACHE[key])


def _section(control_id: str) -> str:
    """'2.5.1' → '2.5'"""
    parts = str(control_id).split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else str(control_id)


def coverage(controls_path=None):
    """
    통제항목 101개 중 어디까지 증적이 들어왔는지.

    summary() 의 control_count 는 판정이 나온 통제항목 수(분자)뿐이다.
    여기는 분모를 controls.json 전체로 두고 미제출까지 센다.

        submitted       판정이 1건 이상 있는 통제항목
        excluded_only   제외 기록만 있고 판정이 없는 통제항목
        not_submitted   둘 다 없는 통제항목

    통제항목 하나에 증적이 여러 건이면 대표 판정은 나쁜 쪽을 쓴다 (ROLLUP_RANK).
    """
    catalog = load_control_catalog(controls_path)
    rows = all_results()
    skipped = excluded()

    per = {}
    for r in rows:
        per.setdefault(r["control_id"], []).append(r)
    skip_per = {}
    for x in skipped:
        if x["control_id"]:
            skip_per.setdefault(x["control_id"], []).append(x)

    controls, unknown_ids = [], []
    for cid in sorted(set(catalog) | set(per) | set(skip_per), key=_sort_key):
        mine = per.get(cid, [])
        skips = skip_per.get(cid, [])
        if cid not in catalog:
            unknown_ids.append(cid)
        if mine:
            state = "submitted"
            verdicts = [r["overall_result_effective"] for r in mine]
            rep = min(verdicts, key=lambda v: ROLLUP_RANK.get(v, 9))
        elif skips:
            state = "excluded_only"
            rep = None
        else:
            state = "not_submitted"
            rep = None
        open_states = [r["review_state"] for r in mine
                       if r["review_state"] in ("PENDING", "REVALIDATING", "REJECTED")]
        if not mine:
            review_label = None
        elif open_states:
            review_label = "검토 대기"
        elif any(r["review_state"] == "APPROVED" for r in mine):
            review_label = "검토 완료"
        else:
            review_label = "검토 불필요"
        controls.append({
            "control_id": cid,
            "control_name": catalog.get(cid) or (mine[0]["control_name"] if mine else None),
            "section": _section(cid),
            "state": state,
            "evidence_count": len(mine),
            "evidence_ids": [r["evidence_id"] for r in mine],
            "overall_result": rep,
            "overall_result_wbs": WBS_LABEL[rep] if rep else None,
            "verdicts": {v: sum(1 for r in mine
                                if r["overall_result_effective"] == v)
                         for v in OVERALL_VALUES} if mine else {},
            "review_open": len(open_states),
            "review_state": review_label,
            "modified_by_human": any(r["modified_by_human"] for r in mine),
            "excluded": skips,
            "in_catalog": cid in catalog,
        })

    by_state = {s: sum(1 for c in controls if c["state"] == s)
                for s in ("submitted", "excluded_only", "not_submitted")}
    denom = len(catalog)
    verdict_roll = {v: 0 for v in OVERALL_VALUES}
    for c in controls:
        if c["overall_result"]:
            verdict_roll[c["overall_result"]] += 1

    sections = {}
    for c in controls:
        s = sections.setdefault(c["section"], {
            "section": c["section"], "control_total": 0,
            "submitted": 0, "excluded_only": 0, "not_submitted": 0,
            "review_open": 0})
        s["control_total"] += 1
        s[c["state"]] += 1
        s["review_open"] += c["review_open"]
    for s in sections.values():
        s["coverage_rate"] = round(s["submitted"] / s["control_total"] * 100, 1) \
            if s["control_total"] else 0.0

    return {
        "control_total": denom,
        "submitted": by_state["submitted"],
        "excluded_only": by_state["excluded_only"],
        "not_submitted": by_state["not_submitted"],
        "coverage_rate": round(by_state["submitted"] / denom * 100, 1) if denom else 0.0,
        # 통제항목 단위로 접은 판정. summary()["verdicts"] 는 증적×항목 단위라 수가 다르다.
        "verdicts_by_control": verdict_roll,
        "verdicts_by_control_wbs": _to_wbs(verdict_roll),
        "review_open_controls": sum(1 for c in controls if c["review_open"]),
        "rollup": "worst",
        "by_section": [sections[k] for k in sorted(sections, key=_sort_key)],
        "controls": controls,
        "not_submitted_controls": [
            {"control_id": c["control_id"], "control_name": c["control_name"]}
            for c in controls if c["state"] == "not_submitted"],
        # controls.json 에 없는 control_id 로 판정이 들어온 경우. 보통 0 이어야 한다.
        "unknown_control_ids": unknown_ids,
    }


def _sort_key(cid: str):
    """'2.10.1' 이 '2.9.1' 보다 뒤로 가게 숫자로 끊어 비교한다."""
    out = []
    for p in str(cid).split("."):
        out.append((0, int(p)) if p.isdigit() else (1, 0, p))
    return out


# ─────────────────────────────────────────────
# 종합
# ─────────────────────────────────────────────
def summary(unknown_policy: str = "separate"):
    """
    종합 조회에 내려줄 값.

    단위가 둘이라 이름을 나눠뒀다.
        judged_result_total    증적 × 통제항목 (판정 건수)
        judged_evidence_total  판정이 나온 증적 수
        evidence_total         올린 증적 전체
    비율을 낼 때 이 셋을 섞지 않는다.

    unknown_policy — '확인 필요'·'증적 없음' 을 분모에 넣을지
        separate  (기본) 분모에 넣되 따로 센다
        exclude            분모에서 뺀다
    """
    if unknown_policy not in ("separate", "exclude"):
        raise ResultStoreError("UNKNOWN_POLICY_INVALID", f"모르는 값: {unknown_policy}")

    cur_where = "WHERE " + _is_current()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            def one(sql, params=()):
                cur.execute(_q(sql), params)
                return int(_rows(cur)[0]["n"])

            cur.execute(_q(f"SELECT overall_result, COUNT(*) AS n FROM phase2_result "
                           f"{cur_where} GROUP BY overall_result"))
            verdicts_raw = {r["overall_result"]: int(r["n"]) for r in _rows(cur)}

            cur.execute(_q(f"SELECT review_state, COUNT(*) AS n FROM phase2_result "
                           f"{cur_where} GROUP BY review_state"))
            states = {r["review_state"]: int(r["n"]) for r in _rows(cur)}

            result_total = sum(verdicts_raw.values())
            evidence_judged = one(f"SELECT COUNT(DISTINCT evidence_id) AS n "
                                  f"FROM phase2_result {cur_where}")
            control_n = one(f"SELECT COUNT(DISTINCT control_id) AS n "
                            f"FROM phase2_result {cur_where}")
            review_n = one(f"SELECT COUNT(*) AS n FROM phase2_result {cur_where} "
                           f"AND review_state = 'PENDING'")
            provisional_n = one(f"SELECT COUNT(*) AS n FROM phase2_result {cur_where} "
                                f"AND provisional = 1")
            p1_open = one(f"SELECT COUNT(*) AS n FROM phase2_result {cur_where} "
                          f"AND phase1_review_required = 1")
            p1_open_completed = one(
                "SELECT COUNT(*) AS n FROM phase2_result r "
                "JOIN evidence e ON e.evidence_id = r.evidence_id "
                "WHERE " + _is_current("r") + " AND r.phase1_review_required = 1 "
                "AND e.status = 'COMPLETED'")
            # _blockers() 와 같은 조건을 쓴다 (P2E006 이 있거나 미승인)
            warned_n = one(
                f"SELECT COUNT(*) AS n FROM phase2_result {cur_where} "
                f"AND (checklist_approved = 0 "
                f"     OR COALESCE(error_codes,'') LIKE '%%P2E006%%')")
            policy_missing_n = one(
                f"SELECT COUNT(*) AS n FROM phase2_result {cur_where} "
                f"AND (COALESCE(freshness_status,'') <> 'OK' "
                f"     OR COALESCE(format_status,'') <> 'OK')")
            approved_n = one(
                f"SELECT COUNT(*) AS n FROM phase2_result {cur_where} "
                f"AND phase1_review_required = 0 AND review_state IN "
                f"    ('NOT_REQUIRED','APPROVED') AND provisional = 0 "
                f"AND checklist_approved = 1 "
                f"AND COALESCE(error_codes,'') NOT LIKE '%%P2E006%%' "
                f"AND freshness_status = 'OK' AND format_status = 'OK'")

            cur.execute(_q(f"SELECT COALESCE(SUM(citation_count),0) AS c, "
                           f"COALESCE(SUM(citation_with_source),0) AS s "
                           f"FROM phase2_result {cur_where}"))
            cr = _rows(cur)[0]
            cit_total, cit_src = int(cr["c"]), int(cr["s"])

            cur.execute(_q("SELECT status, COUNT(*) AS n FROM evidence GROUP BY status"))
            statuses = {r["status"]: int(r["n"]) for r in _rows(cur)}

            cur.execute(_q(
                "SELECT e.status AS status, COUNT(DISTINCT e.evidence_id) AS n "
                "FROM evidence e JOIN phase2_result r ON r.evidence_id = e.evidence_id "
                "WHERE " + _is_current("r") + " GROUP BY e.status"))
            judged_statuses = {r["status"]: int(r["n"]) for r in _rows(cur)}

            cur.execute(_q(
                "SELECT reason_code, COUNT(*) AS n FROM phase2_excluded "
                "WHERE evidence_version = (SELECT e_cur.version FROM evidence e_cur "
                " WHERE e_cur.evidence_id = phase2_excluded.evidence_id) "
                "GROUP BY reason_code"))
            excluded_by = {r["reason_code"]: int(r["n"]) for r in _rows(cur)}
            excluded_ev = one(
                "SELECT COUNT(DISTINCT evidence_id) AS n FROM phase2_excluded "
                "WHERE control_id = '' AND evidence_version = "
                "(SELECT e_cur.version FROM evidence e_cur "
                " WHERE e_cur.evidence_id = phase2_excluded.evidence_id)")

            cur.execute(_q(f"SELECT payload, result_id FROM phase2_result {cur_where}"))
            payloads = _rows(cur)
            hist = {}
            for p in payloads:
                cur.execute(_q("SELECT seq, action, target, value_after FROM review_history "
                               "WHERE result_id = %s ORDER BY seq"), (p["result_id"],))
                hist[p["result_id"]] = _rows(cur)
    finally:
        conn.close()

    # 집계는 원본과 사람이 고친 뒤를 둘 다 낸다.
    # 화면이 쓰는 기본값은 effective 다 — 상세 화면과 숫자가 어긋나면 안 된다.
    item_raw = {v: 0 for v in ITEM_VALUES}
    item_eff = {v: 0 for v in ITEM_VALUES}
    verdict_eff = {}
    for p in payloads:
        pl = json.loads(p["payload"]) if isinstance(p["payload"], str) else p["payload"]
        for it in pl.get("items") or []:
            if it.get("result") in item_raw:
                item_raw[it["result"]] += 1
        e = apply_history(pl, hist.get(p["result_id"], []))
        for it in e.get("items") or []:
            if it.get("result") in item_eff:
                item_eff[it["result"]] += 1
        v = e.get("overall_result")
        verdict_eff[v] = verdict_eff.get(v, 0) + 1

    counts = {v: verdict_eff.get(v, 0) for v in OVERALL_VALUES}
    counts_raw = {v: verdicts_raw.get(v, 0) for v in OVERALL_VALUES}
    undecided = counts["확인 필요"] + counts["증적 없음"]
    denom = result_total if unknown_policy == "separate" \
        else max(result_total - undecided, 0)

    return {
        # ── 단위가 다른 세 숫자. 섞지 않는다 ──
        "evidence_total": sum(statuses.values()),
        "judged_evidence_total": evidence_judged,
        "judged_result_total": result_total,
        "control_count": control_n,

        # verdicts 는 사람이 고친 값이 반영된 쪽이다. 상세 화면과 같은 숫자다.
        "verdicts": counts,
        "verdicts_wbs": _to_wbs(counts),
        "verdicts_original": counts_raw,
        "verdicts_wbs_original": _to_wbs(counts_raw),
        "item_results_effective": item_eff,
        "item_results": item_raw,
        "undecided": undecided,
        "unknown_policy": unknown_policy,
        "denominator": denom,

        "review_states": {s: states.get(s, 0) for s in REVIEW_STATES},
        # review_required 는 PENDING 만 센다. 아직 안 끝난 전체는 unresolved 쪽이다.
        "review_required": review_n,
        "unresolved_review_total": sum(states.get(s, 0) for s in
                                       ("PENDING", "REVALIDATING", "REJECTED")),
        "provisional": provisional_n,

        "approval_blockers": {
            "PHASE1_REVIEW_OPEN": p1_open,
            "PHASE1_REVIEW_OPEN_BUT_COMPLETED": p1_open_completed,
            "CHECKLIST_NOT_APPROVED": warned_n,
            "POLICY_MISSING": policy_missing_n,
            "PROVISIONAL": provisional_n,
        },
        # 보류 사유가 하나도 없는 결과 수. COMPLETED 와 다르다 —
        # 체크리스트 승인·정책 주입이 끝나기 전에는 0 이 정상이다.
        "operationally_approved": approved_n,

        "citation_total": cit_total,
        "citation_with_source": cit_src,

        "excluded_total": sum(excluded_by.values()),
        "excluded_evidence_total": excluded_ev,
        "excluded_by_reason": excluded_by,

        "evidence_status": statuses,
        "judged_status": judged_statuses,
        "pending": sum(statuses.get(s, 0) for s in PENDING_STATUSES),
        "failed": statuses.get("FAILED", 0),
        "review_waiting": statuses.get("REVIEW_REQUIRED", 0),
        "completed": statuses.get("COMPLETED", 0),
    }


def _to_wbs(counts: dict) -> dict:
    out = {"적정": 0, "부분적정": 0, "부적정": 0, "판단불가": 0}
    for k, n in counts.items():
        out[WBS_LABEL[k]] += n
    return out


# ─────────────────────────────────────────────
# UI 응답 — API_response_spec.md 와 같은 모양으로 조립
# ─────────────────────────────────────────────
def summary_response(unknown_policy: str = "separate") -> dict:
    """GET /api/review/summary 가 그대로 내보낼 dict."""
    s = summary(unknown_policy)
    s["excluded"] = excluded()
    return s


def coverage_response(controls_path=None, include_controls: bool = True) -> dict:
    """GET /api/review/coverage 가 그대로 내보낼 dict."""
    c = coverage(controls_path)
    if not include_controls:
        c.pop("controls", None)
    return c


def control_response(control_id: str) -> dict:
    """GET /api/review/control/{control_id} 가 그대로 내보낼 dict."""
    rows = by_control(control_id)
    skipped = [x for x in excluded() if x["control_id"] == control_id]
    return {
        "control_id": control_id,
        "control_name": rows[0]["control_name"] if rows else None,
        "evidence_count": len(rows),
        "evidences": [_one(r) for r in rows],
        "excluded": skipped,
    }


def evidence_response(evidence_id: str) -> dict:
    """한 증적이 걸친 통제항목 전부."""
    rows = by_evidence(evidence_id)
    return {
        "evidence_id": evidence_id,
        "control_count": len(rows),
        "controls": [_one(r) for r in rows],
        "excluded": [x for x in excluded() if x["evidence_id"] == evidence_id],
    }


def _one(r):
    eff = r["effective"]
    return {
        "result_id": r["result_id"],
        "evidence_id": r["evidence_id"],
        "control_id": r["control_id"],
        "control_name": r["control_name"],
        "version": r["evidence_version"],
        "review_state": r["review_state"],
        "overall_result": eff["overall_result"],
        "overall_result_wbs": WBS_LABEL[eff["overall_result"]],
        "overall_result_original": r["overall_result"],
        "modified_by_human": r["modified_by_human"],
        "provisional": r["provisional"],
        "provisional_reason": r["provisional_reason"],
        "approval_blockers": r["approval_blockers"],
        # 화면이 배지로 쓰는 값들. API_response_spec.md 2절에 적힌 것과 같다.
        "phase1_review_required": r["phase1_review_required"],
        "phase1_review_reasons": r["phase1_review_reasons"],
        "error_codes": r["error_codes"],
        "freshness_status": r["freshness_status"],
        "format_status": r["format_status"],
        "checklist_approved": r["checklist_approved"],
        "items": eff.get("items") or [],
        "human_review": eff.get("human_review"),
        "history": r.get("history") or [],
        "judged_at": str(r["created_at"]),
    }
