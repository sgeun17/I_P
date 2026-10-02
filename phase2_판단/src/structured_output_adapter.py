"""찬우 담당 Structured Output 계약을 자운 LLM 하네스에서 소비하기 위한 어댑터.

중요:
- 이 모듈은 Phase 2 Output Schema/Pydantic 모델을 새로 설계하지 않는다.
- `phase2_인터페이스/phase2_output.schema.json`의 `$defs.ItemResult`를 그대로 읽어
  Ollama OpenAI-compatible Structured Output 요청에 전달한다.
- quote 원문 일치, page/chunk_id 검증, 전체 JSON/ENUM/ID Validator는 찬우 영역이다.
- 하네스 내부에서는 재시도 판단에 필요한 최소 형태만 확인한다.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_ADAPTER_VERSION = "phase2_structured_output_adapter_v0.1"
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
DEFAULT_OUTPUT_SCHEMA = PROJECT_ROOT / "phase2_인터페이스" / "phase2_output.schema.json"


class StructuredOutputContractError(ValueError):
    pass


def _load_root(path: Path = DEFAULT_OUTPUT_SCHEMA) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise StructuredOutputContractError(f"Phase 2 Output Schema를 찾을 수 없습니다: {path}") from exc
    if not isinstance(data, dict):
        raise StructuredOutputContractError("Phase 2 Output Schema 최상위는 object여야 합니다.")
    return data


def item_json_schema(path: Path = DEFAULT_OUTPUT_SCHEMA) -> dict[str, Any]:
    """공식 Output Schema의 ItemResult 정의를 Structured Output 요청용으로 반환한다."""
    root = _load_root(path)
    defs = root.get("$defs")
    if not isinstance(defs, Mapping) or not isinstance(defs.get("ItemResult"), Mapping):
        raise StructuredOutputContractError("공식 Output Schema에 $defs.ItemResult가 없습니다.")
    item = deepcopy(dict(defs["ItemResult"]))
    # ItemResult.citations가 #/$defs/Citation을 참조하므로 필요한 정의만 함께 묶는다.
    citation = defs.get("Citation")
    if isinstance(citation, Mapping):
        item["$defs"] = {"Citation": deepcopy(dict(citation))}
    item["$schema"] = root.get("$schema", "https://json-schema.org/draft/2020-12/schema")
    item["title"] = "Phase2ItemResult"
    return item


def output_schema_version(path: Path = DEFAULT_OUTPUT_SCHEMA) -> str:
    root = _load_root(path)
    node = root.get("properties", {}).get("schema_version", {})
    value = node.get("const") if isinstance(node, Mapping) else None
    return str(value or root.get("$id") or "unknown")


def output_schema_sha256(path: Path = DEFAULT_OUTPUT_SCHEMA) -> str:
    return sha256(path.read_bytes()).hexdigest()


def validate_model_generated_item(
    payload: Any,
    *,
    expected_item_id: str,
    allowed_reason_codes: set[str] | None = None,
) -> list[str]:
    """자운 하네스의 *최소* 재시도용 검사.

    최종 Validator가 아니다. 의미/인용/ID/page 검증을 수행하지 않는다.
    Structured Output을 쓰더라도 모델/서버 호환 문제로 JSON이 어긋날 때 재시도 여부를
    결정하기 위한 최소 필드만 확인한다.
    """
    if not isinstance(payload, Mapping):
        return ["output must be a JSON object"]
    issues: list[str] = []
    required = {"item_id", "result", "reason", "citations"}
    missing = required - set(payload)
    if missing:
        issues.append("missing fields: " + ", ".join(sorted(missing)))
    if payload.get("item_id") != expected_item_id:
        issues.append("item_id does not match requested checklist item")
    if payload.get("result") not in {"MET", "NOT_MET", "UNKNOWN"}:
        issues.append("result must be MET, NOT_MET, or UNKNOWN")
    if not isinstance(payload.get("reason"), str) or not payload.get("reason", "").strip():
        issues.append("reason must be a non-empty string")
    codes = payload.get("reason_codes", [])
    if codes is not None:
        if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
            issues.append("reason_codes must be an array of strings")
        elif allowed_reason_codes is not None:
            unknown = sorted(set(codes) - allowed_reason_codes)
            if unknown:
                issues.append("unknown reason_codes: " + ", ".join(unknown))
    if not isinstance(payload.get("citations"), list):
        issues.append("citations must be an array")
    return issues
