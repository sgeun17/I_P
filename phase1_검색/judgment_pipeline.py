"""검색 결과를 판단팀의 실제 LLM 호출·검증 흐름에 전달한다.

검색 모델과 LLM 서버의 생명주기는 각각의 기존 구현에 맡긴다. 이 함수는
원본 검색 결과를 수정하거나 LLM 원문을 파일에 기록하지 않는다.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from judgment_adapter import JUDGMENT, to_mapping_input


# 검색팀 환경에 판단팀을 별도 패키지로 설치하지 않아도 원본 구현을 재사용한다.
_JUDGMENT_SRC = str(JUDGMENT / "src")
if _JUDGMENT_SRC not in sys.path:
    sys.path.insert(0, _JUDGMENT_SRC)

from enums import EvidenceStatus  # noqa: E402
from llm_config import ContextBudgetConfig, GenerationConfig, LLMClientConfig, TokenCounter  # noqa: E402
from llm_runner import LLMRunResult, run_mapping_llm  # noqa: E402
from models import MappingInput, Phase1MappingResult  # noqa: E402
from review_policy import DEFAULT_RETRY_POLICY, RetryPolicy  # noqa: E402
from service import build_result, to_evidence_status  # noqa: E402
from versions import build_version_info  # noqa: E402


@dataclass(frozen=True)
class JudgmentPipelineResult:
    mapping_result: Phase1MappingResult
    evidence_status: EvidenceStatus
    llm_run: LLMRunResult
    elapsed_ms: int


class JudgmentRequestError(RuntimeError):
    """공통 ErrorCode가 없는 HTTP 4xx 요청 오류를 원인 그대로 전달한다."""

    def __init__(self, run_result: LLMRunResult) -> None:
        self.status_code = run_result.request_error_status
        self.run_result = run_result
        super().__init__(run_result.request_error_message or f"LLM request rejected: HTTP {self.status_code}")


def run_judgment(
    search_result: dict[str, Any],
    *,
    model: str,
    client_config: LLMClientConfig | None = None,
    generation: GenerationConfig | None = None,
    context_budget: ContextBudgetConfig | None = None,
    token_counter: TokenCounter | None = None,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    http_client: httpx.Client | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    trace_id: str | None = None,
) -> JudgmentPipelineResult:
    """검증된 검색 출력 → MappingInput → LLM → 최종 판단 결과.

    HTTP 4xx는 현재 판단팀 공통 오류코드가 없으므로 예외로 중단한다.
    그 밖의 호출·파싱 실패는 판단팀 ``build_result``의 FAILED/검토 규칙을 따른다.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be an explicit non-empty runtime model identifier")

    started = time.perf_counter()
    mapping_input = MappingInput.model_validate(to_mapping_input(search_result))
    model_name = model.strip()
    versions = build_version_info(mapping_input, model_name=model_name)
    run = run_mapping_llm(
        mapping_input,
        model=model_name,
        client_config=client_config,
        generation=generation,
        retry_policy=retry_policy,
        http_client=http_client,
        context_budget=context_budget,
        token_counter=token_counter,
        sleeper=sleeper,
    )
    if run.has_unmapped_request_error:
        raise JudgmentRequestError(run)

    mapping_result = build_result(
        run.raw_response,
        mapping_input,
        versions,
        call_error=run.call_error,
        trace_id=trace_id,
    )
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    mapping_result = mapping_result.model_copy(update={"processing_time_ms": elapsed_ms})
    return JudgmentPipelineResult(
        mapping_result=mapping_result,
        evidence_status=to_evidence_status(mapping_result),
        llm_run=run,
        elapsed_ms=elapsed_ms,
    )
