"""Offline contract checks for the opt-in compact prompt, never a live LLM."""
from copy import deepcopy
import json

import pytest

from checklist_adapter import get_checklist_item, load_reason_code_catalog
from grounding_prompts import (
    COMPACT_PROMPT_VERSION, build_prompt_package, build_system_prompt,
    build_user_prompt, compact_evidence_context,
)
from self_check import COMPACT_SELF_CHECK_VERSION, SELF_CHECK_SCHEMA, run_self_check


def source_context():
    return {
        "chunks": [
            {"chunk_id": "source-a", "heading": "검토 기준", "text": "정기 검토를 수행한다.",
             "page_start": 1, "page_end": 1, "role": "evidence", "source": "parser",
             "original_text_sha256": "audit-only", "distance_from_evidence": 0},
            {"chunk_id": "source-b", "heading": "실행 기록", "text": "검토를 수행하지 않았다.",
             "page_start": 2, "page_end": 2, "role": "context", "source": "parser",
             "truncated": False, "distance_from_evidence": 1},
        ],
        "omitted_chunk_ids": ["unavailable-source"],
        "selection_audit": {"internal_marker": "NOT_MODEL_INPUT"},
    }


def test_legacy_builder_is_unchanged_and_schema_is_detached():
    item = get_checklist_item("2.5.6-Q07")
    context = source_context()
    schema = {"type": "object", "properties": {"reason": {"type": "string"}}}
    package = build_prompt_package(item, context, output_schema=schema)
    assert package.system == build_system_prompt(schema)
    assert package.user == build_user_prompt(item, context)
    assert build_prompt_package(item, context, output_schema=schema,
                                prompt_profile="legacy") == package
    package.output_schema["properties"]["reason"]["type"] = "null"
    assert schema["properties"]["reason"]["type"] == "string"


def test_compact_keeps_complete_item_sources_and_explicit_rules_without_schema_echo():
    item = get_checklist_item("2.5.6-Q07")
    context = source_context()
    original = deepcopy((item, context))
    schema = {"type": "object", "description": "SCHEMA_ONLY_SENTINEL"}
    rules = "  supplied rule\nKeep this exact rule.  "
    package = build_prompt_package(item, context, prompt_profile="compact_v2",
                                   global_rules=rules, output_schema=schema)
    data = json.loads(package.user)
    assert package.prompt_version == COMPACT_PROMPT_VERSION
    assert data["checklist_item"] == item
    assert list(data)[-1] == "checklist_item"
    assert data["judgment_rules"] == rules
    assert data["evidence_context"] == compact_evidence_context(context)
    assert data["evidence_context"]["chunks"][1]["text"] == "검토를 수행하지 않았다."
    assert "NOT_MODEL_INPUT" not in package.user
    assert "SCHEMA_ONLY_SENTINEL" not in package.system + package.user
    assert package.output_schema == schema
    assert (item, context) == original


def test_catalog_meaning_preserved_and_prompt_is_materially_smaller():
    item = get_checklist_item("2.5.6-Q07")
    codes = load_reason_code_catalog()["codes"]
    legacy = build_prompt_package(item, source_context(), reason_codes=codes)
    compact = build_prompt_package(item, source_context(), reason_codes=codes,
                                   prompt_profile="compact_v2")
    data = json.loads(compact.user)
    for source, emitted in zip(codes, data["reason_codes"]):
        assert emitted["code"] == source["code"]
        assert emitted["result"] == source["result"]
        for key in ("description", "label", "use_when", "do_not_use_when", "required_context"):
            if key in source and source[key] is not None:
                assert emitted["description"][key] == source[key]
    assert len(compact.system + compact.user) < len(legacy.system + legacy.user) * 0.6
    assert compact.prompt_lengths["system_chars"] == len(compact.system)


def test_compact_self_check_delivers_full_context_for_unknown_and_preserves_schema():
    item = get_checklist_item("2.5.6-Q07")
    output = {"item_id": item["item_id"], "result": "UNKNOWN", "reason": "검토 기록은 있으나 기간 연결이 불명확하다.",
              "reason_codes": ["P2_U_EVIDENCE_INSUFFICIENT"],
              "citations": [{"chunk_id": "source-b", "page": 2, "quote": "검토를 수행하지 않았다."}]}
    seen = {}

    def fake(system, user, model, schema, **kwargs):
        seen.update(system=system, payload=json.loads(user), schema=schema, kwargs=kwargs)
        return json.dumps({"verdict": "UNSUPPORTED", "reason": "기록은 직접 미수행을 말한다.",
                           "confidence": 0.8, "unsupported_conditions": ["원문 의미 불일치"]})

    result = run_self_check(item, output, model="offline-stub", context=source_context(),
                            llm_call=fake, prompt_profile="compact_v2")
    assert result["version"] == COMPACT_SELF_CHECK_VERSION
    assert result["verdict"] == "UNSUPPORTED"
    assert seen["schema"] == SELF_CHECK_SCHEMA
    assert seen["payload"]["checklist_item"] == item
    assert seen["payload"]["cited_evidence"] == output["citations"]
    assert seen["payload"]["available_evidence_context"] == compact_evidence_context(source_context())
    assert list(seen["payload"])[-1] == "checklist_item"
    assert "prompt_profile" not in seen["kwargs"]
    assert "UNKNOWN도 검토한다" in seen["system"]


def test_legacy_self_check_messages_stay_identical_when_profile_is_explicit():
    item = get_checklist_item("2.5.6-Q07")
    output = {"item_id": item["item_id"], "result": "UNKNOWN", "reason": "근거 부족",
              "reason_codes": ["P2_U_EVIDENCE_INSUFFICIENT"], "citations": []}
    messages = []

    def fake(system, user, model, schema, **kwargs):
        messages.append((system, user, deepcopy(schema)))
        return json.dumps({"verdict": "SUPPORTED", "reason": "기간 불명", "confidence": 0.8,
                           "unsupported_conditions": []})

    run_self_check(item, output, model="offline-stub", llm_call=fake)
    run_self_check(item, output, model="offline-stub", llm_call=fake, prompt_profile="legacy")
    assert messages[0] == messages[1]


def test_invalid_profile_fails_before_any_model_call():
    with pytest.raises(ValueError, match="prompt_profile"):
        build_prompt_package({}, {}, prompt_profile="typo")
    with pytest.raises(ValueError, match="prompt_profile"):
        run_self_check({}, {}, model="offline-stub", prompt_profile="typo")
