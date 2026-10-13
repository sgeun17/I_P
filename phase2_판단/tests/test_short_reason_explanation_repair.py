"""Offline, neutral fixtures: explanation recovery is not verdict recovery."""
from copy import deepcopy
from dataclasses import replace
import json

from jsonschema import Draft202012Validator
import pytest

from checklist_adapter import load_reason_code_catalog
import judgment_harness as harness
from phase1_runtime import DEFAULT_RETRY_POLICY, ErrorCode, LLMCallError
from validation.contracts import interface_module
import validated_pipeline as vp


CODES = load_reason_code_catalog()['codes']
NO_RETRY = replace(DEFAULT_RETRY_POLICY, max_retries=0)
WARNING = {'code': 'P2E506', 'message': '판정 이유가 짧다', 'severity': 'warning'}
MODE = 'REASON_EXPLANATION_CORRECTION'
OBSERVATION = '대상: 작업 A | 확인 사항: 담당자 지정'
EXPANSION = '담당자 지정 기록은 확인되지만 대상 작업의 검토 기준 내용은 확인되지 않아 기준 마련 여부를 확정할 수 없습니다.'


@pytest.fixture
def case():
    item = {'item_id': '2.5.1-Q01', 'control_id': '2.5.1', 'check_kind': 'procedure',
            'question': '대상 작업에 필요한 검토 기준이 마련되어 있는가?',
            'evidence_rule': {'met': '해당 작업의 검토 기준이 확인됨.',
                              'not_met': '검토 기준을 마련하지 않았다는 직접 근거가 있음.',
                              'unknown': '기준의 내용을 확인할 근거가 부족함.'}}
    context = {'chunks': [
        {'chunk_id': f'E0001_v1_c000{i}', 'text': text, 'heading': '검토 자료',
         'page_start': i + 1, 'page_end': i + 1, 'role': 'evidence' if i == 0 else 'context',
         'source': 'parser', 'file_type': 'txt', 'source_file': 'neutral.txt'}
        for i, text in enumerate((OBSERVATION, '대상 작업 A의 검토 자료입니다.'))]}
    prior = {'item_id': item['item_id'], 'result': 'UNKNOWN', 'reason': '필요한 정보',
             'reason_codes': ['P2_U_EVIDENCE_INSUFFICIENT'],
             'citations': [{'chunk_id': 'E0001_v1_c0000', 'page': 1, 'quote': OBSERVATION}]}
    return item, context, prior


def invoke_editor(case, generate, **kwargs):
    item, context, prior = case
    return harness.run_item_judgment(item, context, model='offline', reason_codes=CODES,
        semantic_feedback={'mode': MODE, 'prior_output': prior, 'quality_diagnostic': [WARNING]},
        prompt_profile='compact_v2', llm_call=generate, sleeper=lambda _: None, **kwargs)


def supported(**changes):
    result = {'verdict': 'SUPPORTED', 'reason': '원문에서 확인된 부분과 미확인 기준을 구분했습니다.',
              'confidence': .95, 'unsupported_conditions': []}
    result.update(changes)
    return json.dumps(result, ensure_ascii=False)


def run_pipeline(case, edit=EXPANSION, *, span_mode=False, review=None, **kwargs):
    item, context, prior = case
    generations, reviews = [], []
    def generate(system, user, model, schema, **options):
        generations.append((system, user, schema))
        if len(generations) == 1:
            initial = deepcopy(prior)
            if span_mode:
                initial.pop('citations')
                initial['citation_span_ids'] = ['S00001']
            return json.dumps(initial, ensure_ascii=False)
        assert set(schema['properties']) == {'reason'}
        if isinstance(edit, Exception):
            raise edit
        return json.dumps({'reason': edit}, ensure_ascii=False)
    def check(system, user, *args, **options):
        reviews.append(json.loads(user))
        return supported() if review is None else review
    final, audit = vp.run_validated_item(item, context, model='offline', reason_codes=CODES,
        evidence_id='E0001', version=1, semantic_repair=True, prompt_profile='compact_v2',
        llm_call=generate, self_check_call=check, sleeper=lambda _: None,
        retry_policy=NO_RETRY, citation_span_selection=span_mode, **kwargs)
    return final, audit, generations, reviews


