"""Actual HTTP/DB/BGE/Ollama demonstration using an explicitly synthetic TXT."""
import argparse
import json
from pathlib import Path
import time
import httpx


def run(private, output, short=False, base='http://127.0.0.1:18081'):
    tokens=json.loads((private/'system_tokens.json').read_text())
    headers={'Authorization':'Bearer '+tokens['REVIEWER']}
    checks=[]
    report={'synthetic_evidence':True,'real_http':True,'real_db':True,
            'real_bge':False,'real_llm':False,'e2e_passed':False,'checks':checks}
    def save():
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with httpx.Client(base_url=base,timeout=3600,trust_env=False) as client:
        checks.append({'name':'anonymous_denied','passed':client.get('/health').status_code==401})
        checks.append({'name':'forged_role_denied','passed':client.get('/health',headers={'X-Role':'APPROVER'}).status_code==401})
        for name,content in [('empty.txt',b''),('broken.docx',b'invalid-zip'),('invalid.exe',b'fake')]:
            r=client.post('/upload',files={'file':(name,content)},headers=headers)
            checks.append({'name':name,'passed':r.status_code==400,'status':r.status_code})
        text='''[통합 시험용 가상 증적 — 실제 조직 자료 아님]
사용자 계정 관리 절차서
적용 대상: 가상 조직의 업무 포털 전체 사용자 계정.
시행일: 2026-10-01. 승인자: 가상 정보보호 책임자.
신규 계정은 소속 부서장이 업무 필요성과 권한 범위를 검토한 후 승인한다.
시스템 관리자는 승인 요청서의 사용자 신원과 소속을 확인하고 개인별 고유 계정을 생성한다.
계정 관리대장에는 사용자, 소속, 계정 ID, 권한, 신청일, 승인자, 발급일을 기록한다.
공용 계정은 금지한다. 불가피한 예외는 책임자의 별도 승인과 사용 이력 기록을 요구한다.
인사 이동 시 소속 부서는 변경을 요청하고 관리자는 기존 권한 회수 후 새 권한을 부여한다.
퇴직 통보를 받으면 관리자는 퇴직일에 계정을 잠그고 권한을 회수하며 처리 결과를 기록한다.
관리자는 분기마다 계정 목록을 인사 명단과 대조하여 불필요한 계정을 삭제하고 검토 결과를 보관한다.
본 문서는 절차를 정한 시험 문서이며 실제 이행 기록이나 실제 퇴직 발생을 증명하지 않는다.
'''
        if short:
            text='[합성 시험 증적] 사용자 계정 관리 절차: 업무 포털의 신규 계정은 부서장 승인 후 관리자가 개인별로 발급한다. 계정 관리대장에 사용자, 권한, 승인자, 발급일을 기록한다. 퇴직 통보를 받으면 퇴직일에 계정을 잠그고 권한을 회수한다.'
        report['scenario']='short_account_procedure' if short else 'multi_line_account_procedure'
        r=client.post('/upload',files={'file':(f'system_demo_{time.time_ns()}.txt',text.encode())},headers=headers)
        r.raise_for_status()
        eid=r.json()['evidence_id']; report['evidence_id']=eid
        viewer={'Authorization':'Bearer '+tokens['VIEWER']}
        checks.append({'name':'viewer_original_denied','passed':client.get(f'/evidence/{eid}/original',headers=viewer).status_code==403})
        checks.append({'name':'viewer_download_denied','passed':client.get(f'/evidence/{eid}/download',headers=viewer).status_code==403})
        r=client.get(f'/evidence/{eid}/download',headers=headers)
        checks.append({'name':'authorized_original_matches','passed':r.status_code==200 and r.content==text.encode()})
        checks.append({'name':'invalid_topk','passed':client.post(f'/inspect/{eid}?top_k=0',headers=headers).status_code==422})
        save(); print('Boundary checks complete; actual model pipeline starting',eid,flush=True)
        started=time.monotonic()
        r=client.post(f'/inspect/{eid}',headers=headers)
        report['elapsed_seconds']=round(time.monotonic()-started,2)
        report['pipeline_http_status']=r.status_code
        payload=r.json()
        (private/'system_demo'/'latest_http_result.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        if r.status_code==200:
            report['real_bge']=True; report['real_llm']=True
            report['phase2']=payload['phase2']
            result=client.get(f'/evidence/{eid}/result',headers=headers)
            html=client.get(f'/evidence/{eid}/report',headers=headers)
            checks.append({'name':'result_readable','passed':result.status_code==200})
            checks.append({'name':'report_readable','passed':html.status_code==200 and 'text/html' in html.headers.get('content-type','')})
            report['e2e_passed']=all(x['passed'] for x in checks) and payload['phase2']['judged']>0 and payload['phase2']['failed']==0
        else:
            report['error']=payload
        save(); print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--short',action='store_true')
    p.add_argument('--port',type=int,default=18081)
    a=p.parse_args(); run(a.private,a.output,a.short,f'http://127.0.0.1:{a.port}')
