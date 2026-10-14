"""Procedure existence does not prove a key destruction/recovery event."""
from copy import deepcopy
import pytest
from evidence_contracts import question_binding_gap

POLICY = ('생성: HSM에서 생성, 2인 이상 입회\n'
          '보관: HSM 내 보관, 백업 키는 DR센터 금고에 분할 보관\n'
          '복구: CISO 승인 후 분할 키 2개 결합\n'
          '파기: HSM 완전삭제 기능, 파기 기록 작성')
INVENTORY = '키ID: KEY-01 | 생성일: 2026-01-05 | 보관 위치: HSM / DR 백업'

def data(question, kind):
    item = {'question': question, 'check_kind': kind}
    context = {'chunks': [{'chunk_id': 'policy', 'text': POLICY},
                          {'chunk_id': 'inventory', 'text': INVENTORY}]}
    output = {'result': 'MET', 'reason': '정한 절차에 따라 실제 처리했음이 확인됩니다.',
        'citations': [{'chunk_id': 'policy', 'quote': '복구: CISO 승인 후 분할 키 2개 결합'}]}
    return item, context, output

@pytest.mark.parametrize('question,kind', [
    ('폐기 대상 암호키가 안전하게 폐기되었는가?', 'implementation'),
    ('실제 암호키 복구 사건이 정해진 절차에 따라 처리되었는가?', 'record'),
])
def test_policy_plus_inventory_is_not_actual_event(question, kind):
    item, context, output = data(question, kind)
    saved = deepcopy(output)
    assert question_binding_gap(item, output, context)[0] == 'CRYPTO_POLICY_AS_EXECUTION'
    assert saved == output

@pytest.mark.parametrize('text', [
    '키ID: KEY-01 | 파기일: 2026-09-01 | 결과: 정상 삭제',
    '키ID: KEY-01 | 복구일: 2026-09-01 | 승인자: CISO | 결과: 완료',
    '2026-09-01 KEY-01을 분할 키 결합 방식으로 복구하였다.',
])
def test_actual_event_elsewhere_abstains_not_auto_approves(text):
    item, context, output = data('실제 암호키 복구 사건이 처리되었는가?', 'record')
    context['chunks'].append({'chunk_id': 'actual', 'text': text})
    assert question_binding_gap(item, output, context) is None

def test_recovery_procedure_question_not_rejected():
    item, context, output = data('암호키 복구방안이 마련되어 있는가?', 'procedure')
    assert question_binding_gap(item, output, context) is None

@pytest.mark.parametrize('result', ['UNKNOWN', 'NOT_MET'])
def test_other_results_not_changed(result):
    item, context, output = data('폐기 대상 암호키가 폐기되었는가?', 'implementation')
    output['result'] = result
    assert question_binding_gap(item, output, context) is None
