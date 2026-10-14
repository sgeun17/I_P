"""Bounded source reassessment, not threshold or score rewriting."""
import json
import pytest
from test_compact_pipeline import data
import validated_pipeline as vp

@pytest.mark.parametrize('second_confidence,second_result', [(.95,'MET'),(.5,'MET'),(.95,'UNKNOWN')])
def test_source_reassessment_uses_same_acceptance(second_confidence, second_result):
    item, context, reasons, payload = data()
    calls, reviews = [], []
    def generate(system, user, model, schema, **kwargs):
        calls.append(user)
        result = 'UNKNOWN' if len(calls) == 1 else second_result
        chunk = context['chunks'][0]
        return json.dumps({'item_id':item['item_id'],'result':result,
            'reason': '원문에 기록된 대상과 해당 요건의 연결 범위를 검토했습니다.',
            'reason_codes':['P2_U_EVIDENCE_INSUFFICIENT'] if result=='UNKNOWN' else [],
            'citations':[{'chunk_id':chunk['chunk_id'],'page':chunk.get('page'),'quote':chunk['text']}]},ensure_ascii=False)
    def review(*args,**kwargs):
        reviews.append(1)
        return json.dumps({'verdict':'SUPPORTED','reason':'현재 문항과 인용을 대조했습니다.',
            'confidence':.5 if len(reviews)==1 else second_confidence,
            'unsupported_conditions':[]},ensure_ascii=False)
    output,audit=vp.run_validated_item(item,context,model='mock',reason_codes=reasons,
        evidence_id=payload['evidence_id'],version=payload['version'],
        prompt_profile='compact_v2',semantic_repair=True,llm_call=generate,self_check_call=review)
    assert len(calls)==len(reviews)==2
    if second_confidence < .7:
        assert output['result']=='UNKNOWN'
        assert audit['semantic_repair']['status']=='RETAINED_ORIGINAL'
        assert 'SELF_CHECK_LOW_CONFIDENCE' in audit['review_signals']
    else:
        assert output['result']==second_result
        assert audit['semantic_repair']['status']=='ACCEPTED'
        assert audit['review_signals']==[]
    original_audit = audit['semantic_repair'].get('initial_audit', audit)
    assert original_audit['self_check']['confidence']==.5
