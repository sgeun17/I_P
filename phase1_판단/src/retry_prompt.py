"""Validator/호출 오류 후 1회 재출력용 Prompt Builder.

정책 원본은 review_policy.DEFAULT_RETRY_POLICY와 docs/error_codes_v0.1.md다.
실제 실행에서 확인한 구조(E501/E504)·인용(E403/E404) 생성 오류는 한 번만
새로 생성하게 하고, E505 같은 판단 경계 문제는 기존대로 Human Review로 보낸다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from enums import ErrorCode
from models import MappingInput, ValidationIssue
from prompts import PromptPackage, build_prompt_package
from review_policy import DEFAULT_RETRY_POLICY, RetryPolicy


class RetryPromptError(ValueError):
    pass


@dataclass(frozen=True)
class RetryContext:
    attempt: int
    error_codes: tuple[ErrorCode, ...]


def _normalize_issues(issues: Iterable[ValidationIssue | ErrorCode]) -> list[dict[str, str | None]]:
    out: list[dict[str, str | None]] = []
    for item in issues:
        if isinstance(item, ErrorCode):
            out.append({"code": item.value, "message": item.name, "field": None})
        elif isinstance(item, ValidationIssue):
            out.append(
                {
                    "code": item.code.value,
                    "message": item.message,
                    "field": item.field,
                }
            )
        else:
            raise RetryPromptError(f"unsupported retry issue type: {type(item)!r}")
    if not out:
        raise RetryPromptError("at least one retry issue is required")
    return out


def build_retry_package(
    mapping_input: MappingInput,
    issues: Iterable[ValidationIssue | ErrorCode],
    *,
    attempt: int = 1,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> PromptPackage:
    """같은 증적을 '새 JSON을 처음부터 재생성'하도록 하는 재출력 패키지.

    previous raw response를 다시 넣지 않는다. 깨진 JSON을 부분 수정하게 하기보다 원래 evidence/candidates와
    오류 코드만 주고 새 출력을 만들게 해서 잘못된 필드·hallucination을 복제하는 위험을 줄인다.
    """
    normalized = _normalize_issues(issues)
    codes = [ErrorCode(row["code"]) for row in normalized]

    not_retryable = [c for c in codes if not policy.should_retry(c, attempt)]
    if not_retryable:
        raise RetryPromptError(
            "retry is not allowed by policy for: " + ", ".join(c.value for c in not_retryable)
        )

    base = build_prompt_package(mapping_input)
    retry_context = json.dumps(
        {
            "attempt": attempt,
            "max_retries": policy.max_retries,
            "issues": normalized,
        },
        ensure_ascii=False,
        indent=2,
    )

    repair_rules = []
    code_set = set(codes)
    if code_set & {ErrorCode.PRIMARY_COUNT_INVALID, ErrorCode.MATCHED_WITHOUT_CONTROLS}:
        repair_rules.append(
            "- candidate_decisions의 RELATED를 다시 센다. 0개면 mapped_controls=[], "
            "1개면 그 항목을 PRIMARY로, 2개 이상이면 모두 넣고 PRIMARY를 정확히 1개 지정한다."
        )
    if ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE in code_set:
        repair_rules.append(
            "- quote를 요약하거나 띄어쓰기를 고치지 말고, 지정한 chunk.text에서 연속된 문자열을 그대로 복사한다."
        )
    if ErrorCode.CITATION_PAGE_MISMATCH in code_set:
        repair_rules.append(
            "- citation.page는 그 citation.chunk_id의 page_start~page_end 범위 안에서만 고른다. "
            "페이지가 null인 청크에는 null을 쓴다."
        )
    repair_text = "\n".join(repair_rules)

    user = base.user + f"""

<retry_context>
{retry_context}
</retry_context>

직전 시도는 위 오류 때문에 유효한 판단 결과로 사용할 수 없었다.
직전 출력을 부분 수정하거나 그대로 반복하지 말고, evidence와 candidates를 처음부터 다시 읽어
새 JSON 객체 하나를 생성하라.
- output_schema와 enum을 다시 확인한다.
- 필수 판단 절차를 생략하지 않는다.
- JSON 외의 설명/코드블록을 출력하지 않는다.
{repair_text}
"""

    return PromptPackage(
        prompt_version=base.prompt_version,
        ruleset_version=base.ruleset_version,
        system=base.system,
        user=user,
        output_schema=base.output_schema,
    )
