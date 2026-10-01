"""Phase 2 증적 컨텍스트 구성기.

책임
----
- Phase 1에서 확정된 통제항목의 citation chunk를 Phase 2 판단의 핵심 근거로 선택한다.
- 핵심 근거 앞뒤의 인접 청크를 보조 context로 붙인다.
- chunk_id/page/text를 변경 없이 유지한다.
- 토큰 예산이 넘으면 보조 context를 먼저 제거하고, 그래도 넘으면 핵심 청크만 축약한다.

중요
----
이 모듈은 "무엇이 MET/NOT_MET인가"를 결정하지 않는다. 판정 규칙은 체크리스트/규칙 문서의 책임이다.
토큰 수는 휴리스틱으로 추정하지 않는다. max_context_tokens를 사용할 때는 실제 모델 tokenizer와
같은 TokenCounter callback을 반드시 주입한다.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Any, Callable, Iterable, Mapping, Sequence


CONTEXT_BUILDER_VERSION = "phase2_context_v0.1"
TokenCounter = Callable[[str], int]


class ContextBuildError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class ContextChunk:
    chunk_id: str
    text: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    source_file: str | None
    file_type: str | None
    source: str | None
    role: str
    source_position: int
    distance_from_evidence: int
    truncated: bool = False
    original_text_sha256: str | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "role": self.role,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "heading": self.heading,
            "source_file": self.source_file,
            "file_type": self.file_type,
            "source": self.source,
            "text": self.text,
            "truncated": self.truncated,
            "original_text_sha256": self.original_text_sha256,
        }


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _normalized_chunk(raw: Any, source_position: int) -> ContextChunk:
    chunk_id = _value(raw, "chunk_id")
    text = _value(raw, "text")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ContextBuildError("INVALID_CHUNK", "chunk_id가 없는 청크가 있습니다.")
    if not isinstance(text, str):
        raise ContextBuildError(
            "INVALID_CHUNK", f"{chunk_id}: text는 문자열이어야 합니다."
        )

    page_start = _value(raw, "page_start")
    page_end = _value(raw, "page_end")
    if page_start is not None and (not isinstance(page_start, int) or isinstance(page_start, bool)):
        raise ContextBuildError("INVALID_CHUNK", f"{chunk_id}: page_start 형식이 잘못됐습니다.")
    if page_end is not None and (not isinstance(page_end, int) or isinstance(page_end, bool)):
        raise ContextBuildError("INVALID_CHUNK", f"{chunk_id}: page_end 형식이 잘못됐습니다.")
    if (page_start is None) != (page_end is None):
        raise ContextBuildError(
            "INVALID_CHUNK", f"{chunk_id}: page_start/page_end는 같이 null이거나 같이 값이 있어야 합니다."
        )
    if page_start is not None and page_start > page_end:
        raise ContextBuildError("INVALID_CHUNK", f"{chunk_id}: 페이지 범위가 뒤집혔습니다.")

    return ContextChunk(
        chunk_id=chunk_id,
        text=text,
        page_start=page_start,
        page_end=page_end,
        heading=_value(raw, "heading"),
        source_file=_value(raw, "source_file"),
        file_type=_enum_value(_value(raw, "file_type")),
        source=_enum_value(_value(raw, "source")),
        role="context",
        source_position=source_position,
        distance_from_evidence=999999,
        original_text_sha256=sha256(text.encode("utf-8")).hexdigest(),
    )


def normalize_chunks(chunks: Sequence[Any]) -> list[ContextChunk]:
    if not chunks:
        raise ContextBuildError("NO_CHUNKS", "Phase 2 컨텍스트를 만들 증적 청크가 없습니다.")
    normalized = [_normalized_chunk(chunk, index) for index, chunk in enumerate(chunks)]
    ids = [chunk.chunk_id for chunk in normalized]
    duplicates = sorted({chunk_id for chunk_id in ids if ids.count(chunk_id) > 1})
    if duplicates:
        raise ContextBuildError(
            "DUPLICATE_CHUNK_ID",
            "중복 chunk_id가 있습니다.",
            details={"chunk_ids": duplicates},
        )
    return normalized


def extract_phase1_citation_anchors(
    phase1_result: Any,
    control_id: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Phase 1 최종 결과에서 해당 control_id의 citation chunk/quote를 뽑는다.

    dict와 Pydantic 객체를 모두 받는다. Phase 1 결과를 수정하지 않는다.
    """
    mapped_controls = _value(phase1_result, "mapped_controls", []) or []
    chunk_ids: list[str] = []
    quotes: dict[str, list[str]] = {}
    for mapped in mapped_controls:
        if _value(mapped, "control_id") != control_id:
            continue
        for citation in _value(mapped, "citations", []) or []:
            chunk_id = _value(citation, "chunk_id")
            quote = _value(citation, "quote")
            if isinstance(chunk_id, str) and chunk_id and chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
            if isinstance(chunk_id, str) and isinstance(quote, str) and quote:
                quotes.setdefault(chunk_id, [])
                if quote not in quotes[chunk_id]:
                    quotes[chunk_id].append(quote)
    return chunk_ids, quotes


