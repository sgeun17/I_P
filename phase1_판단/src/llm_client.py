"""Ollama OpenAI-compatible LLM HTTP client.

이 모듈의 책임은 전송 계층에 한정한다.
- System/User message + Pydantic JSON Schema를 Ollama에 전달
- OpenAI-compatible response에서 assistant content 추출
- E101~E105로 호출 실패를 분류

재시도 횟수/재출력 Prompt는 `llm_runner.py` + 기존 RetryPolicy가 담당한다.
4xx 요청 오류는 현재 공통 ErrorCode에 전용 코드가 없으므로 별도 예외로 구분하고 재시도하지 않는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from enums import ErrorCode
from llm_config import (
    ContextBudgetConfig,
    GenerationConfig,
    LLMClientConfig,
    TokenCounter,
)


_SCHEMA_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


class LLMClientError(RuntimeError):
    """LLM client 계층의 기본 예외."""


class LLMCallError(LLMClientError):
    """기존 E1xx 코드로 표현 가능한 실제 LLM 호출 실패."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class LLMRequestRejectedError(LLMClientError):
    """HTTP 4xx 요청 오류.

    현재 판단팀 공통 ErrorCode에는 4xx 전용 코드가 없다. E103(5xx)으로 위장하지 않고
    별도로 보존한다. 공통 코드가 합의되면 그때 ErrorCode에 연결한다.
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ContextBudgetExceededError(LLMClientError):
    pass


@dataclass(frozen=True)
class ContextBudgetUsage:
    input_tokens: int
    context_window: int
    max_output_tokens: int
    safety_margin_tokens: int

    @property
    def remaining_tokens(self) -> int:
        return (
            self.context_window
            - self.input_tokens
            - self.max_output_tokens
            - self.safety_margin_tokens
        )


def _schema_name(schema: dict[str, Any]) -> str:
    raw = str(schema.get("title") or "LLMMappingOutput")
    cleaned = _SCHEMA_NAME_RE.sub("_", raw).strip("_")
    return cleaned or "LLMMappingOutput"


def build_request_body(
    system: str,
    user: str,
    model: str,
    schema: dict[str, Any],
    *,
    generation: GenerationConfig | None = None,
) -> dict[str, Any]:
    """Ollama `/v1/chat/completions`용 OpenAI-compatible body를 만든다."""
    generation = generation or GenerationConfig()
    if not model or not model.strip():
        raise ValueError("model must be a non-empty runtime model identifier")

    body: dict[str, Any] = {
        "model": model.strip(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": generation.temperature,
        "max_tokens": generation.max_tokens,
        "stream": False,
        # Ollama OpenAI-compatible API에서 thinking OFF는 reasoning_effort='none'.
        "reasoning_effort": generation.reasoning_effort,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": _schema_name(schema),
                "strict": True,
                "schema": schema,
            },
        },
    }
    if generation.seed is not None:
        body["seed"] = generation.seed
    return body


def check_context_budget(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    generation: GenerationConfig,
    budget: ContextBudgetConfig,
    token_counter: TokenCounter,
) -> ContextBudgetUsage:
    """정확한 tokenizer callback을 사용해 호출 전 context budget을 검사한다.

    tokenizer가 아직 확정되지 않은 현재 단계에서는 `budget.context_window=None`으로 두고
    이 검사를 호출하지 않는다. 휴리스틱 글자 수를 토큰 수로 가장하지 않는다.
    """
    if budget.context_window is None:
        raise ValueError("context_window is not configured")

    payload_text = "\n".join(
        [
            system,
            user,
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        ]
    )
    input_tokens = token_counter(payload_text)
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens < 0:
        raise ValueError("token_counter must return a non-negative integer")

    usage = ContextBudgetUsage(
        input_tokens=input_tokens,
        context_window=budget.context_window,
        max_output_tokens=generation.max_tokens,
        safety_margin_tokens=budget.safety_margin_tokens,
    )
    if usage.remaining_tokens < 0:
        raise ContextBudgetExceededError(
            "LLM context budget exceeded: "
            f"input={usage.input_tokens}, output_reserve={usage.max_output_tokens}, "
            f"margin={usage.safety_margin_tokens}, window={usage.context_window}"
        )
    return usage


def _headers(config: LLMClientConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _response_excerpt(response: httpx.Response, limit: int = 400) -> str:
    text = response.text.replace("\n", " ").strip()
    return text[:limit]


def _extract_content(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError as exc:
        raise LLMCallError(
            ErrorCode.LLM_SERVER_ERROR,
            "LLM server returned a non-JSON OpenAI-compatible envelope",
            status_code=response.status_code,
        ) from exc

    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LLMCallError(
            ErrorCode.LLM_EMPTY_RESPONSE,
            "LLM response has no choices",
            status_code=response.status_code,
        )

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMCallError(
            ErrorCode.LLM_EMPTY_RESPONSE,
            "LLM response choice is not an object",
            status_code=response.status_code,
        )

    if first.get("finish_reason") == "length":
        raise LLMCallError(
            ErrorCode.LLM_TRUNCATED_RESPONSE,
            "LLM response was truncated by max_tokens",
            status_code=response.status_code,
        )

    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise LLMCallError(
            ErrorCode.LLM_EMPTY_RESPONSE,
            "LLM response message.content is empty",
            status_code=response.status_code,
        )
    return content


def call_llm(
    system: str,
    user: str,
    model: str,
    schema: dict[str, Any],
    *,
    client_config: LLMClientConfig | None = None,
    generation: GenerationConfig | None = None,
    http_client: httpx.Client | None = None,
    context_budget: ContextBudgetConfig | None = None,
    token_counter: TokenCounter | None = None,
) -> str:
    """Ollama를 한 번 호출하고 assistant content를 문자열로 반환한다.

    이 함수 자체는 재시도하지 않는다. 오류 코드를 보존한 채 `LLMCallError`를 발생시키고,
    실제 재시도/재출력은 `llm_runner.run_mapping_llm()`이 기존 RetryPolicy에 맞춰 수행한다.
    """
    config = client_config or LLMClientConfig.from_env()
    generation = generation or GenerationConfig.from_env()

    if context_budget and context_budget.context_window is not None:
        if token_counter is None:
            raise ValueError(
                "token_counter is required when context_window is configured"
            )
        check_context_budget(
            system,
            user,
            schema,
            generation=generation,
            budget=context_budget,
            token_counter=token_counter,
        )

    body = build_request_body(system, user, model, schema, generation=generation)

    try:
        if http_client is not None:
            response = http_client.post(
                config.url,
                json=body,
                headers=_headers(config),
                timeout=config.timeout_seconds,
            )
        else:
            with httpx.Client(timeout=config.timeout_seconds) as owned_client:
                response = owned_client.post(
                    config.url,
                    json=body,
                    headers=_headers(config),
                )
    except httpx.TimeoutException as exc:
        raise LLMCallError(ErrorCode.LLM_TIMEOUT, f"LLM request timed out: {exc}") from exc
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        raise LLMCallError(
            ErrorCode.LLM_CONNECTION_FAILED,
            f"LLM server connection failed: {exc}",
        ) from exc

    if 400 <= response.status_code < 500:
        raise LLMRequestRejectedError(
            response.status_code,
            f"LLM request rejected with HTTP {response.status_code}: "
            f"{_response_excerpt(response)}",
        )
    if response.status_code >= 500:
        raise LLMCallError(
            ErrorCode.LLM_SERVER_ERROR,
            f"LLM server returned HTTP {response.status_code}: {_response_excerpt(response)}",
            status_code=response.status_code,
        )

    return _extract_content(response)
