import json
from copy import deepcopy
import validated_pipeline as vp
from test_compact_pipeline import data


def test_missing_record_negative_repair_requires_review_not_auto_acceptance():
    item,context,reasons,payload=data()
    item=deepcopy(item)
    item['question']='정해진 주기에 따라 대상 활동을 수행하였는가?'
    item['evidence_rule']['unknown']='기록 미제출·누락은 실제 미이행을 의미하지 않는다.'
    selected=context['chunks'][0]
    selected.update(heading='기준과 이관 기록',text='주기: 일 1회\n일자: 2026-09-05 | 내용: 주간 복사본 이관')
    quote={'chunk_id':selected['chunk_id'],'page':selected.get('page'), 'quote':selected['text']}
    generations=[]
    def generate(*args,**kwargs):
        generations.append(1)
        second=len(generations)>1
        return json.dumps({'item_id':item['item_id'],'result':'NOT_MET' if second else 'UNKNOWN',
            'reason':'매일 수행 기록은 확인되지 않았다. 이는 주기 기준 위반으로 판단된다.' if second else '근거 연결을 확인해야 합니다.',
            'reason_codes':['P2_NM_RULE_VIOLATED'] if second else ['P2_U_EVIDENCE_INSUFFICIENT'],
            'citations':[quote]},ensure_ascii=False)
    reviews=[]
    def review(*args,**kwargs):
        reviews.append(1)
        supported=len(reviews)>1
        return json.dumps({'verdict':'SUPPORTED' if supported else 'UNSUPPORTED',
            'reason':'원문을 다시 검토했습니다.','confidence':0.95,
            'unsupported_conditions':[] if supported else ['다른 근거와의 연결을 재검토해야 합니다.']},ensure_ascii=False)
    output,audit=vp.run_validated_item(item,context,model='mock',reason_codes=reasons,
        evidence_id=payload['evidence_id'],version=payload['version'],
        prompt_profile='compact_v2',semantic_repair=True,llm_call=generate,self_check_call=review)
    assert output['result']=='UNKNOWN'
    assert audit['semantic_repair']['status']=='RETAINED_ORIGINAL'
    candidate=audit['semantic_repair']['candidate_audit']
    assert candidate['absence_basis_review']['safe_as_sole_verdict_rule'] is False
    assert 'SELF_CHECK_UNCERTAIN' in candidate['review_signals']
    assert audit['total_model_calls']==4
