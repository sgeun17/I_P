"""Offline contracts for one bounded reason/citation edit, never a verdict call."""
from copy import deepcopy
from dataclasses import replace
import json

from jsonschema import Draft202012Validator
import pytest

from checklist_adapter import get_checklist_item, load_reason_code_catalog
from judgment_harness import (CITATION_EDITOR_VERSION, citation_edit_issues,
                              run_item_judgment)
from phase1_runtime import DEFAULT_RETRY_POLICY, ErrorCode, LLMCallError
from reason_citation_binding import inspect_reason_citation_binding
import validated_pipeline as vp


PRIMARY = '일시: 2026-05-20 09:05 | 계정: user_a | 시스템: SYS-A | 행위: 말소 | 권한: -'
SECONDARY = '조치 내용: 계정 삭제 | 조치일: 2026-07-16'
DIFFERENT = '조치 내용: 권한 회수, 미사용 계정 잠금 | 조치일: 2026-07-16'
CODES = load_reason_code_catalog()['codes']
NO_RETRY = replace(DEFAULT_RETRY_POLICY, max_retries=0)


@pytest.fixture
def case():
    item = get_checklist_item('2.5.6-Q06')
    context = {'chunks': [
        {'chunk_id': f'E0001_v1_c000{index}', 'text': text,
         'page_start': index + 1, 'page_end': index + 1,
         'source_file': '처리기록.xlsx', 'file_type': 'xlsx', 'source': 'parser',
         'role': 'evidence' if index != 2 else 'context', 'heading': '처리 기록'}
        for index, text in enumerate((PRIMARY, DIFFERENT + '\n' + SECONDARY, '추가 원문은 모두 보존한다.'))]}
    primary = {'chunk_id': 'E0001_v1_c0000', 'page': 1, 'quote': PRIMARY}
    wrong_row = {'chunk_id': 'E0001_v1_c0001', 'page': 2, 'quote': DIFFERENT}
    prior = {'item_id': item['item_id'], 'result': 'MET', 'reason_codes': [],
             'reason': f"'{PRIMARY}'와 '{SECONDARY}'에서 삭제 처리 이력을 확인합니다.",
             'citations': [primary, wrong_row]}
    edit = {'reason': f"'{PRIMARY}'에 대상 계정 말소가 기록되어 검토 대상 삭제 이력의 존재를 확인합니다.",
            'citations': [primary]}
    feedback = {'mode': 'REASON_CITATION_CORRECTION', 'prior_output': prior,
                'review': {'verdict': 'SUPPORTED', 'reason': 'DO_NOT_EXPOSE_REVIEWER_ASSERTION'},
                'binding_diagnostic': inspect_reason_citation_binding(item, prior, context)}
    return item, context, prior, edit, feedback


def invoke(case, llm, **options):
    item, context, _, _, feedback = case
    return run_item_judgment(item, context, model='offline', reason_codes=CODES,
        semantic_feedback=feedback, prompt_profile='compact_v2', llm_call=llm,
        sleeper=lambda _: None, **options)


@pytest.mark.parametrize('span_mode', [False, True])
def test_editor_has_private_schema_retains_metadata_and_all_sources(case, span_mode):
    item, context, prior, edit, _ = case
    before = deepcopy(case)
    seen = []
    def generate(system, user, model, schema, **kwargs):
        field = 'citation_span_ids' if span_mode else 'citations'
        assert set(schema['properties']) == {'reason', field}
        assert set(schema['required']) == {'reason', field}
        assert schema['additionalProperties'] is False
        assert 'anyOf' not in schema and 'allOf' not in schema
        assert 'DO_NOT_EXPOSE_REVIEWER_ASSERTION' not in user
        payload = json.loads(user.split('\n', 2)[2])
        assert list(payload)[-1] == 'checklist_item'
        assert payload['checklist_item'] == item
        assert payload['organization_context'] == {'scope': '검토 범위'}
        assert payload['judgment_rules'] == 'EXPLICIT_RULES_ARE_PRESERVED'
        for actual, source in zip(payload['evidence_context']['chunks'], context['chunks']):
            assert actual['text'] == source['text']
            assert actual['role'] == source['role']
            assert actual['heading'] == source['heading']
            assert actual['page_start'] == source['page_start']
        assert len(payload['evidence_context']['chunks']) == len(context['chunks'])
        assert 'prior_result' not in payload and 'review' not in payload
        response = ({'reason': edit['reason'], field: ['S00001']} if span_mode else edit)
        Draft202012Validator.check_schema(schema)
        assert not list(Draft202012Validator(schema).iter_errors(response))
        seen.append((system, user, schema))
        return json.dumps(response, ensure_ascii=False)
    result = invoke(case, generate, citation_span_selection=span_mode,
                    global_rules='EXPLICIT_RULES_ARE_PRESERVED',
                    organization_context={'scope': '검토 범위'})
    assert result.succeeded and result.attempts == 1
    assert result.prompt_version == CITATION_EDITOR_VERSION
    assert result.parsed_output == {**prior, **edit}
    assert result.prompt_lengths['system_chars'] == len(seen[0][0])
    assert case == before


