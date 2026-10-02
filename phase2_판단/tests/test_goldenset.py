import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from validation.citations import validate_citations


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "goldenset.json"
CHECKLIST = ROOT.parent / "phase2_기준" / "chapter2_full_checklist_draft.json"
REASON_CODES = ROOT.parent / "phase2_기준" / "chapter2_reason_codes_draft.json"
OUTPUT_SCHEMA = ROOT.parent / "phase2_인터페이스" / "phase2_output.schema.json"

GOLDENSET = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = GOLDENSET["cases"]
ITEM_IDS = {
    item["item_id"]
    for control in json.loads(CHECKLIST.read_text(encoding="utf-8"))["controls"]
    for item in control["items"]
}
ALLOWED_REASON_CODES = {
    row["code"]
    for row in json.loads(REASON_CODES.read_text(encoding="utf-8"))["codes"]
}
OUTPUT_SCHEMA_DATA = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
ITEM_SCHEMA = {
    "$schema": OUTPUT_SCHEMA_DATA["$schema"],
    "$defs": OUTPUT_SCHEMA_DATA["$defs"],
    **OUTPUT_SCHEMA_DATA["$defs"]["ItemResult"],
}
ITEM_VALIDATOR = Draft202012Validator(ITEM_SCHEMA)


def ids(case):
    return case["test_id"]


def load_generator():
    path = ROOT / "tools" / "build_goldenset.py"
    spec = importlib.util.spec_from_file_location("phase2_build_goldenset", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_생성물은_생성기와_일치한다():
    assert GOLDENSET == load_generator().build_payload()


def test_구성은_설계대로_29건이다():
    assert len(CASES) == 29
    assert Counter(row["response_kind"] for row in CASES) == {"ideal": 23, "faulty": 6}
    ideal = [row for row in CASES if row["response_kind"] == "ideal"]
    assert Counter(row["gold"]["result"] for row in ideal) == {
        "MET": 8,
        "NOT_MET": 3,
        "UNKNOWN": 12,
    }
    assert Counter(row["category"] for row in ideal) == {
        "normal_met": 8,
        "normal_not_met": 3,
        "scope_violation": 3,
        "missing_evidence": 3,
        "conflicting_evidence": 3,
        "no_event_or_not_due": 2,
        "filename_or_external_knowledge": 1,
    }
    faulty = [row for row in CASES if row["response_kind"] == "faulty"]
    assert Counter(row["category"] for row in faulty) == {
        "forged_citation": 3,
        "out_of_checklist_condition": 2,
        "prompt_injection": 1,
    }


def test_전부_unknown_baseline은_12_23이다():
    baseline = GOLDENSET["counts"]["all_unknown_baseline"]
    assert baseline["correct"] == 12
    assert baseline["total"] == 23
    assert baseline["accuracy"] == pytest.approx(12 / 23, abs=1e-6)


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_item_id와_사유코드가_draft에_실재한다(case):
    assert case["gold"]["item_id"] in ITEM_IDS
    assert case["llm_output"]["item_id"] in ITEM_IDS
    assert set(case["gold"]["reason_codes"]) <= ALLOWED_REASON_CODES
    assert set(case["llm_output"]["reason_codes"]) <= ALLOWED_REASON_CODES


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_공식_itemresult_출력_계약을_지킨다(case):
    issues = sorted(ITEM_VALIDATOR.iter_errors(case["llm_output"]), key=lambda row: list(row.path))
    assert not issues, [issue.message for issue in issues]
    assert case["llm_output"]["item_id"] == case["input"]["item"]["item_id"]


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_모든_케이스에_설계_근거가_있다(case):
    assert case["note"].strip()


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_gold의_met_not_met_인용은_원문과_정확히_맞는다(case):
    validation = validate_citations(
        case["gold"],
        case["input"]["context"],
        expected_evidence_id=case["input"]["evidence_id"],
    )
    assert validation.passed, [issue.code for issue in validation.issues]


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_고정_응답의_citation_검증_결과(case):
    validation = validate_citations(
        case["llm_output"],
        case["input"]["context"],
        expected_evidence_id=case["input"]["evidence_id"],
    )
    expected = case["expected"]
    assert validation.passed == expected["citation_validation_passed"]
    got = {issue.code for issue in validation.issues}
    assert set(expected["citation_issue_codes"]) <= got


def test_faulty_응답은_gold와_같지_않다():
    for case in CASES:
        if case["response_kind"] == "faulty":
            assert case["llm_output"] != case["gold"], case["test_id"]


def test_실제_모델_평가가_필요한_의미_사례를_명시한다():
    pending = {
        row["test_id"]: row["expected"]["requires_model_evaluation"]
        for row in CASES
        if row["expected"]["requires_model_evaluation"]
    }
    assert pending == {
        "P2-RULE-01": "semantic_rule_boundary",
        "P2-RULE-02": "semantic_rule_boundary",
        "P2-INJECT-01": "prompt_injection",
    }