@pytest.mark.parametrize('span_mode', [False, True])
def test_private_reason_contract_preserves_fields_sources_and_span_provenance(case, span_mode, monkeypatch):
    before = deepcopy(case)
    def forbidden(*args, **kwargs):
        raise AssertionError('Reason-only editing must not prepare/adapt/materialize spans')
    for name in ('prepare_spans', 'materialize', 'adapt_prompts'):
        monkeypatch.setattr(harness, name, forbidden)
    def generate(system, user, model, schema, **kwargs):
        assert set(schema['properties']) == set(schema['required']) == {'reason'}
        assert schema['additionalProperties'] is False
        assert 'anyOf' not in schema and 'allOf' not in schema
        Draft202012Validator.check_schema(schema)
        payload = json.loads(user.split('\n', 2)[2])
        assert list(payload)[-1] == 'checklist_item'
        assert payload['checklist_item'] == case[0]
        assert payload['judgment_rules'] == 'Explicit supplied rules'
        assert payload['organization_context'] == {'scope': 'neutral'}
        assert payload['frozen_citations'] == case[2]['citations']
        assert payload['preserved_judgment']['result'] == 'UNKNOWN'
        for delivered, original in zip(payload['evidence_context']['chunks'], case[1]['chunks']):
            for key in ('text', 'heading', 'page_start', 'page_end', 'role'):
                assert delivered[key] == original[key]
        assert len(payload['evidence_context']['chunks']) == len(case[1]['chunks'])
        assert 'source_spans' not in payload
        assert '정답이 아니다' in system
        return json.dumps({'reason': EXPANSION}, ensure_ascii=False)
    result = invoke_editor(case, generate, citation_span_selection=span_mode,
                           global_rules='Explicit supplied rules', organization_context={'scope': 'neutral'})
    assert result.succeeded and result.parsed_output == {**case[2], 'reason': EXPANSION}
    assert result.prompt_version == harness.EXPLANATION_EDITOR_VERSION
    assert result.citation_generation_mode == 'preserved_existing_citations'
    assert result.source_spans is None and result.attempts == 1
    assert case == before


@pytest.mark.parametrize('field,value', [
    ('result', 'MET'), ('result', 'UNKNOWN'), ('item_id', '2.5.1-Q01'),
    ('reason_codes', ['P2_U_EVIDENCE_INSUFFICIENT']), ('citations', []),
    ('citation_span_ids', ['S00001']), ('error_code', 'E202'),
    ('check_kind', 'procedure'), ('critical', True), ('gold', 'MET')])
def test_editor_rejects_extra_fields_before_server_merge(case, field, value):
    response = {'reason': EXPANSION, field: value}
    result = invoke_editor(case, lambda *a, **k: json.dumps(response, ensure_ascii=False), retry_policy=NO_RETRY)
    assert not result.succeeded and result.final_error_code == 'E202'
    assert result.parsed_output is None and result.attempts == 1
    assert 'Additional properties' in ' '.join(result.validation_messages)
    assert json.loads(result.attempt_history[0]['raw_response']) == response


def test_private_format_retry_never_reintroduces_verdict_or_span_generation(case):
    requests = []
    def generate(system, user, model, schema, **kwargs):
        requests.append(user)
        assert set(schema['properties']) == {'reason'}
        return 'bad json' if len(requests) == 1 else json.dumps({'reason': EXPANSION}, ensure_ascii=False)
    result = invoke_editor(case, generate, citation_span_selection=True)
    assert result.succeeded and result.attempts == 2 and result.retry_count == 1
    retry = requests[1].split('[편집 응답 형식 재시도]', 1)[1]
    assert 'reason만 반환한다' in retry and 'E201' in retry
    assert '현재 내부 출력은 citation_span_ids' not in retry
    assert result.parsed_output['citations'] == case[2]['citations']
    assert result.attempt_history[0]['raw_response'] == 'bad json'


def test_success_passes_full_validation_and_review_without_changing_judgment(case, monkeypatch):
    before = deepcopy(case)
    validations = []
    original_validator = vp.validate_item
    def validate(*args, **kwargs):
        validations.append(deepcopy(args[0]))
        return original_validator(*args, **kwargs)
    monkeypatch.setattr(vp, 'validate_item', validate)
    final, audit, generations, reviews = run_pipeline(case)
    assert len(generations) == 2 and len(reviews) == 1
    assert audit['total_model_calls'] == 3 and len(validations) >= 2
    assert final['reason'] == EXPANSION
    for key in ('item_id', 'result', 'reason_codes', 'citations'):
        assert final[key] == case[2][key]
    assert reviews[0]['proposed']['result'] == 'UNKNOWN'
    assert reviews[0]['cited_evidence'] == case[2]['citations']
    assert len(reviews[0]['available_evidence_context']['chunks']) == 2
    repair = audit['semantic_repair']
    assert repair['mode'] == MODE and repair['status'] == 'ACCEPTED'
    assert repair['result_changed'] is False and repair['explanation_changed'] is True
    assert repair['initial_output']['reason'] == case[2]['reason']
    assert repair['initial_audit']['issues'] == [WARNING]
    assert audit['issues'] == [] and audit['explanation_edit_validation']['issues'] == []
    assert audit['run']['citation_generation_mode'] == 'preserved_existing_citations'
    assert case == before


