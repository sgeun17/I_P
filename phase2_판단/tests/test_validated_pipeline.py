from copy import deepcopy
import json
from pathlib import Path

import pytest

from validated_pipeline import (_absence_only_not_met, _access_appropriateness_binding_unproven,
                                _direct_recurrence_evidence, _ocr_qualification_binding_unproven,
                                _log_retention_observation_unproven, _log_retention_scope,
                                _marketing_notice_basis, _restore_test_scope, _acknowledge_observations,
                                _normalize_recurrence_reason, _partial_storage_reason,
                                run_control_judgment, run_validated_item)
from validation.contracts import validate_schema, interface_module
from validation.logic import validate_input, validate_item, validate_output
from validation.review import decide_review
from self_check import run_self_check
from phase1_runtime import RetryPolicy, LLMCallError, ErrorCode, LLMRequestRejectedError

ROOT = Path(__file__).resolve().parents[2]
FULL = json.loads((ROOT / "phase2_기준/chapter2_full_checklist_draft.json").read_text(encoding="utf-8"))
REASONS = json.loads((ROOT / "phase2_기준/chapter2_reason_codes_draft.json").read_text(encoding="utf-8"))
CONTROLS = {c["control_id"]: c["control_name"] for c in FULL["controls"]}


@pytest.fixture
def setup_case():
    payload = json.loads((ROOT / "phase2_인터페이스/phase2_input_sample.json").read_text(encoding="utf-8"))
    catalog = deepcopy(FULL)
    control = next(c for c in catalog["controls"] if c["control_id"] == "2.5.1")
    control["items"] = control["items"][:1]
    payload["targets"] = payload["targets"][:1]
    payload["checklist_version"] = FULL["draft_version"]
    payload["source_versions"]["kb_sha256"] = FULL["source"]["sha256"]
    item = control["items"][0]
    context = {"chunks": [{**c, "role": "evidence"} for c in payload["chunks"]]}
    output = {"item_id": item["item_id"], "result": "MET", "reason": "인용된 계정 발급 승인 절차가 문항의 기준을 뒷받침한다.",
              "reason_codes": [], "citations": [deepcopy(payload["targets"][0]["mapping_citations"][0])]}
    return payload, catalog, item, context, output


def response(output):
    return lambda *args, **kwargs: json.dumps(output, ensure_ascii=False)


def supported(*args, **kwargs):
    return json.dumps({"verdict": "SUPPORTED", "reason": "인용문이 해당 절차를 직접 설명한다.", "confidence": 0.9, "unsupported_conditions": []})


def test_combined_profile_reaches_grounding_not_runtime_or_selfcheck(setup_case):
    payload, catalog, item, context, output = setup_case
    seen = []
    def grounding(system, user, model, schema, **kwargs):
        assert '"profile_version": "compat-profile-v1"' in user
        assert "organization_context" not in kwargs
        seen.append("grounding")
        return json.dumps(output, ensure_ascii=False)
    def review(system, user, model, schema, **kwargs):
        assert "organization_context" not in kwargs
        assert json.loads(user)["available_evidence_context"]["chunks"] == context["chunks"]
        seen.append("review")
        return supported()
    result, audit = run_validated_item(item, context, evidence_id=payload["evidence_id"],
        version=payload["version"], model="mock", reason_codes=REASONS["codes"],
        organization_context={"profile_version": "compat-profile-v1"},
        llm_call=grounding, self_check_call=review)
    assert seen == ["grounding", "review"]
    assert result["result"] == "MET"
    assert audit["run"]["organization_profile_version"] == "compat-profile-v1"
    assert len(audit["run"]["organization_context_sha256"]) == 64


def test_missing_record_alone_cannot_prove_not_met():
    item = {"evidence_rule": {
        "not_met": "필요한 검토를 생략한 사실이 직접 확인됨. 단순 미제출은 제외한다.",
        "unknown": "필요한 근거가 미제출·누락되면 UNKNOWN이다.",
    }}
    absent = {
        "result": "NOT_MET",
        "citations": [{"quote": "직무·역할 기준의 일치 여부는 기록되어 있지 않다."}],
    }
    direct = {
        "result": "NOT_MET",
        "citations": [{"quote": "담당자는 필수 적절성 검토를 수행하지 않았다."}],
    }
    assert _absence_only_not_met(item, absent) is True
    assert _absence_only_not_met(item, direct) is False


