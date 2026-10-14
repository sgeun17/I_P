"""Synthetic v14 deployment contracts; no private evidence, DB or live inference."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm_runner import LLMRunResult
from models import LLMMappingOutput
from primary_review import source_spans
from purpose_selection import build_single_purpose_schema, decode_single_purpose
from review_response_contract import normalize_reference_sets


def purpose_wire():
    return {'purpose_span_ids': ['s1'], 'purpose_reason': 'Synthetic source purpose.',
            'purpose_selection': '2.5.1', 'candidate_checks': {'2.5.1': {
                'evidence_span_ids': ['s1'], 'purpose_evidence_span_ids': ['s1'],
                'reason': 'Synthetic direct activity.', 'purpose_comparison': 'Synthetic comparison.',
                'decision': 'RELATED', 'relevance_basis': 'DIRECT_ACTIVITY',
                'nonprimary_role': 'SUPPORTING_SECTION'}}}


def test_explicit_selection_preserves_independently_generated_decision():
    wire = purpose_wire()
    before = deepcopy(wire)
    raw, events = decode_single_purpose(json.dumps(wire), ['2.5.1'])
    result = json.loads(raw)
    assert wire == before
    assert result['primary_control_id'] == '2.5.1'
    assert result['candidate_checks']['2.5.1']['decision'] == 'RELATED'
    assert result['candidate_checks']['2.5.1']['document_role'] == 'MAIN_PURPOSE'
    assert events[0]['operation'] == 'DECODE_SINGLE_PURPOSE_SELECTION'


@pytest.mark.parametrize('selection', ['AMBIGUOUS', 'INSUFFICIENT'])
def test_unresolved_purpose_does_not_invent_a_representative(selection):
    wire = purpose_wire()
    wire['purpose_selection'] = selection
    raw, _ = decode_single_purpose(json.dumps(wire), ['2.5.1'])
    assert json.loads(raw)['primary_control_id'] is None


def test_inconsistent_model_labels_are_rejected_not_silently_rewritten():
    wire = purpose_wire()
    wire['candidate_checks']['2.5.1']['nonprimary_role'] = 'TOPIC_ONLY'
    with pytest.raises(ValueError, match='inconsistent decision/basis/nonprimary role'):
        decode_single_purpose(json.dumps(wire), ['2.5.1'])


def test_default_generation_schema_constrains_label_consistency(mapping_input):
    schema = build_single_purpose_schema(mapping_input, consistent_labels=True)
    rows = schema['properties']['candidate_checks']['properties']
    for row in rows.values():
        assert row['anyOf']
        for branch in row['anyOf']:
            props = branch['properties']
            if props['decision'].get('enum') == ['RELATED']:
                assert 'TOPIC_ONLY' not in props['nonprimary_role']['enum']
    def walk(value):
        if isinstance(value, dict):
            if 'enum' in value:
                assert value['enum'], 'Do not send an empty enum to the LLM server'
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)


def test_reference_deduplication_cannot_change_a_verdict_or_unknown_id():
    raw, changes = normalize_reference_sets(json.dumps({
        'decision': 'UNCERTAIN', 'source_span_ids': ['s1', 's1', 'unknown']}))
    assert json.loads(raw) == {'decision': 'UNCERTAIN', 'source_span_ids': ['s1', 'unknown']}
    assert changes


@pytest.mark.parametrize('bad_citation', [False, True])
def test_site_search_entry_uses_v14_or_stops_on_invalid_input(
        monkeypatch, mapping_input, good_response, bad_citation):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    source = Path(__file__).resolve().parents[2] / 'phase1_검색/judgment_pipeline.py'
    judgment = source.parents[1] / 'phase1_판단'
    monkeypatch.setitem(sys.modules, 'judgment_adapter', SimpleNamespace(
        JUDGMENT=judgment, to_mapping_input=lambda _: mapping_input.model_dump(mode='json')))
    spec = importlib.util.spec_from_file_location('v14_site_pipeline_test', source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.delenv('PHASE1_PRIMARY_REVIEW', raising=False)
    monkeypatch.delenv('PHASE1_SELECTIVE_REVIEW', raising=False)
    output = LLMMappingOutput.model_validate_json(good_response)
    output.mapped_controls[0].llm_confidence = 0.75
    if bad_citation:
        output.mapped_controls[0].citations[0].quote = 'This quotation is absent from the source.'
    run = LLMRunResult(raw_response=output.model_dump_json(), call_error=None,
                       attempts=1, retry_count=0)
    monkeypatch.setattr(module, 'run_mapping_llm', lambda *a, **k: run)
    check = Mock(return_value=run)
    monkeypatch.setattr(module, 'run_check', check)
    monkeypatch.setattr(module, 'latest_assessment', lambda _: None)
    repair = Mock(side_effect=lambda run, inp, assessment, **kw: (run, assessment))
    monkeypatch.setattr('semantic_repair.repair_once', repair)
    result = module.run_judgment({}, model='offline-model')
    if bad_citation:
        assert not result.mapping_result.validation.passed
        check.assert_not_called()
        repair.assert_not_called()
    else:
        check.assert_called_once()
        repair.assert_called_once()
        for flag in ('source_claims_v1', 'source_claim_ids', 'keyed_source_review',
                     'robust_contract', 'purpose_contract_v2', 'purpose_contract_v3',
                     'purpose_contract_v4', 'purpose_contract_v5', 'purpose_label_contract'):
            assert check.call_args.kwargs[flag] is True
            assert repair.call_args.kwargs[flag] is True
        assert repair.call_args.kwargs['reuse_verified_purpose'] is True
        assert repair.call_args.kwargs['document_scope_contract'] is True
        # Missing second-pass audit cannot become an automatic pass.
        assert result.mapping_result.validation.passed
        assert result.mapping_result.human_review.required
