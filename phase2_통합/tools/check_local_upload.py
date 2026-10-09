"""Real HTTP + MySQL upload/progress test against isolated local test instance."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid


def main():
    import httpx
    import pymysql
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():
        raise ValueError('report already exists')
    config=json.loads(args.config.read_text(encoding='utf-8'))
    assert config['DB_HOST']=='127.0.0.1' and int(config['DB_PORT'])==13306
    run=uuid.uuid4().hex[:10]
    content=('가상 조직의 사용자 계정은 팀장 승인 후 생성한다. 발급 기록을 보존한다.\n'*20).encode('utf-8')
    checks=[]
    with httpx.Client(base_url='http://127.0.0.1:18080',timeout=30,trust_env=False) as client:
        page=client.get('/')
        assert page.status_code==200 and '/analysis/' in page.text
        checks.append('existing_upload_and_progress_page_served')
        bad=client.post('/evidence/upload',files={'files':('empty.txt',b'','text/plain')}).json()
        assert bad['failed']==1 and bad['results'][0]['error_code']=='EMPTY_FILE'
        checks.append('empty_file_rejected')
        response=client.post('/evidence/upload',files={'files':(f'local_test_{run}.txt',content,'text/plain')})
        response.raise_for_status()
        uploaded=response.json()['results'][0]
        assert uploaded['status']=='ok',uploaded
        eid=uploaded['evidence_id']
        initial=client.get('/evidence/'+eid).json()
        assert initial['status']=='UPLOADED',initial
        checks.append('upload_saved_in_mysql')
        job=client.post('/analysis/start').json()
        aid=job['analysis_id']
        observations=[job]
        end=time.monotonic()+60
        while observations[-1]['status']!='done' and time.monotonic()<end:
            time.sleep(.2)
            response=client.get('/analysis/'+aid)
            response.raise_for_status()
            observations.append(response.json())
        assert observations[-1]['status']=='done',observations[-1]
        final=client.get('/evidence/'+eid).json()
        assert final['status']=='PREPROCESSED',final
        checks.append('background_preprocess_polled_to_done')
        conn=pymysql.connect(host=config['DB_HOST'],port=int(config['DB_PORT']),user=config['DB_USER'],
            password=config['DB_PASSWORD'],database=config['DB_NAME'],charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT file_hash,status,version FROM evidence WHERE evidence_id=%s',(eid,))
                stored=cur.fetchone()
                cur.execute('SELECT COUNT(*) AS n FROM chunk WHERE evidence_id=%s',(eid,))
                chunks=cur.fetchone()['n']
            assert stored['file_hash']==hashlib.sha256(content).hexdigest() and chunks>0
            checks.append('independent_db_connection_verified_hash_status_and_chunks')
        finally:
            conn.close()
        # A second HTTP client proves this is server state, not this client's local state.
    with httpx.Client(base_url='http://127.0.0.1:18080',trust_env=False,timeout=10) as second:
        assert second.get('/evidence/'+eid).json()['status']=='PREPROCESSED'
        checks.append('new_http_client_reads_persisted_result')
    report={'real_http':True,'real_mysql':True,'mocked_components':[], 'synthetic_evidence':True,
            'evidence_id':eid,'analysis_id':aid,'checks':checks,'chunk_count':chunks,
            'initial_status':initial['status'],'final_status':final['status'],
            'observed_job_states':[o['status'] for o in observations],
            'limits':['Preprocessing only; no search/LLM/Phase2/report execution.',
                      'Existing job progress is in process memory; evidence/chunks persist in MySQL.',
                      'Shared deployed demo site was not modified.']}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