def _selected_with_neighbors(
    normalized: Sequence[ContextChunk],
    evidence_chunk_ids: Sequence[str],
    neighbor_count: int,
) -> list[ContextChunk]:
    if neighbor_count < 0:
        raise ContextBuildError("INVALID_NEIGHBOR_COUNT", "neighbor_count는 0 이상이어야 합니다.")
    if not evidence_chunk_ids:
        raise ContextBuildError(
            "NO_EVIDENCE_ANCHOR",
            "Phase 1 citation 기반 핵심 chunk_id가 없습니다. 임의 청크를 근거로 선택하지 않습니다.",
        )

    index_by_id = {chunk.chunk_id: idx for idx, chunk in enumerate(normalized)}
    missing = [chunk_id for chunk_id in evidence_chunk_ids if chunk_id not in index_by_id]
    if missing:
        raise ContextBuildError(
            "ANCHOR_CHUNK_NOT_FOUND",
            "Phase 1 citation의 chunk_id가 현재 증적 청크에 없습니다.",
            details={"chunk_ids": missing},
        )

    evidence_positions = {index_by_id[chunk_id] for chunk_id in evidence_chunk_ids}
    selected_positions: set[int] = set()
    for position in evidence_positions:
        start = max(0, position - neighbor_count)
        end = min(len(normalized) - 1, position + neighbor_count)
        selected_positions.update(range(start, end + 1))

    result: list[ContextChunk] = []
    for position in sorted(selected_positions):
        chunk = normalized[position]
        distance = min(abs(position - evidence_position) for evidence_position in evidence_positions)
        result.append(
            replace(
                chunk,
                role="evidence" if position in evidence_positions else "context",
                distance_from_evidence=distance,
            )
        )
    return result