def test_ocr_officer_qualification_requires_same_line_binding():
    item = {"question": "지정된 개인정보 보호책임자가 직위·권한·자격 요건을 갖추었는가?"}
    output = {"result": "MET", "citations": [{"chunk_id": "E1_v1_c0001"}]}
    ambiguous = {"chunks": [{
        "chunk_id": "E1_v1_c0001", "source": "ocr",
        "text": "개인정보 보호책임자(CPO) 지정\n다른 임원은 정보보호 경력 15년으로 자격요건을 충족한다.",
    }]}
    direct = {"chunks": [{
        "chunk_id": "E1_v1_c0001", "source": "ocr",
        "text": "개인정보 보호책임자(CPO) 가람은 개인정보보호 경력 12년으로 자격요건을 충족한다.",
    }]}
    assert _ocr_qualification_binding_unproven(item, output, ambiguous) is True
    assert _ocr_qualification_binding_unproven(item, output, direct) is False


def test_non_ocr_officer_evidence_is_not_changed_by_ocr_guard():
    item = {"question": "지정된 개인정보 보호책임자가 자격 요건을 갖추었는가?"}
    output = {"result": "MET", "citations": [{"chunk_id": "E1_v1_c0001"}]}
    context = {"chunks": [{
        "chunk_id": "E1_v1_c0001", "source": "parser",
        "text": "개인정보 보호책임자 지정과 자격 정보가 별도 구조 필드로 보존되었다.",
    }]}
    assert _ocr_qualification_binding_unproven(item, output, context) is False


def test_access_appropriateness_requires_same_event_role_mapping():
    item = {"question": "권한 부여 건에서 승인 절차 등을 통한 적절성 검토가 수행되었는가?"}
    event = (
        "신청자: 김철수 | 대상 시스템: PROC-019 | 요청 권한: 조회 | "
        "직무코드: IB-03 | 1차 승인: 완료 | 2차 승인: 완료"
    )
    unsupported = {"result": "MET", "citations": [
        {"quote": "승인: 부서장 1차 승인 → 시스템 관리자 2차 승인"},
        {"quote": event},
    ]}
    supported = {"result": "MET", "citations": [
        {"quote": "직무코드: WM-01 | PROC-001: 조회"},
        {"quote": event.replace("IB-03", "WM-01").replace("PROC-019", "PROC-001")},
    ]}
    assert _access_appropriateness_binding_unproven(item, unsupported) is True
    assert _access_appropriateness_binding_unproven(item, supported) is False


def test_explicit_recurrence_with_previous_and_current_year_is_direct_evidence():
    item = {"question": "이전 결과와 비교하여 재발 여부를 확인하였는가?"}
    output = {"result": "MET", "citations": [{
        "quote": "2025년 점검의 패스워드 정책 미흡이 재발하여 체크리스트를 의무화하고 상시 감시한다.",
    }]}
    context = {"chunks": [{"text": "점검기간 2026년"}, {"text": output["citations"][0]["quote"]}]}
    assert _direct_recurrence_evidence(item, output, context) is True
    reason = _normalize_recurrence_reason(item, output, context)
    assert "동일 대상에서 재발" in reason
    assert "2026년 점검" not in reason


def test_partial_storage_reason_preserves_confirmed_paper_lock():
    item = {"question": "서약서 접근을 제한하여 보관하는가?"}
    output = {"result": "UNKNOWN", "citations": [{
        "quote": "서약서는 문서고 잠금 캐비넷에 보관하며 전자 사본은 인사시스템에 등록한다.",
    }]}
    reason = _partial_storage_reason(item, output)
    assert reason is not None
    assert "종이 원본의 잠금 보관은 확인" in reason
    assert "전자 접근권한·조회 제한은 확인할 수 없어" in reason
    output["result"] = "MET"
    assert _partial_storage_reason(item, output) == reason

    output["citations"][0]["quote"] = (
        "서약서 원본은 잠금 캐비넷에 보관한다. 전자 사본은 인사시스템에 저장하며 "
        "인사담당자 그룹만 조회할 수 있도록 접근권한을 제한하였다."
    )
    assert _partial_storage_reason(item, output) is None


def test_log_retention_met_requires_observed_duration_citation():
    item = {"question": "대상 로그를 적용 보존기간 동안 보관하고 있는가?"}
    policy_only = {"result": "MET", "citations": [{"quote": "보존기간: 1년 | 일 1회 백업"}]}
    observed = {"result": "MET", "citations": [{"quote": "보관 시작: 2025-09 | 현재 보관기간: 1년 1개월"}]}
    assert _log_retention_observation_unproven(item, policy_only) is True
    assert _log_retention_observation_unproven(item, observed) is False