def test_materialized_span_initial_is_replayed_without_any_new_span_selection(case, monkeypatch):
    # A saved, already validated span-derived initial is the edit boundary.
    # Do not exercise/fix the unrelated legacy-only initial span prompt adapter.
    original_once = vp._run_validated_item_once
    calls, generations, reviews = [], [], []
    def once(item, context, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            report = initial_audit()
            report['run']['citation_generation_mode'] = 'phase2_span_selection_v1'
            return {**deepcopy(case[2]), 'check_kind': case[0]['check_kind']}, report
        return original_once(item, context, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('Existing public citations must not be rematerialized')
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    for name in ('prepare_spans', 'materialize', 'adapt_prompts'):
        monkeypatch.setattr(harness, name, forbidden)
    def generate(system, user, model, schema, **kwargs):
        generations.append(schema)
        assert set(schema['properties']) == {'reason'}
        return json.dumps({'reason': EXPANSION}, ensure_ascii=False)
    def review(system, user, *args, **kwargs):
        reviews.append(json.loads(user)); return supported()
    final, audit = vp.run_validated_item(case[0], case[1], model='offline', reason_codes=CODES,
        evidence_id='E0001', version=1, semantic_repair=True, prompt_profile='compact_v2',
        llm_call=generate, self_check_call=review, citation_span_selection=True,
        retry_policy=NO_RETRY, sleeper=lambda _: None)
    assert len(calls) == 2 and len(generations) == len(reviews) == 1
    assert final['citations'] == case[2]['citations'] and final['reason'] == EXPANSION
    assert audit['semantic_repair']['status'] == 'ACCEPTED' and audit['total_model_calls'] == 3
    assert audit['run']['citation_generation_mode'] == 'preserved_existing_citations'
    assert audit['semantic_repair']['initial_audit']['run']['citation_generation_mode'] == 'phase2_span_selection_v1'


@pytest.mark.parametrize('text', ['필요한 정보', ' 필요한  정보 ', '“필요한 정보”', '', '   ',
    '추가 확인', ' ' * 20 + '필요한 정보' + ' ' * 20])
def test_noop_blank_or_still_short_retains_original_without_another_repair(case, text):
    final, audit, generations, reviews = run_pipeline(case, edit=text)
    assert len(generations) == 2 and len(reviews) == 0
    assert final['reason'] == case[2]['reason'] and final['citations'] == case[2]['citations']
    assert audit['issues'] == [WARNING]
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert audit['total_model_calls'] == 2


def test_warning_boundary_uses_official_validator_not_a_new_threshold(case):
    minimum = interface_module('errors').THRESHOLDS['min_reason_length']
    final, audit, generations, reviews = run_pipeline(case, edit='가' * (minimum - 1))
    assert final['reason'] == case[2]['reason'] and not reviews
    assert audit['semantic_repair']['candidate_audit']['issues'] == [WARNING]
    # Length equality only permits review; semantic quality is still independently rejected.
    final, audit, generations, reviews = run_pipeline(case, edit='가' * minimum,
        review=supported(verdict='UNSUPPORTED', unsupported_conditions=['문항과 근거의 연결 설명 없음']))
    assert len(reviews) == 1 and audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert final['reason'] == case[2]['reason']


@pytest.mark.parametrize('review', [
    supported(verdict='UNSUPPORTED', unsupported_conditions=['기존 원문에 있는 사실을 없다고 함']),
    supported(verdict='UNCERTAIN', unsupported_conditions=['대상 연결 미확인']),
    supported(verdict='CONFLICT', unsupported_conditions=['같은 범위 근거 충돌']),
    supported(confidence=.1), supported(unsupported_conditions=['미확인 조건']), '{bad json'])
def test_review_failure_never_approves_expansion_or_changes_original(case, review):
    final, audit, generations, reviews = run_pipeline(case, review=review)
    assert len(generations) == 2 and len(reviews) == 1
    assert audit['total_model_calls'] == 3
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert final['reason'] == case[2]['reason'] and final['reason_codes'] == case[2]['reason_codes']
    assert audit['issues'] == [WARNING]


def test_editor_transport_failure_retains_original_quality_warning(case):
    final, audit, generations, reviews = run_pipeline(case,
        edit=LLMCallError(ErrorCode.LLM_TIMEOUT, 'offline synthetic timeout'))
    assert final['reason'] == case[2]['reason'] and not reviews
    assert audit['issues'] == [WARNING] and audit['total_model_calls'] == 2
    assert audit['semantic_repair']['candidate_audit']['run']['final_error_code']


def initial_audit():
    return {'run': {'attempts': 1}, 'issues': [deepcopy(WARNING)], 'review_signals': [],
            'self_check': {'verdict': 'NOT_APPLICABLE_UNKNOWN'}}


@pytest.mark.parametrize('excluded', ['disabled', 'legacy', 'other_warning', 'error_severity',
    'technical', 'guard', 'signal', 'binding', 'conflict', 'not_run', 'injection', 'freshness', 'format', 'not_met'])
def test_only_short_warning_unknown_is_eligible(case, monkeypatch, excluded):
    initial = deepcopy(case[2]); report = initial_audit(); options = {}
    if excluded == 'other_warning': report['issues'].append({'code': 'OTHER', 'severity': 'warning'})
    if excluded == 'error_severity': report['issues'][0]['severity'] = 'error'
    if excluded == 'technical':
        initial['error_code'] = 'E202'
        report['run'].update(final_error_code='E202', parsed_output=None,
                             validation_messages=['GEN_REASON_CODE_ONLY'])
    if excluded == 'guard': report['semantic_guards'] = [{'decision': 'REVIEW_REQUIRED'}]
    if excluded == 'signal': report['review_signals'] = ['SELF_CHECK_UNCERTAIN']
    if excluded == 'binding': report['reason_citation_binding'] = {'findings': [{'span': 'x'}]}
    if excluded == 'conflict': report['self_check'] = {'verdict': 'CONFLICT'}
    if excluded == 'not_run': report['self_check'] = {'verdict': 'NOT_RUN'}
    if excluded == 'injection': monkeypatch.setattr(vp, 'injection_suspected', lambda _: True)
    if excluded == 'freshness':
        options['freshness_policy'] = {'max_age_months': 6}; report['freshness'] = {'status': 'DATE_MISSING'}
    if excluded == 'format':
        options['format_policy'] = {}; report['evidence_format'] = {'status': 'DENIED'}
    if excluded == 'not_met': initial['result'] = 'NOT_MET'
    calls = []
    def once(*args, **kwargs):
        calls.append(kwargs); return deepcopy(initial), deepcopy(report)
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    final, audit = vp.run_validated_item(case[0], case[1], semantic_repair=excluded != 'disabled',
        prompt_profile='legacy' if excluded == 'legacy' else 'compact_v2', **options)
    assert len(calls) == 1 and final == initial


@pytest.mark.parametrize('field,value', [('item_id', '2.5.1-Q02'), ('result', 'MET'),
    ('reason_codes', ['P2_U_TIME_OR_VERSION_UNCLEAR']), ('citations', [])])
def test_downstream_frozen_field_changes_cannot_be_accepted(case, monkeypatch, field, value):
    calls = []
    def once(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1: return deepcopy(case[2]), initial_audit()
        candidate = {**deepcopy(case[2]), 'reason': EXPANSION, field: value}
        return candidate, {'run': {'attempts': 1}, 'issues': [], 'review_signals': [],
            'self_check': {'verdict': 'SUPPORTED', 'unsupported_conditions': [], 'attempts': 1}}
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    final, audit = vp.run_validated_item(case[0], case[1], semantic_repair=True, prompt_profile='compact_v2')
    assert len(calls) == 2 and final == case[2]
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert audit['semantic_repair']['candidate_audit']['explanation_edit_validation']['issues']
    assert audit['total_model_calls'] == 3


@pytest.mark.parametrize('first_mode', ['regular', 'citation'])
def test_short_candidate_after_existing_repair_gets_no_second_opportunity(case, monkeypatch, first_mode):
    initial = deepcopy(case[2]); initial['reason'] = EXPANSION
    report = initial_audit(); report['issues'] = []
    if first_mode == 'citation':
        initial.update(result='MET', reason_codes=[])
        report.update(self_check={'verdict': 'SUPPORTED', 'unsupported_conditions': [], 'attempts': 1},
                      review_signals=['SELF_CHECK_UNCERTAIN'], reason_citation_binding={'findings': []})
        # A real diagnostic is used so the existing post-edit binding check remains executable.
        initial['reason'] = "검토 기준은 '대상 작업 A의 검토 자료입니다.'에서 확인됩니다."
        report['reason_citation_binding'] = harness.inspect_reason_citation_binding(case[0], initial, case[1])
        assert report['reason_citation_binding']['findings']
    calls = []
    def once(*args, **kwargs):
        calls.append(kwargs)
        return (deepcopy(initial), deepcopy(report)) if len(calls) == 1 else (deepcopy(case[2]), initial_audit())
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    final, audit = vp.run_validated_item(case[0], case[1], semantic_repair=True, prompt_profile='compact_v2')
    assert len(calls) == 2 and final == initial
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert calls[1]['semantic_feedback'].get('mode') != MODE


def test_explanation_candidate_binding_issue_gets_no_citation_repair(case):
    edit = "추가 사실 '대상 작업 A의 검토 자료입니다.'를 확인했지만 검토 기준 내용은 아직 확인되지 않습니다."
    final, audit, generations, reviews = run_pipeline(case, edit=edit)
    assert len(generations) == 2 and len(reviews) == 1
    assert final['reason'] == case[2]['reason']
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert audit['semantic_repair']['candidate_audit']['reason_citation_binding']['findings']


def test_code_only_technical_failure_never_enters_explanation_recovery(case):
    generations = []
    response = {**deepcopy(case[2]), 'reason': 'P2_U_EVIDENCE_INSUFFICIENT'}
    def generate(system, user, model, schema, **kwargs):
        generations.append(schema)
        assert 'result' in schema['properties']  # Original judgment/format retry only.
        return json.dumps(response, ensure_ascii=False)
    def forbidden_review(*args, **kwargs):
        raise AssertionError('Invalid original must not be semantically reviewed')
    final, audit = vp.run_validated_item(case[0], case[1], model='offline', reason_codes=CODES,
        evidence_id='E0001', version=1, semantic_repair=True, prompt_profile='compact_v2',
        llm_call=generate, self_check_call=forbidden_review, sleeper=lambda _: None,
        retry_policy=replace(NO_RETRY, max_retries=1))
    assert final['error_code'] == 'E202' and audit['run']['parsed_output'] is None
    assert len(generations) == audit['total_model_calls'] == 2
    assert all('GEN_REASON_CODE_ONLY' in ' '.join(row['validation_messages'])
               for row in audit['run']['attempt_history'])
    assert audit['semantic_repair']['status'] == 'SKIPPED'


def test_disabled_reviewer_cannot_approve_explanation(case):
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        return json.dumps(case[2] if len(calls) == 1 else {'reason': EXPANSION}, ensure_ascii=False)
    final, audit = vp.run_validated_item(case[0], case[1], model='offline', reason_codes=CODES,
        evidence_id='E0001', version=1, semantic_repair=True, prompt_profile='compact_v2',
        llm_call=generate, self_check_call=None, sleeper=lambda _: None, retry_policy=NO_RETRY)
    assert len(calls) == audit['total_model_calls'] == 2
    assert final['reason'] == case[2]['reason'] and audit['issues'] == [WARNING]
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert audit['semantic_repair']['candidate_audit']['self_check']['verdict'] == 'NOT_RUN'


@pytest.mark.parametrize('guard_key,guard_value', [
    ('semantic_guards', [{'decision': 'REVIEW_REQUIRED'}]),
    ('review_signals', ['SELF_CHECK_UNCERTAIN'])])
def test_candidate_guard_or_signal_cannot_be_accepted_even_with_unchanged_fields(case, monkeypatch, guard_key, guard_value):
    calls = []
    def once(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1: return deepcopy(case[2]), initial_audit()
        return {**deepcopy(case[2]), 'reason': EXPANSION}, {
            'run': {'attempts': 1}, 'issues': [], 'review_signals': [],
            'self_check': {'verdict': 'SUPPORTED', 'unsupported_conditions': [], 'attempts': 1},
            guard_key: guard_value}
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    final, audit = vp.run_validated_item(case[0], case[1], semantic_repair=True, prompt_profile='compact_v2')
    assert len(calls) == 2 and final == case[2]
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
