import json
import pytest
from test_event_stage_basis import example
import validated_pipeline as vp


@pytest.mark.parametrize('candidate_kind',['correct','repeat','changed_verdict'])
def test_deanchored_event_rewrite_keeps_acceptance_and_noop_checks(candidate_kind):
    item, context, reasons, payload, output = example()
    report='보고 안건: 위험평가 결과 보고'
    resolution='안건: 보호대책 이행계획 | 결과: 원안 의결'
    # Valid literal source fact, but reason cites an unselected report while
    # the selected resolution actually supports a different stage of the event.
    output['reason']=f"'{report}'를 근거로 위원회가 주요 사항을 심의했습니다."
    output['citations'][0]['quote']=resolution
    calls=[]
    def generate(system,user,model,schema,**kwargs):
        calls.append(1)
        if len(calls)>1:
            assert 'result' in schema['properties']  # Not a copy-edit contract.
            assert 'prior_reason' not in user
            assert '[전체 원문에서 찾은 실제 사건 표현 후보' in user
            if candidate_kind=='correct':
                output['reason']='보호대책 이행계획의 원안 의결 결과로 실제 심의를 확인합니다.'
            elif candidate_kind=='changed_verdict':
                output.update(result='UNKNOWN',reason='동일 회의 사건 연결은 확인되지 않습니다.',
                              reason_codes=['P2_U_SUBJECT_OR_EVENT_UNLINKED'])
        return json.dumps(output,ensure_ascii=False)
    def review(*args,**kwargs):
        return json.dumps({'verdict':'SUPPORTED','reason':'원문을 대조했습니다.',
            'confidence':.95,'unsupported_conditions':[]},ensure_ascii=False)
    result,audit=vp.run_validated_item(item,context,evidence_id=payload['evidence_id'],
        version=payload['version'],reason_codes=reasons,model='mock',prompt_profile='compact_v2',
        semantic_repair=True,llm_call=generate,self_check_call=review)
    assert len(calls)==2
    assert audit['semantic_repair']['mode']=='EVENT_REASON_CITATION_REWRITE'
    assert audit['semantic_repair']['status']==('ACCEPTED' if candidate_kind=='correct' else 'RETAINED_ORIGINAL')
    assert result['result']=='MET'  # Labels were not freed by the rewrite route.