def test_marketing_notice_met_requires_event_or_direct_no_event_exception():
    item = {"question": "홍보·판매 권유 업무 위탁 내용과 수탁자를 정보주체에게 알렸는가?"}
    policy_only = {"result": "MET", "citations": [{
        "quote": "홍보·판매 권유 업무 위탁 시 문자·이메일로 별도 안내합니다.",
    }]}
    no_event = {"result": "MET", "citations": [{
        "quote": "검토 기간에 홍보·판매 권유 업무 위탁은 없었다.",
    }, {
        "quote": "해당 업무를 위탁하면 업무 내용과 수탁자를 문자·이메일로 통지한다.",
    }]}
    actual = {"result": "MET", "citations": [{
        "quote": "통지일: 2026-09-03 | 통지 내용: 위탁 업무 및 수탁자 | 발송 완료",
    }]}
    assert _marketing_notice_basis(item, policy_only) is None
    assert _marketing_notice_basis(item, no_event) == "no_event_exception"
    assert _marketing_notice_basis(item, actual) == "actual_notice"


def test_restore_test_records_do_not_prove_required_cadence():
    item = {"question": "정해진 주기에 따라 복구 테스트를 실시했는가?"}
    context = {"chunks": [{
        "chunk_id": "c1", "page_start": 2,
        "text": "일자: 2026-03-21 | 대상: PROC-002 | 방법: 전체 복구 후 무결성 검증 | 결과: 정상",
    }]}
    records, cadence = _restore_test_scope(item, context)
    assert cadence is False
    assert records == [{
        "chunk_id": "c1", "page": 2,
        "quote": "일자: 2026-03-21 | 대상: PROC-002 | 방법: 전체 복구 후 무결성 검증 | 결과: 정상",
    }]


def test_log_retention_scope_does_not_infer_missing_evaluation_categories():
    item = {"question": "대상 로그를 적용 보존기간 동안 보관하고 있는가?"}
    context = {"chunks": [{
        "chunk_id": "c1", "page_start": None,
        "text": (
            "대상: 서버(OS) | 보관기간: 1년\n"
            "대상: 응용프로그램(PROC-001~006) | 보관기간: 2년\n"
            "대상: 네트워크 장비 | 보관기간: 1년\n"
            "시스템: PROC-001/002 | 보관 시작: 2023-07 | 현재 보관기간: 3년 2개월"
        ),
    }]}
    observed, missing = _log_retention_scope(item, context)
    assert len(observed) == 1
    assert missing == []


@pytest.mark.parametrize("system", ["ALPHA-987", "판매지원서비스", "PROC-001"])
def test_observations_do_not_assign_company_specific_scope(system):
    item = {"question": "대상 로그를 적용 보존기간 동안 보관하고 있는가?"}
    context = {"chunks": [{"chunk_id": "c1", "page_start": None,
        "text": f"대상: 다른 서버 | 보관기간: 1년\n시스템: {system} | 보관 시작: 2023-07 | 현재 보관기간: 3년"}]}
    met = {"result": "MET", "reason": "지정한 시스템의 보관 확인", "citations": []}
    original = deepcopy(met)
    assert _acknowledge_observations(item, met, context) is None
    assert met == original
    output = {"result": "UNKNOWN", "reason": "대상 연결 불명", "citations": []}
    report = _acknowledge_observations(item, output, context)
    assert report["original_reason"] == "대상 연결 불명"
    assert system in output["citations"][0]["quote"]
    assert "전체 대상" not in output["reason"]
    assert "서버·보안시스템" not in output["reason"]


def test_restore_observation_does_not_invent_missing_dates_or_erase_citations():
    item = {"question": "정해진 주기에 따라 복구 테스트를 실시했는가?"}
    original = {"chunk_id": "c0", "page": 1, "quote": "기준일: 2026-10-01"}
    context = {"chunks": [{"chunk_id": "c1", "page_start": 2,
        "text": "일자: 2026-09-01 | 대상: 새시스템 | 방법: 복원 | 결과: 정상"}]}
    output = {"result": "UNKNOWN", "reason": "주기 연결 검토 필요", "citations": [original]}
    _acknowledge_observations(item, output, context)
    assert original in output["citations"]
    assert "기준일이 확인되지" not in output["reason"]
    assert "기간이 확인되지" not in output["reason"]


