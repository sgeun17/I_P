"""Opt-in real LLM regression on synthetic policy cases; no operational accuracy claim."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_with_criteria_policy import HERE, JUDGMENT, read, prepare, execute


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', default='http://127.0.0.1:11435/v1')
    p.add_argument('--model', default='qwen3:14b')
    p.add_argument('--out-dir', required=True)
    args = p.parse_args()
    sys.path[:0] = [str(JUDGMENT), str(JUDGMENT / 'src')]
    from validated_pipeline import run_control_judgment
    from phase1_runtime import LLMClientConfig, GenerationConfig
    from validation.contracts import interface_module
    config = LLMClientConfig(base_url=args.base_url)
    generation = GenerationConfig(temperature=0, max_tokens=4096, thinking=False)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=False)
    full = read(HERE / 'full_checklist_draft.json')
    reasons = read(HERE / 'full_reason_codes_draft.json')
    fixture = read(HERE / 'tests/fixtures/runtime_policy_cases.json')
    sample = read(HERE.parent / 'phase2_인터페이스/phase2_input_sample.json')
    kb_path = HERE.parent / 'phase1_검색/controls.json'
    kb_hash = interface_module('kb_identity').kb_sha256(kb_path.read_bytes())
    controls = {c['control_id']: c['control_name'] for c in full['controls']}
    rows = []

    def runner(*a, **kw):
        return run_control_judgment(*a, **kw, client_config=config, generation=generation)

    for case in fixture['cases']:
        catalog = deepcopy(full)
        control = next(c for c in catalog['controls'] if any(i['item_id'] == case['item_id'] for i in c['items']))
        control['items'] = [i for i in control['items'] if i['item_id'] == case['item_id']]
        payload = deepcopy(sample)
        payload['checklist_version'] = full['draft_version']
        payload['source_versions']['kb_sha256'] = kb_hash
        payload['chunks'] = [payload['chunks'][0]]
        chunk = payload['chunks'][0]
        chunk.update(text=case['text'], file_type='txt', source_file='synthetic_policy_test.txt')
        target = payload['targets'][0]
        target.update(control_id=control['control_id'], control_name=control['control_name'])
        target['mapping_citations'] = [{'chunk_id': chunk['chunk_id'], 'page': None, 'quote': case['text']}]
        payload['targets'] = [target]
        policies = {case['item_id']: case['policy']} if 'policy' in case else {}
        prepared = prepare(catalog, control['control_id'], fixture['assessment'], policies)
        started = time.monotonic()
        result = execute(payload, control['control_id'], catalog, reasons, controls, kb_hash,
                         prepared, args.model, runner=runner)
        (out / (case['id']+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        items = (result.get('output') or {}).get('items', [])
        actual = items[0]['result'] if items else None
        rows.append({'case_id': case['id'], 'expected': case['expected'], 'actual': actual,
                     'match': actual == case['expected'], 'processing_status': result['processing_status'],
                     'seconds': round(time.monotonic()-started, 2),
                     'human_review': (result.get('output') or {}).get('human_review'),
                     'issues': result['audit'].get('issues', [])})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        (out / 'summary.json').write_text(json.dumps({
            'synthetic': True, 'human_approved_gold': False, 'llm_executed': True,
            'scope': 'One selected item per control per case, not full-control coverage',
            'model': args.model, 'checklist_version': full['draft_version'], 'cases': rows,
            'limitations': ['Test policy ages/extensions are synthetic, not adopted organizational policy.',
                            'No web, DB, real evidence or operational accuracy evaluation.'],
        }, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return int(any(not r['match'] for r in rows))


if __name__ == '__main__':
    raise SystemExit(main())