def _serialized_context(chunks: Sequence[ContextChunk]) -> str:
    return json.dumps(
        {"chunks": [chunk.to_prompt_dict() for chunk in chunks]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _count_tokens(text: str, token_counter: TokenCounter) -> int:
    value = token_counter(text)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContextBuildError(
            "INVALID_TOKEN_COUNTER", "token_counter는 0 이상의 정수를 반환해야 합니다."
        )
    return value


def _snippet_around_anchor(text: str, max_chars: int, anchors: Sequence[str]) -> str:
    if max_chars >= len(text):
        return text
    if max_chars <= 0:
        return ""

    usable_anchor = next((anchor for anchor in anchors if anchor and anchor in text), None)
    if usable_anchor is None:
        return text[:max_chars]

    anchor_start = text.index(usable_anchor)
    anchor_end = anchor_start + len(usable_anchor)
    if len(usable_anchor) >= max_chars:
        return usable_anchor[:max_chars]

    remaining = max_chars - len(usable_anchor)
    left = remaining // 2
    right = remaining - left
    start = max(0, anchor_start - left)
    end = min(len(text), anchor_end + right)
    if end - start < max_chars:
        if start == 0:
            end = min(len(text), max_chars)
        elif end == len(text):
            start = max(0, len(text) - max_chars)
    return text[start:end]


def _truncate_one_to_fit(
    chunks: list[ContextChunk],
    index: int,
    max_tokens: int,
    token_counter: TokenCounter,
    anchor_quotes: Mapping[str, Sequence[str]],
) -> tuple[list[ContextChunk], bool]:
    original = chunks[index]
    if not original.text:
        return chunks, False

    # 해당 청크를 빈 문자열로 만들어도 예산을 넘는다면 다른 청크도 줄여야 한다.
    zeroed = list(chunks)
    zeroed[index] = replace(original, text="", truncated=True)
    if _count_tokens(_serialized_context(zeroed), token_counter) > max_tokens:
        # 지나치게 작은 예산에서 무한 루프를 막기 위해 절반으로 줄이고 다음 반복으로 넘긴다.
        target = max(1, len(original.text) // 2)
        snippet = _snippet_around_anchor(
            original.text, target, anchor_quotes.get(original.chunk_id, ())
        )
        reduced = list(chunks)
        reduced[index] = replace(original, text=snippet, truncated=True)
        return reduced, len(snippet) < len(original.text)

    low, high = 0, len(original.text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        snippet = _snippet_around_anchor(
            original.text, mid, anchor_quotes.get(original.chunk_id, ())
        )
        candidate = list(chunks)
        candidate[index] = replace(original, text=snippet, truncated=True)
        used = _count_tokens(_serialized_context(candidate), token_counter)
        if used <= max_tokens:
            best = snippet
            low = mid + 1
        else:
            high = mid - 1

    reduced = list(chunks)
    reduced[index] = replace(original, text=best, truncated=True)
    return reduced, len(best) < len(original.text)


def fit_to_token_budget(
    chunks: Sequence[ContextChunk],
    *,
    max_context_tokens: int,
    token_counter: TokenCounter,
    anchor_quotes: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[ContextChunk], list[str], int]:
    """선택된 컨텍스트를 토큰 예산 안에 넣는다.

    1) evidence가 아닌 인접 context 중 먼 것부터 제거
    2) 그래도 넘으면 evidence 청크 텍스트를 축약
       - Phase 1 quote가 있으면 quote 주변을 우선 보존
    3) 메타데이터만으로도 예산을 넘는 비정상적으로 작은 limit이면 명시적 오류
    """
    if max_context_tokens < 1:
        raise ContextBuildError("INVALID_TOKEN_BUDGET", "max_context_tokens는 1 이상이어야 합니다.")
    anchor_quotes = anchor_quotes or {}
    kept = list(chunks)
    omitted: list[str] = []

    def used() -> int:
        return _count_tokens(_serialized_context(kept), token_counter)

    if used() <= max_context_tokens:
        return kept, omitted, used()

    removable = sorted(
        (chunk for chunk in kept if chunk.role == "context"),
        key=lambda chunk: (chunk.distance_from_evidence, chunk.source_position),
        reverse=True,
    )
    for chunk in removable:
        kept = [row for row in kept if row.chunk_id != chunk.chunk_id]
        omitted.append(chunk.chunk_id)
        if used() <= max_context_tokens:
            kept.sort(key=lambda row: row.source_position)
            return kept, omitted, used()

    # 모든 핵심 evidence chunk는 ID 단위로 보존한다. 텍스트만 필요한 만큼 줄인다.
    safety = 0
    while used() > max_context_tokens:
        safety += 1
        if safety > max(10, len(kept) * 8):
            raise ContextBuildError(
                "TOKEN_BUDGET_TOO_SMALL",
                "핵심 근거 청크 메타데이터를 보존하면서 토큰 예산을 맞출 수 없습니다.",
            )
        evidence_indices = [i for i, row in enumerate(kept) if row.role == "evidence" and row.text]
        if not evidence_indices:
            raise ContextBuildError(
                "TOKEN_BUDGET_TOO_SMALL",
                "핵심 근거 청크 텍스트를 모두 줄여도 토큰 예산을 맞출 수 없습니다.",
            )
        index = max(evidence_indices, key=lambda i: len(kept[i].text))
        kept, changed = _truncate_one_to_fit(
            kept, index, max_context_tokens, token_counter, anchor_quotes
        )
        if not changed:
            raise ContextBuildError(
                "TOKEN_BUDGET_TOO_SMALL",
                "핵심 근거 청크 메타데이터를 보존하면서 토큰 예산을 맞출 수 없습니다.",
            )

    kept.sort(key=lambda row: row.source_position)
    return kept, omitted, used()


def build_evidence_context(
    chunks: Sequence[Any],
    evidence_chunk_ids: Sequence[str],
    *,
    anchor_quotes: Mapping[str, Sequence[str]] | None = None,
    neighbor_count: int = 1,
    max_chunks: int | None = None,
    max_context_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
) -> dict[str, Any]:
    """Phase 2 Prompt에 넣을 증적 context를 구성한다."""
    normalized = normalize_chunks(chunks)
    selected = _selected_with_neighbors(normalized, evidence_chunk_ids, neighbor_count)
    omitted: list[str] = []

    if max_chunks is not None:
        if max_chunks < len(evidence_chunk_ids):
            raise ContextBuildError(
                "MAX_CHUNKS_TOO_SMALL",
                "max_chunks는 최소한 핵심 evidence chunk 수 이상이어야 합니다.",
            )
        if max_chunks < 1:
            raise ContextBuildError("INVALID_MAX_CHUNKS", "max_chunks는 1 이상이어야 합니다.")
        if len(selected) > max_chunks:
            # evidence 우선, 인접 context는 가까운 순으로 채운 뒤 최종 출력은 원문 순서로 복구한다.
            ranked = sorted(
                selected,
                key=lambda row: (
                    0 if row.role == "evidence" else 1,
                    row.distance_from_evidence,
                    row.source_position,
                ),
            )
            keep_ids = {row.chunk_id for row in ranked[:max_chunks]}
            omitted.extend(row.chunk_id for row in selected if row.chunk_id not in keep_ids)
            selected = [row for row in selected if row.chunk_id in keep_ids]

    token_usage: dict[str, int | None] = {
        "limit": max_context_tokens,
        "used": None,
    }
    if max_context_tokens is not None:
        if token_counter is None:
            raise ContextBuildError(
                "TOKEN_COUNTER_REQUIRED",
                "max_context_tokens를 사용할 때는 실제 tokenizer 기반 token_counter가 필요합니다.",
            )
        selected, token_omitted, used_tokens = fit_to_token_budget(
            selected,
            max_context_tokens=max_context_tokens,
            token_counter=token_counter,
            anchor_quotes=anchor_quotes,
        )
        omitted.extend(chunk_id for chunk_id in token_omitted if chunk_id not in omitted)
        token_usage["used"] = used_tokens

    return {
        "context_version": CONTEXT_BUILDER_VERSION,
        "chunks": [chunk.to_prompt_dict() for chunk in selected],
        "evidence_chunk_ids": list(dict.fromkeys(evidence_chunk_ids)),
        "omitted_chunk_ids": omitted,
        "token_usage": token_usage,
    }


def build_context_from_phase1(
    chunks: Sequence[Any],
    phase1_result: Any,
    control_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Phase 1 최종 매핑의 citation을 anchor로 Phase 2 context를 만든다."""
    evidence_ids, quotes = extract_phase1_citation_anchors(phase1_result, control_id)
    return build_evidence_context(
        chunks,
        evidence_ids,
        anchor_quotes=quotes,
        **kwargs,
    )
