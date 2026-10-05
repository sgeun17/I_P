"""확정된 Phase1 결과를 기존 체크리스트의 내부 검수 계획에 연결한다.

문항 판정이나 운영 Phase2 API 규격을 생성하지 않는다. 기준 사용 승인 여부와
별개로 내부 검수 계획 생성에는 ``allow_draft=True``를 명시한다.
Phase1 판단팀의 Pydantic 모델과 사람 검토 게이트를 그대로 재사용한다.
사람이 승인·수정한 결과는 기존 검증 오류가 남아 있어도 기존 검토 정책에 따라
문항 조회를 허용한다. 저장된 검증 결과를 보존하며 수정 인용을 새로 검증하지 않는다.
실행 환경에는 Pydantic 2가 필요하다(검색팀의 기존 .venv 사용 가능).
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import sys

from pydantic import ValidationError

HERE = Path(__file__).resolve().parent
PHASE1_SOURCE = HERE.parent / "phase1_판단" / "src"
# 판단팀 소스가 평면 import를 사용하므로 기존 모듈 검색 경로를 연결한다.
if str(PHASE1_SOURCE) not in sys.path:
    sys.path.insert(0, str(PHASE1_SOURCE))

from enums import MatchStatus, ProcessingStatus
from models import Phase1MappingResult
from review import is_confirmed_for_phase2

from checklist_store import ChecklistError, ChecklistStore
from criteria_state import valid_state


class ReviewPlanError(ValueError):
    """검수 계획을 만들 수 없는 이유와 후속 확인에 필요한 정보를 담는다."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        self.code = code
        self.details = deepcopy(details or {})
        super().__init__(message)


def _require(condition, code, message, **details):
    if not condition:
        raise ReviewPlanError(code, message, details=details)


def _store_call(operation):
    try:
        return operation()
    except ChecklistError as error:
        raise ReviewPlanError(error.code, str(error),
                              details={"store_error_code": error.code}) from error
    except (OSError, sqlite3.Error, json.JSONDecodeError, UnicodeError) as error:
        raise ReviewPlanError("CHECKLIST_STORE_ERROR", "검수용 체크리스트를 읽을 수 없습니다.",
                              details={"error_type": type(error).__name__}) from error


def _validated_result(result):
    _require(isinstance(result, (dict, Phase1MappingResult)), "INVALID_PHASE1_RESULT",
             "Phase1MappingResult 또는 해당 규격의 객체가 필요합니다.")
    # 모델 객체도 다시 검증한다. model_copy/model_construct로 변경된 결과를
    # 유효한 최종 결과로 간주하지 않으며 전달받은 원본은 수정하지 않는다.
    payload = (result.model_dump(mode="python")
               if isinstance(result, Phase1MappingResult) else deepcopy(result))
    try:
        return Phase1MappingResult.model_validate(payload)
    except ValidationError as error:
        raise ReviewPlanError("INVALID_PHASE1_RESULT", "Phase1 최종 결과 규격이 잘못됐습니다.",
                              details={"errors": [
                                  {"field": ".".join(str(part) for part in item["loc"]),
                                   "type": item["type"]}
                                  for item in error.errors()
                              ]}) from error


