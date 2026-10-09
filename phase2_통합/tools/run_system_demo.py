"""Opt-in, loopback integration deployment. Existing team sources are unchanged.

Private JSON config extends local-upload config with access_tokens (sha256 digest
-> actor/role), grants, llm_base_url, model. No raw credentials belong in Git.
"""
import argparse
from dataclasses import asdict
from functools import wraps
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def validate_config(config):
    url = urlsplit(config['llm_base_url'])
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment
            or url.path.rstrip('/') != '/v1'):
        raise ValueError('This deployment requires a loopback Ollama /v1 URL')
    if config['DB_HOST'] != '127.0.0.1' or str(config['DB_PORT']) != '13306':
        raise ValueError('Dedicated local test DB only')
    if config.get('retention') != 'retain_until_explicit_approval':
        raise ValueError('Automatic deletion is not enabled')
    if not config.get('access_tokens') or not config.get('grants'):
        raise ValueError('Explicit identities and permissions required')
    for digest, identity in config['access_tokens'].items():
        if not re.fullmatch('[a-f0-9]{64}', digest) or not identity.get('actor'):
            raise ValueError('Invalid identity configuration')
        if identity.get('role') not in config['grants']:
            raise ValueError('Identity role has no policy')


def create_app(config, work):
    validate_config(config)
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    for key in ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'STORAGE_DIR', 'TEMP_DIR'):
        os.environ[key] = str(config[key])
    for key in ('STORAGE_DIR', 'TEMP_DIR'):
        path = Path(config[key]).resolve()
        if not path.is_relative_to((ROOT.parent / 'tmp').resolve()):
            raise ValueError('Use isolated test storage under workspace tmp')
        path.mkdir(parents=True, exist_ok=True)
    os.environ.update(LLM_BASE_URL=config['llm_base_url'], LLM_PROVIDER='ollama',
                      PHASE1_JUDGMENT_MODEL=config['model'], HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      ANONYMIZED_TELEMETRY='False', ISMS_OFFLINE_LOOPBACK_ONLY='1')
    for folder in ('phase1_검색', 'phase1_입력', 'phase1_입력/database',
                   'phase1_입력/chunking', 'phase1_통합', 'phase2_통합'):
        sys.path.insert(0, str(ROOT / folder))
    from offline_network import install
    install()
    from fastapi import FastAPI, Request, UploadFile, File, HTTPException
    from fastapi.responses import JSONResponse, Response, HTMLResponse
    import main as upload
    import orchestrator
    import phase2_runner
    import phase2_result_store as results
    import phase2_review_store as reviews
    import phase2_report
    from evidence_store import get_evidence, get_file_path, update_status
    from access_audit import AccessAudit

    upload.TMP_DIR = Path(config['TEMP_DIR'])
    import ocr_parser
    class OfflineOCR:
        reader = None
        def readtext(self, image):
            if self.reader is None:
                import easyocr
                self.reader = easyocr.Reader(['ko','en'], gpu=False, download_enabled=False,
                    model_storage_directory=str(work / 'ocr_models'))
            return self.reader.readtext(image)
    ocr_parser._reader = OfflineOCR()
    orchestrator.RESULT_DIR = work / 'phase1_results'
    orchestrator.RESULT_DIR.mkdir(exist_ok=True)
    phase2_runner.RESULT_DIR = orchestrator.RESULT_DIR
    log_dir = work / 'model_logs'
    log_dir.mkdir(exist_ok=True)
    def persist_model(stage, data):
        # Unique files preserve repeated attempts; never overwrite or auto-prune.
        with (log_dir / f'{stage}_{uuid.uuid4().hex}.json').open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2,
                      default=lambda value: getattr(value, 'value', str(value)))
    original_judgment = orchestrator.run_judgment
    from judgment_pipeline import JudgmentRequestError
    @wraps(original_judgment)
    def logged_judgment(*args, **kwargs):
        try:
            result = original_judgment(*args, **kwargs)
        except JudgmentRequestError as exc:
            persist_model('phase1', {'trace_id': kwargs.get('trace_id'),
                                    'llm_run': asdict(exc.run_result)})
            raise
        persist_model('phase1', {'trace_id': kwargs.get('trace_id'),
                                'llm_run': asdict(result.llm_run)})
        return result
    orchestrator.run_judgment = logged_judgment
    import validated_pipeline
    original_phase2 = validated_pipeline.run_control_judgment
    @wraps(original_phase2)
    def logged_phase2(*args, **kwargs):
        result = original_phase2(*args, **kwargs)
        persist_model('phase2', result)
        return result
    validated_pipeline.run_control_judgment = logged_phase2
    reviews.ENFORCE_ROLE = True
    reviews.load_reason_codes(ROOT / 'phase2_기준/full_reason_codes_draft.json')
    audit = AccessAudit(work / 'access.sqlite3', {
        role: [a for a in actions if a in ('VIEW_ORIGINAL','DOWNLOAD_ORIGINAL','REVIEW')]
        for role, actions in config['grants'].items()})
    (work / 'retention.json').write_text(json.dumps({
        'policy': config['retention'], 'automatic_deletion': False,
        'retention_days': None, 'scope': ['stored_originals','model_results','audit_events'],
        'note': 'Upload temporary files are not retained originals. No deletion endpoint is provided.'
    }, indent=2), encoding='utf-8')
    app = FastAPI(title='Local integrated demonstration', docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        token = request.headers.get('authorization', '')
        digest = hashlib.sha256(token[7:].encode()).hexdigest() if token.startswith('Bearer ') else ''
        identity = next((v for k, v in config['access_tokens'].items()
                         if hmac.compare_digest(k, digest)), None)
        action = ('REVIEW' if request.url.path.startswith('/review/') else
                  'DOWNLOAD_ORIGINAL' if request.url.path.endswith('/download') else
                  'VIEW_ORIGINAL' if request.url.path.endswith('/original') else
                  'EXECUTE' if request.method == 'POST' else 'VIEW_RESULT')
        allowed = identity and action in config['grants'].get(identity['role'], [])
        request_id = uuid.uuid4().hex
        # Path only: no query, token, body, document text or exception details.
        def record(outcome):
            audit._record(request_id, identity['actor'] if identity else None,
                          identity['role'] if identity else None, action,
                          request.url.path[:250], 0, outcome)
        try:
            record('STARTED' if allowed else 'DENIED')
        except Exception:
            return JSONResponse({'code': 'AUDIT_UNAVAILABLE'}, status_code=503)
        if not allowed:
            return JSONResponse({'code': 'UNAUTHORIZED' if not identity else 'FORBIDDEN'},
                                status_code=401 if not identity else 403)
        request.state.identity = identity
        try:
            response = await call_next(request)
            record('RESPONSE_READY' if response.status_code < 400 else 'FAILED')
            return response
        except Exception:
            try:
                record('FAILED')
            except Exception:
                pass
            return JSONResponse({'code': 'REQUEST_FAILED', 'request_id': request_id}, status_code=500)

    def check_id(eid):
        if not re.fullmatch('E[0-9]+', eid):
            raise HTTPException(422, 'Invalid evidence ID')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'retention': config['retention'], 'model': config['model']}

    @app.post('/upload')
    async def post_upload(file: UploadFile = File(...)):
        result = await upload.process_file(file, None)
        return JSONResponse(result, status_code=200 if result.get('status') == 'ok' else 400)

    @app.post('/inspect/{eid}')
    def inspect(eid: str, top_k: int = 5):
        check_id(eid)
        if not 1 <= top_k <= 101:
            raise HTTPException(422, 'top_k must be 1..101')
        try:
            p1 = orchestrator.inspect_evidence(eid, top_k=top_k, model=config['model'])
        except orchestrator.IntegrationError as exc:
            if exc.stage == 'judgment':
                update_status(eid, 'FAILED', error_code=exc.code,
                              error_message='Phase 1 judgment request failed')
                results.exclude(eid, 'FAILED', 'Phase 1 judgment request failed')
            raise HTTPException(503, {'code': exc.code, 'stage': exc.stage}) from exc
        p2 = phase2_runner.run_evidence(eid, phase1_result=p1['judgment'], model=config['model'])
        report = work / f'{eid}_v{p1["version"]}.html'
        phase2_report.to_html(report, evidence_id=eid)
        return {'phase1': p1, 'phase2': p2, 'report_url': f'/evidence/{eid}/report',
                'operational_approval': False}

    @app.get('/evidence/{eid}/result')
    def get_result(eid: str):
        check_id(eid)
        return results.evidence_response(eid)

    @app.get('/evidence/{eid}/report')
    def get_report(eid: str):
        check_id(eid)
        info = get_evidence(eid)
        path = work / f'{eid}_v{info["version"]}.html'
        phase2_report.to_html(path, evidence_id=eid)
        return HTMLResponse(path.read_text(encoding='utf-8'))

    def original(eid, download, identity):
        check_id(eid)
        info = get_evidence(eid)
        path = Path(get_file_path(eid, info['version'])).resolve()
        if not path.is_relative_to(Path(config['STORAGE_DIR']).resolve()):
            raise HTTPException(403, 'Storage boundary violation')
        # Read inside guarded request; arbitrary original HTML is never rendered.
        content = audit.execute(actor=identity['actor'], role=identity['role'],
            action='DOWNLOAD_ORIGINAL' if download else 'VIEW_ORIGINAL',
            evidence_id=eid, version=info['version'], operation=path.read_bytes)
        return Response(content, media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="{eid}_v{info["version"]}.bin"',
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    @app.get('/evidence/{eid}/original')
    def view_original(eid: str, request: Request):
        return original(eid, False, request.state.identity)

    @app.get('/evidence/{eid}/download')
    def download_original(eid: str, request: Request):
        return original(eid, True, request.state.identity)

    @app.post('/review/{result_id}/approve')
    def approve(result_id: int, request: Request):
        identity = request.state.identity
        if identity['role'] != 'APPROVER':
            raise HTTPException(403, 'Approver role required')
        try:
            return reviews.approve(result_id, actor=identity['actor'], role=identity['role'])
        except reviews.ReviewError as exc:
            raise HTTPException(409, {'code': exc.code}) from exc

    @app.post('/review/{result_id}/modify')
    def modify(result_id: int, request: Request, body: dict):
        identity = request.state.identity
        if identity['role'] != 'REVIEWER':
            raise HTTPException(403, 'Reviewer role required')
        if not all(k in body for k in ('target','value_after','reason')) or not body['reason']:
            raise HTTPException(422, 'target/value_after/reason required')
        try:
            return reviews.modify(result_id, actor=identity['actor'], role=identity['role'],
                target=body['target'], value_after=body['value_after'], reason=body['reason'])
        except reviews.ReviewError as exc:
            raise HTTPException(409, {'code': exc.code}) from exc

    @app.post('/review/{result_id}/reject')
    def reject(result_id: int, request: Request, body: dict):
        identity = request.state.identity
        if not isinstance(body.get('reason'), str) or not body['reason'].strip():
            raise HTTPException(422, 'reason required')
        try:
            return reviews.reject(result_id, actor=identity['actor'], role=identity['role'], reason=body['reason'])
        except reviews.ReviewError as exc:
            raise HTTPException(409, {'code': exc.code}) from exc

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--port', type=int, default=18081)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    import uvicorn
    uvicorn.run(create_app(config, args.config.resolve().parent / 'system_demo'),
                host='127.0.0.1', port=args.port, access_log=False)
