"""No network: repair bounds, preserved guards, and actual prompt composition."""
from copy import deepcopy
import json
import pytest
import validated_pipeline as vp
from grounding_prompts import build_system_prompt


def report(verdict='NOT_APPLICABLE_UNKNOWN', **extra):
    return dict(run={'attempts': 1}, issues=[], review_signals=[],
                self_check={'verdict': verdict, 'attempts': 0 if verdict == 'NOT_APPLICABLE_UNKNOWN' else 1,
                            'unsupported_conditions': []}, **extra)


def answer(result='UNKNOWN', **extra):
    return dict(item_id='1.2.3-Q02', result=result, reason='검토 필요',
                reason_codes=['P2_U_EVIDENCE_INSUFFICIENT'] if result == 'UNKNOWN' else [],
                citations=[], **extra)


def test_composed_prompt_keeps_previously_shadowed_rules():
    prompt = build_system_prompt({})
    for heading in ('[원문 종류와 실제 활동의 연결]', '[고지사항 완전성 근거 보완]', '[문항별 근거 대조]'):
        assert prompt.count(heading) == 1
    assert '계획 수립 문항' in prompt
    assert '백업 주기를 복구시험 주기로 대체하지' in prompt
    assert '암호키 지침의 생성방법' in prompt


def test_account_subject_is_not_transferred_between_events():
    context = {'chunks':[{'text':'account_a 생성 완료\naccount_b 말소 완료'}]}
    out = {'result':'MET', 'reason':'account_a 계정 말소 완료를 확인했습니다.',
           'citations':[{'quote':'account_b 말소 완료'}]}
    assert vp._reason_account_subject_gap(out, context) == ['account_a']
    out['reason'] = 'account_b 계정 말소 완료를 확인했습니다.'
    assert vp._reason_account_subject_gap(out, context) == []


def test_account_guard_does_not_treat_unrecognized_words_as_accounts():
    assert vp._reason_account_subject_gap({'result':'MET', 'reason':'검토 대상 설명입니다.',
        'citations':[]}, {'chunks':[]}) == []


@pytest.mark.parametrize('kind,reason,expected', [
    ('implementation', '전문인력 현황과 예산 및 인력 지원 계획이 포함되어 있다.', True),
    ('procedure', '예산 및 인력 지원 계획이 수립되어 있다.', False),
    ('implementation', '계획에 따른 예산 지급과 인력 충원 완료를 기록으로 확인했다.', False),
])
def test_resource_plan_does_not_explain_actual_support(kind, reason, expected):
    item = {'question':'평가 결과 필요한 예산과 인력이 지원되었는가?', 'check_kind':kind}
    output = {'result':'MET', 'reason':reason, 'citations':[{'chunk_id':'c1','quote':'예산 지원 계획'}]}
    context = {'chunks':[{'chunk_id':'c1','text':'예산 지원 계획'}]}
    assert bool(vp._observed_evidence_gap(item, output, context)) is expected


@pytest.mark.parametrize('kind,question,rows,expected', [
    ('implementation', '백업에서 정해진 주기 기준을 이행했는가?', '일자: 2026-04-01 | 방법: 전체 복구', True),
    ('implementation', '백업에서 정해진 주기 기준을 이행했는가?', '일자: 2026-04-01 | 방법: 전체 백업', False),
    ('procedure', '백업 주기 기준이 있는가?', '일자: 2026-04-01 | 방법: 전체 복구', False),
    ('implementation', '복구 테스트 주기를 이행했는가?', '일자: 2026-04-01 | 방법: 전체 복구', False),
])
def test_restore_record_is_not_a_backup_event(kind, question, rows, expected):
    item = {'question':question, 'check_kind':kind}
    output = {'result':'MET', 'reason':'기록을 확인했다.', 'citations':[{'chunk_id':'c1','quote':rows}]}
    assert bool(vp._observed_evidence_gap(item, output, {'chunks':[{'chunk_id':'c1','text':rows}]})) is expected


@pytest.mark.parametrize('initial_check', ['NOT_APPLICABLE_UNKNOWN', 'UNSUPPORTED'])
def test_one_repair_can_recover_only_revalidated_answer(monkeypatch, initial_check):
    calls = []
    def once(item, context, **kwargs):
        calls.append(kwargs)
        return (answer(), report(initial_check)) if len(calls) == 1 else (answer('MET'), report('SUPPORTED'))
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    output, audit = vp.run_validated_item({}, {'chunks': []}, semantic_repair=True)
    assert len(calls) == 2 and 'semantic_feedback' in calls[1]
    assert output['result'] == 'MET'
    assert audit['semantic_repair']['status'] == 'ACCEPTED'
    assert audit['total_model_calls'] == (3 if initial_check == 'NOT_APPLICABLE_UNKNOWN' else 4)


