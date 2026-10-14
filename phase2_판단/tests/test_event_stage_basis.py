import json
import pytest
from event_stage_basis import event_stage_basis_gap
from test_targeted_source_reassessment import setup
import validated_pipeline as vp


def example():
    item, context, reasons, payload, quote = setup('위원회가 주요 보안사항을 심의하였는가?',
        'implementation', '보고 안건: 위험평가 결과 보고\n안건: 보호대책 이행계획 | 결과: 원안 의결')
    quote['quote'] = '보고 안건: 위험평가 결과 보고'
    output = {'item_id':item['item_id'], 'result':'MET', 'reason':'보고 안건을 근거로 주요 사항을 심의했습니다.',
              'reason_codes':[], 'citations':[quote]}
    return item, context, reasons, payload, output


def test_report_is_not_deliberation_but_existing_resolution_is_preserved():
    item, context, _, _, output = example()
    diagnostic = event_stage_basis_gap(item, output, context)
    assert diagnostic['code']=='REPORT_CITATION_AS_DELIBERATION'
    assert diagnostic['candidate_source_locations'][0]['source_line'].endswith('원안 의결')
    assert not diagnostic['safe_as_sole_verdict_rule']
    assert output['result']=='MET'


@pytest.mark.parametrize('quote', ['안건: 보호대책 | 결과: 원안 의결',
    '위원회는 위험평가 보고를 심의하였다.', '안건: 예산 보고서 | 결과: 부결'])
def test_actual_resolution_not_blocked(quote):
    item, context, _, _, output = example()
    output['citations'][0]['quote']=quote
    assert event_stage_basis_gap(item, output, context) is None


def test_procedure_and_unknown_are_not_targeted():
    item, context, _, _, output = example()
    item['check_kind']='procedure'
    assert event_stage_basis_gap(item, output, context) is None
    item['check_kind']='implementation';output['result']='UNKNOWN'
    assert event_stage_basis_gap(item, output, context) is None


@pytest.mark.parametrize('repair', [True,False])
def test_same_validator_rejects_repeated_report_only_candidate(repair):
    item, context, reasons, payload, output = example()
    calls=[]
    def generate(system,user,model,schema,**kwargs):
        calls.append(1)
        if len(calls)>1:
            assert '[보고와 실제 심의 사건의 구분]' in user
            assert '[전체 원문에서 찾은 실제 사건 표현 후보' in user
            assert '안건: 보호대책 이행계획 | 결과: 원안 의결' in user
            if repair:
                output['reason']='보호대책 이행계획 안건의 실제 원안 의결 결과로 심의 이행을 확인합니다.'
                output['citations'][0]['quote']='안건: 보호대책 이행계획 | 결과: 원안 의결'
        return json.dumps(output,ensure_ascii=False)
    def review(*args,**kwargs):
        return json.dumps({'verdict':'SUPPORTED','reason':'같은 회의의 실제 결과를 대조했습니다.',
                           'confidence':.95,'unsupported_conditions':[]},ensure_ascii=False)
    result,audit=vp.run_validated_item(item,context,evidence_id=payload['evidence_id'],
        version=payload['version'],model='mock',reason_codes=reasons,prompt_profile='compact_v2',
        semantic_repair=True,llm_call=generate,self_check_call=review)
    assert len(calls)==2
    assert result['result']=='MET'
    assert audit['semantic_repair']['status']==('ACCEPTED' if repair else 'RETAINED_ORIGINAL')
    if not repair:
        assert 'SELF_CHECK_UNCERTAIN' in audit['review_signals']
