"""Inventory saved Phase2 evidence and review signals without executing or changing judgments."""
import argparse
import hashlib
import json
from pathlib import Path

TARGETS = {'E0032':'2.2.5-Q07','E0028':'1.1.2-Q04','E0013':'2.9.3-Q14','E0010':'2.5.1-Q17','E0027':'2.2.3-Q04'}


def inspect(root):
    summary = json.loads((root/'summary.json').read_text(encoding='utf-8'))
    targets, held = [], []
    files = {}
    for folder in sorted(root.glob('E*')):
        if not folder.is_dir():
            continue
        payload = json.loads((folder/'phase2_input.json').read_text(encoding='utf-8'))
        audit = json.loads((folder/'audit.json').read_text(encoding='utf-8'))
        for name in ('phase2_input.json','audit.json','output.json'):
            files[f'{folder.name}/{name}'] = hashlib.sha256((folder/name).read_bytes()).hexdigest()
        for item in audit['items']:
            run = item.get('run', {})
            parsed = run.get('parsed_output') or {}
            iid = item.get('item_id', parsed.get('item_id'))
            check = item.get('self_check') or {}
            selected = {v['chunk_id'] for v in run.get('source_spans', {}).values()}
            row = {'evidence_id':folder.name,'item_id':iid,
                   'initial_result':parsed.get('result'),'self_check_verdict':check.get('verdict'),
                   'self_check_reason':check.get('reason'),
                   'selected_chunk_ids':sorted(selected),
                   'unselected_input_chunk_ids':[c['chunk_id'] for c in payload['chunks'] if c['chunk_id'] not in selected],
                   'citation_chunk_ids':sorted({c['chunk_id'] for c in parsed.get('citations',[])}),
                   'review_status':'NEEDS_EVIDENCE_AND_SCOPE_REVIEW', 'expected_result':None}
            if iid == TARGETS.get(folder.name):
                targets.append(row)
            if check.get('verdict') == 'UNSUPPORTED':
                held.append(row)
    return {'source_run':summary.get('version'), 'source_git_commit':summary.get('git_commit'),
            'source_checklist_approved':summary.get('checklist_approved'),
            'source_hashes':files,'target_items':targets,'self_check_held_items':held,
            'held_count':len(held),'expected_labels_approved':False,'llm_executed':False,
            'limits':['Saved extracted chunks are not original PDF/XLSX images.',
                      'No expected labels are assigned from model results or self-check reasons.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('Output must be a new file')
    result=inspect(args.run)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'targets':len(result['target_items']),'held':result['held_count'],'output':str(args.output)},ensure_ascii=False))