@pytest.mark.parametrize('field,value', [('result', 'UNKNOWN'), ('item_id', 'different'),
    ('reason_codes', []), ('error_code', 'E202'), ('check_kind', 'record')])
def test_editor_rejects_extra_metadata_before_merge(case, field, value):
    response = {**case[3], field: value}
    result = invoke(case, lambda *a, **k: json.dumps(response, ensure_ascii=False), retry_policy=NO_RETRY)
    assert not result.succeeded and result.final_error_code == 'E202'
    assert result.parsed_output is None and result.attempts == 1
    assert 'Additional properties' in ' '.join(result.validation_messages)
    assert json.loads(result.attempt_history[0]['raw_response']) == response


def test_editor_technical_retry_stays_with_private_edit_contract(case):
    requests = []
    def generate(system, user, model, schema, **kwargs):
        requests.append(user)
        assert set(schema['properties']) == {'reason', 'citations'}
        return 'not-json' if len(requests) == 1 else json.dumps(case[3], ensure_ascii=False)
    result = invoke(case, generate)
    assert result.succeeded and result.attempts == 2 and result.retry_count == 1
    assert '[편집 응답 형식 재시도]' in requests[1] and 'E201' in requests[1]
    assert 'UNKNOWN으로' not in requests[1].split('[편집 응답 형식 재시도]', 1)[1]
    assert result.attempt_history[0]['raw_response'] == 'not-json'


def test_editor_fallback_citation_definitions_resolve(case):
    # Exceed exact-row schema bound; the private schema must retain nested refs.
    case[1]['chunks'][2]['text'] = '추가 내용 ' * 5000
    def generate(system, user, model, schema, **kwargs):
        assert schema['$defs']['GenerationCitationItem'] == {'$ref': '#/$defs/Citation'}
        Draft202012Validator.check_schema(schema)
        assert not list(Draft202012Validator(schema).iter_errors(case[3]))
        return json.dumps(case[3], ensure_ascii=False)
    assert invoke(case, generate).succeeded


@pytest.mark.parametrize('editor', [False, True])
def test_retryable_transport_failure_preserves_both_modes(case, editor):
    item, context, prior, edit, _ = case
    requests = []
    def generate(system, user, model, schema, **kwargs):
        requests.append(user)
        if len(requests) == 1:
            raise LLMCallError(ErrorCode.LLM_TIMEOUT, 'offline simulated timeout')
        return json.dumps(edit if editor else prior, ensure_ascii=False)
    if editor:
        result = invoke(case, generate)
    else:
        result = run_item_judgment(item, context, model='offline', reason_codes=CODES,
                                  llm_call=generate, sleeper=lambda _: None)
    assert result.succeeded and result.attempts == 2 and result.retry_count == 1
    assert len(requests) == 2


@pytest.mark.parametrize('alter', ['invalid_quote', 'empty_citations', 'empty_reason', 'blank_reason'])
def test_editor_does_not_bypass_public_content_validation(case, alter):
    edit = deepcopy(case[3])
    if alter == 'invalid_quote': edit['citations'][0]['quote'] = '원문에 없는 인용'
    elif alter == 'empty_citations': edit['citations'] = []
    else: edit['reason'] = ' \n\t' if alter == 'blank_reason' else ''
    result = invoke(case, lambda *a, **k: json.dumps(edit, ensure_ascii=False), retry_policy=NO_RETRY)
    assert not result.succeeded and result.final_error_code == 'E202'


@pytest.mark.parametrize('change', ['identical', 'whitespace', 'reorder', 'unquote',
                                   'unquote_and_append', 'compact_and_append'])
