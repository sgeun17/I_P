from copy import deepcopy
import json
import pytest
from absence_basis import absence_basis_gap
from test_targeted_source_reassessment import setup
import validated_pipeline as vp


def example():
    item, context, reasons, payload, quote = setup('해당 처리의 동의 기록이 확인되는가?',
        'record', '해당 서비스는 대상 아동의 가입을 제한한다.')
    item['evidence_rule']['unknown'] = '적용 조건이 확인되지 않거나 기록 미제출이면 UNKNOWN이다.'
    output = {'item_id':item['item_id'], 'result':'NOT_MET',
        'reason':'법정대리인의 동의를 확인할 수 있는 기록이 명시적으로 제공되지 않았다.',
        'reason_codes':['P2_NM_RULE_VIOLATED'], 'citations':[quote]}
    return item, context, reasons, payload, output


def test_missing_record_without_causal_word_has_diagnostic_not_verdict():
    item, _, _, _, output = example()
    diagnostic = absence_basis_gap(item, output)
    assert diagnostic['code'] == 'RECORD_ABSENCE_AS_VIOLATION_ARGUMENT'
    assert not diagnostic['safe_as_sole_verdict_rule']
    assert output['result'] == 'NOT_MET'


@pytest.mark.parametrize('quote', ['동의를 받지 않고 처리를 진행하였다.',
                                  '승인 없이 수집을 진행하였다.'])
def test_direct_violation_is_not_missing_record(quote):
    item, _, _, _, output = example()
    output['citations'][0]['quote'] = quote
    assert absence_basis_gap(item, output) is None


@pytest.mark.parametrize('accepted', [False, True])
def test_one_normal_reassessment_not_automatic_unknown_or_approval(accepted):
    item, context, reasons, payload, initial = example()
    calls, reviews = [], []
    def generate(*args, **kwargs):
        calls.append(1)
        output = deepcopy(initial)
        if len(calls)>1:
            output.update(result='UNKNOWN', reason='가입 제한 정책은 확인되지만 실제 대상 처리와 동의 사건은 확인되지 않습니다.',
                          reason_codes=['P2_U_SCOPE_OR_RULE_UNCLEAR'])
        return json.dumps(output, ensure_ascii=False)
    def review(*args, **kwargs):
        reviews.append(1)
        return json.dumps({'verdict':'SUPPORTED' if len(reviews)==1 or accepted else 'UNSUPPORTED',
            'confidence':.95, 'reason':'문항과 동일 대상 원문을 대조했습니다.',
            'unsupported_conditions':[] if len(reviews)==1 or accepted else ['연결 미확인']}, ensure_ascii=False)
    output, audit = vp.run_validated_item(item, context, model='mock', reason_codes=reasons,
        evidence_id=payload['evidence_id'], version=payload['version'], prompt_profile='compact_v2',
        semantic_repair=True, llm_call=generate, self_check_call=review)
    assert len(calls)==2 and len(reviews)==2
    assert output['result']==('UNKNOWN' if accepted else 'NOT_MET')
    assert audit['semantic_repair']['status']==('ACCEPTED' if accepted else 'RETAINED_ORIGINAL')
