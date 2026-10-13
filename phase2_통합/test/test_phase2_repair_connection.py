"""Website runner contract checks with in-memory stores; no DB or model calls."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def setup(monkeypatch):
    # The runner discovers sibling modules; restore all path/module changes per test.
    monkeypatch.setattr(sys, 'path', list(sys.path))
    path = Path(__file__).resolve().parents[1] / 'phase2_runner.py'
    spec = importlib.util.spec_from_file_location('site_runner_under_test', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    store = ModuleType('phase2_result_store')
    store.save = Mock(return_value=17)
    store.exclude = Mock()
    store.unexclude = Mock()
    store.by_evidence = Mock(return_value=[])
    mapping = ModuleType('phase1_mapping_store')
    mapping.save = Mock(return_value={'saved': 1, 'changed': False, 'superseded': []})
    builder = ModuleType('build_phase2_input')
    builder.load_checklist_scope = Mock(return_value=({'2.5.1'}, 'neutral-checklist'))
    target = {'control_id': '2.5.1', 'judge': True, 'checklist_in_scope': True}
    payload = {'source_versions': {'kb_sha256': 'neutral-hash'}, 'targets': [target]}
    builder.build = Mock(side_effect=lambda *a, **k: deepcopy(payload))
    builder.judge_targets = Mock(side_effect=lambda p: [t for t in p['targets'] if t['judge']])
    identity = ModuleType('kb_identity')
    identity.kb_sha256 = Mock(return_value='neutral-hash')
    judge = ModuleType('validated_pipeline')
    judge.validate_input = Mock(return_value=[])
    judge.run_control_judgment = Mock(return_value={
        'processing_status': 'REVIEW_REQUIRED',
        'output': {'overall_result': 'UNKNOWN'},
        'audit': {'prompt_profile': 'compact_v2'}})
    for module in (store, mapping, builder, identity, judge):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(runner, '_read', lambda _: {'approved': True})
    monkeypatch.setattr(runner, 'load_controls', lambda: {'2.5.1': 'Neutral control'})
    p1 = {'version': 1, 'processing_status': 'COMPLETED', 'match_status': 'MATCHED',
          'human_review': {'required': True, 'reasons': ['R201']}}
    return SimpleNamespace(runner=runner, store=store, mapping=mapping, builder=builder,
                           judge=judge, payload=payload, p1=p1)


def invoke(setup, **kwargs):
    return setup.runner.run_evidence('E-SITE', phase1_result=deepcopy(setup.p1), chunks=[],
                                    model='offline-model', as_of='2026-10-14', **kwargs)


def test_website_passes_compact_profile_and_bounded_repair_to_judgment(setup):
    result = invoke(setup)
    setup.judge.run_control_judgment.assert_called_once()
    call = setup.judge.run_control_judgment.call_args
    assert call.args == (setup.payload, '2.5.1')
    assert call.kwargs['prompt_profile'] == 'compact_v2'
    assert call.kwargs['semantic_repair'] is True
    assert call.kwargs['model'] == 'offline-model'
    assert call.kwargs['critical_policy'] == {'mode': 'explicit'}
    assert call.kwargs['as_of'] == '2026-10-14'
    assert call.kwargs['controls_sha256'] == 'neutral-hash'
    assert result['judged'] == 1 and result['failed'] == 0
    saved = setup.store.save.call_args
    assert saved.kwargs['row'] == {'phase1_review_required': True, 'phase1_review_reasons': ['R201']}
    assert saved.kwargs['audit']['prompt_profile'] == 'compact_v2'


@pytest.mark.parametrize('field,status,reason', [
    ('processing_status', 'FAILED', 'FAILED'),
    ('match_status', 'NO_MATCH', 'SKIP_NO_MATCH')])
def test_phase1_stop_still_prevents_judgment(setup, field, status, reason):
    setup.p1[field] = status
    result = invoke(setup)
    assert result['judged'] == 0 and result['skipped'][0]['reason_code'] == reason
    setup.judge.run_control_judgment.assert_not_called()
    setup.store.save.assert_not_called()


def test_invalid_input_is_not_sent_to_repair(setup):
    setup.judge.validate_input.return_value = [
        {'code': 'P2E005', 'severity': 'error', 'message': 'Neutral invalid input'}]
    result = invoke(setup)
    assert result['skipped'][0]['reason_code'] == 'HOLD_PHASE1_INVALID'
    setup.judge.run_control_judgment.assert_not_called()
    setup.store.save.assert_not_called()


def test_only_missing_preserves_existing_version_result(setup):
    setup.store.by_evidence.return_value = [{'control_id': '2.5.1'}]
    result = invoke(setup, only_missing=True)
    setup.store.by_evidence.assert_called_once_with('E-SITE', version=1)
    setup.judge.run_control_judgment.assert_not_called()
    setup.store.save.assert_not_called()
    assert result['judged'] == 0


def test_mapping_failure_stops_before_judgment(setup):
    setup.mapping.save.side_effect = RuntimeError('Neutral store unavailable')
    with pytest.raises(setup.runner.RunnerError) as error:
        invoke(setup)
    assert error.value.code == 'MAPPING_PERSIST_FAILED'
    setup.judge.run_control_judgment.assert_not_called()


def test_judgment_failure_is_recorded_without_saving_a_success(setup):
    setup.judge.run_control_judgment.return_value = {
        'processing_status': 'FAILED', 'output': None,
        'audit': {'issues': [{'code': 'E202', 'severity': 'error'}]}}
    result = invoke(setup)
    assert result['failed'] == 1 and result['judged'] == 0
    assert result['results'][0]['error_codes'] == ['E202']
    setup.store.save.assert_not_called()
    assert any(c.args[1] == 'FAILED' for c in setup.store.exclude.call_args_list)
