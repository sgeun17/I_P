"""Synthetic regressions for the eight-item technical-error repair."""
from copy import deepcopy
import json

import pytest

from test_validated_pipeline import REASONS, setup_case, supported
from validated_pipeline import run_validated_item
from self_check import run_self_check
from phase1_runtime import RetryPolicy


@pytest.mark.parametrize("kind", ["quote", "empty_error_code", "wrong_reason_code"])
def test_retry_receives_previous_output_and_specific_errors(setup_case, kind):
    payload, _, item, context, good = setup_case
    bad = deepcopy(good)
    if kind == "quote":
        bad["citations"][0]["quote"] = "원문에 없는 바꿔 쓴 인용"
    elif kind == "empty_error_code":
        bad["error_code"] = ""
    else:
        bad["reason_codes"] = ["P2_U_EVIDENCE_INSUFFICIENT"]
    calls = []

    def fake(system, user, *args, **kwargs):
        calls.append(user)
        return json.dumps(bad if len(calls) == 1 else good, ensure_ascii=False)

    output, audit = run_validated_item(
        item, context, evidence_id=payload["evidence_id"], version=payload["version"],
        model="qwen3:test", reason_codes=REASONS["codes"], llm_call=fake,
        self_check_call=supported, sleeper=lambda _: None,
    )
    assert output["result"] == good["result"]
    assert len(calls) == 2
    assert "previous_response_untrusted" in calls[1]
    assert "validation_errors" in calls[1]
    history = audit["run"]["attempt_history"]
    assert json.loads(history[0]["raw_response"]) == bad
    assert history[0]["validation_messages"]
    assert history[1]["validation_messages"] == []
    assert not any(row["severity"] == "error" for row in audit["issues"])


def test_bad_quote_is_not_silently_accepted(setup_case):
    payload, _, item, context, bad = setup_case
    bad["citations"][0]["quote"] = "원문에 없는 인용"
    output, audit = run_validated_item(
        item, context, evidence_id=payload["evidence_id"], version=payload["version"],
        model="qwen3:test", reason_codes=REASONS["codes"],
        llm_call=lambda *args, **kwargs: json.dumps(bad, ensure_ascii=False),
        self_check_call=supported, sleeper=lambda _: None,
    )
    assert output["result"] == "UNKNOWN"
    assert output["error_code"]
    assert audit["run"]["attempts"] == 2
    assert any(row["code"] == "E403" for row in audit["issues"])


@pytest.mark.parametrize("correct_second", [True, False])
def test_self_check_confidence_retries_without_clamping(correct_second):
    calls = []

    def fake(system, user, *args, **kwargs):
        calls.append(user)
        confidence = 0.8 if correct_second and len(calls) == 2 else 3
        return json.dumps({"verdict": "UNSUPPORTED", "reason": "근거가 충분하지 않다.",
                           "confidence": confidence, "unsupported_conditions": ["범위 불명"]},
                          ensure_ascii=False)

    result = run_self_check({}, {"citations": []}, model="qwen3:test", llm_call=fake,
                            sleeper=lambda _: None)
    assert len(calls) == 2
    assert "confidence" in calls[1] and "greater than" in calls[1]
    assert len(result["attempt_history"]) == 2
    assert result["verdict"] == ("UNSUPPORTED" if correct_second else "UNCERTAIN")
    assert bool(result["error_code"]) is (not correct_second)


def test_no_retry_policy_is_respected():
    result = run_self_check({}, {"citations": []}, model="qwen3:test",
                            llm_call=lambda *args, **kwargs: "{}",
                            retry_policy=RetryPolicy(max_retries=0))
    assert result["attempts"] == 1
    assert result["error_code"]
