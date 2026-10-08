"""Synthetic, local-only API boundary probes. No production DB, evidence or LLM calls."""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def run(work):
    for folder in ('phase1_검색', 'phase1_입력', 'phase1_입력/chunking', 'phase1_통합'):
        sys.path.insert(0, str(ROOT / folder))
    import api
    import orchestrator
    from fastapi.testclient import TestClient
    from llm_config import LLMClientConfig
    assert Path(api.__file__).resolve() == ROOT / 'phase1_통합/api.py'
    fixture = {'evidence_id': 'E9999', 'version': 1, 'synthetic': True}
    (work / 'E9999_v1.json').write_text(json.dumps(fixture), encoding='utf-8')
    checks = []
    with patch.object(orchestrator, 'RESULT_DIR', work), \
         patch.object(orchestrator, 'get_evidence', side_effect=AssertionError('DB must not be called')), \
         patch.dict(os.environ, {'PHASE1_JUDGMENT_MODEL': ''}), TestClient(api.app) as client:
        response = client.get('/api/results/E9999')
        checks.append({'name': 'anonymous_result_access', 'status': response.status_code,
                       'authorization_enforced': response.status_code in (401, 403),
                       'synthetic_fixture_returned': response.json() == fixture})
        for name, content, code in [('empty.txt', b'', 'EMPTY_FILE'),
                                    ('bad.exe', b'fake', 'INVALID_EXTENSION'),
                                    ('bad.docx', b'not-docx', 'CONTENT_MISMATCH')]:
            response = client.post('/api/upload', files={'file': (name, content)})
            observed = response.json().get('detail', {}).get('error_code')
            checks.append({'name': name, 'status': response.status_code, 'error_code': observed,
                           'passed': response.status_code == 400 and observed == code})
        for url, code, status in [('/api/inspect/E9999?top_k=0', 'INVALID_TOP_K', 422),
                                  ('/api/inspect/E9999', 'MODEL_NOT_CONFIGURED', 503)]:
            response = client.post(url)
            observed = response.json().get('detail', {}).get('code')
            checks.append({'name': code, 'status': response.status_code, 'error_code': observed,
                           'passed': response.status_code == status and observed == code})
    # Reserved .invalid name: construct configuration only, never send a request.
    remote = LLMClientConfig(base_url='https://offline-test.invalid/v1')
    return {'checked_at': '2026-10-07', 'scope': 'SYNTHETIC_API_BOUNDARIES', 'checks': checks,
            'phase1_nonloopback_config_accepted': remote.base_url.startswith('https://'),
            'external_request_sent': False, 'real_evidence_used': False, 'database_used': False,
            'full_e2e_completed': False,
            'limits': ['Authorization probe covers the application alone, not an upstream gateway.',
                       'Configuration acceptance is not evidence that external traffic occurred.',
                       'API rejection tests do not verify database state changes.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    result = run(args.work)
    (args.work / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