def prepare_review_plan(result, store: ChecklistStore, checklist_version: str,
                        *, allow_draft=False):
    """확정된 Phase1 매핑 전체에 대응하는 검수용 문항을 조회한다.

    1:N 매핑 중 하나라도 초안 범위에 없으면 ``CHECKLIST_SCOPE_MISSING``으로
    전체 계획을 보류한다. NO_MATCH는 문항이 없는 계획이며 적정성 판정이 아니다.
    반환 객체는 내부 검수 자료다. 운영 입력/출력 스키마의 확정본이 아니다.
    검토 불필요 결과는 저장된 기계 검증 통과도 필요하다. 사람이 APPROVED 또는
    MODIFIED로 확정한 결과는 기존 최종 결정 권한을 따른다. 저장된 validation이
    수정 매핑·인용의 최신 검증이라고 간주하지 않으며 범위를 메타데이터에 남긴다.
    """
    _require(allow_draft is True, "DRAFT_NOT_APPROVED",
             "내부 검수 계획 생성에는 allow_draft=True를 명시하세요. 기준 사용 승인과 별개입니다.")
    _require(isinstance(checklist_version, str) and bool(checklist_version.strip()),
             "CHECKLIST_VERSION_REQUIRED", "체크리스트 버전을 지정해야 합니다.")
    phase1 = _validated_result(result)
    _require(phase1.processing_status == ProcessingStatus.COMPLETED,
             "PHASE1_NOT_COMPLETED", "처리가 완료된 Phase1 결과만 연결할 수 있습니다.",
             processing_status=phase1.processing_status.value)
    _require(is_confirmed_for_phase2(phase1), "PHASE1_REVIEW_NOT_CONFIRMED",
             "사람 검토가 대기 중이거나 거절된 결과는 연결할 수 없습니다.",
             review_status=phase1.human_review.status.value)
    validation = phase1.validation
    flags = ("passed", "schema_valid", "control_ids_valid", "citations_valid", "rules_valid")
    failed_flags = [name for name in flags if getattr(validation, name) is not True]
    stored_validation_passed = not failed_flags and not validation.issues
    if not phase1.human_review.required:
        _require(stored_validation_passed, "PHASE1_VALIDATION_FAILED",
                 "검토 불필요 결과는 저장된 Phase1 검증을 모두 통과해야 합니다.",
                 failed_flags=failed_flags,
                 issue_codes=[issue.code.value for issue in validation.issues])
    # APPROVED/MODIFIED의 사람 결정은 기존 Phase1 정책상 최종 권한을 갖는다.
    # 오류 목록·플래그를 지우거나 수정 인용을 검증했다고 표시하지 않는다.
    validation_overridden = phase1.human_review.required and not stored_validation_passed

    kb_sha = phase1.versions.kb_sha256
    _require(isinstance(kb_sha, str) and bool(kb_sha.strip()), "KB_HASH_MISSING",
             "Phase1 결과에 검색 KB 해시가 없습니다.")
    _require(re.fullmatch(r"[0-9a-f]{64}", kb_sha) is not None, "KB_HASH_INVALID",
             "Phase1 검색 KB 해시는 SHA-256 소문자 64자리여야 합니다.")
    versions = _store_call(store.list_versions)
    selected = next((item for item in versions if item["version"] == checklist_version), None)
    _require(selected is not None, "VERSION_NOT_FOUND", "체크리스트 버전이 없습니다.",
             checklist_version=checklist_version)
    _require(valid_state(selected), "CHECKLIST_STATE_UNSUPPORTED",
             "체크리스트 승인 값과 상태가 일치해야 합니다.")
    _require(selected.get("kb_sha256") == kb_sha, "KB_SOURCE_MISMATCH",
             "Phase1 검색 KB와 선택한 체크리스트의 원본 KB가 다릅니다.",
             phase1_kb_sha256=kb_sha, checklist_kb_sha256=selected.get("kb_sha256"))

    controls, missing_ids = [], []
    mapped_ids = [control.control_id for control in phase1.mapped_controls]
    for mapping in phase1.mapped_controls:
        try:
            response = _store_call(lambda: store.get_control(
                checklist_version, mapping.control_id, allow_draft=True))
        except ReviewPlanError as error:
            if error.code != "CONTROL_NOT_FOUND":
                raise
            missing_ids.append(mapping.control_id)
            continue
        _require(response.get("version") == checklist_version
                 and response.get("source_sha256") == selected["source_sha256"]
                 and response.get("source", {}).get("sha256") == kb_sha
                 and response.get("status") == selected["status"]
                 and response.get("approved") is selected['approved'],
                 "CHECKLIST_CHANGED_DURING_READ", "체크리스트 조회 결과의 버전·출처가 다릅니다.",
                 control_id=mapping.control_id)
        checklist = response["control"]
        _require(checklist.get("control_id") == mapping.control_id,
                 "CHECKLIST_CONTROL_MISMATCH", "요청한 통제항목과 조회 결과가 다릅니다.",
                 control_id=mapping.control_id)
        _require(checklist.get("control_name") == mapping.control_name,
                 "CONTROL_NAME_MISMATCH", "Phase1 매핑과 체크리스트의 통제항목 명칭이 다릅니다.",
                 control_id=mapping.control_id,
                 phase1_control_name=mapping.control_name,
                 checklist_control_name=checklist.get("control_name"))
        controls.append({"phase1_mapping": mapping.model_dump(mode="json"),
                         "checklist": deepcopy(checklist),
                         "source": deepcopy(response["source"]),
                         "source_documents": deepcopy(response["source_documents"])})

    _require(not missing_ids, "CHECKLIST_SCOPE_MISSING",
             "Phase1 매핑 전체를 포함하는 체크리스트가 필요합니다. 일부만 연결하지 않습니다.",
             missing_control_ids=missing_ids, mapped_control_ids=mapped_ids,
             checklist_version=checklist_version)
    # 별도 읽기 연결을 사용하는 저장소이므로 조회 종료 시 버전 메타데이터도 확인한다.
    latest = next((item for item in _store_call(store.list_versions)
                   if item["version"] == checklist_version), None)
    _require(latest == selected, "CHECKLIST_CHANGED_DURING_READ",
             "조회 중 체크리스트 버전 정보가 변경됐습니다.")
    return {"review_only": True, "approved": False,
            "status": "DRAFT_FOR_TEAM_REVIEW", "checklist_version": checklist_version,
            "checklist_source_sha256": selected["source_sha256"],
            "source_versions": phase1.versions.model_dump(mode="json"),
            "evidence_id": phase1.evidence_id, "version": phase1.version,
            "match_status": phase1.match_status.value,
            "phase1_validation_overridden_by_review": validation_overridden,
            "phase1_validation_scope": "STORED_RESULT_ONLY",
            "source_phase1_result": phase1.model_dump(mode="json"),
            "controls": controls,
            "question_count": sum(len(item["checklist"]["items"]) for item in controls),
            "empty_plan_reason": ("PHASE1_NO_MATCH"
                                  if phase1.match_status == MatchStatus.NO_MATCH else None)}
