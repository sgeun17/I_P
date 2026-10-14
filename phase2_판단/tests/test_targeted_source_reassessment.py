from copy import deepcopy
import json
import pytest
from lifecycle_basis import lifecycle_basis_gap
from test_compact_pipeline import data
import validated_pipeline as vp

def setup(question, kind, text):
    item, context, reasons, payload = data()
    item, context = deepcopy(item), deepcopy(context)
    item.update(question=question, check_kind=kind)
    context['chunks'] = [dict(context['chunks'][0], text=text)]
    chunk = context['chunks'][0]
    quote={'chunk_id':chunk['chunk_id'],'page':chunk.get('page'),'quote':text.split('\n')[-1]}
    return item, context, reasons, payload, quote

@pytest.mark.parametrize('repeat_wrong_account',[False,True])
def test_account_guard_stays_active_with_one_correction(repeat_wrong_account):
    item,context,reasons,payload,quote=setup('검토 대상 접근권한 삭제 이력이 기록되어 있는가?',
        'record','계정: test_created_user | 행위: 생성\n계정: test_deleted_user | 행위: 말소 | 일시: 2026-05-20')
    calls,reviews=[],[]
    def generate(system,user,model,schema,**kwargs):
        calls.append(1)
        if len(calls)>1:
            assert '[계정과 실제 사건의 연결]' in user
        reason=('test_created_user 및 test_deleted_user의 삭제 이력이 확인됩니다.'
            if len(calls)==1 or repeat_wrong_account else 'test_deleted_user의 말소 일시와 계정 삭제 이력이 원문에 기록되어 있습니다.')
        return json.dumps({'item_id':item['item_id'],'result':'MET','reason':reason,
            'reason_codes':[],'citations':[quote]},ensure_ascii=False)
    def review(*args,**kwargs):
        reviews.append(1)
        return json.dumps({'verdict':'SUPPORTED','reason':'동일 계정과 삭제 사건을 대조했습니다.',
            'confidence':.95,'unsupported_conditions':[]},ensure_ascii=False)
    output,audit=vp.run_validated_item(item,context,model='mock',reason_codes=reasons,
        evidence_id=payload['evidence_id'],version=payload['version'],
        prompt_profile='compact_v2',semantic_repair=True,llm_call=generate,self_check_call=review)
    assert len(calls)==2
    assert output['result']==('UNKNOWN' if repeat_wrong_account else 'MET')
    assert audit['semantic_repair']['status']==('RETAINED_ORIGINAL' if repeat_wrong_account else 'ACCEPTED')
    assert len(reviews)==(0 if repeat_wrong_account else 1)

@pytest.mark.parametrize('explicit_trigger',[False,True])
def test_lifecycle_diagnostic_not_vendor_date_or_verdict(explicit_trigger):
    item,context,_,_,quote=setup('지원이 종료된 OS 사용 시 대응계획이 마련되어 있는가?',
        'procedure','자산: A | OS/SW: DB 19c | 적용일: 미적용')
    output={'result':'NOT_MET','reason':'지원이 종료된 OS 사용 중이나 대응계획이 마련되지 않았다.',
        'citations':[quote]}
    if explicit_trigger:
        context['chunks'][0]['text'] += '\n자산: A | 지원 종료일: 2026-01-01 | 대응계획: 미수립'
    diagnostic=lifecycle_basis_gap(item,output,context)
    if explicit_trigger:
        assert diagnostic is None  # Abstention, not verification of NOT_MET.
    else:
        assert diagnostic['code']=='LIFECYCLE_TRIGGER_NOT_SOURCE_BOUND'
        assert diagnostic['safe_as_sole_verdict_rule'] is False

def test_missing_lifecycle_trigger_reassessment_can_remain_unknown():
    item,context,reasons,payload,quote=setup('지원이 종료된 OS 사용 시 대응계획이 마련되어 있는가?',
        'procedure','자산: A | OS/SW: DB 19c | 적용일: 미적용 | 보완대책: 방화벽 룰 강화')
    calls=[]
    def generate(system,user,model,schema,**kwargs):
        calls.append(1)
        if len(calls)>1:
            assert '[지원 종료 적용 조건의 원문 근거]' in user
        return json.dumps({'item_id':item['item_id'],'result':'NOT_MET' if len(calls)==1 else 'UNKNOWN',
            'reason':'지원이 종료된 OS를 사용하고 있으나 대응계획이 마련되지 않았다.' if len(calls)==1 else
                '버전과 패치 미적용 및 보완대책 기록은 있으나 제조사 지원 종료 여부를 원문에서 확인할 수 없습니다.',
            'reason_codes':['P2_NM_REQUIRED_ACTION_NOT_DONE'] if len(calls)==1 else ['P2_U_SCOPE_OR_RULE_UNCLEAR'],
            'citations':[quote]},ensure_ascii=False)
    def review(*args,**kwargs):
        return json.dumps({'verdict':'SUPPORTED','reason':'원문과 현재 판정의 적용 범위를 대조했습니다.',
            'confidence':.95,'unsupported_conditions':[]},ensure_ascii=False)
    output,audit=vp.run_validated_item(item,context,model='mock',reason_codes=reasons,
        evidence_id=payload['evidence_id'],version=payload['version'],
        prompt_profile='compact_v2',semantic_repair=True,llm_call=generate,self_check_call=review)
    assert len(calls)==2 and output['result']=='UNKNOWN'
    assert audit['semantic_repair']['status']=='ACCEPTED'
