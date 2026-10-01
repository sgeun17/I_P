"""Phase 2 판단 결과 재현을 위한 버전 메타데이터.

최종 Output Schema가 오기 전에도 Prompt/Context/Checklist/Reason code/Retry 설정의 조합을
기록할 수 있게 한다. 파일 버전 문자열과 SHA-256을 함께 보존해 같은 이름의 draft가 바뀌는 것도 잡는다.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from checklist_adapter import DEFAULT_CHECKLIST, DEFAULT_REASON_CODES
from context_builder import CONTEXT_BUILDER_VERSION
from draft_contract import DEVELOPMENT_SCHEMA_VERSION
from grounding_prompts import GLOBAL_RULESET_VERSION, PROMPT_VERSION
from phase1_runtime import DEFAULT_RETRY_POLICY, retry_policy_snapshot


VERSION_RECORD_VERSION = "phase2_versions_v0.1"


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON 최상위는 object여야 합니다: {path}")
    return data


def build_version_info(
    *,
    model_name: str,
    checklist_path: Path = DEFAULT_CHECKLIST,
    reason_codes_path: Path = DEFAULT_REASON_CODES,
    ruleset_version: str = GLOBAL_RULESET_VERSION,
    output_schema_version: str = DEVELOPMENT_SCHEMA_VERSION,
) -> dict[str, Any]:
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name은 비어 있지 않은 문자열이어야 합니다.")
    checklist = _json(checklist_path)
    reason_codes = _json(reason_codes_path)
    return {
        "version_record_version": VERSION_RECORD_VERSION,
        "prompt_version": PROMPT_VERSION,
        "ruleset_version": ruleset_version,
        "context_builder_version": CONTEXT_BUILDER_VERSION,
        "output_schema_version": output_schema_version,
        "model_name": model_name.strip(),
        "checklist_version": checklist.get("draft_version") or checklist.get("version"),
        "checklist_approved": checklist.get("approved"),
        "checklist_sha256": sha256_file(checklist_path),
        "reason_codes_version": reason_codes.get("catalog_version"),
        "reason_codes_approved": reason_codes.get("approved"),
        "reason_codes_sha256": sha256_file(reason_codes_path),
        "phase1_retry_policy": retry_policy_snapshot(DEFAULT_RETRY_POLICY),
    }
