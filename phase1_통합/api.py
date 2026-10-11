from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INPUT_DIR = ROOT / "phase1_입력"
UI_DIR = ROOT / "IS_SO_UI" / "dist"
for p in (INPUT_DIR, INPUT_DIR / "database", INPUT_DIR / "chunking",
          ROOT / "auth", ROOT / "phase2_통합", ROOT / "phase2_인터페이스"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import owner  # noqa: E402

from evidence_store import is_visible  # noqa: E402
from main import actor_of, app as input_app, company_of, process_file  # noqa: E402
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
# 입력팀 쪽 예외 처리기도 같이 옮긴다. 라우터에는 안 붙어 있다.
for _t, _h in getattr(input_app, "exception_handlers", {}).items():
    if _t not in app.exception_handlers:
        app.add_exception_handler(_t, _h)

# 로그인·회사·초대·운영자 엔드포인트
import auth_api  # noqa: E402
import phase2_api  # noqa: E402
app.include_router(auth_api.router)
app.include_router(phase2_api.router)

# MySQL 이 꺼져 있을 때. 추적 내용이 섞인 500 대신 503 으로 답한다.
try:
    import pymysql as _pymysql  # noqa: E402
    _DB_DOWN = (_pymysql.err.OperationalError, _pymysql.err.InterfaceError)
except Exception:
    _DB_DOWN = ()


def _db_down(request: Request, exc: Exception | None = None) -> JSONResponse:
    return JSONResponse(status_code=503,
                        content={"detail": {"error_code": "DB_UNAVAILABLE",
                                            "message": "DB에 연결할 수 없습니다."}})


for _e in _DB_DOWN:
    app.add_exception_handler(_e, _db_down)


# ── 문지기 ────────────────────────────────────────────────────────────────
# PUBLIC 에 없는 모든 주소에 로그인을 건다. 새 주소를 추가하면 자동으로
# 잠긴다 — 깜빡해서 열려 있는 쪽보다 낫다.
PUBLIC_EXACT = {
    "/health", "/classic", "/docs", "/redoc", "/openapi.json",
    "/docs/oauth2-redirect", "/favicon.ico",
    "/api/readiness",
    "/api/auth/login", "/api/auth/signup", "/api/auth/verify",
    "/api/auth/resend", "/api/invites/accept",
}
PUBLIC_PREFIX = ("/api/invites/",)          # 초대 링크는 계정이 없는 사람이 연다
AUTH_OFF = os.getenv("AUTH_OFF") == "1"     # 개발용. 운영에서 1 로 두지 말 것

# 역할까지 봐야 하는 자리. 입력팀 라우터에는 권한 검사가 없어서 여기서 건다.
# (method, 주소 앞부분) -> company_store.PERMS 의 할 일 이름
ROLE_RULES = (
    ("DELETE", "/evidence/", "delete"),
)


def _is_public(path: str) -> bool:
    if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX):
        return True
    # UI 정적 파일 (StaticFiles 가 "/" 에 붙어 있다)
    return not path.startswith(("/api/", "/evidence", "/analysis"))


@app.middleware("http")
async def require_login(request: Request, call_next):
    if AUTH_OFF or request.method == "OPTIONS" or _is_public(request.url.path):
        owner.clear()
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    parts = auth.split(None, 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None
    me = None
    if token:
        import company_store, user_store
        try:
            sess = user_store.current_user(token)
            me = company_store.profile(sess["user_id"]) if sess else None
        except _DB_DOWN:
            return _db_down(request)
    if me is None:
        return JSONResponse(status_code=401,
                            content={"detail": {"error_code": "NOT_LOGGED_IN",
                                                "message": "로그인이 필요합니다."}})
    # 역할 검사. 화면이 버튼을 숨기는 것과 서버가 막는 것은 다르다.
    import company_store as _cs
    for _m, _pre, _act in ROLE_RULES:
        if request.method == _m and request.url.path.startswith(_pre):
            if not _cs.can(me.get("role"), _act):
                return JSONResponse(
                    status_code=403,
                    content={"detail": {"error_code": "NOT_ALLOWED",
                                        "message": "권한이 없습니다."}})

    request.state.user = me
    # 이 요청이 볼 수 있는 회사를 심는다. 조회는 전부 owner.py 를 지나간다.
    try:
        owner.use(company_id=me.get("companyId"), actor=me.get("email"),
                  role=me.get("role"), is_super=(me.get("role") == "super"))
    except ValueError:
        return JSONResponse(status_code=403,
                            content={"detail": {"error_code": "COMPANY_MISSING",
                                                "message": "소속 회사가 없습니다."}})
    try:
        return await call_next(request)
    finally:
        owner.clear()


def fail(exc: IntegrationError):
    status = 422
    if exc.stage in {"search", "judgment"}:
        status = 503
    raise HTTPException(status_code=status, detail={"code": exc.code, "stage": exc.stage, "message": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "service": "phase1-integration"}


@app.get("/api/readiness")
def readiness(request: Request):
    data = integration_check()
    if getattr(request.state, "user", None) is None:
        # 로그인 전에도 UI 가 "준비 상태 확인" 을 눌러야 한다. 다만 서버
        # 안쪽 경로는 보내지 않는다.
        data["checks"] = [{"name": c["name"], "ok": c["ok"]} for c in data["checks"]]
    return data


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...),
                 evidence_id: str | None = Form(None)):
    result = await process_file(file, evidence_id,
                                actor_of(request), company_of(request))
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result)
    return result


@app.post("/api/inspect/{evidence_id}")
def inspect(evidence_id: str, top_k: int = 5, model: str | None = None):
    if not is_visible(evidence_id):
        raise HTTPException(status_code=404, detail={"code": "EVIDENCE_NOT_FOUND"})
    try:
        return inspect_evidence(evidence_id, top_k=top_k, model=model)
    except IntegrationError as exc:
        fail(exc)


@app.post("/api/upload-and-inspect")
async def upload_and_inspect(
    request: Request,
    file: UploadFile = File(...),
    evidence_id: str | None = Form(None),
    top_k: int = Form(5),
    model: str | None = Form(None),
):
    uploaded = await process_file(file, evidence_id,
                                  actor_of(request), company_of(request))
    if uploaded.get("status") != "ok":
        raise HTTPException(status_code=400, detail=uploaded)
    try:
        return inspect_evidence(uploaded["evidence_id"], top_k=top_k, model=model)
    except IntegrationError as exc:
        fail(exc)


@app.get("/api/results/{evidence_id}")
def result(evidence_id: str, version: int | None = None):
    # 파일을 읽기 전에 DB 로 회사를 묻는다. load_result 는 디스크만 본다.
    if not is_visible(evidence_id):
        # 운영자(super)가 고객 증적을 건드리려 한 것은 DENIED 로 남긴다.
        owner.audit_denied("VIEW_ORIGINAL", evidence_id, version or 1)
        raise HTTPException(status_code=404, detail={"code": "RESULT_NOT_FOUND"})
    found = owner.audit_super("VIEW_ORIGINAL", evidence_id, version or 1,
                              lambda: load_result(evidence_id, version))
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


# /api/auth/login 은 auth_api.py 로 옮겼다 (2026-10-11).
# 전에 있던 것은 아무 메일이나 비밀번호 없이 admin 으로 통과시키고,
# 돌려주는 토큰을 어디에도 저장하지 않아 아무도 검증할 수 없었다.


@app.get("/classic", response_class=HTMLResponse)
def classic():
    return PAGE


if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
else:
    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE
