import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

import filetype
from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse

# database 폴더의 코드가 "from config import ..." 식으로 서로를 불러오므로 경로를 추가한다
sys.path.insert(0, str(Path(__file__).resolve().parent / "database"))

from db import get_connection  # noqa: E402
from evidence_store import (  # noqa: E402
    EvidenceError,
    compute_hash,
    delete_evidence,
    get_evidence,
    list_evidence,
    replace_evidence,
    save_evidence,
)

# 파서 → 청킹 → 청크 DB 를 잇는 부분 (팀장님 피드백 4번)
# pipeline.py 가 chunking·database 폴더 경로를 스스로 추가하므로 여기서는 그냥 부르면 됩니다.
from pipeline import process_one  # noqa: E402

app = FastAPI()

ALLOWED = {"pdf", "docx", "xlsx", "pptx", "png", "jpg", "txt", "csv"}
MAX_MB = 20                        # 파일 1개당 최대 용량
MAX_BYTES = MAX_MB * 1024 * 1024
MAX_FILES = 1000                   # 한 번에 올릴 수 있는 파일 수

# 이 파일(main.py)이 있는 폴더를 기준으로 삼습니다.
# 상대경로로 두면 "어느 폴더에서 python 을 실행했는지"에 따라 임시 파일 위치가 달라집니다.
BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "uploads" / "tmp"   # 검사 중인 임시 파일 (통과하면 B 코드가 정식 폴더로 이동)
TMP_DIR.mkdir(parents=True, exist_ok=True)

lock = asyncio.Lock()              # 번호 발급이 겹치지 않도록 저장은 한 번에 하나씩

# 아직 파싱·청킹을 안 한 상태. 이 상태인 증적만 분석 대상으로 잡습니다.
#   FAILED 를 넣어둔 이유: 한 번 실패한 증적도 다시 누르면 재시도되게 하려는 것입니다.
TO_PROCESS = ("UPLOADED", "FAILED")

# 분석 진행 상황. 서버를 끄면 사라지지만, 각 증적의 결과는 evidence.status 에 남습니다.
JOBS = {}

ERROR_STATUS = {
    "EVIDENCE_NOT_FOUND": 404,
    "FILE_NOT_FOUND": 404,
    "INVALID_FILE_TYPE": 415,
}


def error(status, code, message):
    """팀 규격 오류 응답"""
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": {"code": code, "message": message}},
    )


@app.exception_handler(EvidenceError)
async def evidence_error_handler(request, exc: EvidenceError):
    return error(ERROR_STATUS.get(exc.code, 400), exc.code, exc.message)


@app.exception_handler(Exception)
async def unexpected_error_handler(request, exc: Exception):
    return error(500, "SERVER_ERROR", "서버 오류가 발생했습니다. 서버 터미널의 오류 내용을 확인하세요.")


def all_evidence():
    """저장된 증적 전체 (list_evidence가 한 번에 100개까지라 나눠서 가져온다)"""
    items, page = [], 1
    while True:
        d = list_evidence(page=page, size=100)
        items += d["items"]
        if not d["items"] or len(items) >= d["total"]:
            return items
        page += 1


def looks_like_text(head: bytes) -> bool:
    """
    txt·csv 가 진짜 글자 파일인지 확인합니다. (전처리 text_parser.py 와 같은 기준)
      ① BOM 이 있으면 글자 파일 (UTF-16 은 NUL 이 많아서 먼저 확인)
      ② NUL 이 있으면 글자 파일이 아님 (그림·압축·실행 파일)
      ③ UTF-8 또는 CP949 로 읽히고, 보이지 않는 제어문자가 거의 없어야 함
    """
    if head.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True                      # UTF-16 은 NUL 이 많아서 BOM 을 먼저 봐야 함
    if b"\x00" in head:
        return False
    for encoding in ("utf-8", "cp949"):
        data = head
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as e:
            if e.start < len(data) - 4:      # 파일 끝(8KB 경계)에서 잘린 게 아니면 이 인코딩은 아님
                continue
            text = data[: e.start].decode(encoding, "ignore")
        except LookupError:
            continue
        if not text:
            continue
        control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
        return control <= len(text) * 0.01     # 제어문자가 1% 이하면 글자 파일로 봄
    return False


