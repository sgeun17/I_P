"""Phase 2 draft reason code 기반 보완/추가확인 가이드 생성기.

현재 reason_codes_draft.json은 팀 미승인 초안이므로 결과에도 draft임을 명시한다.
NOT_MET은 결함 범위에 맞는 보완 방향을, UNKNOWN은 판정을 위해 추가로 확인할 자료를 안내한다.
일반적인 보안 강화 문구를 무조건 붙이지 않고 reason code의 required_context를 그대로 활용한다.
"""
from __future__ import annotations

from typing import Any, Mapping


GUIDE_VERSION = "phase2_remediation_v0.1-draft"

_NOT_MET_ACTIONS = {
    "P2_NM_RULE_NOT_DEFINED": (
        "확인된 미수립 범위에 대해 해당 체크리스트 문항이 요구하는 절차·기준을 문서화하고, "
        "적용 대상과 책임 주체, 필요한 절차 또는 기준을 명확히 한 뒤 승인·시행 근거를 남긴다."
    ),
    "P2_NM_REQUIRED_ACTION_NOT_DONE": (
        "확인된 미수행 사건에 필요한 조치를 실제로 이행하고, 누가 언제 어떤 대상으로 무엇을 처리했는지 "
        "확인할 수 있는 수행 이력을 남긴다. 반복 가능한 항목이면 후속 누락을 확인할 절차도 함께 점검한다."
    ),
    "P2_NM_RULE_VIOLATED": (
        "확인된 대상·사건의 실제 상태를 당시 적용 기준에 맞게 바로잡고, 변경 전후 상태와 조치 시점·주체를 "
        "확인할 수 있는 기록을 남긴다. 다른 대상이나 이후 개정 기준으로 이번 불일치를 덮지 않는다."
    ),
}

_UNKNOWN_ACTIONS = {
    "P2_U_EVIDENCE_INSUFFICIENT": "판정에 필요한 자료와 현재 제공된 자료의 차이를 확인하고 부족한 직접 근거를 추가한다.",
    "P2_U_SCOPE_OR_RULE_UNCLEAR": "어떤 대상·범위와 어떤 기준이 이번 검토에 적용되는지 식별할 자료를 추가한다.",
    "P2_U_SUBJECT_OR_EVENT_UNLINKED": "각 자료가 동일한 대상·사건을 설명한다는 연결 식별자를 확인한다.",
    "P2_U_TIME_OR_VERSION_UNCLEAR": "수행일·검토기간·효력일 또는 당시 적용 버전을 확인할 자료를 추가한다.",
    "P2_U_EVIDENCE_UNREADABLE": "판독 가능한 원본 또는 동일 내용을 확인할 수 있는 신뢰 가능한 대체 자료를 확보한다.",
    "P2_U_EVIDENCE_CONFLICT": "동일 범위에서 상충하는 자료의 효력·적용 시점·원본성을 확인해 어느 근거가 유효한지 해소한다.",
    "P2_U_NO_TRIGGER_EVENT": "검토 기간에 해당 이행을 발생시키는 사건이 실제로 없었는지 확인하고 적용 제외 정책 확정 전에는 임의 판정하지 않는다.",
    "P2_U_EXCEPTION_UNRESOLVED": "예외 사유, 승인 여부와 책임추적성 등 예외 적용에 필요한 정보를 확인한다.",
    "P2_U_NOT_YET_DUE": "검토 기준일과 적용 기한, 현재 진행 상태를 함께 확인하고 기한 도래 후 실제 이행 결과를 확인한다.",
    "P2_U_OBSERVATION_INCOMPLETE": "문항이 요구하는 전체 관찰기간과 현재 확인된 구간의 차이를 채울 추가 기록을 확보한다.",
}


class GuideError(ValueError):
    pass


def build_guide(
    reason_definition: Mapping[str, Any],
    checklist_item: Mapping[str, Any],
) -> dict[str, Any]:
    code = reason_definition.get("code")
    result = reason_definition.get("result")
    if not isinstance(code, str) or not code:
        raise GuideError("reason code definition에 code가 없습니다.")
    if result == "NOT_MET":
        action = _NOT_MET_ACTIONS.get(code)
        guide_type = "REMEDIATION"
    elif result == "UNKNOWN":
        action = _UNKNOWN_ACTIONS.get(code)
        guide_type = "ADDITIONAL_EVIDENCE"
    else:
        raise GuideError(f"현재 가이드는 NOT_MET/UNKNOWN 코드만 지원합니다: {code}")
    if action is None:
        raise GuideError(f"가이드 템플릿이 없는 reason code입니다: {code}")

    return {
        "guide_version": GUIDE_VERSION,
        "draft": True,
        "item_id": checklist_item.get("item_id"),
        "control_id": checklist_item.get("control_id"),
        "question": checklist_item.get("question"),
        "reason_code": code,
        "result": result,
        "guide_type": guide_type,
        "finding_label": reason_definition.get("label"),
        "recommended_action": action,
        "evidence_or_context_to_prepare": list(reason_definition.get("required_context") or []),
        "guardrail": (
            "확인된 대상·사건·기간의 범위에 한정한다. 근거에 없는 조직 전체의 미흡이나 완료를 단정하지 않는다."
        ),
    }
