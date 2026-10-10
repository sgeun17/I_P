from __future__ import annotations

import hmac
import os
import secrets
import sys
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INPUT_DIR = ROOT / "phase1_입력"
UI_DIR = ROOT / "IS_SO_UI" / "dist"
for p in (INPUT_DIR, INPUT_DIR / "database", INPUT_DIR / "chunking"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from main import app as input_app, process_file  # noqa: E402
from orchestrator import IntegrationError, inspect_evidence, load_result  # noqa: E402
from self_check import check as integration_check  # noqa: E402

app = FastAPI(title="ISMS-P Phase1 Integrated API", version="0.1.0")

# IS_SO_UI를 start_local.py(8080)로 따로 띄울 때를 위한 CORS. 이 서버가 UI를 직접 서빙하면 필요 없다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 입력팀 API(/evidence, /analysis)를 같은 주소에서 제공한다. 입력팀 기본 화면("/")은 제외.
app.router.routes.extend(r for r in input_app.routes if getattr(r, "path", None) not in {"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def fail(exc: IntegrationError):
    status = 422
    if exc.stage in {"search", "judgment"}:
        status = 503
    raise HTTPException(status_code=status, detail={"code": exc.code, "stage": exc.stage, "message": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "service": "phase1-integration"}


@app.get("/api/readiness")
def readiness():
    return integration_check()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), evidence_id: str | None = Form(None)):
    result = await process_file(file, evidence_id)
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result)
    return result


@app.post("/api/inspect/{evidence_id}")
def inspect(evidence_id: str, top_k: int = 5, model: str | None = None):
    try:
        return inspect_evidence(evidence_id, top_k=top_k, model=model)
    except IntegrationError as exc:
        fail(exc)


@app.post("/api/upload-and-inspect")
async def upload_and_inspect(
    file: UploadFile = File(...),
    evidence_id: str | None = Form(None),
    top_k: int = Form(5),
    model: str | None = Form(None),
):
    uploaded = await process_file(file, evidence_id)
    if uploaded.get("status") != "ok":
        raise HTTPException(status_code=400, detail=uploaded)
    try:
        return inspect_evidence(uploaded["evidence_id"], top_k=top_k, model=model)
    except IntegrationError as exc:
        fail(exc)


@app.get("/api/results/{evidence_id}")
def result(evidence_id: str, version: int | None = None):
    found = load_result(evidence_id, version)
    if found is None:
        raise HTTPException(status_code=404, detail={"code": "RESULT_NOT_FOUND"})
    return found


PAGE = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ISMS-P 증적 사전점검</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#172033;font-family:Arial,'Malgun Gothic',sans-serif}.wrap{max-width:1100px;margin:36px auto;padding:0 18px}h1{font-size:24px;margin-bottom:6px}.muted{color:#6b7280}.card{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:20px;margin:16px 0;box-shadow:0 2px 8px #0000000d}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}input,button{font:inherit}input[type=file],input[type=text],input[type=number]{border:1px solid #cfd5df;border-radius:8px;padding:10px}button{border:0;border-radius:8px;padding:11px 16px;background:#2457d6;color:white;cursor:pointer}button:disabled{opacity:.55}.status{font-weight:700}.pill{display:inline-block;border:1px solid #d8deea;border-radius:999px;padding:4px 9px;margin-right:6px;font-size:12px}.control{border-top:1px solid #edf0f4;padding:16px 0}.control:first-child{border-top:0}.quote{background:#f8fafc;border-left:4px solid #9aa7bc;padding:10px 12px;margin:8px 0;white-space:pre-wrap}.warn{background:#fff7ed;border:1px solid #fed7aa;padding:10px;border-radius:8px}.err{background:#fef2f2;border:1px solid #fecaca;padding:10px;border-radius:8px;color:#991b1b}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.metric{background:#f8fafc;border-radius:10px;padding:12px}.metric b{display:block;margin-top:5px;font-size:18px}
</style></head><body><div class="wrap">
<h1>ISMS-P 증적 사전점검</h1><div class="muted">업로드 → 전처리 → 검색 → 판단 → 근거 인용을 한 화면에서 확인</div>
<div class="card"><h3>1. 증적 업로드 및 점검</h3><div class="row"><input id="file" type="file"><input id="model" type="text" placeholder="판단 모델 (환경변수 설정 시 생략)"><input id="topk" type="number" min="1" max="101" value="5"><button id="run">점검 시작</button></div><p id="progress" class="muted"></p></div>
<div id="out"></div>
</div><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const run=document.getElementById('run'), out=document.getElementById('out'), progress=document.getElementById('progress');
run.onclick=async()=>{const f=document.getElementById('file').files[0];if(!f){progress.textContent='파일을 선택하세요.';return}run.disabled=true;out.innerHTML='';progress.textContent='업로드 및 점검 중...';const fd=new FormData();fd.append('file',f);fd.append('top_k',document.getElementById('topk').value);const m=document.getElementById('model').value.trim();if(m)fd.append('model',m);try{const r=await fetch('/api/upload-and-inspect',{method:'POST',body:fd});const d=await r.json();if(!r.ok)throw new Error(JSON.stringify(d.detail||d));render(d);progress.textContent='점검 완료';}catch(e){out.innerHTML='<div class="card err">'+esc(e.message)+'</div>';progress.textContent='점검 실패';}finally{run.disabled=false}};
function render(d){const s=d.screen.summary, review=d.screen.human_review||{}, controls=d.screen.mapped_controls||[];let html='<div class="card"><h3>2. 점검 결과</h3><div class="grid">'+metric('처리 상태',s.processing_status)+metric('매핑',s.display_match_status||s.match_status)+metric('매핑 수',s.mapping_count)+metric('사람 검토',s.review_required?'필요':'불필요')+'</div>';
if(s.review_required)html+='<p class="warn"><b>검토 필요</b><br>'+esc((review.reasons||[]).join(', '))+'</p>';html+='</div><div class="card"><h3>3. 통제항목 및 인용</h3>';
if(!controls.length)html+='<p class="muted">표시할 매핑 항목이 없습니다.</p>';for(const c of controls){html+='<div class="control"><div><span class="pill">'+esc(c.control_id)+'</span><span class="pill">'+esc(c.relation)+'</span></div><h3>'+esc(c.control_name)+'</h3><p>'+esc(c.reason)+'</p><p class="muted">검색 유사도: '+esc(c.similarity_score)+' · LLM confidence: '+esc(c.llm_confidence)+'</p>';for(const q of c.citations||[]){html+='<div class="quote"><b>'+esc(q.source_file)+(q.page?' · p.'+esc(q.page):'')+'</b><br>'+esc(q.quote)+'</div>'}html+='</div>'}html+='</div>';out.innerHTML=html}
function metric(k,v){return '<div class="metric">'+esc(k)+'<b>'+esc(v)+'</b></div>'}
</script></body></html>'''


@app.post("/api/auth/login")
def login(payload: dict = Body(...)):
    """로컬 실행용 최소 로그인. 회원·회사 관리는 아직 백엔드가 없다.

    PHASE1_UI_PASSWORD가 설정되어 있으면 비밀번호가 일치해야 하고,
    없으면 127.0.0.1 로컬 실행을 전제로 어떤 이메일이든 관리자 권한으로 통과시킨다.
    """
    email = str(payload.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="이메일을 입력하세요.")
    expected = os.environ.get("PHASE1_UI_PASSWORD")
    if expected and not hmac.compare_digest(str(payload.get("password") or ""), expected):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    return {
        "token": secrets.token_urlsafe(24),
        "user": {"name": email.split("@")[0], "email": email, "role": "admin", "company": os.environ.get("PHASE1_UI_COMPANY", "로컬")},
    }


@app.get("/classic", response_class=HTMLResponse)
def classic():
    return PAGE


if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
else:
    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE
