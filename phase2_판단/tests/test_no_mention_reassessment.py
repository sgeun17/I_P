"""No-mention is not proof of no rule; rejudge once, never force MET."""
from copy import deepcopy
import json
import pytest

from absence_basis import absence_basis_gap
from test_compact_pipeline import data
import validated_pipeline as vp

REASON = ('권한 부여에 대한 승인 절차가 마련되어 있지 않다. 요청 및 검토 후 지정은 '
          '지정 절차를 설명하므로 승인 절차가 명시적으로 마련되어 있지 않다.')
SOURCE = '최소화 원칙: 부서장 요청 + 정보보호정책팀 검토 후 지정, 반기별 필요성 재검토'

def fixtures():
    item, context, reasons, payload = data()
    item = deepcopy(item)
    item.update(question='권한 신청·부여에 대한 승인 절차가 마련되어 있는가?', check_kind='procedure')
    item['evidence_rule']['unknown'] = '자료 미제출·누락만으로 미이행을 확정하지 않는다.'
    context = deepcopy(context)
    context['chunks'][0]['text'] = SOURCE
    quote = {'chunk_id': context['chunks'][0]['chunk_id'],
             'page': context['chunks'][0].get('page'), 'quote': SOURCE}
    return item, context, reasons, payload, quote

def value(item, quote, result='NOT_MET', reason=REASON):
    return {'item_id': item['item_id'], 'result': result, 'reason': reason,
        'reason_codes': ['P2_NM_RULE_NOT_DEFINED'] if result == 'NOT_MET' else ['P2_U_EVIDENCE_INSUFFICIENT'],
        'citations': [quote]}

def test_missing_mention_gets_diagnostic_not_a_code_verdict():
    item, context, _, _, quote = fixtures()
    original = value(item, quote)
    saved = deepcopy(original)
    review = absence_basis_gap(item, original)
    assert review['code'] == 'NO_MENTION_TO_MISSING_RULE_ARGUMENT'
    assert review['safe_as_sole_verdict_rule'] is False
    assert original == saved

@pytest.mark.parametrize('text', [
    '감사 결과: 해당 승인 절차가 마련되어 있지 않다.',
    '운영 규정: 승인 없이 권한 부여를 허용한다.',
    '정책에 따른 권한 승인 절차를 수립하지 않았다.',
])
def test_direct_negative_or_explicit_unapproved_policy_is_not_erased(text):
    item, _, _, _, quote = fixtures()
    quote = dict(quote, quote=text)
    assert absence_basis_gap(item, value(item, quote)) is None

@pytest.mark.parametrize('result', ['MET', 'UNKNOWN'])
def test_other_results_are_not_touched(result):
    item, _, _, _, quote = fixtures()
    assert absence_basis_gap(item, value(item, quote, result=result)) is None

@pytest.mark.parametrize('second_result', ['UNKNOWN', 'NOT_MET'])
def test_bounded_review_rejects_repeated_unsupported_negative(second_result):
    item, context, reasons, payload, quote = fixtures()
    generations, reviews = [], []
    def generate(system, user, model, schema, **kwargs):
        generations.append(user)
        if len(generations) == 1 or second_result == 'NOT_MET':
            output = value(item, quote)
        else:
            assert '[근거와 절차 부재의 구분]' in user
            output = value(item, quote, 'UNKNOWN',
                '요청·검토 후 지정 절차는 확인되나 권한 부여 승인과 같은 범위인지 확인할 근거가 부족합니다.')
        return json.dumps(output, ensure_ascii=False)
    def review(*args, **kwargs):
        reviews.append(1)
        return json.dumps({'verdict': 'SUPPORTED',
            'reason': '직접 기록과 확인되지 않은 범위를 대조했습니다.', 'confidence': 0.95,
            'unsupported_conditions': []}, ensure_ascii=False)
    output, audit = vp.run_validated_item(item, context, model='mock', reason_codes=reasons,
        evidence_id=payload['evidence_id'], version=payload['version'],
        semantic_repair=True, prompt_profile='compact_v2', llm_call=generate, self_check_call=review)
    assert len(generations) == 2 and len(reviews) == 2
    if second_result == 'UNKNOWN':
        assert output['result'] == 'UNKNOWN'
        assert audit['semantic_repair']['status'] == 'ACCEPTED'
        assert audit['semantic_repair']['initial_output']['result'] == 'NOT_MET'
    else:
        assert output['result'] == 'NOT_MET'
        assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
        assert 'SELF_CHECK_UNCERTAIN' in audit['review_signals']

def test_direct_negative_does_not_trigger_extra_generation():
    item, context, reasons, payload, quote = fixtures()
    text = '감사 결과: 해당 승인 절차가 마련되어 있지 않다.'
    context['chunks'][0]['text'] = text
    quote['quote'] = text
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        return json.dumps(value(item, quote), ensure_ascii=False)
    def review(*args, **kwargs):
        return json.dumps({'verdict': 'SUPPORTED', 'reason': '실제 미수립 확인 기록입니다.',
            'confidence': 0.95, 'unsupported_conditions': []}, ensure_ascii=False)
    output, audit = vp.run_validated_item(item, context, model='mock', reason_codes=reasons,
        evidence_id=payload['evidence_id'], version=payload['version'], prompt_profile='compact_v2',
        semantic_repair=True, llm_call=generate, self_check_call=review)
    assert calls == [1] and output['result'] == 'NOT_MET'
    assert audit['semantic_repair']['status'] == 'SKIPPED'
