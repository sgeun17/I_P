"""Saved search results -> judgment only. Original inputs are never modified.

Output contains private evidence and model responses: do not commit it.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('baseline', 'spans'), required=True)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--model', default='qwen3:14b')
    parser.add_argument('--ollama-url', default='http://127.0.0.1:11434/v1')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args(argv)
    source, out = args.source_dir.expanduser().resolve(), args.out_dir.expanduser().resolve()
    files = sorted(source.glob('E*/search.json'))
    if not files or out.exists() or out == source or source in out.parents:
        parser.error('Require saved E*/search.json and a NEW output directory outside source-dir')
    # Must precede imports. Do not reuse an interpreter across modes.
    os.environ['PHASE1_SPAN_REPAIR'] = '1' if args.mode == 'spans' else '0'
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root/'phase1_검색'), str(root/'phase1_판단/src')]
    import httpx
    from judgment_pipeline import run_judgment
    from llm_config import LLMClientConfig, GenerationConfig
    from review_policy import RetryPolicy
    from prompts import PROMPT_VERSION

    config = LLMClientConfig(base_url=args.ollama_url, timeout_seconds=args.timeout)
    generation = GenerationConfig(temperature=0.0, max_tokens=4096, thinking=False)
    policy = RetryPolicy(max_retries=1, timeout_seconds=args.timeout, backoff_seconds=2)
    out.mkdir(parents=True, exist_ok=False)
    save(out/'experiment_manifest.json', {
        'mode': args.mode, 'prompt_version': PROMPT_VERSION, 'model': args.model,
        'source_sha256': {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'judgment_source_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((root/'phase1_판단/src').glob('*.py'))},
    })
    rows, applied, rejected = [], [], []
    for number, path in enumerate(files, 1):
        eid = path.parent.name
        target = out/eid
        target.mkdir()
        row = {'evidence_id': eid, 'judgment_status': 'FAILED'}
        records = []
        def request_hook(request):
            records.append({'request_body': request.content.decode('utf-8')})
        def response_hook(response):
            response.read()
            records[-1].update(status_code=response.status_code, response_body=response.text)
        print(f'JUDGE [{number}/{len(files)}] {eid}', flush=True)
        try:
            payload = json.loads(path.read_text(encoding='utf-8-sig'))
            row['file_name'] = payload.get('source_file')
            with httpx.Client(timeout=args.timeout, event_hooks={'request':[request_hook], 'response':[response_hook]}) as client:
                run = run_judgment(payload, model=args.model, client_config=config,
                                   generation=generation, retry_policy=policy, http_client=client)
            result = run.mapping_result.model_dump(mode='json')
            save(target/'judgment.json', result)
            save(target/'retry_audit.json', {'attempts': run.llm_run.attempt_records})
            row.update(judgment_status=result['processing_status'], match_status=result['match_status'],
                       mapped_controls=','.join(c['control_id'] for c in result['mapped_controls']),
                       validation_passed=result['validation']['passed'],
                       validation_issues=','.join(i['code'] for i in result['validation']['issues']),
                       review_required=result['human_review']['required'],
                       review_reasons=','.join(result['human_review']['reasons']),
                       retry_count=run.llm_run.retry_count)
            if any(a.get('patch_applied') is True for a in run.llm_run.attempt_records): applied.append(eid)
            if any(a.get('patch_applied') is False for a in run.llm_run.attempt_records): rejected.append(eid)
        except Exception as exc:
            row['judgment_error'] = f'{type(exc).__name__}: {exc}'
        finally:
            save(target/'http_attempts.json', records)
            save(target/'row.json', row)
            rows.append(row)
    summary = {'run_version': 'phase1-span-judgment-v1', 'mode': args.mode, 'prompt_version': PROMPT_VERSION,
               'created_at': datetime.now(timezone.utc).isoformat(), 'source_dir': str(source),
               'model': args.model, 'total': len(rows),
               'judgment': dict(Counter(r['judgment_status'] for r in rows)),
               'validation_passed': sum(r.get('validation_passed') is True for r in rows),
               'review_required': sum(r.get('review_required') is True for r in rows),
               'retried': sum(r.get('retry_count', 0) > 0 for r in rows),
               'auto_candidate_count': sum(r['judgment_status']=='COMPLETED' and r.get('validation_passed') is True
                                           and r.get('review_required') is False for r in rows),
               'validation_issue_counts': dict(Counter(c for r in rows for c in r.get('validation_issues','').split(',') if c)),
               'patch_applied_ids': applied, 'patch_rejected_ids': rejected,
               'request_or_runtime_failures': sum('judgment_error' in r for r in rows)}
    save(out/'rows.json', rows)
    save(out/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return int(summary['request_or_runtime_failures'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
