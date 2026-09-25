import json
from pathlib import Path

import pytest

from enums import ErrorCode
from models import LLMMappingOutput, SCHEMA_VERSION
from prompts import PROMPT_VERSION, RULESET_VERSION, build_prompt_package, get_output_schema
from retrieval_adapter import RetrievalAdapterError, mapping_input_from_retriever
from retry_prompt import RetryPromptError, build_retry_package
from versions import build_version_info

ROOT = Path(__file__).resolve().parents[2]
SEARCH_EXAMPLE = ROOT / "Search" / "examples" / "docx_output.json"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schemas" / "phase1_llm_output.schema.json"


def _retriever_payload():
    return json.loads(SEARCH_EXAMPLE.read_text(encoding="utf-8"))


def test_real_retriever_output_adapts_to_mapping_input():
    mapping = mapping_input_from_retriever(_retriever_payload())

    assert mapping.evidence_id == "000001"
    assert mapping.version == 1
    assert mapping.top_k == 5
    assert len(mapping.chunks) == 3
    assert len(mapping.candidate_controls) == 5
    assert mapping.candidate_controls[0].control_id == "2.9.4"
    assert mapping.candidate_controls[0].rank == 1
    assert mapping.candidate_controls[0].requirement
    assert mapping.candidate_controls[0].source_chunk_ids == ["000001_v1_c0001"]
    assert mapping.kb_sha256
    # retriever-0.2 does not expose the model revision itself.
    assert mapping.model_revision is None


def test_retriever_failure_does_not_enter_llm_pipeline():
    payload = {"schema_version": "retriever-0.2", "success": False, "error": {"code": "TIMEOUT"}}
    with pytest.raises(RetrievalAdapterError):
        mapping_input_from_retriever(payload)


def test_prompt_uses_actual_chunks_requirements_but_hides_retrieval_scores():
    mapping = mapping_input_from_retriever(_retriever_payload())
    package = build_prompt_package(mapping)

    assert "000001_v1_c0001" in package.user
    assert "로그 및 접속기록 관리" in package.user
    assert "서버, 응용프로그램" in package.user  # requirement
    assert "similarity_score" not in package.user
    assert '"rank"' not in package.user
    assert "matched_chunk_ids" not in package.user
    assert "source_chunk_ids" not in package.user
    assert "<evidence>" in package.user
    assert "<candidates>" in package.user


def test_schema_is_generated_from_same_pydantic_model_as_validator():
    generated = get_output_schema()
    checked_in = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))

    assert generated == checked_in
    assert generated["title"] == "LLMMappingOutput"
    # Check the actual runtime source of truth, not a hand-maintained duplicate.
    assert LLMMappingOutput.model_json_schema()["title"] == "LLMMappingOutput"


def test_prompt_contains_latest_v06_rules_and_confidence_definition():
    mapping = mapping_input_from_retriever(_retriever_payload())
    package = build_prompt_package(mapping)

    system = package.system
    assert "UNCERTAIN" in system
    assert "10자 이상" in system
    assert "적정/미흡" in system
    assert "정확도, 정답 확률" in system
    assert "0.70~0.89" in system
    assert "4개 이상이어도" in system
    assert "mapping_type" in system


def test_retry_prompt_only_accepts_retry_policy_errors():
    mapping = mapping_input_from_retriever(_retriever_payload())

    retry = build_retry_package(mapping, [ErrorCode.JSON_PARSE_FAILED], attempt=1)
    assert "retry_context" in retry.user
    assert "E201" in retry.user
    assert "새 JSON 객체 하나" in retry.user

    with pytest.raises(RetryPromptError):
        build_retry_package(mapping, [ErrorCode.CONTROL_ID_NOT_IN_CANDIDATES], attempt=1)


def test_retry_second_attempt_is_rejected_by_max_retries():
    mapping = mapping_input_from_retriever(_retriever_payload())
    with pytest.raises(RetryPromptError):
        build_retry_package(mapping, [ErrorCode.JSON_PARSE_FAILED], attempt=2)


def test_version_info_uses_team_owned_sources():
    mapping = mapping_input_from_retriever(_retriever_payload())
    versions = build_version_info(mapping, model_name="runtime-model")

    assert versions.schema_version == SCHEMA_VERSION
    assert versions.prompt_version == PROMPT_VERSION
    assert versions.ruleset_version == RULESET_VERSION
    assert versions.model_name == "runtime-model"
    assert versions.kb_sha256 == mapping.kb_sha256
    assert versions.embedding_model_revision is None