@pytest.mark.parametrize('change', [
    {'issues': [{'code': 'E202'}]}, {'semantic_guards': [{'decision': 'MET_TO_UNKNOWN'}]},
    {'self_check': {'verdict': 'CONFLICT'}}, {'self_check': {'verdict': 'NOT_RUN'}},
    {'self_check': {'verdict': 'UNCERTAIN'}},
])
def test_existing_failures_do_not_trigger_repair(monkeypatch, change):
    calls = []
    initial = report(); initial.update(change)
    def once(*args, **kwargs):
        calls.append(1); return answer(), deepcopy(initial)
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    output, audit = vp.run_validated_item({}, {'chunks': []}, semantic_repair=True)
    assert len(calls) == 1 and output['result'] == 'UNKNOWN'
    assert audit['semantic_repair']['status'] == 'SKIPPED'


@pytest.mark.parametrize('kind', ['unknown', 'guard', 'technical', 'low_confidence'])
def test_bad_candidate_never_promotes_and_never_loops(monkeypatch, kind):
    calls = []
    def once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1: return answer(), report()
        audit = report('SUPPORTED')
        output = answer('MET')
        if kind == 'unknown': output = answer()
        if kind == 'guard':
            output = answer(); audit['semantic_guards'] = [{'decision': 'MET_TO_UNKNOWN'}]
        if kind == 'technical': audit['issues'] = [{'code': 'E202'}]
        if kind == 'low_confidence': audit['review_signals'] = ['SELF_CHECK_LOW_CONFIDENCE']
        return output, audit
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    output, audit = vp.run_validated_item({}, {'chunks': []}, semantic_repair=True)
    assert len(calls) == 2 and output['result'] == 'UNKNOWN'
    assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
    assert audit['total_model_calls'] == 3


def test_disabled_by_default(monkeypatch):
    calls = []
    def once(*args, **kwargs): calls.append(1); return answer(), report()
    monkeypatch.setattr(vp, '_run_validated_item_once', once)
    vp.run_validated_item({}, {'chunks': []})
    assert calls == [1]


def test_real_harness_repair_uses_original_source_and_schema():
    from pathlib import Path
    from validation.contracts import ROOT
    catalog = json.loads((ROOT / 'phase2_기준/chapter2_full_checklist_draft.json').read_text(encoding='utf-8'))
    reasons = json.loads((ROOT / 'phase2_기준/chapter2_reason_codes_draft.json').read_text(encoding='utf-8'))['codes']
    payload = json.loads((ROOT / 'phase2_인터페이스/phase2_input_sample.json').read_text(encoding='utf-8'))
    item = next(c for c in catalog['controls'] if c['control_id'] == '2.5.1')['items'][0]
    context = {'chunks': [{**c, 'role': 'evidence'} for c in payload['chunks']]}
    original_context = deepcopy(context)
    calls = []
    def generate(system, user, model, schema, **kwargs):
        calls.append(user)
        out = {'item_id': item['item_id'], 'result': 'UNKNOWN', 'reason': '승인 절차 근거를 확인하지 못했습니다.',
               'reason_codes': ['P2_U_EVIDENCE_INSUFFICIENT'], 'citations': []}
        if len(calls) == 2:
            assert '[한 번의 의미 재검토]' in user
            assert item['question'] in user
            assert '승인 절차 근거를 확인하지 못했습니다.' not in user
            out.update(result='MET', reason='인용에 계정 발급 승인 절차가 명시되어 있어 절차 기준을 확인합니다.',
                       reason_codes=[], citations=payload['targets'][0]['mapping_citations'][:1])
        return json.dumps(out, ensure_ascii=False)
    def review(*args, **kwargs):
        return json.dumps({'verdict': 'SUPPORTED', 'reason': '해당 승인 절차 원문과 일치한다.',
                           'confidence': 0.95, 'unsupported_conditions': []})
    output, audit = vp.run_validated_item(item, context, semantic_repair=True,
        evidence_id=payload['evidence_id'], version=payload['version'], model='mock', reason_codes=reasons,
        llm_call=generate, self_check_call=review)
    assert output['result'] == 'MET' and len(calls) == 2
    assert audit['total_model_calls'] == 3
    assert context == original_context