def execute(case, **kwargs):
    payload, catalog, item, context, output = case
    return run_control_judgment(payload, "2.5.1", catalog=catalog, reason_catalog=REASONS, controls=CONTROLS,
                model="fake:test", allow_draft=True, llm_call=response(output), self_check_call=supported,
                sleeper=lambda _: None, **kwargs)


def codes(issues):
    return {i["code"] for i in issues}


def test_full_path_and_official_contract(setup_case):
    run = execute(setup_case)
    assert run["processing_status"] == "COMPLETED"
    assert not validate_schema(run["output"], "output")
    assert run["output"]["items"][0]["result"] == "MET"
    assert run["audit"]["hashes"]["rules_sha256"]
    assert run["audit"]["items"][0]["self_check"]["verdict"] == "SUPPORTED"
    assert "P2E006" in run["output"]["human_review"]["error_codes"]


@pytest.mark.parametrize("key,value,code", [
    ("result", "PASS", "E204"), ("reason", 42, "E202"),
    ("item_id", "2.5.1-Q999", "P2E301"), ("citations", [], "P2E501"),
    ("reason_codes", ["P2_NM_RULE_NOT_DEFINED"], "P2E502"),
    ("reason_codes", ["P2_U_MADE_UP"], "P2E502"),
])
def test_runtime_rejects_invalid_item(setup_case, key, value, code):
    _, _, item, context, output = setup_case
    output[key] = value
    assert code in codes(validate_item(output, item, context, REASONS["codes"]))


@pytest.mark.parametrize("field", ["item_id", "reason", "result", "citations"])
def test_missing_fields(setup_case, field):
    output = setup_case[-1]
    del output[field]
    assert "E203" in codes(validate_schema(output, "item"))


def test_forged_quote_p2e505_and_unknown_not_no_match(setup_case):
    setup_case[-1]["citations"][0]["quote"] = "LLM이 만들어 낸 근거 문장"
    run = execute(setup_case)
    assert run["processing_status"] == "REVIEW_REQUIRED"
    assert run["output"]["items"][0]["result"] == "UNKNOWN"
    assert {"E403", "P2E505"} <= set(run["output"]["human_review"]["error_codes"])
    assert "P2R104" in run["output"]["human_review"]["reasons"]


@pytest.mark.parametrize("change,expected", [
    (lambda p: p.update(version=2), "P2E009"),
    (lambda p: p["chunks"].append(deepcopy(p["chunks"][0])), "P2E007"),
    (lambda p: p["targets"][0].update(control_id="9.9.9"), "P2E005"),
    (lambda p: p["targets"][0].update(control_name="틀린 이름"), "P2E005"),
    (lambda p: p["targets"][0].update(judge=False), "P2E005"),
    (lambda p: p["targets"][0].update(mapping_citations=[]), "P2E008"),
    (lambda p: p["source_versions"].update(kb_sha256="wrong"), "P2E005"),
])
def test_input_preflight(setup_case, change, expected):
    payload, catalog, *_ = setup_case
    change(payload)
    assert expected in codes(validate_input(payload, catalog, CONTROLS))
    assert execute(setup_case)["output"] is None


def test_output_duplicates_missing_and_unknown_items(setup_case):
    run = execute(setup_case)
    payload, catalog, item, *_ = setup_case
    output = run["output"]
    output["items"].append(deepcopy(output["items"][0]))
    errors = validate_output(output, payload, [item], output["critical_policy"])
    assert {"P2E302", "P2E303"} <= codes(errors)
    output["items"][1]["item_id"] = "made_up"
    assert "P2E301" in codes(validate_output(output, payload, [item], output["critical_policy"]))


@pytest.mark.parametrize("field,value", [("overall_result", "미충족"), ("counts", {"total": 999}), ("rule_version", "wrong")])
def test_overall_is_recomputed(setup_case, field, value):
    output = execute(setup_case)["output"]
    output[field] = value
    assert "P2E504" in codes(validate_output(output, setup_case[0], [setup_case[2]], output["critical_policy"]))


def test_ocr_review_enrichment_does_not_mutate_wire_schema(setup_case):
    setup_case[0]["chunks"][0]["source"] = "ocr"
    run = execute(setup_case)
    assert "P2R203" in run["output"]["human_review"]["reasons"]
    assert "source" not in run["output"]["items"][0]["citations"][0]
    assert not validate_schema(run["output"], "output")


