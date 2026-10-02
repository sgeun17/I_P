"""
overall_result.py : 문항별 판정(MET/NOT_MET/UNKNOWN) → 통제항목 하나의 종합 결론.

규칙 (★ 미합의, 임시)
    미충족          critical 문항 중 NOT_MET 이 있다
    확인 필요        critical 문항 중 UNKNOWN 이 있다 (NOT_MET 은 없다)
    충족(보완 권고)   critical 은 전부 MET, 비critical 에 NOT_MET·UNKNOWN 이 있다
    충족            전부 MET

    나쁜 쪽이 이긴다. 미충족 > 확인 필요 > 충족(보완 권고) > 충족

왜 '확인 필요'가 따로 있나
    critical 문항이 UNKNOWN 일 때, 충족이라 하면 확인 안 된 걸 됐다고 하는 것이고
    미충족이라 하면 잘 하고 있는데 증적만 안 낸 조직을 결함 처리하게 된다. 둘 다 틀리다.

critical 을 무엇으로 보나 (★ 미합의)
    체크리스트의 item.critical 이 700문항 전부 UNDECIDED 라, 당분간 check_kind 로 대신한다.
      procedure      절차가 수립돼 있는가      → critical (절차가 없으면 결함)
      implementation 실제로 이행했는가         → critical (이행 안 했으면 결함)
      record         기록이 남아 있는가        → 비critical (보완 권고)
    체크리스트에 critical 이 채워지면 mode="explicit" 로 바꾸면 된다.
"""
from __future__ import annotations

RULE_VERSION = "overall-v0.1-checkkind"

# 나쁜 쪽이 이긴다. 숫자가 클수록 나쁨.
SEVERITY = {"충족": 0, "충족(보완 권고)": 1, "확인 필요": 2, "미충족": 3}

DEFAULT_CRITICAL_KINDS = ("procedure", "implementation")


def default_policy():
    return {"mode": "check_kind", "critical_kinds": list(DEFAULT_CRITICAL_KINDS)}


def is_critical(item, policy=None):
    """문항 하나가 critical 인지. 질문지 item 또는 판정 결과 item 둘 다 받는다."""
    policy = policy or default_policy()
    mode = policy.get("mode", "check_kind")
    if mode == "all":
        return True
    if mode == "explicit":
        # 체크리스트가 채워진 뒤. 값이 없으면 보수적으로 critical 로 본다.
        value = item.get("critical")
        return True if value is None else bool(value)
    kinds = policy.get("critical_kinds") or list(DEFAULT_CRITICAL_KINDS)
    return item.get("check_kind") in kinds


def compute(items, policy=None):
    """
    items  : [{item_id, result, check_kind?, critical?}, ...]
    돌려주는 값 : {overall_result, overall_reason, decisive_items, counts, critical_policy, rule_version}
    """
    policy = policy or default_policy()
    if not items:
        return {
            "overall_result": "증적 없음",
            "overall_reason": "이 통제항목을 판정할 증적이 없습니다.",
            "decisive_items": [],
            "counts": {"total": 0, "MET": 0, "NOT_MET": 0, "UNKNOWN": 0, "critical_total": 0},
            "critical_policy": policy,
            "rule_version": RULE_VERSION,
        }

    marked = [{**i, "critical": is_critical(i, policy)} for i in items]
    crit = [i for i in marked if i["critical"]]

    counts = {
        "total": len(marked),
        "MET": sum(1 for i in marked if i["result"] == "MET"),
        "NOT_MET": sum(1 for i in marked if i["result"] == "NOT_MET"),
        "UNKNOWN": sum(1 for i in marked if i["result"] == "UNKNOWN"),
        "critical_total": len(crit),
    }

    def ids(pool, value):
        return [i["item_id"] for i in pool if i["result"] == value]

    crit_not_met = ids(crit, "NOT_MET")
    crit_unknown = ids(crit, "UNKNOWN")
    rest = [i for i in marked if not i["critical"]]
    rest_bad = ids(rest, "NOT_MET") + ids(rest, "UNKNOWN")

    if crit_not_met:
        verdict = "미충족"
        decisive = crit_not_met
        reason = f"중요 문항 {len(crit_not_met)}건이 미충족입니다 ({', '.join(crit_not_met[:3])})."
    elif crit_unknown:
        verdict = "확인 필요"
        decisive = crit_unknown
        reason = f"중요 문항 {len(crit_unknown)}건을 증적으로 확인할 수 없습니다 ({', '.join(crit_unknown[:3])})."
    elif rest_bad:
        verdict = "충족(보완 권고)"
        decisive = rest_bad
        reason = f"중요 문항은 모두 충족했으나 {len(rest_bad)}건에 보완이 필요합니다."
    else:
        verdict = "충족"
        decisive = []
        reason = f"{counts['total']}개 문항을 모두 충족했습니다."

    return {
        "overall_result": verdict,
        "overall_reason": reason,
        "decisive_items": decisive,
        "counts": counts,
        "critical_policy": policy,
        "rule_version": RULE_VERSION,
    }


def worse(a, b):
    """두 종합 결론 중 나쁜 쪽. 2단계(여러 증적 합치기)에서 쓴다."""
    return a if SEVERITY.get(a, 0) >= SEVERITY.get(b, 0) else b


# ──────────────────────────────────────────────────────────────────────────
# 의미 검사 — JSON Schema 로 표현할 수 없는 규칙
#
# 스키마는 "모양"만 본다. 그래서 NOT_MET 문항이 있는데 overall_result 가
# '충족' 인 결과도 스키마는 통과시킨다. 그런 모순은 여기서 걸러야 한다.
# ──────────────────────────────────────────────────────────────────────────
def validate(output, policy=None):
    """
    조립이 끝난 Phase 2 출력 한 건이 규칙과 맞는지 본다.
    돌려주는 값 : 오류 코드 목록. 빈 목록이면 이상 없음.

        P2E504  종합 판정이 규칙과 어긋난다 (→ 검토 사유 P2R109)
        P2E503  critical 정책이 결과에 안 적혀 있다

    phase2_errors.decide_review(errors=...) 에 그대로 넘기면 된다.
    """
    errs = []
    items = output.get("items") or []

    if not output.get("critical_policy"):
        errs.append("P2E503")

    expected = compute(items, policy)
    if output.get("overall_result") != expected["overall_result"]:
        errs.append("P2E504")

    # 집계값이 있으면 그것도 대조한다
    counts = output.get("counts")
    if counts and counts != expected["counts"]:
        if "P2E504" not in errs:
            errs.append("P2E504")

    return errs


def expected_for(output, policy=None):
    """검사가 틀렸다고 할 때 '그럼 뭐였어야 하나' 를 보여주기 위한 값."""
    return compute(output.get("items") or [], policy)