async def process_file(file: UploadFile, evidence_id: str | None = None) -> dict:
    name = file.filename

    def fail(code, message):
        return {"file_name": name, "status": "error", "error_code": code, "error_message": message}

    # 1. 확장자 검사
    ext = Path(name or "").suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED:
        return fail("INVALID_EXTENSION", "허용되지 않는 파일 형식입니다.")

    # 2. 빈 파일 검사
    head = await file.read(8192)
    if not head:
        return fail("EMPTY_FILE", "빈 파일입니다.")

    # 3. 실제 파일 종류 검사 (매직 넘버)
    #    txt·csv 는 매직 넘버가 없어서 filetype 이 판별하지 못함(None) → "글자로 읽히는지"로 확인
    if ext in ("txt", "csv"):
        if not looks_like_text(head):
            return fail("CONTENT_MISMATCH", "파일 내용이 확장자와 일치하지 않습니다.")
    else:
        kind = filetype.guess(head)
        if kind is None or kind.extension != ext:
            return fail("CONTENT_MISMATCH", "파일 내용이 확장자와 일치하지 않습니다.")

    # 4. 크기 검사 + 임시 저장
    tmp_path = TMP_DIR / f"{uuid.uuid4().hex}.{ext}"
    size = len(head)
    too_large = False

    with open(tmp_path, "wb") as f:
        f.write(head)
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                too_large = True
                break
            f.write(chunk)

    if too_large:
        tmp_path.unlink(missing_ok=True)
        return fail("FILE_TOO_LARGE", f"파일 크기가 {MAX_MB}MB를 초과합니다.")

    # 5. 저장 (B 파트): 신규 등록 / 버전 갱신 / 변경 없음
    async with lock:
        try:
            existing = None
            if evidence_id:
                existing = await asyncio.to_thread(get_evidence, evidence_id)
                if existing is None:
                    tmp_path.unlink(missing_ok=True)
                    return fail("EVIDENCE_NOT_FOUND", "지정한 evidence_id를 찾을 수 없습니다.")

            if existing is None:
                saved = await asyncio.to_thread(save_evidence, str(tmp_path), name, ext)
                action = "created"
            else:
                new_hash = await asyncio.to_thread(compute_hash, str(tmp_path))
                if new_hash == existing["file_hash"]:
                    tmp_path.unlink(missing_ok=True)
                    return {
                        "file_name": name,
                        "status": "ok",
                        "action": "unchanged",
                        "evidence_id": existing["evidence_id"],
                        "version": existing["version"],
                        "file_type": ext,
                        "file_size": size,
                        "is_duplicate": False,
                        "duplicate_of": None,
                    }
                saved = await asyncio.to_thread(
                    replace_evidence, existing["evidence_id"], str(tmp_path), name, ext
                )
                action = "replaced"

        except EvidenceError as e:
            tmp_path.unlink(missing_ok=True)
            return fail(e.code, e.message)
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            print("저장 중 오류:", repr(e))
            return fail("DB_ERROR", "저장 중 오류가 발생했습니다. DB 연결과 서버 터미널을 확인하세요.")

    return {
        "file_name": name,
        "status": "ok",
        "action": action,
        "evidence_id": saved["evidence_id"],
        "version": saved["version"],
        "file_type": saved["file_type"],
        "file_size": saved["file_size"],
        "is_duplicate": saved.get("is_duplicate", False),
        "duplicate_of": saved.get("duplicate_of"),
    }


@app.post("/evidence/upload")
async def upload(files: list[UploadFile], evidence_id: str | None = Form(None)):
    if not files:
        return error(400, "NO_FILES", "업로드된 파일이 없습니다.")
    if len(files) > MAX_FILES:
        return error(413, "TOO_MANY_FILES", f"한 번에 {MAX_FILES}개까지 업로드할 수 있습니다.")

    results = [await process_file(f, evidence_id) for f in files]
    ok = sum(1 for r in results if r["status"] == "ok")
    return {
        "total": len(results),
        "success": ok,
        "failed": len(results) - ok,
        "results": results,
    }


@app.get("/evidence")
def list_api(status: str | None = None, page: int = 1, size: int = 100):
    return list_evidence(status=status, page=page, size=size)


@app.get("/evidence/{evidence_id}")
def get_api(evidence_id: str):
    return get_evidence(evidence_id)


@app.delete("/evidence/{evidence_id}")
def delete_api(evidence_id: str):
    delete_evidence(evidence_id)
    return {"deleted": evidence_id}