def test_noop_or_retained_unquoted_literal_is_not_a_correction(case, change):
    item, context, prior, _, feedback = case
    candidate = deepcopy(prior)
    if change == 'whitespace': candidate['reason'] = '  ' + prior['reason'].replace(' ', '  ') + '\n'
    if change == 'reorder': candidate['citations'].reverse()
    if change in {'unquote', 'unquote_and_append', 'compact_and_append'}:
        candidate['reason'] = prior['reason'].replace("'", '')
    if change == 'compact_and_append':
        candidate['reason'] = candidate['reason'].replace(SECONDARY, SECONDARY.replace(' ', ''))
    if change in {'unquote_and_append', 'compact_and_append'}:
        candidate['reason'] += ' 다른 자료도 확인했습니다.'
        candidate['citations'].append({'chunk_id': 'E0001_v1_c0002', 'page': 3,
                                       'quote': context['chunks'][2]['text']})
    issues = citation_edit_issues(item, prior, candidate, context, feedback['binding_diagnostic'])
    assert 'CITATION_EDIT_UNRESOLVED_LITERAL' in {row['code'] for row in issues}
    if change not in {'unquote_and_append', 'compact_and_append'}:
        assert 'CITATION_EDIT_NO_SUBSTANTIVE_CHANGE' in {row['code'] for row in issues}


def test_removed_extraneous_clause_or_exact_coverage_are_not_mechanically_rejected(case):
    item, context, prior, edit, feedback = case
    assert not citation_edit_issues(item, prior, {**prior, **edit}, context, feedback['binding_diagnostic'])
    candidate = deepcopy(prior)
    candidate['reason'] = candidate['reason'].replace("'", '')
    candidate['citations'][1]['quote'] = SECONDARY
    # This only resolves literal coverage. Relevance/time scope still need review.
    assert not citation_edit_issues(item, prior, candidate, context, feedback['binding_diagnostic'])


@pytest.mark.parametrize('edit_kind', ['meaningful', 'unchanged', 'unquoted', 'unsupported_paraphrase'])
def test_pipeline_rechecks_editor_and_uses_only_one_shared_repair(case, edit_kind):
    item, context, prior, edited, _ = case
    if edit_kind == 'unchanged':
        edited = {key: deepcopy(prior[key]) for key in ('reason', 'citations')}
    elif edit_kind == 'unquoted':
        edited = {key: deepcopy(prior[key]) for key in ('reason', 'citations')}
        edited['reason'] = edited['reason'].replace("'", '')
    elif edit_kind == 'unsupported_paraphrase':
        edited = {**deepcopy(edited), 'reason': '검토 기간 밖 다른 시스템의 계정 삭제까지 완료한 사실을 입증합니다.'}
    generations, reviews = [], []
    def generate(system, user, model, schema, **kwargs):
        generations.append(schema)
        if len(generations) == 1:
            assert 'result' in schema['properties']
            return json.dumps(prior, ensure_ascii=False)
        assert set(schema['properties']) == {'reason', 'citations'}
        return json.dumps(edited, ensure_ascii=False)
    def review(system, user, *args, **kwargs):
        reviews.append(user)
        unsupported = edit_kind == 'unsupported_paraphrase' and len(reviews) == 2
        return json.dumps({'verdict': 'UNSUPPORTED' if unsupported else 'SUPPORTED',
            'reason': '인용과 대상·기간이 연결되지 않습니다.' if unsupported else '원문과 문항의 연결을 확인했습니다.',
            'confidence': .95, 'unsupported_conditions': ['대상·기간 연결'] if unsupported else []}, ensure_ascii=False)
    final, audit = vp.run_validated_item(item, context, model='offline', evidence_id='E0001',
        version=1, reason_codes=CODES, semantic_repair=True, prompt_profile='compact_v2',
        llm_call=generate, self_check_call=review, sleeper=lambda _: None)
    assert len(generations) == len(reviews) == 2
    assert audit['total_model_calls'] == 4
    assert audit['semantic_repair']['mode'] == 'REASON_CITATION_CORRECTION'
    if edit_kind == 'meaningful':
        assert audit['semantic_repair']['status'] == 'ACCEPTED'
        assert final['result'] == prior['result']
        assert final['reason'] == edited['reason'] and final['citations'] == edited['citations']
        assert not audit['reason_citation_binding']['findings']
        assert not audit['citation_edit_validation']['issues']
        assert SECONDARY not in final['reason']
    else:
        assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
        assert final['result'] == 'MET' and final['reason'] == prior['reason']
        assert audit['review_signals'] == ['SELF_CHECK_UNCERTAIN']
        candidate_audit = audit['semantic_repair']['candidate_audit']
        if edit_kind != 'unsupported_paraphrase':
            assert candidate_audit['citation_edit_validation']['issues']
        else:
            assert candidate_audit['self_check']['verdict'] == 'UNSUPPORTED'
