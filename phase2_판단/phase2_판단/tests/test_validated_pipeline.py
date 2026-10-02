from copy import deepcopy
import json
from pathlib import Path

import pytest

from validated_pipeline import run_control_judgment, run_validated_item
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