async def run_pipeline(analysis_id, evidence_ids):
    """
    증적을 하나씩 파싱 → 청킹 → 청크 DB 저장 합니다. (백그라운드에서 돎)

    to_thread 로 부르는 이유 : pipeline.process_one 은 DB·OCR 을 기다리는 동안 멈춰 있는
      코드(동기 함수)라서, 그냥 부르면 그 시간 내내 서버가 다른 요청을 못 받습니다.
    """
    job = JOBS[analysis_id]
    for evidence_id in evidence_ids:
        job["current"] = evidence_id
        try:
            result, detail = await asyncio.to_thread(process_one, evidence_id, False)
        except Exception as e:                    # process_one 안에서 이미 FAILED 로 바꿔주지만,
            result, detail = "failed", f"{type(e).__name__}: {e}"   # 그마저 실패한 경우
        job["done"] += 1
        if result == "ok":
            job["ok"] += 1
            job["chunks"] += detail if isinstance(detail, int) else 0
        else:
            job["failed"] += 1
            job["errors"].append({"evidence_id": evidence_id, "reason": str(detail)})
    job["current"] = None
    job["status"] = "done"
    job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@app.post("/analysis/start")
async def start_analysis():
    """
    저장된 증적을 파싱·청킹합니다. (Main → Parser → Chunker 연결)

    바로 끝내지 않고 analysis_id 를 먼저 돌려줍니다.
      PNG·JPG 는 OCR 이 한 장에 10~30초라, 다 끝날 때까지 기다리면 브라우저가 먼저 끊깁니다.
      진행 상황은 GET /analysis/{analysis_id} 로 확인합니다.
    """
    items = all_evidence()
    if not items:
        return error(400, "NO_EVIDENCE", "분석할 증적이 없습니다. 먼저 파일을 업로드하세요.")

    running = next((k for k, v in JOBS.items() if v["status"] == "running"), None)
    if running:
        return error(409, "ANALYSIS_RUNNING", f"이미 분석이 돌고 있습니다: {running}")

    targets = [i["evidence_id"] for i in items if i["status"] in TO_PROCESS]
    skipped = len(items) - len(targets)
    analysis_id = "A" + datetime.now().strftime("%Y%m%d%H%M%S")

    if not targets:
        return {"analysis_id": analysis_id, "status": "done", "total": 0, "skipped": skipped,
                "ok": 0, "failed": 0, "chunks": 0, "done": 0, "errors": [],
                "message": f"이미 전처리된 증적 {skipped}개뿐입니다. 새로 처리할 것이 없습니다."}

    JOBS[analysis_id] = {
        "analysis_id": analysis_id, "status": "running",
        "total": len(targets), "done": 0, "ok": 0, "failed": 0, "chunks": 0,
        "skipped": skipped, "current": None, "errors": [],
        "started_at": datetime.now().isoformat(timespec="seconds"), "finished_at": None,
        "evidence_ids": targets,
    }
    asyncio.create_task(run_pipeline(analysis_id, targets))
    return JOBS[analysis_id]


@app.get("/analysis/{analysis_id}")
def analysis_status(analysis_id: str):
    """분석 진행 상황. status 가 running 이면 화면이 1초마다 다시 물어봅니다."""
    job = JOBS.get(analysis_id)
    if job is None:
        return error(404, "ANALYSIS_NOT_FOUND",
                     "그런 분석 기록이 없습니다. (서버를 다시 켜면 진행 기록은 사라집니다)")
    return job