@pytest.mark.parametrize("verdict,confidence,conditions", [("UNSUPPORTED", .9, []), ("CONFLICT", .9, []), ("UNCERTAIN", .9, []), ("SUPPORTED", .69, []), ("SUPPORTED", .9, ["매주 점검"] )])
def test_semantic_or_low_confidence_routes_to_review(setup_case, verdict, confidence, conditions):
    payload, _, item, context, output = setup_case
    checked, audit = run_validated_item(item, context, evidence_id=payload["evidence_id"], version=1,
        model="fake", reason_codes=REASONS["codes"], llm_call=response(output),
        self_check_call=response({"verdict": verdict, "confidence": confidence, "reason": "재검토 근거", "unsupported_conditions": conditions}))
    assert checked["result"] == "UNKNOWN" and audit["review_signals"]


def test_self_check_uses_only_citations(setup_case):
    _, _, item, context, output = setup_case
    def inspect(system, user, model, schema, **kwargs):
        data = json.loads(user)
        assert "cited_evidence" in data and "chunks" not in data
        assert context["chunks"][1]["text"] not in user
        return supported()
    assert run_self_check(item, output, model="fake", llm_call=inspect)["verdict"] == "SUPPORTED"


def test_self_check_receives_full_delivered_context(setup_case):
    _, _, item, context, output = setup_case
    def inspect(system, user, model, schema, **kwargs):
        data = json.loads(user)
        assert data["available_evidence_context"]["chunks"] == context["chunks"]
        assert "계정 삭제" in system and "비밀번호 변경" in system
        assert "사람·직위·역할·경력" in system
        assert "별도 비교보고서" in system
        assert "전자 등록은 접근 제한과 같지 않다" in system
        assert "다른 사람의 속성을 옮기지 마라" in system
        assert "직무·역할·최소권한 기준" in system
        assert "다른 사례의 기준표" in system
        assert "OCR로 평탄화된 표" in system
        return supported()
    result = run_self_check(item, output, context=context, model="fake", llm_call=inspect)
    assert result["verdict"] == "SUPPORTED"
    assert result["guard_scope"] == "full_context"


def test_r5_catalog_policy_is_used_without_caller_override(setup_case):
    payload, _, _, _, output = setup_case
    catalog = json.loads((ROOT / "phase2_기준/full_checklist_draft.json").read_text(encoding="utf-8"))
    reasons = json.loads((ROOT / "phase2_기준/full_reason_codes_draft.json").read_text(encoding="utf-8"))
    control = next(c for c in catalog["controls"] if c["control_id"] == "2.5.1")
    control["items"] = control["items"][:1]
    payload["checklist_version"] = catalog["draft_version"]
    output["item_id"] = control["items"][0]["item_id"]
    run = run_control_judgment(payload, "2.5.1", catalog=catalog, reason_catalog=reasons,
        controls={c["control_id"]: c["control_name"] for c in catalog["controls"]}, model="fake",
        llm_call=response(output), self_check_call=supported, sleeper=lambda _: None)
    assert run["output"]["critical_policy"] == {"mode": "explicit"}
    assert run["output"]["items"][0]["critical"] is True


def test_self_check_malformed_fails_closed(setup_case):
    item, output = setup_case[2], setup_case[-1]
    result = run_self_check(item, output, model="fake", llm_call=response({"verdict": "SUPPORTED"}), sleeper=lambda _: None)
    assert result["error_code"] == "E202" and result["verdict"] == "UNCERTAIN"


def test_freshness_wires_into_final_result(setup_case):
    item_id = setup_case[2]["item_id"]
    result = execute(setup_case, item_policies={item_id: {"freshness": {"max_age_months": 6, "date_label": "document_date"}}}, as_of="2026-10-02")
    assert result["output"]["items"][0]["result"] == "UNKNOWN"
    assert result["audit"]["items"][0]["freshness"]["status"] == "DATE_MISSING"
    assert result["output"]["human_review"]["required"]


def test_explicit_critical_never_guesses_unset_value(setup_case):
    result = execute(setup_case, critical_policy={"mode": "explicit"})
    assert result["processing_status"] == "REVIEW_REQUIRED" and result["output"] is None
    assert "P2E503" in codes(result["audit"]["issues"])


def test_short_reason_is_record_only(setup_case):
    setup_case[-1]["reason"] = "확인됨"
    result = execute(setup_case)
    assert result["processing_status"] == "COMPLETED"
    assert "P2E506" in result["output"]["human_review"]["error_codes"]


def test_review_unknown_ratio_boundary():
    items = [{"result": "UNKNOWN"}, {"result": "MET"}]
    assert "P2R202" in decide_review(items, {"chunks": []})["reasons"]
    items.append({"result": "MET"})
    assert not decide_review(items, {"chunks": []})["required"]
