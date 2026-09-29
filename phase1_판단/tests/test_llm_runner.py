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

    assert result.raw_response == good_response
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

    assert result.raw_response == good_response
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
