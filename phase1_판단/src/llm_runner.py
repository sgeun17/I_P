"""Phase 1 LLM 호출 + Retry Prompt 실행 오케스트레이션.

기존 `RetryPolicy`와 `retry_prompt.py`를 실제 Ollama 호출에 연결한다.
- 최초 호출 1회
- E101~E105 또는 E201/E202 중 정책이 허용하는 오류만 재출력 Prompt로 1회 재시도
- 재시도 후 호출 계층이 다시 실패하면 E106
- 형식 파싱 뒤 발견되는 E403/E404/E501/E504도 정책에 따라 한 번 교정 재시도한다

Validator/Human Review 로직 자체는 수정하지 않는다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from enums import ErrorCode
from llm_client import LLMCallError, LLMRequestRejectedError, call_llm
from llm_config import ContextBudgetConfig, GenerationConfig, LLMClientConfig, TokenCounter
from models import MappingInput, ValidationIssue
from output_parser import extract_json_text, parse_llm_output
from prompts import PromptPackage, build_prompt_package
from retry_prompt import RetryPromptError, build_retry_package
from review_policy import DEFAULT_RETRY_POLICY, RetryPolicy
from validators import validate


@dataclass(frozen=True)
class LLMRunResult:
    raw_response: str | None
    call_error: ErrorCode | None
    attempts: int
    retry_count: int
    # match_status 파생 전, provider가 실제로 반환한 content. 실험/감사용.
    provider_raw_response: str | None = None
    last_issue_codes: tuple[ErrorCode, ...] = ()
    request_error_status: int | None = None
    request_error_message: str | None = None

    @property
    def transport_succeeded(self) -> bool:
        return self.raw_response is not None and self.call_error is None

    @property
    def has_unmapped_request_error(self) -> bool:
        return self.request_error_status is not None


def _retryable_issues(
    issues: list[ValidationIssue],
    *,
    policy: RetryPolicy,
    retry_number: int,
) -> bool:
    return bool(issues) and all(policy.should_retry(i.code, retry_number) for i in issues)


def _inject_derived_match_status(raw: str) -> str:
    """candidate_decisions에서 match_status를 결정해 최종 계약에 채운다.

    match_status는 모델 판단값이 아니라 파생값이다.
      - RELATED가 하나라도 있으면 MATCHED
      - RELATED가 하나도 없으면 NO_MATCH

    JSON 자체가 깨졌거나 candidate_decisions가 배열이 아니면 손대지 않고
    기존 Parser가 정상적으로 오류를 보고하게 한다.
    """
    json_text = extract_json_text(raw)
    if json_text is None:
        return raw

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return raw

    if not isinstance(data, dict):
        return raw

    decisions = data.get("candidate_decisions")
    if not isinstance(decisions, list):
        return raw

    has_related = any(
        isinstance(item, dict) and item.get("decision") == "RELATED"
        for item in decisions
    )
    data["match_status"] = "MATCHED" if has_related else "NO_MATCH"
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def run_mapping_llm(
    mapping_input: MappingInput,
    *,
    model: str,
    client_config: LLMClientConfig | None = None,
    generation: GenerationConfig | None = None,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    http_client: httpx.Client | None = None,
    context_budget: ContextBudgetConfig | None = None,
    token_counter: TokenCounter | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> LLMRunResult:
    """실제 LLM 호출과 프로젝트 재시도 정책을 연결한다.

    성공 시 raw JSON 문자열을 반환한다. 파싱/스키마 오류가 정책상 재시도 대상이면 기존
    `build_retry_package()`로 오류 코드를 포함한 새 Prompt를 만든 뒤 한 번 더 호출한다.

    HTTP 4xx는 현재 공통 ErrorCode에 전용 코드가 없어 `request_error_*`로 별도 반환한다.
    이 경우 E103으로 잘못 분류하거나 자동 재시도하지 않는다.
    """
    package: PromptPackage = build_prompt_package(mapping_input)
    attempts = 0
    retry_count = 0
    last_codes: tuple[ErrorCode, ...] = ()

    while True:
        attempts += 1
        try:
            raw = call_llm(
                package.system,
                package.user,
                model,
                package.output_schema,
                client_config=client_config,
                generation=generation,
                http_client=http_client,
                context_budget=context_budget,
                token_counter=token_counter,
            )
        except LLMRequestRejectedError as exc:
            return LLMRunResult(
                raw_response=None,
                call_error=None,
                attempts=attempts,
                retry_count=retry_count,
                last_issue_codes=last_codes,
                request_error_status=exc.status_code,
                request_error_message=str(exc),
            )
        except LLMCallError as exc:
            last_codes = (exc.code,)
            next_retry_number = retry_count + 1
            if retry_policy.should_retry(exc.code, next_retry_number):
                try:
                    package = build_retry_package(
                        mapping_input,
                        [exc.code],
                        attempt=next_retry_number,
                        policy=retry_policy,
                    )
                except RetryPromptError:
                    return LLMRunResult(
                        raw_response=None,
                        call_error=exc.code,
                        attempts=attempts,
                        retry_count=retry_count,
                        last_issue_codes=last_codes,
                    )
                retry_count += 1
                if retry_policy.backoff_seconds > 0:
                    sleeper(retry_policy.backoff_seconds)
                continue

            final_code = (
                ErrorCode.LLM_RETRY_EXHAUSTED
                if retry_count > 0
                else exc.code
            )
            return LLMRunResult(
                raw_response=None,
                call_error=final_code,
                attempts=attempts,
                retry_count=retry_count,
                last_issue_codes=last_codes,
            )

        # match_status는 모델에게 생성시키지 않는다. candidate_decisions를 기준으로
        # 런타임에서 결정한 뒤 기존 Parser/Validator 계약에 맞춘다.
        normalized_raw = _inject_derived_match_status(raw)
        output, parse_issues = parse_llm_output(normalized_raw)
        if output is not None:
            # Pydantic 형식만 맞는다고 성공으로 끝내면 MATCHED+빈 매핑,
            # PRIMARY 누락, 변형 인용처럼 실제 결과로 조립할 수 없는 응답은
            # 재시도 기회를 잃는다. 전체 Validator를 여기서도 실행하되,
            # 정책이 허용한 교정 가능 오류만 골라 한 번 다시 생성한다.
            validation = validate(output, mapping_input)
            last_codes = tuple(issue.code for issue in validation.issues)
            next_retry_number = retry_count + 1
            repairable = [
                issue for issue in validation.issues
                if retry_policy.should_retry(issue.code, next_retry_number)
            ]
            if repairable:
                try:
                    package = build_retry_package(
                        mapping_input,
                        repairable,
                        attempt=next_retry_number,
                        policy=retry_policy,
                    )
                except RetryPromptError:
                    pass
                else:
                    retry_count += 1
                    if retry_policy.backoff_seconds > 0:
                        sleeper(retry_policy.backoff_seconds)
                    continue
            return LLMRunResult(
                raw_response=normalized_raw,
                call_error=None,
                attempts=attempts,
                retry_count=retry_count,
                provider_raw_response=raw,
                last_issue_codes=last_codes,
            )

        last_codes = tuple(issue.code for issue in parse_issues)
        next_retry_number = retry_count + 1
        if _retryable_issues(
            parse_issues,
            policy=retry_policy,
            retry_number=next_retry_number,
        ):
            try:
                package = build_retry_package(
                    mapping_input,
                    parse_issues,
                    attempt=next_retry_number,
                    policy=retry_policy,
                )
            except RetryPromptError:
                pass
            else:
                retry_count += 1
                if retry_policy.backoff_seconds > 0:
                    sleeper(retry_policy.backoff_seconds)
                continue

        # E203~E205 등 정책상 재시도하지 않는 출력 오류는 match_status 파생만 적용한 응답을 넘긴다.
        return LLMRunResult(
            raw_response=normalized_raw,
            call_error=None,
            attempts=attempts,
            retry_count=retry_count,
            provider_raw_response=raw,
            last_issue_codes=last_codes,
        )
