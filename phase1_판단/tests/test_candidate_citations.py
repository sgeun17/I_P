"""CandidateDecision 인용도 최종 매핑 인용과 같은 출처 계약으로 검증한다."""

import json

import pytest

from conftest import GOOD_RESPONSE
from enums import ErrorCode, EvidenceStatus, ProcessingStatus, ReviewReason
from output_parser import parse_llm_output
from service import build_result, to_evidence_status
from validators import validate


def _response():
    # 두 인용 목록은 JSON을 별도로 파싱한 객체여야 한다. 한쪽 변경이 다른 쪽에
    # 전파되면 후보 인용만 잘못된 회귀 사례를 재현할 수 없다.
    data = json.loads(GOOD_RESPONSE)
    assert data["candidate_decisions"][0]["citations"][0] is not data["mapped_controls"][0]["citations"][0]
    return data


def _validate(data, mapping_input):
    output, parse_issues = parse_llm_output(json.dumps(data, ensure_ascii=False))
    assert output is not None and not parse_issues
    return validate(output, mapping_input)


def _assert_reviewed_for_citation(data, mapping_input, versions, code, reason):
    validation = _validate(data, mapping_input)
    result = build_result(json.dumps(data, ensure_ascii=False), mapping_input, versions)

    assert not validation.passed and not validation.citations_valid
    assert code in [issue.code for issue in validation.issues]
    assert result.processing_status == ProcessingStatus.COMPLETED
    assert not result.validation.passed and not result.validation.citations_valid
    assert code in [issue.code for issue in result.validation.issues]
    assert result.human_review.required
    assert reason in result.human_review.reasons
    assert to_evidence_status(result) == EvidenceStatus.REVIEW_REQUIRED
    return result


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"chunk_id": "E0001_v1_c9999"}, ErrorCode.CITATION_CHUNK_NOT_FOUND),
        ({"chunk_id": "E0001_v2_c0000"}, ErrorCode.CITATION_FOREIGN_EVIDENCE),
        ({"chunk_id": "E9999_v1_c0000"}, ErrorCode.CITATION_FOREIGN_EVIDENCE),
        ({"quote": "원문에 없는 조작된 문장"}, ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE),
        ({"page": 99}, ErrorCode.CITATION_PAGE_MISMATCH),
        ({"quote": "   "}, ErrorCode.CITATION_EMPTY_QUOTE),
    ],
)
def test_related_candidate_only_invalid_citation_is_blocking(mapping_input, versions, change, expected):
    data = _response()
    mapped_citation = data["mapped_controls"][0]["citations"][0].copy()
    data["candidate_decisions"][0]["citations"][0].update(change)

    result = _assert_reviewed_for_citation(
        data, mapping_input, versions, expected, ReviewReason.CITATION_INVALID
    )
    assert data["mapped_controls"][0]["citations"][0] == mapped_citation
    assert result.mapped_controls[0].citations[0].model_dump() == mapped_citation


def test_related_candidate_without_citation_still_requires_review(mapping_input, versions):
    data = _response()
    mapped_citation = data["mapped_controls"][0]["citations"][0].copy()
    data["candidate_decisions"][0]["citations"] = []

    result = _assert_reviewed_for_citation(
        data, mapping_input, versions, ErrorCode.CITATION_MISSING, ReviewReason.CITATION_MISSING
    )
    assert result.mapped_controls[0].citations[0].model_dump() == mapped_citation


@pytest.mark.parametrize("decision", ["NOT_RELATED", "UNCERTAIN"])
def test_optional_candidate_citation_is_checked_when_supplied(mapping_input, versions, decision):
    data = _response()
    candidate = data["candidate_decisions"][1]
    candidate["decision"] = decision
    candidate["citations"] = [
        {"chunk_id": "E0001_v1_c9999", "page": 1, "quote": "원문에 없는 문장"}
    ]

    result = _assert_reviewed_for_citation(
        data,
        mapping_input,
        versions,
        ErrorCode.CITATION_CHUNK_NOT_FOUND,
        ReviewReason.CITATION_INVALID,
    )
    assert result.mapped_controls[0].citations[0].chunk_id == "E0001_v1_c0000"


def test_candidate_only_missing_page_is_nonblocking_warning(mapping_input, versions):
    data = _response()
    data["candidate_decisions"][0]["citations"][0]["page"] = None

    validation = _validate(data, mapping_input)
    result = build_result(json.dumps(data, ensure_ascii=False), mapping_input, versions)
    assert validation.passed and validation.citations_valid
    assert [warning.code for warning in validation.warnings].count(ErrorCode.CITATION_PAGE_MISSING) == 1
    assert result.validation.passed and result.validation.citations_valid
    assert [warning.code for warning in result.validation.warnings].count(ErrorCode.CITATION_PAGE_MISSING) == 1
    assert not result.human_review.required
    assert to_evidence_status(result) == EvidenceStatus.COMPLETED


def test_same_bad_citation_in_both_lists_produces_one_issue(mapping_input, versions):
    data = _response()
    for row in (data["candidate_decisions"][0], data["mapped_controls"][0]):
        row["citations"][0]["quote"] = "원문에 없는 조작된 문장"

    result = _assert_reviewed_for_citation(
        data,
        mapping_input,
        versions,
        ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE,
        ReviewReason.CITATION_INVALID,
    )
    assert [issue.code for issue in result.validation.issues].count(
        ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE
    ) == 1


def test_same_missing_page_in_both_lists_produces_one_warning(mapping_input, versions):
    data = _response()
    for row in (data["candidate_decisions"][0], data["mapped_controls"][0]):
        row["citations"][0]["page"] = None

    validation = _validate(data, mapping_input)
    result = build_result(json.dumps(data, ensure_ascii=False), mapping_input, versions)
    assert validation.passed and validation.citations_valid
    assert [warning.code for warning in validation.warnings].count(ErrorCode.CITATION_PAGE_MISSING) == 1
    assert result.validation.passed and result.validation.citations_valid
    assert [warning.code for warning in result.validation.warnings].count(ErrorCode.CITATION_PAGE_MISSING) == 1
    assert not result.human_review.required


@pytest.mark.parametrize("invalid_side", ["candidate_decisions", "mapped_controls"])
def test_same_quote_and_chunk_with_different_pages_is_not_deduplicated(
    mapping_input, versions, invalid_side
):
    data = _response()
    data[invalid_side][0]["citations"][0]["page"] = 99
    candidate_citation = data["candidate_decisions"][0]["citations"][0]
    mapped_citation = data["mapped_controls"][0]["citations"][0]
    assert candidate_citation["chunk_id"] == mapped_citation["chunk_id"]
    assert candidate_citation["quote"] == mapped_citation["quote"]
    assert candidate_citation["page"] != mapped_citation["page"]

    result = _assert_reviewed_for_citation(
        data, mapping_input, versions, ErrorCode.CITATION_PAGE_MISMATCH, ReviewReason.CITATION_INVALID
    )
    assert [issue.code for issue in result.validation.issues].count(
        ErrorCode.CITATION_PAGE_MISMATCH
    ) == 1
