"""Phase 2 운영 Schema 확정 전 개발용 최소 출력 계약.

이 파일은 혜진·세윤 팀의 최종 Phase 2 Output JSON Schema를 대체하지 않는다.
Grounding Prompt/LLM 호출을 먼저 연결하고 테스트하기 위한 DEV ONLY 계약이다.
최종 Schema가 오면 `grounding_prompts.get_output_schema()` 주입부와 harness validator만 교체한다.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


DEVELOPMENT_SCHEMA_VERSION = "phase2_dev_output_v0.1"
RESULT_VALUES = ("MET", "NOT_MET", "UNKNOWN")

_DEVELOPMENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Phase2DevelopmentJudgmentOutput",
    "type": "object",
    "additionalProperties": False,
    "required": ["item_id", "result", "reason", "reason_codes", "citations"],
    "properties": {
        "item_id": {"type": "string", "minLength": 1},
        "result": {"type": "string", "enum": list(RESULT_VALUES)},
        "reason": {"type": "string", "minLength": 1},
        "reason_codes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["chunk_id", "page", "quote"],
                "properties": {
                    "chunk_id": {"type": "string", "minLength": 1},
                    "page": {"type": ["integer", "null"]},
                    "quote": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


def get_development_output_schema() -> dict[str, Any]:
    return deepcopy(_DEVELOPMENT_OUTPUT_SCHEMA)


def validate_development_output(
    payload: Any,
    *,
    expected_item_id: str,
    allowed_reason_codes: set[str] | None = None,
) -> list[str]:
    """DEV 계약의 최소 구조만 검사한다.

    quote 원문 일치, page 일치, 의미적 판정 검증은 찬우 Validator의 책임이므로 여기서 수행하지 않는다.
    """
    issues: list[str] = []
    if not isinstance(payload, Mapping):
        return ["output must be a JSON object"]

    expected_keys = {"item_id", "result", "reason", "reason_codes", "citations"}
    missing = expected_keys - set(payload)
    extra = set(payload) - expected_keys
    if missing:
        issues.append("missing fields: " + ", ".join(sorted(missing)))
    if extra:
        issues.append("unexpected fields: " + ", ".join(sorted(extra)))

    if payload.get("item_id") != expected_item_id:
        issues.append("item_id does not match requested checklist item")
    if payload.get("result") not in RESULT_VALUES:
        issues.append("result must be MET, NOT_MET, or UNKNOWN")
    if not isinstance(payload.get("reason"), str) or not payload.get("reason", "").strip():
        issues.append("reason must be a non-empty string")

    codes = payload.get("reason_codes")
    if not isinstance(codes, list) or any(not isinstance(code, str) or not code for code in codes):
        issues.append("reason_codes must be an array of non-empty strings")
    elif len(codes) != len(set(codes)):
        issues.append("reason_codes must not contain duplicates")
    elif allowed_reason_codes is not None:
        unknown = sorted(set(codes) - allowed_reason_codes)
        if unknown:
            issues.append("unknown reason_codes: " + ", ".join(unknown))

    citations = payload.get("citations")
    if not isinstance(citations, list):
        issues.append("citations must be an array")
    else:
        for index, citation in enumerate(citations):
            if not isinstance(citation, Mapping):
                issues.append(f"citations[{index}] must be an object")
                continue
            if set(citation) != {"chunk_id", "page", "quote"}:
                issues.append(f"citations[{index}] fields are invalid")
            if not isinstance(citation.get("chunk_id"), str) or not citation.get("chunk_id", ""):
                issues.append(f"citations[{index}].chunk_id is invalid")
            page = citation.get("page")
            if page is not None and (not isinstance(page, int) or isinstance(page, bool)):
                issues.append(f"citations[{index}].page is invalid")
            if not isinstance(citation.get("quote"), str) or not citation.get("quote", ""):
                issues.append(f"citations[{index}].quote is invalid")

    if payload.get("result") in {"MET", "NOT_MET"} and isinstance(citations, list) and not citations:
        issues.append("MET/NOT_MET requires at least one citation in the development contract")
    return issues
