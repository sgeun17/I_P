"""Measure actual K calls with one persistent local model; synthetic inputs only."""
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diagnose_aggregation import payload
from chunk_retriever import retrieve, close_retriever
from judgment_adapter import to_mapping_input


def main():
    output = ROOT/'reports/topk_calls_2026-10-09.json'
    if output.exists():
        raise ValueError('report already exists')
    cases = json.loads((ROOT/'tests/aggregation_stress_cases.json').read_text(encoding='utf-8'))['cases']
    selected = [cases[0], next(c for c in cases if len(c['expected']) > 1)]
    rows = []
    try:
        start = time.perf_counter()
        warmup = retrieve(payload(selected[0]), top_k=5)
        if not warmup.get('success'):
            raise RuntimeError(warmup.get('error'))
        cold_seconds = time.perf_counter()-start
        for case in selected:
            for repeat in range(3):
                # Rotate K order to reduce order effects; not a statistical latency study.
                ks = [3,5,7,10]
                for k in ks[repeat:]+ks[:repeat]:
                    start = time.perf_counter()
                    result = retrieve(payload(case), top_k=k)
                    elapsed = time.perf_counter()-start
                    mapping = to_mapping_input(result)
                    ids = [c['control_id'] for c in result['retrieval']['candidates']]
                    assert len(ids)==k
                    rows.append({'case_id':case['id'],'k':k,'repeat':repeat+1,
                                 'search_seconds':round(elapsed,4),
                                 'mapping_input_utf8_bytes':len(json.dumps(mapping,ensure_ascii=False).encode('utf-8')),
                                 'candidate_ids':ids,'adapter_passed':True})
                    print(case['id'], k, round(elapsed,3), flush=True)
    finally:
        close_retriever()
    summary=[]
    for case in selected:
        for k in [3,5,7,10]:
            group=[r for r in rows if r['case_id']==case['id'] and r['k']==k]
            summary.append({'case_id':case['id'],'k':k,
                'median_search_seconds':statistics.median(r['search_seconds'] for r in group),
                'mapping_input_utf8_bytes':group[0]['mapping_input_utf8_bytes'],
                'ranks_stable_across_repeats':all(r['candidate_ids']==group[0]['candidate_ids'] for r in group)})
    output.write_text(json.dumps({'synthetic':True,'llm_executed':False,'cold_start_seconds':cold_seconds,
        'repeats':3,'summary':summary,'runs':rows,'limits':[
        'Search-only warm timings on two synthetic inputs; not production throughput or LLM latency.',
        'UTF-8 bytes are serialized mapping input size, not tokenizer counts.',
        'No claim that larger K improves final judgment accuracy.']},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
