"""Add local asynchronous search UI to the input team's app without source edits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2] / 'phase1_검색'


class SearchJobs:
    def __init__(self, path, get_evidence, get_chunks, retrieve):
        self.path = Path(path)
        self.get_evidence, self.get_chunks, self.retrieve = get_evidence, get_chunks, retrieve
        self.pool = ThreadPoolExecutor(max_workers=1)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS search_job (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            rows = db.execute('SELECT id,data FROM search_job').fetchall()
            for key, raw in rows:
                data=json.loads(raw)
                if data['status'] in ('QUEUED','RUNNING'):
                    data.update(status='FAILED',error={'code':'SERVER_RESTARTED','message':'서버 재시작으로 중단됐습니다. 다시 검색하세요.'})
                    db.execute('UPDATE search_job SET data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),key))
            db.commit()

    def save(self, job):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            db.execute('INSERT OR REPLACE INTO search_job VALUES (?,?)',(job['id'],json.dumps(job,ensure_ascii=False)))
            db.commit()

    def get(self, key):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            row=db.execute('SELECT data FROM search_job WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def create(self,eid,k):
        if type(k) is not int or not 1 <= k <= 101:
            raise ValueError('top_k는 1~101이어야 합니다.')
        info=self.get_evidence(eid)
        if not info or info['status'] not in ('PREPROCESSED','MAPPING','VALIDATING','COMPLETED','REVIEW_REQUIRED'):
            raise ValueError('먼저 업로드 화면에서 전처리를 완료하세요.')
        chunks=self.get_chunks(eid,info['version'])
        if not chunks:
            raise ValueError('전처리된 청크가 없습니다.')
        payload={'evidence_id':eid,'version':info['version'],'source_file':info['file_name'],
                 'file_type':info['file_type'],'chunks':chunks}
        job={'id':uuid.uuid4().hex,'evidence_id':eid,'version':info['version'],'top_k':k,
             'file_hash':info['file_hash'],'status':'QUEUED','created_at':datetime.now(timezone.utc).isoformat(),
             'result':None,'error':None}
        self.save(job)
        self.pool.submit(self.run,job,payload)
        return {'id':job['id'],'status':'QUEUED'}

    def run(self,job,payload):
        try:
            job['status']='RUNNING'; self.save(job)
            result=self.retrieve(payload,top_k=job['top_k'])
            if not result.get('success'):
                job.update(status='FAILED',error=result.get('error'))
            else:
                now=self.get_evidence(job['evidence_id'])
                if now['version']!=job['version'] or now['file_hash']!=job['file_hash']:
                    job.update(status='FAILED',error={'code':'STALE_EVIDENCE','message':'검색 중 원본이 변경되었습니다. 다시 검색하세요.'})
                else:
                    job.update(status='SUCCEEDED',result={'index':result['index'],'retrieval':result['retrieval']})
        except Exception as exc:
            job.update(status='FAILED',error={'code':'SEARCH_FAILED','message':str(exc)})
        finally:
            self.save(job)


def attach(upload,work):
    from fastapi import HTTPException, Query
    from fastapi.responses import HTMLResponse
    sys.path.insert(0,str(ROOT))
    from chunk_retriever import retrieve
    from evidence_store import get_evidence
    from chunk_store import get_chunks
    jobs=SearchJobs(Path(work)/'search_jobs.sqlite3',get_evidence,get_chunks,retrieve)
    upload.app.state.local_search_jobs=jobs

    @upload.app.post('/local-search/{evidence_id}',status_code=202)
    def start(evidence_id:str,top_k:int=Query(5,ge=1,le=101)):
        try:
            return jobs.create(evidence_id,top_k)
        except ValueError as exc:
            raise HTTPException(409,str(exc))

    @upload.app.get('/local-search/jobs/{job_id}')
    def status(job_id:str):
        job=jobs.get(job_id)
        if job is None:
            raise HTTPException(404,'검색 작업을 찾을 수 없습니다.')
        # Completed historical results are not silently presented as current.
        try:
            current=get_evidence(job['evidence_id'])
            job['stale']=current['version']!=job['version'] or current['file_hash']!=job['file_hash']
        except Exception:
            job['stale']=True
        return job

    @upload.app.get('/search',response_class=HTMLResponse)
    def page():
        return PAGE
    upload.PAGE=upload.PAGE.replace('</body>','<p style="text-align:center"><a href="/search">전처리된 증적 → BGE 후보 검색</a></p></body>')


PAGE=r'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>로컬 BGE 검색</title>
<style>body{font:16px sans-serif;max-width:1000px;margin:40px auto;padding:20px;background:#f5f7fb}section{background:white;padding:24px;border-radius:12px;margin:20px 0}button,select,input{padding:10px;margin:6px}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}table{width:100%}#state{white-space:pre-wrap}</style>
<a href="/">← 업로드·전처리</a><h1>전처리된 증적의 BGE 후보 검색</h1>
<p>네 PC의 BGE-M3로 검색합니다. 후보 목록은 LLM의 최종 매핑·적합 판정이 아닙니다.</p>
<section><label>증적 <select id="evidence"></select></label><label>후보 수 <input id="k" type="number" min="1" max="101" value="5"></label><button id="run">BGE 검색 시작</button><p id="state" role="status">증적 목록을 불러오는 중</p></section><section id="result" hidden></section>
<script>
const $=id=>document.getElementById(id);let timer;
async function request(url,options){const r=await fetch(url,options);const d=await r.json();if(!r.ok)throw Error(JSON.stringify(d.detail||d));return d}
async function list(){try{const d=await request('/evidence');for(const e of d.items){const o=document.createElement('option');o.value=e.evidence_id;o.textContent=e.evidence_id+' · '+e.file_name+' · '+e.status;$('evidence').append(o)}$('state').textContent='먼저 업로드 화면에서 전처리를 완료한 후 검색하세요.'}catch(e){$('state').textContent=e.message}}
async function poll(id){try{const j=await request('/local-search/jobs/'+id);$('state').textContent='검색 상태: '+j.status+(j.stale?' · 원본 변경됨: 다시 검색 필요':'');if(j.status==='QUEUED'||j.status==='RUNNING'){timer=setTimeout(()=>poll(id),700);return}$('run').disabled=false;if(j.error){$('state').textContent+='\n'+j.error.code+': '+j.error.message;return}const box=$('result');box.replaceChildren();box.hidden=false;const title=document.createElement('h2');title.textContent='Top-'+j.top_k+' 후보 · '+j.evidence_id+' v'+j.version;box.append(title);const table=document.createElement('table');for(const c of j.result.retrieval.candidates){const tr=document.createElement('tr');for(const value of [c.rank,c.control_id,c.control_name,c.similarity_score]){const td=document.createElement('td');td.textContent=value;tr.append(td)}table.append(tr)}box.append(table)}catch(e){$('run').disabled=false;$('state').textContent=e.message}}
$('run').onclick=async()=>{clearTimeout(timer);$('run').disabled=true;$('result').hidden=true;try{const j=await request('/local-search/'+encodeURIComponent($('evidence').value)+'?top_k='+encodeURIComponent($('k').value),{method:'POST'});localStorage.setItem('localSearchJob',j.id);await poll(j.id)}catch(e){$('run').disabled=false;$('state').textContent=e.message}};
list().then(()=>{const id=localStorage.getItem('localSearchJob');if(id){$('run').disabled=true;poll(id)}});
</script></html>'''
