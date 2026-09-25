"""Search/retriever-0.2 출력 -> 판단팀 MappingInput 어댑터.

검색팀의 실제 문서 단위 결과는 두 군데로 나뉜다.

- retrieval.candidates: rank / similarity_score / 문서 단위 Top-K
- candidate_controls: requirement / matched_chunk_ids

판단팀 모델은 이 둘을 CandidateControl 하나로 합쳐 받는다. 이 모듈만 검색팀
JSON 구조를 안다. 검색팀 필드가 바뀌면 Prompt나 Validator가 아니라 여기만 수정한다.
"""
from __future__ import annotations

from typing import Any
from collections.abc import Mapping

from models import MappingInput

SUPPORTED_RETRIEVER_SCHEMA = "retriever-0.2"


class RetrievalAdapterError(ValueError):
    """검색팀 handoff JSON이 판단 입력 계약과 맞지 않을 때 발생한다."""


def _obj(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalAdapterError(f"{name} must be an object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise RetrievalAdapterError(f"{name} must be an array")
    return value


def mapping_input_from_retriever(payload: Mapping[str, Any]) -> MappingInput:
    """검색팀 ``chunk_retriever.retrieve()`` 성공 결과를 MappingInput으로 변환한다.

    원칙
    ----
    * 실패 결과(success=false)는 판단 LLM에 넘기지 않는다.
    * document Top-K 순서를 그대로 보존한다.
    * retrieval 점수와 candidate_controls의 requirement를 control_id로 정확히 join한다.
    * ``matched_chunk_ids``는 판단 모델의 ``source_chunk_ids``로 이름만 변환한다.
    * 검색 결과에 없는 embedding model revision을 임의로 만들어내지 않는다.
    """
    payload = _obj(payload, "payload")

    schema_version = payload.get("schema_version")
    if schema_version != SUPPORTED_RETRIEVER_SCHEMA:
        raise RetrievalAdapterError(
            f"unsupported retriever schema: expected {SUPPORTED_RETRIEVER_SCHEMA!r}, "
            f"got {schema_version!r}"
        )

    if payload.get("success") is not True:
        error = payload.get("error")
        raise RetrievalAdapterError(f"retriever result is not successful: {error!r}")

    retrieval = _obj(payload.get("retrieval"), "retrieval")
    if retrieval.get("unit") != "document":
        raise RetrievalAdapterError("retrieval.unit must be 'document'")
    if retrieval.get("aggregation") != "max_chunk_similarity":
        raise RetrievalAdapterError(
            "unsupported document candidate aggregation; expected 'max_chunk_similarity'"
        )

    ranked = _list(retrieval.get("candidates"), "retrieval.candidates")
    details = _list(payload.get("candidate_controls"), "candidate_controls")

    by_id: dict[str, Mapping[str, Any]] = {}
    for i, raw in enumerate(details):
        item = _obj(raw, f"candidate_controls[{i}]")
        cid = item.get("control_id")
        if not isinstance(cid, str) or not cid:
            raise RetrievalAdapterError(f"candidate_controls[{i}].control_id is invalid")
        if cid in by_id:
            raise RetrievalAdapterError(f"duplicate candidate_controls control_id: {cid}")
        by_id[cid] = item

    if len(ranked) != len(details):
        raise RetrievalAdapterError(
            "retrieval.candidates and candidate_controls must have the same length"
        )

    top_k = retrieval.get("top_k")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise RetrievalAdapterError("retrieval.top_k must be a positive integer")
    if len(ranked) != top_k:
        raise RetrievalAdapterError(
            f"document candidate count must equal top_k: {len(ranked)} != {top_k}"
        )

    chunk_ids = {
        c.get("chunk_id")
        for c in _list(payload.get("evidence_chunks"), "evidence_chunks")
        if isinstance(c, Mapping)
    }

    merged: list[dict[str, Any]] = []
    seen_ranked: set[str] = set()
    for i, raw in enumerate(ranked):
        row = _obj(raw, f"retrieval.candidates[{i}]")
        cid = row.get("control_id")
        if not isinstance(cid, str) or not cid:
            raise RetrievalAdapterError(f"retrieval.candidates[{i}].control_id is invalid")
        if cid in seen_ranked:
            raise RetrievalAdapterError(f"duplicate retrieval candidate control_id: {cid}")
        seen_ranked.add(cid)

        expected_rank = i + 1
        if row.get("rank") != expected_rank:
            raise RetrievalAdapterError(
                f"retrieval.candidates[{i}].rank must be {expected_rank}"
            )

        detail = by_id.get(cid)
        if detail is None:
            raise RetrievalAdapterError(
                f"candidate_controls is missing control_id from retrieval.candidates: {cid}"
            )

        if detail.get("control_name") != row.get("control_name"):
            raise RetrievalAdapterError(f"control_name mismatch for {cid}")

        ranked_chunks = row.get("matched_chunk_ids", [])
        detail_chunks = detail.get("matched_chunk_ids", [])
        if ranked_chunks != detail_chunks:
            raise RetrievalAdapterError(f"matched_chunk_ids mismatch for {cid}")
        unknown_chunks = [chunk_id for chunk_id in detail_chunks if chunk_id not in chunk_ids]
        if unknown_chunks:
            raise RetrievalAdapterError(
                f"matched_chunk_ids contains unknown chunks for {cid}: {unknown_chunks}"
            )

        merged.append(
            {
                "rank": row.get("rank"),
                "control_id": cid,
                "control_name": row.get("control_name"),
                "similarity_score": row.get("similarity_score"),
                # retriever-0.2 문서 출력은 distance를 내보내지 않는다.
                # CandidateControl.distance는 optional이므로 None으로 둔다.
                "distance": row.get("distance"),
                "requirement": detail.get("requirement"),
                "source_chunk_ids": list(detail_chunks),
            }
        )

    if set(by_id) != seen_ranked:
        extra = sorted(set(by_id) - seen_ranked)
        raise RetrievalAdapterError(
            f"candidate_controls has controls not present in retrieval.candidates: {extra}"
        )

    index = _obj(payload.get("index"), "index")

    # Search/retriever-0.2에는 model revision이 아직 결과에 없고
    # embedding_cache_key만 있다. 둘은 같은 값이 아니므로 치환하지 않는다.
    model_revision = payload.get("model_revision")
    if model_revision is None:
        model_revision = index.get("model_revision")

    data = {
        "evidence_id": payload.get("evidence_id"),
        "version": payload.get("version"),
        "chunks": payload.get("evidence_chunks"),
        "candidate_controls": merged,
        "top_k": top_k,
        "kb_sha256": index.get("kb_sha256"),
        "model_revision": model_revision,
    }

    try:
        return MappingInput.model_validate(data)
    except Exception as exc:  # Pydantic 세부 오류를 경계 밖으로 그대로 흘리지 않는다.
        raise RetrievalAdapterError(f"retriever output does not satisfy MappingInput: {exc}") from exc
