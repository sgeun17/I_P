"""Missing positive evidence is not direct proof of nonperformance."""
from copy import deepcopy
import json
import pytest
from absence_basis import absence_basis_gap
from test_compact_pipeline import data
import validated_pipeline as vp

REASON = ('정기적인 검토가 수행되지 않았다. 8/31 이상없음, 9/30 분실 보고 기록은 '
          '확인되지만, 정기적인 검토가 수행된다는 직접적인 근거는 없다.')
SOURCE = '8/31 이상없음\n9/30 USB-009 분실 확인 > 사용 중지, 보고'

def fixtures():
    item, context, reasons, payload = data()
    item, context = deepcopy(item), deepcopy(context)
    item['check_kind'] = 'record'
    item['evidence_rule']['unknown'] = '자료 미제출만으로 미이행을 확정하지 않는다.'
    context['chunks'][0]['text'] = SOURCE
    quote = {'chunk_id': context['chunks'][0]['chunk_id'],
        'page': context['chunks'][0].get('page'), 'quote': SOURCE}
    output = {'item_id': item['item_id'], 'result': 'NOT_MET', 'reason': REASON,
        'reason_codes': ['P2_NM_REQUIRED_ACTION_NOT_DONE'], 'citations': [quote]}
    return item, context, reasons, payload, output

def test_reverse_order_absence_inference_diagnostic_only():
    item, _, _, _, output = fixtures()
    saved = deepcopy(output)
    assert absence_basis_gap(item, output)['code'] == 'NO_DIRECT_PERFORMANCE_BASIS_ARGUMENT'
    assert output == saved

@pytest.mark.parametrize('text', ['실사 결과: 대상 구역의 점검을 실시하지 않았다.',
    '점검 결과: 미수행으로 확인', '검토 결과: 미수행'])
def test_independent_direct_negative_is_not_overridden(text):
    item, _, _, _, output = fixtures()
    output['citations'][0]['quote'] = text
    assert absence_basis_gap(item, output) is None

def test_bounded_reassessment_never_forces_met():
    item, context, reasons, payload, original = fixtures()
    generations = []
    def generate(system, user, model, schema, **kwargs):
        generations.append(user)
        output = deepcopy(original)
        if len(generations) > 1:
            assert '[미수행과 확인 불가의 구분]' in user
            output.update(result='UNKNOWN', reason='점검 기록은 확인되나 대상 구역 및 적용 주기를 연결할 수 없습니다.',
                reason_codes=['P2_U_EVIDENCE_INSUFFICIENT'])
        return json.dumps(output, ensure_ascii=False)
    def review(*args, **kwargs):
        return json.dumps({'verdict': 'SUPPORTED', 'reason': '문항과 원문을 대조했습니다.',
            'confidence': .95, 'unsupported_conditions': []}, ensure_ascii=False)
    output, audit = vp.run_validated_item(item, context, model='mock', reason_codes=reasons,
        evidence_id=payload['evidence_id'], version=payload['version'],
        prompt_profile='compact_v2', semantic_repair=True, llm_call=generate, self_check_call=review)
    assert len(generations) == 2
    assert output['result'] == 'UNKNOWN'
    assert audit['semantic_repair']['status'] == 'ACCEPTED'