PAGE = r"""
<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>증적 업로드</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f6fa;font-family:'Malgun Gothic',sans-serif;color:#1f2937}
.wrap{max-width:980px;margin:40px auto;padding:0 16px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:0 0 12px}
.sub{color:#6b7280;font-size:14px;margin-bottom:20px}
.card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:16px}
#drop{border:2px dashed #9ca3af;border-radius:12px;padding:40px 16px;text-align:center;cursor:pointer;color:#6b7280}
#drop.over{border-color:#2563eb;background:#eff6ff;color:#2563eb}
#drop b{color:#2563eb}
.item{display:flex;align-items:center;justify-content:space-between;padding:8px 4px;border-bottom:1px solid #eef0f4;font-size:14px}
.item span.n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:65%}
.item .s{color:#6b7280;margin:0 12px 0 auto}
.x{border:0;background:none;color:#9ca3af;cursor:pointer;font-size:16px}
.x:hover{color:#dc2626}
button.up{width:100%;margin-top:16px;padding:12px;border:0;border-radius:8px;background:#2563eb;color:#fff;font-size:15px;cursor:pointer}
button.up:disabled{background:#cbd5e1;cursor:not-allowed}
button.go{background:#16a34a}
.sum{display:flex;gap:12px;margin-bottom:16px}
.box{flex:1;text-align:center;padding:14px;border-radius:10px;background:#f4f6fa}
.box b{display:block;font-size:24px}
.box.ok b{color:#16a34a}.box.bad b{color:#dc2626}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:10px 6px;border-bottom:1px solid #eef0f4;vertical-align:top}
th{color:#6b7280;font-weight:600}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.badge.ok{background:#dcfce7;color:#166534}.badge.bad{background:#fee2e2;color:#991b1b}
.badge.new2{background:#fef3c7;color:#92400e}.badge.same{background:#e5e7eb;color:#374151}
.type{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;font-weight:600;background:#e0e7ff;color:#3730a3}
.err{color:#991b1b}.code{color:#9ca3af;font-size:12px}.muted{color:#6b7280;font-size:14px}
.stored{max-height:340px;overflow-y:auto}
</style></head>
<body><div class="wrap">
<h1>증적 파일 업로드</h1>
<div class="sub">PDF, DOCX, XLSX, PPTX, TXT, CSV, PNG, JPG / 파일당 20MB / 한 번에 1000개까지 / 같은 파일명은 새 버전으로 저장됩니다</div>

<div class="card">
  <h2>1. 파일 업로드</h2>
  <div id="drop">파일을 여기에 끌어다 놓거나 <b>클릭해서 선택</b>하세요</div>
  <input type="file" id="f" multiple hidden>
  <div id="list"></div>
  <button class="up" id="btn" disabled>업로드</button>
</div>

<div class="card" id="result" style="display:none"></div>

<div class="card">
  <h2>2. 보관된 증적 <span id="count" class="muted"></span></h2>
  <div class="stored" id="stored"></div>
  <button class="up go" id="go" disabled>분석 시작</button>
</div>

<div class="card" id="analysis" style="display:none"></div>
</div>

<script>
let files = [];
const $ = id => document.getElementById(id);
const drop = $('drop'), input = $('f'), list = $('list'), btn = $('btn'), result = $('result');
const stored = $('stored'), count = $('count'), go = $('go'), analysis = $('analysis');

function fmt(n){
  return (n / 1024).toLocaleString('ko-KR', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + ' KB';
}
function fmtTime(s){
  return s ? String(s).replace('T', ' ').slice(0, 19) : '';
}
function esc(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function type(ext){
  return '<span class="type">' + esc(ext).toUpperCase() + '</span>';
}
function errMsg(d){
  return (d && d.error && d.error.message) || JSON.stringify(d);
}
function actionBadge(r){
  if (r.action === 'replaced') return '<span class="badge new2">버전 갱신 v' + (r.version - 1) + ' → v' + r.version + '</span>';
  if (r.action === 'unchanged') return '<span class="badge same">변경 없음</span>';
  return '<span class="badge ok">신규 등록</span>';
}
function add(fl){
  for (const f of fl){
    if (!files.some(x => x.name === f.name && x.size === f.size)) files.push(f);
  }
  render();
}
function render(){
  list.innerHTML = files.map((f,i) =>
    '<div class="item"><span class="n">' + esc(f.name) + '</span><span class="s">' + fmt(f.size) +
    '</span><button class="x" data-i="' + i + '">✕</button></div>').join('');
  btn.disabled = files.length === 0;
  btn.textContent = files.length ? files.length + '개 파일 업로드' : '업로드';
}

drop.onclick = () => input.click();
input.onchange = () => { add(input.files); input.value = ''; };
drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); add(e.dataTransfer.files); };
list.onclick = e => {
  if (e.target.dataset.i !== undefined){ files.splice(Number(e.target.dataset.i), 1); render(); }
};

function showResult(d){
  const rows = d.results.map(r => r.status === 'ok'
    ? '<tr><td>' + actionBadge(r) + '</td><td>' + esc(r.file_name) + '</td><td>' +
      type(r.file_type) + ' ' + esc(r.evidence_id) + ' · v' + r.version + ' · ' + fmt(r.file_size) +
      (r.is_duplicate ? ' · <span class="code">동일 내용: ' + esc(r.duplicate_of) + '</span>' : '') + '</td></tr>'
    : '<tr><td><span class="badge bad">실패</span></td><td>' + esc(r.file_name) + '</td><td class="err">' +
      esc(r.error_message) + ' <span class="code">' + esc(r.error_code) + '</span></td></tr>').join('');
  result.innerHTML =
    '<div class="sum"><div class="box"><b>' + d.total + '</b>전체</div>' +
    '<div class="box ok"><b>' + d.success + '</b>성공</div>' +
    '<div class="box bad"><b>' + d.failed + '</b>실패</div></div>' +
    '<table><tr><th>상태</th><th>파일명</th><th>결과</th></tr>' + rows + '</table>';
  result.style.display = 'block';
}
function showError(msg){
  result.innerHTML = '<div class="err">' + esc(msg) + '</div>';
  result.style.display = 'block';
}

btn.onclick = async () => {
  btn.disabled = true; btn.textContent = '업로드 중...';
  try {
    const fd = new FormData();
    files.forEach(f => fd.append('files', f));
    const res = await fetch('/evidence/upload', {method: 'POST', body: fd});
    const data = await res.json();
    if (!res.ok) showError(errMsg(data));
    else { showResult(data); files = []; }
  } catch (e) {
    showError('서버에 연결할 수 없습니다.');
  }
  render();
  loadStored();
};

async function loadStored(){
  try {
    const res = await fetch('/evidence?size=100');
    const d = await res.json();
    if (!res.ok) throw new Error(errMsg(d));
    count.textContent = '(' + d.total + '개' + (d.total > d.items.length ? ', 최근 ' + d.items.length + '개만 표시' : '') + ')';
    go.disabled = d.total === 0;
    go.textContent = d.total ? d.total + '개 증적 분석 시작' : '분석 시작';
    stored.innerHTML = d.total === 0
      ? '<div class="muted">아직 보관된 증적이 없습니다.</div>'
      : '<table><tr><th>ID</th><th>종류</th><th>파일명</th><th>버전</th><th>크기</th><th>업로드 시간</th><th>상태</th><th></th></tr>' +
        d.items.map(i =>
          '<tr><td>' + esc(i.evidence_id) + '</td><td>' + type(i.file_type) + '</td><td>' + esc(i.file_name) +
          '</td><td>v' + i.version + '</td><td>' + fmt(i.file_size) + '</td><td>' + esc(fmtTime(i.uploaded_at)) +
          '</td><td class="muted">' + esc(i.status) +
          '</td><td><button class="x" data-id="' + esc(i.evidence_id) + '">✕</button></td></tr>').join('') +
        '</table>';
  } catch (e) {
    stored.innerHTML = '<div class="err">목록을 불러오지 못했습니다. DB 연결을 확인하세요. (' + esc(e.message) + ')</div>';
  }
}

stored.onclick = async e => {
  const id = e.target.dataset.id;
  if (!id) return;
  if (!confirm(id + ' 증적을 삭제할까요? (모든 버전이 삭제됩니다)')) return;
  await fetch('/evidence/' + encodeURIComponent(id), {method: 'DELETE'});
  loadStored();
};

function drawJob(d) {
  const pct = d.total ? Math.round(d.done / d.total * 100) : 100;
  let html = '<h2>' + (d.status === 'running' ? '전처리 중...' : '전처리 완료') + '</h2>' +
    '<div>분석 ID: <b>' + esc(d.analysis_id) + '</b></div>';
  if (d.message) html += '<div class="muted" style="margin-top:8px">' + esc(d.message) + '</div>';
  if (d.total) {
    html += '<div style="margin-top:8px">' + d.done + ' / ' + d.total + '개 (' + pct + '%)' +
      (d.current ? ' — 지금 ' + esc(d.current) : '') + '</div>' +
      '<div>성공 ' + d.ok + '개 · 실패 ' + d.failed + '개 · 청크 ' + d.chunks + '개</div>';
  }
  if (d.skipped) html += '<div class="muted">이미 전처리된 ' + d.skipped + '개는 건너뛰었습니다</div>';
  if (d.errors && d.errors.length) {
    html += '<div style="margin-top:8px"><b>실패한 증적</b>' +
      d.errors.map(e => '<div class="muted">' + esc(e.evidence_id) + ' — ' + esc(e.reason) + '</div>').join('') +
      '</div>';
  }
  analysis.innerHTML = html;
}

go.onclick = async () => {
  go.disabled = true; go.textContent = '전처리 중...';
  analysis.style.display = 'block';
  try {
    const res = await fetch('/analysis/start', {method: 'POST'});
    const d = await res.json();
    if (!res.ok) {
      analysis.innerHTML = '<div class="err">' + esc(errMsg(d)) + '</div>';
    } else {
      drawJob(d);
      // 끝날 때까지 1초마다 진행 상황을 물어봅니다 (OCR 이 있으면 몇 분 걸릴 수 있음)
      let job = d;
      while (job.status === 'running') {
        await new Promise(r => setTimeout(r, 1000));
        try {
          const p = await fetch('/analysis/' + encodeURIComponent(job.analysis_id));
          if (!p.ok) break;
          job = await p.json();
          drawJob(job);
        } catch (e) { break; }
      }
    }
  } catch (e) {
    analysis.innerHTML = '<div class="err">서버에 연결할 수 없습니다.</div>';
  }
  go.disabled = false;
  loadStored();          // 버튼 글자와 목록(상태 칸)을 다시 그립니다
};

loadStored();
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE
