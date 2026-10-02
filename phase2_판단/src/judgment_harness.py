"""Phase 2 항목별 판단 개발 하네스.

Context -> Grounding Prompt -> 팀 제공 Structured Output Schema 호출을 수행한다.
기본 문항 출력 계약은 phase2_인터페이스/phase2_output.schema.json의 ItemResult이며,
최종 JSON/ENUM/ID/Citation 검증은 찬우 Validator가 수행한다.

Citation 원문 일치/page 일치와 Human Review 전환은 찬우 Validator 영역이므로 이 하네스가
대체하지 않는다. 여기서는 JSON 파싱과 개발용 최소 구조만 확인한다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import time
from typing import Any, Callable, Mapping, Sequence

import httpx

from structured_output_adapter import validate_model_generated_item
from grounding_prompts import PromptPackage, build_prompt_package, build_retry_user_prompt
from phase1_runtime import (
    DEFAULT_RETRY_POLICY,
    ErrorCode,
    GenerationConfig,
    LLMCallError,
    LLMClientConfig,
    LLMRequestRejectedError,
    RetryPolicy,
    call_llm,
)


OutputValidator = Callable[[Any], list[str]]
LLMCall = Callable[..., str]


@dataclass(frozen=True)
class JudgmentRunResult:
    raw_response: str | None
    parsed_output: dict[str, Any] | None
    attempts: int
    retry_count: int
    final_error_code: str | None = None
    validation_messages: tuple[str, ...] = ()
    request_error_status: int | None = None
    request_error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.parsed_output is not None and self.final_error_code is None


def _parse_json(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"JSON parse failed: {exc.msg}"]
    if not isinstance(payload, dict):
        return None, ["LLM output must be a JSON object"]
    return payload, []


def _client_config_using_policy(
    client_config: LLMClientConfig | None,
    retry_policy: RetryPolicy,
) -> LLMClientConfig:
    config = client_config or LLMClientConfig.from_env()
    if config.timeout_seconds != float(retry_policy.timeout_seconds):
        config = replace(config, timeout_seconds=float(retry_policy.timeout_seconds))
    return config


def run_item_judgment(
    checklist_item: Mapping[str, Any],
    evidence_context: Mapping[str, Any],
    *,
    model: str,
    reason_codes: Sequence[Mapping[str, Any]] | None = None,
    global_rules: str | None = None,
    output_schema: dict[str, Any] | None = None,
    output_validator: OutputValidator | None = None,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    client_config: LLMClientConfig | None = None,
    generation: GenerationConfig | None = None,
    http_client: httpx.Client | None = None,
    llm_call: LLMCall = call_llm,
    sleeper: Callable[[float], None] = time.sleep,
) -> JudgmentRunResult:
    """체크리스트 item 하나를 LLM에 요청한다.

    Phase 1 RetryPolicy의 max_retries/timeout/backoff/retry_on을 그대로 사용한다.
    HTTP 4xx는 Phase 1과 동일하게 자동 재시도하지 않는다.
    """
    package: PromptPackage = build_prompt_package(
        checklist_item,
        evidence_context,
        reason_codes=reason_codes,
        global_rules=global_rules,
        output_schema=output_schema,
    )
    item_id = str(checklist_item.get("item_id") or "")
    allowed_reason_codes = {
        str(row.get("code")) for row in (reason_codes or []) if row.get("code")
    }

    if output_validator is None:
        output_validator = lambda payload: validate_model_generated_item(
            payload, expected_item_id=item_id, allowed_reason_codes=allowed_reason_codes
        )

    config = _client_config_using_policy(client_config, retry_policy)
    attempts = 0
    retry_count = 0
    current_user = package.user
    last_messages: list[str] = []
    last_code: ErrorCode | None = None
    last_raw: str | None = None

    while True:
        attempts += 1
        try:
            raw = llm_call(
                package.system,
                current_user,
                model,
                package.output_schema,
                client_config=config,
                generation=generation,
                http_client=http_client,
            )
            last_raw = raw
        except LLMRequestRejectedError as exc:
            return JudgmentRunResult(
                raw_response=None,
                parsed_output=None,
                attempts=attempts,
                retry_count=retry_count,
                request_error_status=exc.status_code,
                request_error_message=str(exc),
            )
        except LLMCallError as exc:
            last_code = exc.code
            last_messages = [str(exc)]
            next_retry = retry_count + 1
            if retry_policy.should_retry(exc.code, next_retry):
                retry_count = next_retry
                if retry_policy.backoff_seconds > 0:
                    sleeper(retry_policy.backoff_seconds)
                current_user = build_retry_user_prompt(
                    package.user, error_code=exc.code.value, attempt=retry_count
                )
                continue
            return JudgmentRunResult(
                raw_response=None,
                parsed_output=None,
                attempts=attempts,
                retry_count=retry_count,
                final_error_code=exc.code.value,
                validation_messages=tuple(last_messages),
            )

        parsed, parse_messages = _parse_json(raw)
        if parsed is None:
            last_code = ErrorCode.JSON_PARSE_FAILED
            last_messages = parse_messages
        else:
            validation_messages = output_validator(parsed)
            if not validation_messages:
                return JudgmentRunResult(
                    raw_response=raw,
                    parsed_output=parsed,
                    attempts=attempts,
                    retry_count=retry_count,
                )
            last_code = ErrorCode.SCHEMA_INVALID
            last_messages = validation_messages

        next_retry = retry_count + 1
        if retry_policy.should_retry(last_code, next_retry):
            retry_count = next_retry
            if retry_policy.backoff_seconds > 0:
                sleeper(retry_policy.backoff_seconds)
            current_user = build_retry_user_prompt(
                package.user, error_code=last_code.value, attempt=retry_count
            )
            continue

        return JudgmentRunResult(
            raw_response=last_raw,
            parsed_output=None,
            attempts=attempts,
            retry_count=retry_count,
            final_error_code=last_code.value,
            validation_messages=tuple(last_messages),
        )
