import json

import httpx

from enums import ErrorCode
from llm_config import GenerationConfig, LLMClientConfig
from llm_runner import run_mapping_llm
from review_policy import RetryPolicy


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion(content: str, finish_reason: str = "stop"):
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }


def test_runner_retries_call_error_with_retry_prompt(mapping_input, good_response):
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(503, text="server temporarily unavailable")
        return httpx.Response(200, json=_completion(good_response))

    client = _client(handler)
    policy = RetryPolicy(max_retries=1, timeout_seconds=60, backoff_seconds=0)
    try:
        result = run_mapping_llm(
            mapping_input,
            model="qwen3:8b",
            http_client=client,
            retry_policy=policy,
            generation=GenerationConfig(),
            sleeper=lambda _: None,
        )
    finally:
        client.close()

    assert json.loads(result.raw_response) == json.loads(good_response)
    assert result.provider_raw_response == good_response
    assert result.call_error is None
    assert result.attempts == 2
    assert result.retry_count == 1
    assert len(calls) == 2
    retry_user = calls[1]["messages"][1]["content"]
    assert "<retry_context>" in retry_user
    assert "E103" in retry_user


def test_runner_retries_json_parse_error_with_retry_prompt(mapping_input, good_response):
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        content = "not-json" if len(calls) == 1 else good_response
        return httpx.Response(200, json=_completion(content))

    client = _client(handler)
    policy = RetryPolicy(max_retries=1, timeout_seconds=60, backoff_seconds=0)
    try:
        result = run_mapping_llm(
            mapping_input,
            model="qwen3:8b",
            http_client=client,
            retry_policy=policy,
            sleeper=lambda _: None,
        )
    finally:
        client.close()

    assert json.loads(result.raw_response) == json.loads(good_response)
    assert result.provider_raw_response == good_response
    assert result.attempts == 2
    assert result.retry_count == 1
    assert "E201" in calls[1]["messages"][1]["content"]


def test_runner_returns_e106_after_retry_is_exhausted(mapping_input):
    client = _client(lambda request: httpx.Response(503, text="still down"))
    policy = RetryPolicy(max_retries=1, timeout_seconds=60, backoff_seconds=0)
    try:
        result = run_mapping_llm(
            mapping_input,
            model="qwen3:8b",
            http_client=client,
            retry_policy=policy,
            sleeper=lambda _: None,
        )
    finally:
        client.close()

    assert result.raw_response is None
    assert result.call_error == ErrorCode.LLM_RETRY_EXHAUSTED
    assert result.attempts == 2
    assert result.retry_count == 1
    assert result.last_issue_codes == (ErrorCode.LLM_SERVER_ERROR,)


def test_runner_does_not_retry_http_4xx_without_common_error_code(mapping_input):
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(404, text="model not found")

    client = _client(handler)
    policy = RetryPolicy(max_retries=1, timeout_seconds=60, backoff_seconds=0)
    try:
        result = run_mapping_llm(
            mapping_input,
            model="missing-model",
            http_client=client,
            retry_policy=policy,
            sleeper=lambda _: None,
        )
    finally:
        client.close()

    assert calls == 1
    assert result.raw_response is None
    assert result.call_error is None
    assert result.has_unmapped_request_error is True
    assert result.request_error_status == 404



def test_runner_derives_no_match_when_all_candidates_are_not_related(mapping_input):
    content = json.dumps(
        {
            "candidate_decisions": [
                {
                    "control_id": c.control_id,
                    "decision": "NOT_RELATED",
                    "llm_confidence": 0.95,
                    "reason": "증적에 이 후보의 고유 활동이 나타나지 않는다.",
                    "citations": [],
                }
                for c in mapping_input.candidate_controls
            ],
            "mapped_controls": [],
        },
        ensure_ascii=False,
    )

    seen_schema = {}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen_schema.update(body["response_format"]["json_schema"]["schema"])
        return httpx.Response(200, json=_completion(content))

    client = _client(handler)
    try:
        result = run_mapping_llm(
            mapping_input,
            model="qwen3:8b",
            http_client=client,
            sleeper=lambda _: None,
        )
    finally:
        client.close()

    parsed = json.loads(result.raw_response)
    assert parsed["match_status"] == "NO_MATCH"
    assert "match_status" not in json.loads(result.provider_raw_response)
    assert "match_status" not in seen_schema["properties"]
    assert "match_status" not in seen_schema["required"]


def test_runner_overwrites_model_match_status_with_candidate_decisions(mapping_input):
    # 혹시 모델이 Schema 밖 필드인 match_status를 임의로 출력해도 파생값이 우선한다.
    content = json.dumps(
        {
            "match_status": "MATCHED",
            "candidate_decisions": [
                {
                    "control_id": c.control_id,
                    "decision": "NOT_RELATED",
                    "llm_confidence": 0.95,
                    "reason": "증적에 이 후보의 고유 활동이 나타나지 않는다.",
                    "citations": [],
                }
                for c in mapping_input.candidate_controls
            ],
            "mapped_controls": [],
        },
        ensure_ascii=False,
    )

    client = _client(lambda request: httpx.Response(200, json=_completion(content)))
    try:
        result = run_mapping_llm(
            mapping_input,
            model="qwen3:8b",
            http_client=client,
            sleeper=lambda _: None,
        )
    finally:
        client.close()

    assert json.loads(result.raw_response)["match_status"] == "NO_MATCH"
    assert result.last_issue_codes == ()


