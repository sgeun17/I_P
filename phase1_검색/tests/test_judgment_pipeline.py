"""저장된 검색 출력과 가짜 HTTP로 검색→판단 호출 경계를 검증한다.

고정 응답의 전달/오류 처리 시험이며 실제 LLM 추론 품질 평가는 아니다.
"""

import json
from copy import deepcopy
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from judgment_adapter import MappingAdapterError  # noqa: E402
from judgment_pipeline import JudgmentRequestError, run_judgment  # noqa: E402
from enums import ErrorCode, EvidenceStatus, MatchStatus, ProcessingStatus, ReviewReason  # noqa: E402
from llm_client import ContextBudgetExceededError  # noqa: E402
from llm_config import ContextBudgetConfig, GenerationConfig, LLMClientConfig  # noqa: E402
from models import SCHEMA_VERSION  # noqa: E402
from prompts import PROMPT_VERSION  # noqa: E402
from review_policy import RetryPolicy  # noqa: E402


def _search(name):
    path = ROOT / "reports/file_pipeline_2026-09-24" / f"{name}_output.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _response(name):
    path = ROOT / "reports/judgment_integration_2026-09-24" / f"{name}_fixed_response.json"
    return json.loads(path.read_text(encoding="utf-8"))["raw_response"]


def _completion(content):
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }


def _run(search_result, client, **kwargs):
    options = {
        "client_config": LLMClientConfig(),
        "generation": GenerationConfig(),
        "http_client": client,
        "retry_policy": RetryPolicy(max_retries=1, timeout_seconds=60, backoff_seconds=0),
        "sleeper": lambda _: None,
    }
    options.update(kwargs)
    return run_judgment(search_result, model="MOCK_ONLY", **options)


@pytest.mark.parametrize(
    ("search_name", "response_name", "status", "mapped_count"),
    [
        ("UTF8", "single", MatchStatus.MATCHED, 1),
        ("DOCX", "multiple", MatchStatus.MATCHED, 2),
        ("UNRELATED", "no_match", MatchStatus.NO_MATCH, 0),
    ],
)
def test_saved_search_result_reaches_real_judgment_service(
    search_name, response_name, status, mapped_count
):
    search_result = _search(search_name)
    original = deepcopy(search_result)
    raw = _response(response_name)
    bodies = []

    def handler(request):
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(raw))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = _run(search_result, client, trace_id="mock-flow")

    result = outcome.mapping_result
    assert search_result == original
    assert len(bodies) == 1
    assert bodies[0]["model"] == "MOCK_ONLY"
    assert bodies[0]["stream"] is False
    assert bodies[0]["reasoning_effort"] == "none"
    assert bodies[0]["response_format"]["type"] == "json_schema"
    first_chunk = search_result["evidence_chunks"][0]
    assert first_chunk["chunk_id"] in bodies[0]["messages"][1]["content"]
    assert json.dumps(first_chunk["text"], ensure_ascii=False)[1:-1] in bodies[0]["messages"][1]["content"]
    assert result.processing_status == ProcessingStatus.COMPLETED
    assert result.match_status == status
    assert result.validation.passed
    assert len(result.mapped_controls) == mapped_count
    assert [d.control_id for d in result.candidate_decisions] == [
        c["control_id"] for c in search_result["retrieval"]["candidates"]
    ]
    assert result.evidence_id == search_result["evidence_id"]
    assert result.version == search_result["version"]
    assert result.trace_id == "mock-flow"
    assert result.versions.model_name == "MOCK_ONLY"
    assert result.versions.schema_version == SCHEMA_VERSION
    assert result.versions.prompt_version == PROMPT_VERSION
    assert result.versions.kb_sha256 == search_result["index"]["kb_sha256"]
    assert result.versions.embedding_model_revision is None
    assert outcome.llm_run.raw_response == raw
    assert outcome.llm_run.attempts == 1 and outcome.llm_run.retry_count == 0
    assert outcome.elapsed_ms >= 0 and result.processing_time_ms == outcome.elapsed_ms
    assert outcome.evidence_status == EvidenceStatus.REVIEW_REQUIRED
    for mapped in result.mapped_controls:
        expected = next(
            c["similarity_score"]
            for c in search_result["retrieval"]["candidates"]
            if c["control_id"] == mapped.control_id
        )
        assert mapped.similarity_score == expected


