"""phase1_검색/retriever-0.2 출력 -> 판단팀 MappingInput 어댑터.

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
from kb import DEFAULT_INDEX

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

    # 검색팀 공식 judgment_adapter와 같은 경계 조건을 판단팀 쪽에서도 확인한다.
    # 두 어댑터가 정상 입력에서 같은 MappingInput을 만들 뿐 아니라, 오래된 KB나
    # 순서가 깨진 후보를 서로 다르게 받아들이는 contract drift도 막기 위한 검사다.
    index = _obj(payload.get("index"), "index")
    kb_sha256 = index.get("kb_sha256")
    if kb_sha256 != DEFAULT_INDEX.kb_sha256:
        raise RetrievalAdapterError(
            "retriever KB hash does not match judgment control_index: "
            f"{kb_sha256!r} != {DEFAULT_INDEX.kb_sha256!r}"
        )

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

    ranked_ids = [
        row.get("control_id") if isinstance(row, Mapping) else None
        for row in ranked
    ]
    detail_ids = [
        row.get("control_id") if isinstance(row, Mapping) else None
        for row in details
    ]
    if ranked_ids != detail_ids:
        raise RetrievalAdapterError(
            "candidate_controls control_id order must match retrieval.candidates"
        )

    top_k = retrieval.get("top_k")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise RetrievalAdapterError("retrieval.top_k must be a positive integer")
    if len(ranked) != top_k:
        raise RetrievalAdapterError(
            f"document candidate count must equal top_k: {len(ranked)} != {top_k}"
        )

    evidence_chunks = _list(payload.get("evidence_chunks"), "evidence_chunks")
    chunk_ids = {
        c.get("chunk_id")
        for c in evidence_chunks
        if isinstance(c, Mapping)
    }

    scores: list[float] = []
    for i, raw in enumerate(ranked):
        row = _obj(raw, f"retrieval.candidates[{i}]")
        score = row.get("similarity_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise RetrievalAdapterError(
                f"retrieval.candidates[{i}].similarity_score must be numeric"
            )
        scores.append(float(score))
    if scores != sorted(scores, reverse=True):
        raise RetrievalAdapterError(
            "retrieval.candidates must be ordered by descending similarity_score"
        )

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

        control_name = row.get("control_name")
        if detail.get("control_name") != control_name:
            raise RetrievalAdapterError(f"control_name mismatch for {cid}")
        if not DEFAULT_INDEX.exists(cid):
            raise RetrievalAdapterError(f"control_id is not present in judgment KB: {cid}")
        if not DEFAULT_INDEX.name_matches(cid, str(control_name or "")):
            raise RetrievalAdapterError(f"judgment KB control_name mismatch for {cid}")

        requirement = detail.get("requirement")
        if not isinstance(requirement, str) or not requirement.strip():
            raise RetrievalAdapterError(f"candidate_controls requirement is empty for {cid}")

        ranked_chunks = row.get("matched_chunk_ids", [])
        detail_chunks = detail.get("matched_chunk_ids", [])
        if not isinstance(ranked_chunks, list) or not isinstance(detail_chunks, list):
            raise RetrievalAdapterError(f"matched_chunk_ids must be arrays for {cid}")
        if ranked_chunks != detail_chunks:
            raise RetrievalAdapterError(f"matched_chunk_ids mismatch for {cid}")
        if len(detail_chunks) != len(set(detail_chunks)):
            raise RetrievalAdapterError(f"matched_chunk_ids contains duplicates for {cid}")
        unknown_chunks = [chunk_id for chunk_id in detail_chunks if chunk_id not in chunk_ids]
        if unknown_chunks:
            raise RetrievalAdapterError(
                f"matched_chunk_ids contains unknown chunks for {cid}: {unknown_chunks}"
            )
        best_chunk_id = row.get("best_chunk_id")
        if best_chunk_id not in detail_chunks:
            raise RetrievalAdapterError(
                f"best_chunk_id must be included in matched_chunk_ids for {cid}: {best_chunk_id!r}"
            )

        merged.append(
            {
                "rank": row.get("rank"),
                "control_id": cid,
                "control_name": control_name,
                "similarity_score": row.get("similarity_score"),
                # retriever-0.2 문서 출력은 distance를 내보내지 않는다.
                # CandidateControl.distance는 optional이므로 None으로 둔다.
                "distance": row.get("distance"),
                "requirement": requirement,
                "source_chunk_ids": list(detail_chunks),
            }
        )

    if set(by_id) != seen_ranked:
        extra = sorted(set(by_id) - seen_ranked)
        raise RetrievalAdapterError(
            f"candidate_controls has controls not present in retrieval.candidates: {extra}"
        )

    # phase1_검색/retriever-0.2에는 model revision이 아직 결과에 없고
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
        "kb_sha256": kb_sha256,
        "model_revision": model_revision,
    }

    try:
        return MappingInput.model_validate(data)
    except Exception as exc:  # Pydantic 세부 오류를 경계 밖으로 그대로 흘리지 않는다.
        raise RetrievalAdapterError(f"retriever output does not satisfy MappingInput: {exc}") from exc
