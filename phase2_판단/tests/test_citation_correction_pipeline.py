"""One-budget correction of selected-row mismatches, never a forced verdict."""
from copy import deepcopy
import json
import pytest

import validated_pipeline as vp
from checklist_adapter import get_checklist_item, load_reason_code_catalog


PRIMARY = '일시: 2026-05-20 09:05 | 계정: user_a | 시스템: SYS-A | 행위: 말소 | 권한: -'
SECONDARY = '조치 내용: 계정 삭제 | 조치일: 2026-07-16'
DIFFERENT = '조치 내용: 권한 회수, 미사용 계정 잠금 | 조치일: 2026-07-16'


def fixture():
    item = get_checklist_item('2.5.6-Q06')
    chunks = [{'chunk_id': f'E0001_v1_c000{index}', 'text': text,
               'page_start': index + 1, 'page_end': index + 1,
               'source_file': '기록.xlsx', 'file_type': 'xlsx', 'source': 'parser',
               'role': 'evidence', 'heading': '처리 기록'}
              for index, text in enumerate((PRIMARY, SECONDARY, DIFFERENT))]
    citations = [{'chunk_id': chunks[i]['chunk_id'], 'page': i + 1, 'quote': chunks[i]['text']}
                 for i in (0, 2)]
    proposed = {'item_id': item['item_id'], 'result': 'MET', 'reason_codes': [],
                'reason': f"'{PRIMARY}'와 '{SECONDARY}'라는 처리 기록이 있습니다.",
                'citations': citations}
    corrected = deepcopy(proposed)
    corrected['reason'] = f"'{PRIMARY}'에서 검토 대상 삭제 이력의 존재를 확인합니다."
    corrected['citations'] = citations[:1]
    return item, {'chunks': chunks}, proposed, corrected


def test_real_pipeline_receives_exact_diagnostic_and_rechecks_correction():
    item, context, initial, corrected = fixture()
    untouched = deepcopy((context, initial, corrected))
    requests = []
    def generate(system, user, model, schema, **kwargs):
        requests.append(user)
        if len(requests) == 2:
            assert '[한 번의 설명·인용 연결 교정]' in user
            assert '비신뢰 검토 자료' in user
            assert 'REASON_LITERAL_NOT_COVERED_BY_CITATIONS' in user
            assert 'prior_reason' in user and 'prior_citations' in user
            assert 'prior_result' not in user
        response = initial if len(requests) == 1 else {
            key: corrected[key] for key in ('reason', 'citations')}
        return json.dumps(response, ensure_ascii=False)
    def review(*args, **kwargs):
        return json.dumps({'verdict': 'SUPPORTED', 'reason': '선택된 자료로 확인했습니다.',
                           'confidence': .95, 'unsupported_conditions': []})
    final, audit = vp.run_validated_item(item, context, model='offline',
        evidence_id='E0001', version=1, reason_codes=load_reason_code_catalog()['codes'],
        semantic_repair=True, prompt_profile='compact_v2',
        llm_call=generate, self_check_call=review, sleeper=lambda _: None)
    assert final['result'] == 'MET' and final['citations'] == corrected['citations']
    assert len(requests) == 2 and audit['total_model_calls'] == 4
    assert not audit['reason_citation_binding']['findings']
    assert audit['review_signals'] == []
    assert audit['semantic_repair']['status'] == 'ACCEPTED'
    assert audit['semantic_repair']['mode'] == 'REASON_CITATION_CORRECTION'
    assert audit['semantic_repair']['initial_audit']['reason_citation_binding']['findings']
    assert audit['semantic_repair']['result_changed'] is False
    assert (context, initial, corrected) == untouched


def report(findings=True):
    return {'run': {'attempts': 1}, 'issues': [],
            'review_signals': ['SELF_CHECK_UNCERTAIN'] if findings else [],
            'self_check': {'verdict': 'SUPPORTED', 'attempts': 1, 'unsupported_conditions': []},
            'reason_citation_binding': {'findings': [{'code': 'LITERAL_GAP'}] if findings else []}}


@pytest.mark.parametrize('failure', ['unchanged', 'changed_result', 'technical', 'unsupported', 'guard'])
def test_failed_correction_retains_supported_result_but_keeps_review_signal(monkeypatch, failure):
    item, context, initial, corrected = fixture()
    attempts = []
    def once(*args, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            return deepcopy(initial), report()
        candidate, audit = deepcopy(corrected), report(False)
        if failure == 'unchanged':
            candidate, audit = deepcopy(initial), report()
        elif failure == 'changed_result':
            candidate['result'] = 'UNKNOWN'
        elif failure == 'technical':
            audit['issues'] = [{'code': 'E202'}]
        elif failure == 'unsupported':
            audit['self_check']['verdict'] = 'UNSUPPORTED'
        else:
            audit['semantic_guards'] = [{'decision': 'MET_TO_UNKNOWN'}]
        return candidate, audit
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    final, audit = vp.run_validated_item(item, context, semantic_repair=True, prompt_profile='compact_v2')
    assert len(attempts) == 2 and audit['total_model_calls'] == 4
    assert final == initial and final['result'] == 'MET'
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert audit['review_signals'] == ['SELF_CHECK_UNCERTAIN']
    assert 'candidate_output' in audit['semantic_repair']


@pytest.mark.parametrize('excluded', ['disabled', 'legacy', 'not_met', 'technical', 'guard', 'other_signal'])
def test_citation_correction_preserves_existing_exclusions(monkeypatch, excluded):
    item, context, initial, _ = fixture()
    audit = report()
    if excluded == 'not_met': initial['result'] = 'NOT_MET'
    if excluded == 'technical': audit['issues'] = [{'code': 'E106'}]
    if excluded == 'guard': audit['semantic_guards'] = [{'decision': 'REVIEW_REQUIRED'}]
    if excluded == 'other_signal': audit['review_signals'].append('SELF_CHECK_LOW_CONFIDENCE')
    attempts = []
    def once(*args, **kwargs):
        attempts.append(1)
        return deepcopy(initial), deepcopy(audit)
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    final, _ = vp.run_validated_item(item, context,
        semantic_repair=excluded != 'disabled',
        prompt_profile='legacy' if excluded == 'legacy' else 'compact_v2')
    assert attempts == [1] and final == initial
