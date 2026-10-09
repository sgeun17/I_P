"""Inject HTTP LLM failures through a loopback proxy; use real upload/BGE/MySQL.

Recovery forwards to the configured Ollama. Failure responses are intentional
test injections, not measurements of Qwen availability or judgment accuracy.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import httpx
import pymysql

ROOT = Path(__file__).resolve().parents[2]


def run(private, output, modes):
    original = json.loads((private / 'system_config.json').read_text(encoding='utf-8'))
    tokens = json.loads((private / 'system_tokens.json').read_text(encoding='utf-8'))
    state = {'mode': '503', 'calls': 0}
    class Faults(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            mode = state['mode']; state['calls'] += 1
            if mode == 'timeout':
                time.sleep(65)
                code, data = 503, b'{}'
            elif mode == '400':
                code, data = 400, b'{"error":"injected rejection"}'
            elif mode == 'malformed':
                code, data = 200, b'{"choices":[{"message":{"content":"not-json"}}]}'
            elif (mode == 'recover' and state['calls'] > 1) or (mode == 'phase2_503' and state['calls'] == 1):
                with httpx.Client(timeout=180, trust_env=False) as client:
                    response = client.post(original['llm_base_url'].rstrip('/') + '/chat/completions',
                                           content=body, headers={'Content-Type':'application/json'})
                code, data = response.status_code, response.content
            else:
                code, data = 503, b'{"error":"injected outage"}'
            try:
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Faults)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    work = private / ('faults_' + uuid.uuid4().hex[:8]); work.mkdir()
    config = dict(original, llm_base_url=f'http://127.0.0.1:{server.server_port}/v1')
    (work/'config.json').write_text(json.dumps(config), encoding='utf-8')
    log = (work/'server.log').open('w', encoding='utf-8')
    process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8',
        str(ROOT/'phase2_통합/tools/run_system_demo.py'), '--config', str(work/'config.json'),
        '--port', '18083'], stdout=log, stderr=log)
    report = {'date':'2026-10-10', 'real_http_db_bge':True, 'fault_injection':True,
              'recovery_uses_actual_ollama':True, 'cases':[]}
    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        with httpx.Client(base_url='http://127.0.0.1:18083', timeout=600, trust_env=False,
                          headers={'Authorization':'Bearer '+tokens['REVIEWER']}) as client:
            for _ in range(40):
                if process.poll() is not None: raise RuntimeError('App startup failed')
                try:
                    if client.get('/health', timeout=2).status_code == 200: break
                except httpx.TransportError: pass
                time.sleep(.5)
            else: raise RuntimeError('Startup timeout')
            for mode in modes:
                state.update(mode=mode,calls=0)
                content=('[합성 시험 증적] 사용자 계정 관리 절차: 업무 포털의 신규 계정은 부서장 승인 후 관리자가 개인별로 발급한다. '
                         '계정 관리대장에 사용자, 권한, 승인자, 발급일을 기록한다. 퇴직 통보를 받으면 퇴직일에 계정을 잠그고 권한을 회수한다.')
                uploaded=client.post('/upload',files={'file':(f'fault_{mode}_{uuid.uuid4().hex[:8]}.txt',content.encode())})
                uploaded.raise_for_status(); eid=uploaded.json()['evidence_id']
                before=set((work/'system_demo/model_logs').glob('phase1_*.json'))
                before_phase2=set((work/'system_demo/model_logs').glob('phase2_*.json'))
                print('Starting',mode,eid,flush=True)
                r=client.post(f'/inspect/{eid}')
                db=pymysql.connect(host=original['DB_HOST'],port=int(original['DB_PORT']),
                    user=original['DB_USER'],password=original['DB_PASSWORD'],database=original['DB_NAME'])
                with db:
                    with db.cursor() as cursor:
                        cursor.execute('SELECT status FROM evidence WHERE evidence_id=%s',(eid,))
                        status=cursor.fetchone()[0]
                        cursor.execute('SELECT COUNT(*) FROM phase2_result WHERE evidence_id=%s',(eid,))
                        count=cursor.fetchone()[0]
                created=set((work/'system_demo/model_logs').glob('phase1_*.json'))-before
                runs=[json.loads(p.read_text(encoding='utf-8'))['llm_run'] for p in created]
                row={'mode':mode,'evidence_id':eid,'http_status':r.status_code,'http_model_calls':state['calls'],
                     'db_status':status,'phase2_rows':count,
                     'attempts':[x['attempts'] for x in runs],
                     'retry_counts':[x['retry_count'] for x in runs],
                     'last_issue_codes':[x.get('last_issue_codes') for x in runs]}
                if mode=='phase2_503':
                    phase2_logs=set((work/'system_demo/model_logs').glob('phase2_*.json'))-before_phase2
                    items=[item for p in phase2_logs for item in
                           (json.loads(p.read_text(encoding='utf-8')).get('output') or {}).get('items',[])]
                    row['phase2_item_results']=[item.get('result') for item in items]
                    row['passed']=bool(r.status_code==200 and status=='REVIEW_REQUIRED' and count>0
                                       and items and all(item.get('result')=='UNKNOWN' for item in items))
                elif mode=='recover':
                    row['passed']=bool(r.status_code==200 and runs and runs[0]['retry_count']==1 and status!='FAILED' and count>0)
                else:
                    row['passed']=status=='FAILED' and count==0 and state['calls']==(1 if mode=='400' else 2)
                (work/f'{mode}_response.json').write_text(json.dumps(r.json(),ensure_ascii=False,indent=2),encoding='utf-8')
                report['cases'].append(row);save(); print(json.dumps(row,ensure_ascii=False),flush=True)
        report['all_passed']=all(c['passed'] for c in report['cases']);save()
    finally:
        process.terminate();process.wait(timeout=20);log.close()
        server.shutdown();server.server_close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cases',nargs='+',choices=['503','400','malformed','timeout','recover','phase2_503'],
                   default=['503','400','malformed','timeout','recover'])
    a=p.parse_args();run(a.private.resolve(),a.output,a.cases)
