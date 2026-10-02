"""Phase 2 판단 결과의 citation을 원문 컨텍스트와 대조한다.

공식 phase2-output-0.2의 문항 결과와 실제로 LLM에 제공한 컨텍스트를 받는다.
원문/페이지 오류는 Phase 1 E4xx, Phase 2 판정 규칙은 P2Exxx로 반환한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CitationIssue:
    code: str
    message: str
    citation_index: int | None = None
    chunk_id: str | None = None
    severity: str = "error"


@dataclass(frozen=True)
class CitationValidation:
    issues: tuple[CitationIssue, ...]
    warnings: tuple[CitationIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


def normalize(text: str) -> str:
    """연속 공백만 합친다. 의미 유사도나 구두점 보정은 하지 않는다."""
    return " ".join((text or "").split())


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _covers_page(chunk: Any, page: int) -> bool:
    start = _value(chunk, "page_start")
    end = _value(chunk, "page_end")
    if start is None and end is None:
        return False
    if start is None or end is None:
        return False
    return start <= page <= end


def validate_citations(
    output: Any,
    context: Any,
    *,
    expected_evidence_id: str | None = None,
    expected_version: int | None = None,
) -> CitationValidation:
    """Phase 2 출력의 인용을 검사한다.

    - MET/NOT_MET은 인용이 하나 이상 필요하다.
    - UNKNOWN은 무인용이 정상이며, 인용이 있으면 그 인용 자체는 검사한다.
    - 제공된 ``evidence``/``context`` 역할의 청크만 인용할 수 있다.
    - ``expected_evidence_id``가 있으면 다른 증적·버전 인용을 먼저 차단한다.
    - 페이지형 청크에서 page 누락은 Phase 1과 같이 비차단 경고다.
    """
    issues: list[CitationIssue] = []
    warnings: list[CitationIssue] = []

    chunks = _value(context, "chunks", []) or []
    chunk_map = {
        _value(chunk, "chunk_id"): chunk
        for chunk in chunks
        if isinstance(_value(chunk, "chunk_id"), str)
    }
    if len(chunk_map) != len(chunks):
        return CitationValidation((CitationIssue("P2E007", "중복 또는 잘못된 chunk_id"),), ())
    result = _value(output, "result")
    citations = _value(output, "citations", [])

    if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
        return CitationValidation(
            issues=(CitationIssue("E202", "citations가 배열이 아니다"),),
            warnings=(),
        )

    if result in {"MET", "NOT_MET"} and not citations:
        issues.append(CitationIssue("P2E501", "MET/NOT_MET 판정에 인용이 없다"))

    expected_prefix = f"{expected_evidence_id}_v" if expected_evidence_id else None
    if expected_evidence_id and expected_version is not None:
        expected_prefix = f"{expected_evidence_id}_v{expected_version}_c"
    for index, citation in enumerate(citations):
        if not isinstance(citation, Mapping):
            issues.append(CitationIssue("E202", "인용이 객체가 아니다", index))
            continue

        chunk_id = citation.get("chunk_id")
        quote = citation.get("quote")
        page = citation.get("page")

        if not isinstance(chunk_id, str) or not chunk_id:
            issues.append(CitationIssue("E402", "chunk_id가 비어 있다", index))
            continue
        if expected_prefix and not chunk_id.startswith(expected_prefix):
            issues.append(
                CitationIssue(
                    "E406",
                    f"다른 증적·버전의 청크를 인용했다 (기대 접두사 {expected_prefix})",
                    index,
                    chunk_id,
                )
            )
            continue

        chunk = chunk_map.get(chunk_id)
        if chunk is None:
            issues.append(
                CitationIssue("E402", "제공된 context에 없는 chunk_id다", index, chunk_id)
            )
            continue

        role = _value(chunk, "role")
        if role not in {"evidence", "context"}:
            issues.append(
                CitationIssue(
                    "P2E004",
                    "청크 role은 evidence 또는 context여야 한다",
                    index,
                    chunk_id,
                )
            )

        if not isinstance(quote, str) or not normalize(quote):
            issues.append(CitationIssue("E405", "인용문이 비어 있다", index, chunk_id))
            continue

        page_start = _value(chunk, "page_start")
        page_end = _value(chunk, "page_end")
        if page is None and page_start is not None:
            warnings.append(
                CitationIssue(
                    "E407",
                    "페이지형 청크를 인용했지만 page가 null이다",
                    index,
                    chunk_id,
                    severity="warning",
                )
            )
        elif page is not None and (
            not isinstance(page, int) or isinstance(page, bool) or not _covers_page(chunk, page)
        ):
            issues.append(
                CitationIssue(
                    "E404",
                    f"청크 페이지 범위({page_start}~{page_end})와 page가 맞지 않는다",
                    index,
                    chunk_id,
                )
            )

        if normalize(quote) not in normalize(str(_value(chunk, "text", ""))):
            issues.append(
                CitationIssue("E403", "인용문이 청크 원문에 없다", index, chunk_id)
            )
    if result == "MET" and any(i.code in {"E402", "E403", "E405", "E406"} for i in issues):
        issues.append(CitationIssue("P2E505", "MET 판정이 유효한 원문 근거를 사용하지 않았다"))

    return CitationValidation(tuple(issues), tuple(warnings))
