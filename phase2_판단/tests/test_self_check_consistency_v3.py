"""A contradictory reviewer response must retry, never become silent support."""
from dataclasses import replace
import json

from jsonschema import Draft202012Validator
import pytest

from phase1_runtime import DEFAULT_RETRY_POLICY, ErrorCode
from self_check import SELF_CHECK_SCHEMA, run_self_check


ITEM = {"item_id": "sample-Q01", "question": "요구된 활동을 수행하였는가?",
        "check_kind": "implementation", "evidence_rule": {"met": "동일 대상의 실제 수행을 확인한다."}}
CONTEXT = {"chunks": [{"chunk_id": "source-1", "text": "활동 계획을 수립하였다.",
                       "heading": "계획", "page_start": 1, "page_end": 1, "role": "evidence"}]}
POLICY = replace(DEFAULT_RETRY_POLICY, max_retries=1, backoff_seconds=0)


def candidate(result="MET"):
    return {"item_id": ITEM["item_id"], "result": result,
            "reason": "계획은 확인되지만 실제 수행은 확인할 수 없다." if result == "UNKNOWN" else "수행하였다.",
            "reason_codes": ["P2_U_EVIDENCE_INSUFFICIENT"] if result == "UNKNOWN" else [],
            "citations": [{"chunk_id": "source-1", "page": 1, "quote": "활동 계획을 수립하였다."}]}


def review(verdict="SUPPORTED", conditions=None):
    return {"verdict": verdict, "reason": "원문에 비추어 검토하였다.", "confidence": 0.95,
            "unsupported_conditions": [] if conditions is None else conditions}


@pytest.mark.parametrize("profile", ["legacy", "compact_v2"])
@pytest.mark.parametrize("proposed_result", ["MET", "UNKNOWN", "NOT_MET"])
def test_repeated_supported_with_conditions_exhausts_existing_budget(profile, proposed_result):
    invalid = review(conditions=["실제 수행 연결은 확인되지 않는다."])
    raw = json.dumps(invalid, ensure_ascii=False)
    prompts = []

    def fake(system, user, model, schema, **kwargs):
        prompts.append(user)
        assert schema == SELF_CHECK_SCHEMA
        return raw

    result = run_self_check(ITEM, candidate(proposed_result), model="offline-stub",
                            context=CONTEXT, llm_call=fake, retry_policy=POLICY,
                            prompt_profile=profile)
    assert len(prompts) == POLICY.max_retries + 1
    assert result["attempts"] == 2
    assert result["verdict"] == "UNCERTAIN"
    assert result["error_code"] == ErrorCode.SCHEMA_INVALID.value
    assert "SELF_CHECK_VERDICT_CONDITION_CONFLICT" in result["reason"]
    assert "SELF_CHECK_VERDICT_CONDITION_CONFLICT" in prompts[1]
    assert result["raw_response"] == raw
    assert [entry["raw_response"] for entry in result["attempt_history"]] == [raw, raw]
    assert all(entry["error_code"] == ErrorCode.SCHEMA_INVALID.value
               for entry in result["attempt_history"])
    assert "consistency_guard" not in result


@pytest.mark.parametrize("fixed_verdict,conditions", [
    ("SUPPORTED", []), ("UNSUPPORTED", ["실제 수행 연결은 확인되지 않는다."]),
])
def test_retry_accepts_model_corrected_contract_without_rewriting(fixed_verdict, conditions):
    invalid = review(conditions=["실제 수행 연결은 확인되지 않는다."])
    valid = review(fixed_verdict, conditions)
    raw = [json.dumps(row, ensure_ascii=False) for row in (invalid, valid)]
    answers = iter(raw)
    result = run_self_check(ITEM, candidate("UNKNOWN"), model="offline-stub", context=CONTEXT,
                            llm_call=lambda *args, **kwargs: next(answers), retry_policy=POLICY)
    assert result["verdict"] == valid["verdict"]
    assert result["unsupported_conditions"] == valid["unsupported_conditions"]
    assert result["reason"] == valid["reason"]
    assert result["confidence"] == valid["confidence"]
    assert result["error_code"] is None
    assert result["attempts"] == 2
    assert result["attempt_history"][0]["raw_response"] == raw[0]
    assert result["attempt_history"][0]["error_code"] == ErrorCode.SCHEMA_INVALID.value
    assert result["attempt_history"][1]["raw_response"] == raw[1]
    assert result["attempt_history"][1]["error_code"] is None


def test_supported_unknown_with_empty_conditions_remains_valid():
    valid = review()
    result = run_self_check(ITEM, candidate("UNKNOWN"), model="offline-stub", context=CONTEXT,
                            llm_call=lambda *args, **kwargs: json.dumps(valid), retry_policy=POLICY)
    assert result["attempts"] == 1
    assert result["error_code"] is None
    assert {key: result[key] for key in valid} == valid


def test_contract_is_in_schema_and_zero_retry_budget_is_honored():
    invalid = review(conditions=["source does not establish execution"])
    assert list(Draft202012Validator(SELF_CHECK_SCHEMA).iter_errors(invalid))
    calls = []

    def fake(*args, **kwargs):
        calls.append(1)
        return json.dumps(invalid)

    result = run_self_check(ITEM, candidate(), model="offline-stub", llm_call=fake,
                            retry_policy=replace(POLICY, max_retries=0))
    assert calls == [1]
    assert result["verdict"] == "UNCERTAIN"
    assert result["error_code"] == ErrorCode.SCHEMA_INVALID.value


def test_generation_contract_uses_complete_object_alternatives_without_conditionals():
    branches = SELF_CHECK_SCHEMA["anyOf"]
    assert len(branches) == 2
    for branch in branches:
        assert branch["type"] == "object"
        assert branch["additionalProperties"] is False
        assert branch["required"] == SELF_CHECK_SCHEMA["required"]
        assert set(branch["properties"]) == set(SELF_CHECK_SCHEMA["properties"])
    assert branches[0]["properties"]["verdict"] == {"const": "SUPPORTED"}
    assert branches[0]["properties"]["unsupported_conditions"]["maxItems"] == 0
    assert branches[1]["properties"]["verdict"]["enum"] == ["UNSUPPORTED", "CONFLICT", "UNCERTAIN"]
    assert not any(key in json.dumps(SELF_CHECK_SCHEMA) for key in ('"if"', '"then"', '"allOf"'))
    validator = Draft202012Validator(SELF_CHECK_SCHEMA)
    for verdict in ("UNSUPPORTED", "CONFLICT", "UNCERTAIN"):
        assert not list(validator.iter_errors(review(verdict, [])))
        assert not list(validator.iter_errors(review(verdict, ["unresolved condition"])))
    assert not list(validator.iter_errors(review("SUPPORTED", [])))
    assert list(validator.iter_errors(review("SUPPORTED", ["unresolved condition"])))