def test_runner_does_not_retry_non_policy_schema_detail_error(mapping_input):
    # Valid JSON, but required fields are missing -> E203. Current retry policy only allows E201/E202.
    bad = json.dumps({"match_status": "MATCHED"})
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion(bad))

    client = _client(handler)
    policy = RetryPolicy(max_retries=1, timeout_seconds=60, backoff_seconds=0)
    try:
        result = run_mapping_llm(
            mapping_input,
            model="qwen3:8b",
            http_client=client,
            retry_policy=policy,
            sleeper=lambda _: None,
        )
    finally:
        client.close()

    assert calls == 1
    assert result.raw_response == bad
    assert ErrorCode.REQUIRED_FIELD_MISSING in result.last_issue_codes


def _without_derived_match_status(payload: dict) -> str:
    data = json.loads(json.dumps(payload, ensure_ascii=False))
    data.pop("match_status", None)
    return json.dumps(data, ensure_ascii=False)


def test_runner_retries_missing_mapped_controls_then_accepts_repair(mapping_input, good_response):
    good = json.loads(good_response)
    bad = json.loads(good_response)
    bad.pop("match_status", None)
    bad.pop("mapped_controls", None)
    responses = [json.dumps(bad, ensure_ascii=False), _without_derived_match_status(good)]
    calls = []

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(responses[len(calls) - 1]))

    client = _client(handler)
    try:
        result = run_mapping_llm(mapping_input, model="qwen3:14b", http_client=client, sleeper=lambda _: None)
    finally:
        client.close()

    assert result.attempts == 2
    assert result.retry_count == 1
    assert result.call_error is None
    assert result.last_issue_codes == ()
    retry_prompt = calls[1]["messages"][1]["content"]
    assert "E501" in retry_prompt
    assert "E504" in retry_prompt
    assert "PRIMARY" in retry_prompt
    assert "mapping_consistency" in retry_prompt
    assert "related_control_ids" in retry_prompt
    assert mapping_input.candidate_controls[0].control_id in retry_prompt
    assert "mapped_control_ids" in retry_prompt
    assert json.loads(result.provider_raw_response) == json.loads(responses[1])
    assert json.loads(result.raw_response)["match_status"] == "MATCHED"


def test_runner_retries_bad_quote_and_page_then_accepts_repair(mapping_input, good_response):
    good = json.loads(good_response)
    bad = json.loads(good_response)
    bad.pop("match_status", None)
    for section in ("candidate_decisions", "mapped_controls"):
        citation = bad[section][0]["citations"][0]
        citation["quote"] = "원문에 존재하지 않는 변형 인용문"
        citation["page"] = 999
    responses = [json.dumps(bad, ensure_ascii=False), _without_derived_match_status(good)]
    calls = []

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(responses[len(calls) - 1]))

    client = _client(handler)
    try:
        result = run_mapping_llm(mapping_input, model="qwen3:14b", http_client=client, sleeper=lambda _: None)
    finally:
        client.close()

    assert result.attempts == 2
    assert result.last_issue_codes == ()
    retry_prompt = calls[1]["messages"][1]["content"]
    assert "E403" in retry_prompt
    assert "E404" in retry_prompt
    assert "연속해서 존재하는 문자열" in retry_prompt
    assert "page_start~page_end" in retry_prompt
    assert "citation_failures" in retry_prompt
    assert "failed_quote" in retry_prompt
    assert "원문에 존재하지 않는 변형 인용문" in retry_prompt
    assert "failed_page" in retry_prompt
    assert "999" in retry_prompt
    assert mapping_input.chunks[0].chunk_id in retry_prompt


def test_runner_retries_repairable_issue_even_when_e505_is_also_present(mapping_input, good_response):
    bad = json.loads(good_response)
    bad.pop("match_status", None)
    bad["mapped_controls"] = []
    bad["candidate_decisions"][0]["reason"] = "요구사항을 충족하는 것으로 판단된다."
    responses = [json.dumps(bad, ensure_ascii=False), _without_derived_match_status(json.loads(good_response))]
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        response = responses[calls]
        calls += 1
        return httpx.Response(200, json=_completion(response))

    client = _client(handler)
    try:
        result = run_mapping_llm(mapping_input, model="qwen3:14b", http_client=client, sleeper=lambda _: None)
    finally:
        client.close()

    assert calls == 2
    assert result.last_issue_codes == ()


def test_runner_does_not_retry_e505_only(mapping_input, good_response):
    bad = json.loads(good_response)
    bad.pop("match_status", None)
    bad["candidate_decisions"][0]["reason"] = "요구사항을 충족하는 것으로 판단된다."
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion(json.dumps(bad, ensure_ascii=False)))

    client = _client(handler)
    try:
        result = run_mapping_llm(mapping_input, model="qwen3:14b", http_client=client, sleeper=lambda _: None)
    finally:
        client.close()

    assert calls == 1
    assert result.retry_count == 0
    assert ErrorCode.ADEQUACY_JUDGMENT_DETECTED in result.last_issue_codes


def test_runner_returns_final_semantic_failure_after_single_retry(mapping_input, good_response):
    bad = json.loads(good_response)
    bad.pop("match_status", None)
    bad["mapped_controls"] = []
    raw = json.dumps(bad, ensure_ascii=False)
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion(raw))

    client = _client(handler)
    try:
        result = run_mapping_llm(mapping_input, model="qwen3:14b", http_client=client, sleeper=lambda _: None)
    finally:
        client.close()

    assert calls == 2
    assert result.attempts == 2
    assert result.retry_count == 1
    assert result.call_error is None
    assert ErrorCode.PRIMARY_COUNT_INVALID in result.last_issue_codes
    assert ErrorCode.MATCHED_WITHOUT_CONTROLS in result.last_issue_codes
    assert result.provider_raw_response == raw
