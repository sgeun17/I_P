import json
import pytest
from validation.contracts import ROOT
import validated_pipeline as vp


@pytest.mark.parametrize('review,basis', [
    ({'verdict':'SUPPORTED','reason':'따라서 MET 판정은 정당함.',
      'confidence':0.1,'unsupported_conditions':[]}, 'REVIEW_CONFIDENCE_ONLY'),
    ({'verdict':'UNCERTAIN','reason':'malformed review response',
      'confidence':0,'unsupported_conditions':[],'error_code':'E102'},
     'REVIEW_EXECUTION_OR_CONTRACT_FAILURE'),
    ({'verdict':'SUPPORTED','reason':'따라서 MET 판정은 정당함.',
      'confidence':0.95,'unsupported_conditions':['미확인 조건']}, 'REVIEW_INTERNAL_CONTRADICTION'),
])
def test_unknown_hold_does_not_present_positive_review_as_final_fact(monkeypatch, review, basis):
    catalog=json.loads((ROOT/'phase2_기준/chapter2_full_checklist_draft.json').read_text(encoding='utf-8'))
    reasons=json.loads((ROOT/'phase2_기준/chapter2_reason_codes_draft.json').read_text(encoding='utf-8'))['codes']
    payload=json.loads((ROOT/'phase2_인터페이스/phase2_input_sample.json').read_text(encoding='utf-8'))
    item=next(c for c in catalog['controls'] if c['control_id']=='2.5.1')['items'][0]
    context={'chunks':[{**c,'role':'evidence'} for c in payload['chunks']]}
    citations=payload['targets'][0]['mapping_citations'][:1]
    def generate(*args, **kwargs):
        return json.dumps({'item_id':item['item_id'],'result':'MET',
            'reason':'인용한 승인 절차로 기준의 존재를 확인합니다.',
            'reason_codes':[],'citations':citations},ensure_ascii=False)
    monkeypatch.setattr(vp,'run_self_check',lambda *args,**kwargs: {**review,'attempts':1})
    output,audit=vp.run_validated_item(item,context,model='mock',reason_codes=reasons,
        evidence_id=payload['evidence_id'],version=payload['version'],
        prompt_profile='compact_v2',llm_call=generate,self_check_call=generate)
    assert output['result']=='UNKNOWN'
    assert 'MET 판정은 정당함' not in output['reason']
    assert output['citations']==citations
    assert audit['review_hold']['basis']==basis
    assert audit['review_hold']['prior_output']['result']=='MET'
    assert audit['self_check']['reason']==review['reason']
