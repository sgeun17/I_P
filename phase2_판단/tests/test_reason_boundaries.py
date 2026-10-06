"""Instruction regression, not an Ollama semantic accuracy test."""
import json
from grounding_prompts import build_system_prompt
from self_check import run_self_check
from phase1_runtime import RetryPolicy


def test_rules_are_conditions_not_observations():
    prompt = build_system_prompt({})
    assert "관찰된 사실이 아니다" in prompt
    assert "기준 자체의 타당성은 임의로 변경하지 않는다" in prompt
    assert "후속 기록의 부재를 추가 결함·보류 사유로 붙이지 않는다" in prompt
    assert "특정 실행의 완료 기록으로 확대하지 않는다" in prompt


def test_review_instructions_preserve_actual_requirement_and_claim_audit():
    def fake(system, user, model, schema, **kwargs):
        assert "단어가 똑같아야 한다고 요구하지 마라" in system
        assert "의미상 직접 표현한 사실" in system
        assert "unsupported_conditions에 적는다" in system
        assert "새 요구라고 무시하지 않" in system
        assert "Self-check 자신의 reason에는" in system
        return json.dumps({"verdict": "UNSUPPORTED", "reason": "시점 근거 미확인",
                           "confidence": 0.9, "unsupported_conditions": ["승인 시점 확인 주장"]})
    output = {"result": "MET", "reason": "시점까지 확인됨", "citations": []}
    result = run_self_check({"evidence_rule": {"met": "승인 시점 확인"}}, output,
                            model="mock", llm_call=fake)
    assert result["unsupported_conditions"] == ["승인 시점 확인 주장"]


def test_review_prompt_names_direct_state_without_case_identifiers():
    source = __import__("inspect").getsource(run_self_check)
    assert "발송 완료" in source
    assert "받지 않고 진행" in source
    assert "현재 보관기간" in source
    assert "PROFILE-" not in source
    assert "MARKETING-ACTUAL-NOTICE" not in source


def test_review_verdict_must_follow_its_own_supported_findings():
    source = __import__("inspect").getsource(run_self_check)
    assert "verdict·reason·unsupported_conditions는 서로 모순되면 안 된다" in source
    assert "해당 result 조건에 해당한다고 인정했다면" in source
    assert "이미 원문에서 확인했다고" in source
    assert "법적 근거·방침을 적었다는 사실은" in source


def test_direct_failure_entailment_contradiction_retains_not_met_without_retry():
    calls = []
    def fake(system, user, model, schema, **kwargs):
        calls.append(user)
        return json.dumps({
            "verdict": "UNSUPPORTED",
            "reason": "필수 동의를 받지 않고 진행한 사실은 확인되었으나 NOT_MET 근거가 부족하다.",
            "confidence": 0.6,
            "unsupported_conditions": ["NOT_MET 판정의 직접 근거 부족"],
        })
    output = {"result": "NOT_MET", "reason": "필수 동의를 받지 않고 진행함",
              "citations": [{"quote": "담당자는 필수 동의를 받지 않고 수집을 진행하였다."}]}
    result = run_self_check({"question": "필수 동의를 받았는가?"}, output, model="mock",
                            llm_call=fake, retry_policy=RetryPolicy(max_retries=1, backoff_seconds=0))
    assert result["verdict"] == "SUPPORTED"
    assert result["attempts"] == 1
    assert len(calls) == 1
    assert result["consistency_guard"]["decision"] == "RETAIN_PROPOSED_NOT_MET"


def test_direct_failure_guard_does_not_override_target_mismatch():
    def fake(*args, **kwargs):
        return json.dumps({"verdict": "UNSUPPORTED", "reason": "대상 불일치",
                           "confidence": 0.8, "unsupported_conditions": ["동일 대상"]})
    output = {"result": "NOT_MET", "reason": "미수행",
              "citations": [{"quote": "다른 대상은 수행하지 않았다."}]}
    result = run_self_check({"question": "검토 대상이 수행했는가?"}, output, model="mock",
                            llm_call=fake, retry_policy=RetryPolicy(max_retries=1, backoff_seconds=0))
    assert result["verdict"] == "UNSUPPORTED"
    assert result["error_code"] is None
    assert "consistency_guard" not in result
