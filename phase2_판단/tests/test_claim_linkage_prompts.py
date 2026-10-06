"""Prompt/payload contracts only; these do not measure real-model accuracy."""
import json

from grounding_prompts import build_system_prompt
from self_check import run_self_check


def test_grounding_separates_observed_facts_from_review_sufficiency():
    prompt = build_system_prompt({})
    assert "승인 주체·시점·결과는 각각" in prompt
    assert "검토 수행 과정을 추정하지 않는다" in prompt
    assert "자동으로 MET으로 만들지 않는다" in prompt


def test_review_receives_full_linkage_and_does_not_change_proposal():
    quote = "직무별 허용 권한은 조회다. 신청자는 같은 직무로 조회를 신청했고 승인되었다."
    output = {"item_id": "test", "result": "MET", "reason": "승인 시점까지 확인됨",
              "reason_codes": [], "citations": [{"chunk_id": "a", "page": 1, "quote": quote}]}
    context = {"chunks": [{"chunk_id": "a", "text": quote}], "evidence_chunk_ids": ["a"]}
    original = json.dumps(output, ensure_ascii=False)

    def fake(system, user, model, schema, **kwargs):
        payload = json.loads(user)
        assert payload["cited_evidence"][0]["quote"] == quote
        assert payload["available_evidence_context"]["chunks"] == context["chunks"]
        assert payload["proposed"]["reason"] == output["reason"]
        assert "'승인만 있다'고 설명하지 마라" in system
        assert "해당 주장을 특정" in system
        assert "자동 지지하거나" in system
        return json.dumps({"verdict": "UNSUPPORTED", "reason": "시점 근거 없음",
                           "confidence": 0.8, "unsupported_conditions": ["승인 시점"]})

    result = run_self_check({"item_id": "test"}, output, model="test", context=context, llm_call=fake)
    assert result["verdict"] == "UNSUPPORTED"
    assert json.dumps(output, ensure_ascii=False) == original