def test_server_error_retries_then_uses_successful_response():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json=_completion(_response("single")))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = _run(_search("UTF8"), client)

    assert len(bodies) == 2
    assert "<retry_context>" in bodies[1]["messages"][1]["content"]
    assert "E103" in bodies[1]["messages"][1]["content"]
    assert outcome.llm_run.attempts == 2 and outcome.llm_run.retry_count == 1
    assert outcome.llm_run.call_error is None
    assert outcome.mapping_result.validation.passed
    assert outcome.mapping_result.processing_status == ProcessingStatus.COMPLETED


def test_repeated_server_error_returns_existing_e106_failed_result():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="still unavailable")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = _run(_search("UTF8"), client)

    assert len(calls) == 2
    assert outcome.llm_run.call_error == ErrorCode.LLM_RETRY_EXHAUSTED
    assert outcome.llm_run.attempts == 2 and outcome.llm_run.retry_count == 1
    assert outcome.mapping_result.processing_status == ProcessingStatus.FAILED
    assert ErrorCode.LLM_RETRY_EXHAUSTED in [
        issue.code for issue in outcome.mapping_result.validation.issues
    ]
    assert ReviewReason.LLM_CALL_FAILED in outcome.mapping_result.human_review.reasons
    assert outcome.evidence_status == EvidenceStatus.FAILED


@pytest.mark.parametrize("second_content", ["valid", "still_invalid"])
def test_malformed_json_retry_preserves_service_result(second_content):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        content = "not-json" if len(bodies) == 1 or second_content == "still_invalid" else _response("single")
        return httpx.Response(200, json=_completion(content))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = _run(_search("UTF8"), client)

    assert len(bodies) == 2
    assert "E201" in bodies[1]["messages"][1]["content"]
    assert outcome.llm_run.attempts == 2 and outcome.llm_run.retry_count == 1
    if second_content == "valid":
        assert outcome.mapping_result.processing_status == ProcessingStatus.COMPLETED
        assert outcome.mapping_result.validation.passed
    else:
        assert outcome.llm_run.raw_response == "not-json"
        assert outcome.mapping_result.processing_status == ProcessingStatus.FAILED
        assert ErrorCode.JSON_PARSE_FAILED in [
            issue.code for issue in outcome.mapping_result.validation.issues
        ]
        assert ReviewReason.JSON_PARSE_FAILED in outcome.mapping_result.human_review.reasons
        assert outcome.evidence_status == EvidenceStatus.FAILED


def test_http_4xx_stops_without_misclassifying_empty_response():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, text="model not found")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(JudgmentRequestError) as error:
            _run(_search("UTF8"), client)

    assert len(calls) == 1
    assert error.value.status_code == 404
    assert error.value.run_result.request_error_status == 404
    assert error.value.run_result.raw_response is None
    assert error.value.run_result.call_error is None
    assert error.value.run_result.attempts == 1


def test_candidate_only_false_citation_goes_to_r104_review():
    raw = json.loads(_response("single"))
    selected = next(d for d in raw["candidate_decisions"] if d["decision"] == "RELATED")
    mapped_quote = raw["mapped_controls"][0]["citations"][0]["quote"]
    selected["citations"][0]["quote"] = "원문에 없는 허위 인용"

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=_completion(json.dumps(raw, ensure_ascii=False)))
        )
    ) as client:
        outcome = _run(_search("UTF8"), client)

    result = outcome.mapping_result
    assert result.processing_status == ProcessingStatus.COMPLETED
    assert not result.validation.passed and not result.validation.citations_valid
    assert ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE in [issue.code for issue in result.validation.issues]
    assert ReviewReason.CITATION_INVALID in result.human_review.reasons
    assert result.mapped_controls[0].citations[0].quote == mapped_quote
    assert outcome.evidence_status == EvidenceStatus.REVIEW_REQUIRED


def test_rejected_search_input_makes_no_http_request():
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("invalid search input must not reach HTTP")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MappingAdapterError):
            _run(_search("CORRUPTED"), client)

    assert not calls


def test_model_name_must_be_explicit_before_http():
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("missing model must not reach HTTP")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="model"):
            run_judgment(_search("UTF8"), model=" ", http_client=client)

    assert not calls


def test_context_budget_excess_is_raised_before_http():
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("context budget excess must not reach HTTP")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ContextBudgetExceededError):
            _run(
                _search("UTF8"),
                client,
                generation=GenerationConfig(max_tokens=32),
                context_budget=ContextBudgetConfig(context_window=32, safety_margin_tokens=0),
                token_counter=lambda _: 1,
            )

    assert not calls
