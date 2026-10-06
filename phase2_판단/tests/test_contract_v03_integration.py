"""Regression coverage for the interface team's phase2-output-0.3 contract."""
from copy import deepcopy
from pathlib import Path

from test_validated_pipeline import execute, setup_case
from validation.contracts import interface_module, output_schema_version, validate_schema
from validation.logic import validate_item
from validation.review import decide_review


def test_output_version_follows_authoritative_schema(setup_case):
    run = execute(setup_case)
    assert output_schema_version() == "phase2-output-0.3"
    assert run["output"]["schema_version"] == output_schema_version()
    assert not validate_schema(run["output"], "output")


def test_known_self_check_signals_use_official_reason_codes():
    review = decide_review([], {"chunks": []}, signals=[
        "SELF_CHECK_UNSUPPORTED", "SELF_CHECK_CONFLICT", "SELF_CHECK_UNCERTAIN",
    ])
    assert {"P2R111", "P2R112", "P2R205"} <= set(review["reasons"])


def test_unknown_signal_is_visible_and_provisional(setup_case):
    review = decide_review([], {"chunks": []}, signals=["FUTURE_REVIEW_SIGNAL"])
    assert review["required"] is True
    assert "P2R113" in review["reasons"]
    assert review["unmapped_signals"] == ["FUTURE_REVIEW_SIGNAL"]
    fields = interface_module("errors").provisional_fields(review)
    assert fields["provisional"] is True
    assert "FUTURE_REVIEW_SIGNAL" in fields["provisional_reason"]


def test_schema_error_and_semantic_reason_mismatch_are_both_recorded(setup_case):
    payload, _, item, context, output = setup_case
    bad = deepcopy(output)
    bad["reason_codes"] = ["P2_U_EVIDENCE_INSUFFICIENT"]
    issues = validate_item(bad, item, context, [], evidence_id=payload["evidence_id"], version=payload["version"])
    codes = {row["code"] for row in issues}
    assert "E202" in codes
    assert "P2E502" in codes


def test_overall_helper_is_loaded_from_interface_root():
    expected = (
        Path(__file__).resolve().parents[2]
        / "phase2_인터페이스"
        / "overall_result.py"
    ).resolve()
    assert Path(interface_module("overall").__file__).resolve() == expected
