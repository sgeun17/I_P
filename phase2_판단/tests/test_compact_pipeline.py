import json
from copy import deepcopy
import pytest
import validated_pipeline as vp
from self_check import COMPACT_SELF_CHECK_VERSION
from validation.contracts import ROOT


def data():
    catalog = json.loads((ROOT/'phase2_기준/chapter2_full_checklist_draft.json').read_text(encoding='utf-8'))
    reasons = json.loads((ROOT/'phase2_기준/chapter2_reason_codes_draft.json').read_text(encoding='utf-8'))['codes']
    payload = json.loads((ROOT/'phase2_인터페이스/phase2_input_sample.json').read_text(encoding='utf-8'))
    item = next(c for c in catalog['controls'] if c['control_id']=='2.5.1')['items'][0]
    context = {'chunks':[{**c,'role':'evidence'} for c in payload['chunks']]}
    return item, context, reasons, payload


@pytest.mark.parametrize('first_supported, second_result', [(True,'UNKNOWN'), (False,'MET'), (False,'UNKNOWN')])
def test_unknown_review_and_bounded_independent_correction(first_supported, second_result):
    item, context, reasons, payload = data()
    original = deepcopy(context)
    generations=[]; reviews=[]
    def generate(system,user,model,schema,**kwargs):
        assert 'prompt_profile' not in kwargs
        assert '<output_schema>' not in system
        generations.append(user)
        result = 'UNKNOWN' if len(generations)==1 else second_result
        out = {'item_id':item['item_id'],'result':result,
               'reason':'적용 대상의 연결이 확인되지 않아 판정을 보류합니다.' if result=='UNKNOWN' else '인용한 승인 절차로 기준의 존재를 확인합니다.',
               'reason_codes':['P2_U_EVIDENCE_INSUFFICIENT'] if result=='UNKNOWN' else [],
               'citations':payload['targets'][0]['mapping_citations'][:1]}
        return json.dumps(out,ensure_ascii=False)
    def review(system,user,model,schema,**kwargs):
        assert 'prompt_profile' not in kwargs
        assert 'UNKNOWN도 검토' in system
        reviews.append(user)
        supported = first_supported or len(reviews)>1
        return json.dumps({'verdict':'SUPPORTED' if supported else 'UNSUPPORTED',
            'reason':'판정과 설명을 원문에 대조했습니다.', 'confidence':0.95,
            'unsupported_conditions':[] if supported else ['선택한 설명과 원문의 연결을 재검토해야 합니다.']},ensure_ascii=False)
    output,audit=vp.run_validated_item(item,context,model='mock',reason_codes=reasons,
        evidence_id=payload['evidence_id'],version=payload['version'],
        semantic_repair=True,prompt_profile='compact_v2',llm_call=generate,self_check_call=review)
    assert output['result']==('UNKNOWN' if first_supported else second_result)
    assert len(generations)==len(reviews)==(1 if first_supported else 2)
    assert audit['total_model_calls']==(2 if first_supported else 4)
    assert audit['run']['prompt_profile']=='compact_v2'
    assert audit['run']['prompt_lengths']['system_chars']>0
    assert audit['self_check']['version']==COMPACT_SELF_CHECK_VERSION
    assert context==original
    if not first_supported:
        assert audit['semantic_repair']['status']=='ACCEPTED'


def test_compact_unknown_technical_failure_never_gets_semantic_promotion(monkeypatch):
    item,context,reasons,payload=data()
    from judgment_harness import JudgmentRunResult
    monkeypatch.setattr(vp,'run_item_judgment',lambda *a,**k: JudgmentRunResult(
        raw_response=None,parsed_output=None,attempts=1,retry_count=0,final_error_code='E106'))
    def forbidden(*a,**k): raise AssertionError('technical failure must not call self-check')
    output,audit=vp.run_validated_item(item,context,model='mock',reason_codes=reasons,
        evidence_id=payload['evidence_id'],version=payload['version'],
        semantic_repair=True,prompt_profile='compact_v2',self_check_call=forbidden)
    assert output['result']=='UNKNOWN' and output['error_code']=='E106'
    assert audit['total_model_calls']==1
    assert audit['semantic_repair']['status']=='SKIPPED'


@pytest.mark.parametrize('profile', ['legacy', 'compact_v2'])
def test_control_audit_identifies_actual_opt_in_prompt_profile(profile):
    from grounding_prompts import COMPACT_PROMPT_VERSION, PROMPT_VERSION
    from self_check import COMPACT_SELF_CHECK_VERSION, SELF_CHECK_VERSION
    result = vp.run_control_judgment({}, '2.5.1', catalog={}, reason_catalog={},
        controls={}, model='mock', prompt_profile=profile)
    assert result['processing_status'] == 'FAILED'  # no valid input, no model call
    audit = result['audit']
    assert audit['prompt_profile'] == profile
    assert audit['prompt_version'] == (COMPACT_PROMPT_VERSION if profile == 'compact_v2' else PROMPT_VERSION)
    assert audit['self_check_version'] == (COMPACT_SELF_CHECK_VERSION if profile == 'compact_v2' else SELF_CHECK_VERSION)
