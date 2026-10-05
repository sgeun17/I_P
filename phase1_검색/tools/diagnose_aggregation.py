"""Compare chunk aggregation offline; never changes production rankings or labels.

Run from any directory with --input search-input.json or --dataset review-cases.json.
The current local model/index is queried for every control. Reports omit evidence text.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def compare(result, expected=(), top_k=5):
    if not result.get('success'):
        raise ValueError(f"Search failed: {result.get('error')}")
    candidates = result['retrieval']['candidates']
    ids = [c['control_id'] for c in candidates]
    groups = result['retrieval']['chunk_results']
    if not ids or len(ids)!=len(set(ids)) or not groups:
        raise ValueError('Complete, unique control scores are required')
    if not set(expected)<=set(ids):
        raise ValueError('Unknown expected control ID')
    scores = {cid:[] for cid in ids}
    ranks = {cid:[] for cid in ids}
    for group in groups:
        rows=group['candidates']
        if len(rows)!=len(ids) or {r['control_id'] for r in rows}!=set(ids):
            raise ValueError('Truncated chunk results cannot compare aggregation fairly; rerun at full KB size')
        for rank,row in enumerate(rows,1):
            score=row['similarity_score']
            if not isinstance(score,(int,float)) or not math.isfinite(score):
                raise ValueError('Invalid similarity')
            if row['rank']!=rank:
                raise ValueError('Chunk candidates must be in rank order')
            scores[row['control_id']].append(score)
            ranks[row['control_id']].append(rank)
    # Current max rank is retained verbatim: displayed scores are rounded by the retriever.
    orders={'max_chunk_similarity':ids}
    orders['mean_chunk_similarity']=sorted(ids,key=lambda c:(-sum(scores[c])/len(groups),c))
    orders['mean_top3_chunk_similarity']=sorted(ids,key=lambda c:(-sum(sorted(scores[c],reverse=True)[:3])/min(3,len(groups)),c))
    orders['rrf_k60']=sorted(ids,key=lambda c:(-sum(1/(60+r) for r in ranks[c]),c))
    rankings={name:{'top_k':order[:top_k],
                    'expected_ranks':{c:order.index(c)+1 for c in expected},
                    'required_recall_at_k':len(set(expected)&set(order[:top_k]))/len(expected) if expected else None}
              for name,order in orders.items()}
    dropped=[c for c in ids if min(ranks[c])<=top_k and c not in ids[:top_k]]
    return {'evidence_id':result['evidence_id'],'index':result['index'],'chunk_count':len(groups),
            'top_k':top_k,'expected':list(expected),'rankings':rankings,
            'chunk_top_k_but_not_document_top_k':[
                {'control_id':c,'document_rank':ids.index(c)+1,'best_chunk_rank':min(ranks[c]),
                 'chunk_top_k_count':sum(r<=top_k for r in ranks[c]),'max_similarity':max(scores[c])}
                for c in dropped],
            'expected_chunk_ranks':{c:ranks[c] for c in expected}}


def payload(case):
    eid='aggregation_'+case['id']
    doc={'evidence_id':eid,'version':1,'source_file':eid+'.txt','file_type':'txt'}
    doc['chunks']=[{**doc,'chunk_id':f'{eid}_v1_c{i:04d}','chunk_index':i,
                    'source':'parser','chunk_type':'text','page_start':None,'page_end':None,
                    'heading':None,'text':text,'block_orders':[i+1]}
                   for i,text in enumerate(case['texts'])]
    return doc


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input',type=Path)
    source.add_argument('--dataset',type=Path)
    parser.add_argument('--expected',nargs='*',default=[])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.output.resolve().parent!=(ROOT/'reports').resolve():
        parser.error('Choose a new report file directly under search/reports')
    source_path=args.input or args.dataset
    raw=source_path.read_bytes()
    data=json.loads(raw.decode('utf-8-sig'))
    jobs=[(data,args.expected)] if args.input else [(payload(c),c['expected']) for c in data['cases']]
    from chunk_retriever import retrieve, close_retriever
    count=len(json.loads((ROOT/'controls.json').read_text(encoding='utf-8')))
    rows=[]
    try:
        for document,expected in jobs:
            result=retrieve(document,top_k=count)
            rows.append(compare(result,expected))
            print(document['evidence_id'],rows[-1]['rankings']['max_chunk_similarity'],flush=True)
    finally:
        close_retriever()
    positive=[r for r in rows if r['expected']]
    metrics={name:{'positive_document_count':len(positive),
                   'all_required_in_top5':sum(set(r['expected'])<=set(r['rankings'][name]['top_k']) for r in positive),
                   'required_recall_at5':sum(len(set(r['expected'])&set(r['rankings'][name]['top_k'])) for r in positive)/sum(len(r['expected']) for r in positive) if positive else None}
             for name in rows[0]['rankings']} if rows else {}
    report={'status':'PASS','source_sha256':hashlib.sha256(raw).hexdigest(),
            'runtime_aggregation_changed':False,'expected_labels_human_approved':False,
            'comparison_note':'Alternatives rank rounded cosine scores; RRF uses ranks. Development comparison, not production approval.',
            'limits':['No E0004 reproduction unless its actual input was supplied.',
                      'Unlabelled/unrelated cases have no recall denominator; rankings do not imply relevance.',
                      'Do not select an aggregation policy from one example or from this development set alone.'],
            'metrics':metrics,'documents':rows}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False))


if __name__=='__main__':
    main()
