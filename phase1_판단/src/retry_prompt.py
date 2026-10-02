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
from models import LLMMappingOutput, MappingInput, ValidationIssue
from prompts import PromptPackage, build_prompt_package
from review_policy import DEFAULT_RETRY_POLICY, RetryPolicy


class RetryPromptError(ValueError):
    pass


@dataclass(frozen=True)
class RetryContext:
    attempt: int
    error_codes: tuple[ErrorCode, ...]


def _normalize_issues(
    issues: Iterable[ValidationIssue | ErrorCode],
) -> list[dict[str, object | None]]:
    """Retry Context에 Validator가 이미 알고 있는 위치 정보를 보존한다.

    E403/E404를 고치려면 단순 오류 코드뿐 아니라 어떤 control/chunk에서
    실패했는지가 필요하다. ValidationIssue의 control_id/chunk_id를 버리지 않는다.
    """
    out: list[dict[str, object | None]] = []
    for item in issues:
        if isinstance(item, ErrorCode):
            out.append(
                {
                    "code": item.value,
                    "message": item.name,
                    "field": None,
                    "control_id": None,
                    "chunk_id": None,
                }
            )
        elif isinstance(item, ValidationIssue):
            out.append(
                {
                    "code": item.code.value,
                    "message": item.message,
                    "field": item.field,
                    "control_id": item.control_id,
                    "chunk_id": item.chunk_id,
                }
            )
        else:
            raise RetryPromptError(f"unsupported retry issue type: {type(item)!r}")
    if not out:
        raise RetryPromptError("at least one retry issue is required")
    return out


def _build_repair_details(
    failed_output: LLMMappingOutput | None,
    normalized_issues: list[dict[str, object | None]],
) -> dict[str, object]:
    """재출력에 필요한 실패한 실제 값만 추려서 만든다.

    이전 응답 전체를 다시 넣으면 모델이 잘못된 응답을 그대로 베낄 수 있다.
    반대로 E403/E404의 코드와 메시지만 주면 어느 quote/page가 실패했는지 알기
    어렵다. 따라서 구조 오류에는 ID 요약을, 인용 오류에는 해당 citation 값만
    최소한으로 전달한다.
    """
    if failed_output is None:
        return {}

    issue_codes = {row["code"] for row in normalized_issues}
    details: dict[str, object] = {}

    if issue_codes & {
        ErrorCode.PRIMARY_COUNT_INVALID.value,
        ErrorCode.MATCHED_WITHOUT_CONTROLS.value,
    }:
        details["mapping_consistency"] = {
            "related_control_ids": [
                decision.control_id
                for decision in failed_output.candidate_decisions
                if decision.decision.value == "RELATED"
            ],
            "mapped_control_ids": [
                mapped.control_id for mapped in failed_output.mapped_controls
            ],
            "primary_control_ids": [
                mapped.control_id
                for mapped in failed_output.mapped_controls
                if mapped.relation.value == "PRIMARY"
            ],
        }

    citation_issues = [
        row
        for row in normalized_issues
        if row["code"]
        in {
            ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE.value,
            ErrorCode.CITATION_PAGE_MISMATCH.value,
        }
    ]
    if citation_issues:
        failures: list[dict[str, object | None]] = []
        seen: set[tuple[object, ...]] = set()

        def collect(where: str, control_id: str, citations) -> None:
            for citation in citations:
                for issue in citation_issues:
                    issue_control = issue.get("control_id")
                    issue_chunk = issue.get("chunk_id")
                    if issue_control is not None and issue_control != control_id:
                        continue
                    if issue_chunk is not None and issue_chunk != citation.chunk_id:
                        continue

                    key = (
                        issue["code"],
                        control_id,
                        citation.chunk_id,
                        citation.page,
                        citation.quote,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    failures.append(
                        {
                            "code": issue["code"],
                            "where": where,
                            "control_id": control_id,
                            "chunk_id": citation.chunk_id,
                            "failed_page": citation.page,
                            "failed_quote": citation.quote,
                        }
                    )

        for decision in failed_output.candidate_decisions:
            collect("candidate_decisions", decision.control_id, decision.citations)
        for mapped in failed_output.mapped_controls:
            collect("mapped_controls", mapped.control_id, mapped.citations)

        if failures:
            details["citation_failures"] = failures

    return details


def build_retry_package(
    mapping_input: MappingInput,
    issues: Iterable[ValidationIssue | ErrorCode],
    *,
    attempt: int = 1,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    failed_output: LLMMappingOutput | None = None,
) -> PromptPackage:
    """같은 증적을 '새 JSON을 처음부터 재생성'하도록 하는 재출력 패키지.

    previous raw response 전체를 다시 넣지 않는다. 깨진 JSON을 부분 수정하게 하기보다
    원래 evidence/candidates와 Validator가 특정한 오류 위치·최소 실패값만 주고 새
    출력을 만들게 해서 잘못된 필드·hallucination을 복제하는 위험을 줄인다.
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

    repair_details = _build_repair_details(failed_output, normalized)
    repair_block = ""
    if repair_details:
        repair_block = (
            "\n<repair_details>\n"
            + json.dumps(repair_details, ensure_ascii=False, indent=2)
            + "\n</repair_details>\n"
        )

    repair_rules = []
    code_set = set(codes)
    if code_set & {ErrorCode.PRIMARY_COUNT_INVALID, ErrorCode.MATCHED_WITHOUT_CONTROLS}:
        repair_rules.append(
            "- repair_details.mapping_consistency를 확인하고 candidate_decisions의 RELATED를 다시 센다. "
            "0개면 mapped_controls=[], 1개면 그 항목을 PRIMARY로, 2개 이상이면 RELATED인 control_id를 "
            "모두 mapped_controls에 넣고 PRIMARY를 정확히 1개 지정한다."
        )
    if ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE in code_set:
        repair_rules.append(
            "- repair_details.citation_failures의 failed_quote와 chunk_id를 확인한다. failed_quote를 "
            "비슷한 문장으로 자동 보정하지 말고, 해당 chunk.text 안에 실제로 연속해서 존재하는 문자열을 "
            "새로 골라 그대로 복사한다. 요약·의역·단어 순서 변경·문장 재구성·공백 임의 변경을 하지 않는다. "
            "유효한 원문을 찾을 수 없으면 인용을 지어내지 말고 관련성 판단 자체를 다시 검토한다."
        )
    if ErrorCode.CITATION_PAGE_MISMATCH in code_set:
        repair_rules.append(
            "- repair_details.citation_failures의 chunk_id와 failed_page를 확인한다. citation.page는 해당 "
            "chunk_id의 page_start~page_end 범위 안에서만 고른다. 페이지가 null인 청크에는 null을 쓴다."
        )
    repair_text = "\n".join(repair_rules)

    user = base.user + f"""

<retry_context>
{retry_context}
</retry_context>
{repair_block}
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
