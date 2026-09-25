"""Phase 1 판단 결과 VersionInfo 조립.

모델 이름은 실제 LLM 서버가 결정하므로 호출부에서 반드시 넘긴다. 다른 버전은 각 팀의 현재 계약에서
가져오며 같은 문자열을 중복 하드코딩하지 않는다.
"""
from __future__ import annotations

from models import MappingInput, SCHEMA_VERSION, VersionInfo
from prompts import PROMPT_VERSION, RULESET_VERSION


def build_version_info(
    mapping_input: MappingInput,
    *,
    model_name: str,
    embedding_model_revision: str | None = None,
) -> VersionInfo:
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name must be a non-empty string")

    return VersionInfo(
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        model_name=model_name.strip(),
        ruleset_version=RULESET_VERSION,
        kb_sha256=mapping_input.kb_sha256,
        embedding_model_revision=(
            embedding_model_revision
            if embedding_model_revision is not None
            else mapping_input.model_revision
        ),
    )
