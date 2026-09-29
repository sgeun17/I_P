import json

import httpx
import pytest

from enums import ErrorCode
from llm_client import (
    ContextBudgetExceededError,
    LLMCallError,
    LLMRequestRejectedError,
    build_request_body,
    call_llm,
    check_context_budget,
)
from llm_config import ContextBudgetConfig, GenerationConfig, LLMClientConfig, LLMConfigError
from prompts import get_output_schema


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_payload(content='{"match_status":"NO_MATCH","candidate_decisions":[],"mapped_controls":[]}'):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def test_generation_config_baseline_uses_non_thinking_openai_mode():
    config = GenerationConfig()
    assert config.temperature == 0
    assert config.max_tokens == 4096
    assert config.stream is False
    assert config.thinking is False
    assert config.reasoning_effort == "none"


def test_generation_config_rejects_streaming():
    with pytest.raises(LLMConfigError):
        GenerationConfig(stream=True)


def test_client_config_uses_ollama_openai_compatible_localhost_default():
    config = LLMClientConfig()
    assert config.provider == "ollama"
    assert config.url == "http://localhost:11434/v1/chat/completions"
    assert config.timeout_seconds == 60


def test_build_request_body_includes_pydantic_schema_and_thinking_off():
    schema = get_output_schema()
    body = build_request_body(
        "system",
        "user",
        "qwen3:8b",
        schema,
        generation=GenerationConfig(),
    )

    assert body["model"] == "qwen3:8b"
    assert body["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert body["temperature"] == 0
    assert body["max_tokens"] == 4096
    assert body["stream"] is False
    assert body["reasoning_effort"] == "none"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == schema
    assert body["response_format"]["json_schema"]["name"] == "LLMMappingOutput"


def test_call_llm_posts_openai_compatible_request_and_returns_content():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_payload("{\"ok\":true}"))

    client = _client(handler)
    try:
        content = call_llm(
            "system",
            "user",
            "qwen3:8b",
            get_output_schema(),
            http_client=client,
        )
    finally:
        client.close()

    assert content == '{"ok":true}'
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["reasoning_effort"] == "none"


def test_call_llm_maps_timeout_to_e101():
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("timeout", request=request)

    client = _client(handler)
    try:
        with pytest.raises(LLMCallError) as exc:
            call_llm("s", "u", "m", get_output_schema(), http_client=client)
    finally:
        client.close()

    assert exc.value.code == ErrorCode.LLM_TIMEOUT


def test_call_llm_maps_connection_failure_to_e102():
    def handler(request: httpx.Request):
        raise httpx.ConnectError("down", request=request)

    client = _client(handler)
    try:
        with pytest.raises(LLMCallError) as exc:
            call_llm("s", "u", "m", get_output_schema(), http_client=client)
    finally:
        client.close()

    assert exc.value.code == ErrorCode.LLM_CONNECTION_FAILED


def test_call_llm_maps_5xx_to_e103():
    client = _client(lambda request: httpx.Response(503, text="unavailable"))
    try:
        with pytest.raises(LLMCallError) as exc:
            call_llm("s", "u", "m", get_output_schema(), http_client=client)
    finally:
        client.close()

    assert exc.value.code == ErrorCode.LLM_SERVER_ERROR
    assert exc.value.status_code == 503


def test_call_llm_keeps_4xx_separate_until_common_error_code_is_agreed():
    client = _client(lambda request: httpx.Response(404, text="model not found"))
    try:
        with pytest.raises(LLMRequestRejectedError) as exc:
            call_llm("s", "u", "missing-model", get_output_schema(), http_client=client)
    finally:
        client.close()

    assert exc.value.status_code == 404


def test_call_llm_maps_empty_content_to_e104():
    payload = _ok_payload("")
    client = _client(lambda request: httpx.Response(200, json=payload))
    try:
        with pytest.raises(LLMCallError) as exc:
            call_llm("s", "u", "m", get_output_schema(), http_client=client)
    finally:
        client.close()

    assert exc.value.code == ErrorCode.LLM_EMPTY_RESPONSE


def test_call_llm_maps_length_finish_reason_to_e105():
    payload = _ok_payload("partial")
    payload["choices"][0]["finish_reason"] = "length"
    client = _client(lambda request: httpx.Response(200, json=payload))
    try:
        with pytest.raises(LLMCallError) as exc:
            call_llm("s", "u", "m", get_output_schema(), http_client=client)
    finally:
        client.close()

    assert exc.value.code == ErrorCode.LLM_TRUNCATED_RESPONSE


def test_context_budget_guard_uses_injected_exact_token_counter():
    usage = check_context_budget(
        "system",
        "user",
        {"type": "object"},
        generation=GenerationConfig(max_tokens=20),
        budget=ContextBudgetConfig(context_window=100, safety_margin_tokens=10),
        token_counter=lambda text: 60,
    )
    assert usage.input_tokens == 60
    assert usage.remaining_tokens == 10


def test_context_budget_guard_rejects_overflow():
    with pytest.raises(ContextBudgetExceededError):
        check_context_budget(
            "system",
            "user",
            {"type": "object"},
            generation=GenerationConfig(max_tokens=30),
            budget=ContextBudgetConfig(context_window=100, safety_margin_tokens=20),
            token_counter=lambda text: 60,
        )
