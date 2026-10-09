"""Build a pending review queue from development labels; never approve labels."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCES=('review_cases.json','aggregation_stress_cases.json','relevance_cases.json','keyword_boundary_cases_2026-09-27.json')


def build():
    controls={c['control_id'] for c in json.loads((ROOT/'controls.json').read_text(encoding='utf-8'))}
    groups={}; source_count=0
    for name in SOURCES:
        path=ROOT/'tests'/name
        data=json.loads(path.read_text(encoding='utf-8'))
        source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        for case in data['cases']:
            source_count+=1
            assert set(case['expected'])<=controls
            normalized=' '.join(' '.join(case['texts']).split())
            key=hashlib.sha256(normalized.encode()).hexdigest()
            item=groups.setdefault(key,{'review_id':'DEV-'+key[:12],'normalized_text_sha256':key,
                'synthetic':True,'texts':case['texts'],'proposals':[],
                'required_control_ids':None,'acceptable_related_control_ids':None,
                'excluded_control_ids':None,'reason_and_quote':None,'reviewer':None,'review_status':'PENDING'})
            item['proposals'].append({'source':'tests/'+name,'source_sha256':source_hash,'case_id':case['id'],
                'kind':case['kind'],'proposed_expected':case['expected'],'rationale':case.get('rationale')})
    items=list(groups.values())
    for item in items:
        labels={tuple(sorted(p['proposed_expected'])) for p in item['proposals']}
        item['conflicting_proposals']=len(labels)>1
    real={'review_id':'E0004','synthetic':False,'proposed_expected':['1.1.6'],
          'review_status':'PENDING','required_control_ids':None,'acceptable_related_control_ids':None,
          'excluded_control_ids':None,'reviewer':None,'reason_and_quote':None,
          'note':'Real evidence review candidate; verify original, version and hash locally before labeling. Original text is not included.'}
    report={'version':'gold-review-pending-2026-10-10','approved_gold':False,
        'source_case_count':source_count,'unique_synthetic_texts':len(items),
        'conflicting_text_groups':sum(i['conflicting_proposals'] for i in items),
        'rules':['Proposed expected values are development suggestions, not approved required labels.',
                 'null means not reviewed; [] means an explicit reviewed empty category.',
                 'Resolve labels from the original evidence and KB, independently of retrieval rank.',
                 'Synthetic and real-evidence metrics must be reported separately.'],
        'synthetic_cases':items,'real_evidence_candidates':[real]}
    return report


if __name__=='__main__':
    report=build()
    path=ROOT/'reports/gold_review_queue_2026-10-10.json'
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k not in ('synthetic_cases','real_evidence_candidates','rules')}))
