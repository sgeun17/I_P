"""Offline synthetic sensitivity experiment; never changes production settings."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diagnose_aggregation import payload


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('output already exists')
    source = ROOT / 'tests/aggregation_stress_cases.json'
    dataset = json.loads(source.read_text(encoding='utf-8'))
    from chunk_retriever import retrieve, close_retriever
    count = len(json.loads((ROOT/'controls.json').read_text(encoding='utf-8')))
    rows = []
    try:
        for case in dataset['cases']:
            for size in (None, 400, 800, 1600):
                changed = deepcopy(case)
                if size:
                    text = '\n'.join(case['texts'])
                    changed['texts'] = [text[i:i+size] for i in range(0, len(text), size)]
                start = time.perf_counter()
                result = retrieve(payload(changed), top_k=count)
                seconds = time.perf_counter()-start
                if not result.get('success'):
                    raise RuntimeError(result.get('error'))
                candidates = result['retrieval']['candidates']
                ids = [c['control_id'] for c in candidates]
                expected = case['expected']
                rows.append({'case_id': case['id'], 'chunk_setting': size or 'original',
                    'chunk_count': len(changed['texts']), 'seconds_full_k': round(seconds,4),
                    'cold_start_included': len(rows)==0,
                    'expected_ranks': {i: ids.index(i)+1 for i in expected},
                    'top10': ids[:10], 'index': result['index'],
                    'k_metrics': {str(k): {'hits': len(set(expected)&set(ids[:k])),
                                          'required': len(expected),
                                          'all_required': bool(expected) and set(expected)<=set(ids[:k])}
                                  for k in (1,3,5,7,10)}})
                print(case['id'],size or 'original',round(seconds,2),flush=True)
    finally:
        close_retriever()
    summary = []
    for size in ('original',400,800,1600):
        group = [r for r in rows if r['chunk_setting']==size]
        for k in (1,3,5,7,10):
            total = sum(r['k_metrics'][str(k)]['required'] for r in group)
            hits = sum(r['k_metrics'][str(k)]['hits'] for r in group)
            summary.append({'chunk_setting': size, 'k': k, 'hits': hits, 'required': total,
                            'synthetic_required_recall': hits/total if total else None,
                            'all_required_documents': sum(r['k_metrics'][str(k)]['all_required'] for r in group)})
    report = {'synthetic': True, 'human_approved_gold': False, 'production_settings_changed': False,
        'source_sha256': sha256(source.read_bytes()).hexdigest(),
        'limits': ['Top-K prefixes reuse full-K retrieval; timings are NOT per-K latency or LLM latency.',
                   'Fixed-character chunks with no overlap are diagnostic, not input-team production chunking.',
                   'Existing synthetic labels are development expectations, not independent accuracy.',
                   'No similarity cutoff chosen without independently reviewed negative examples.'],
        'summary': summary, 'documents': rows}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
