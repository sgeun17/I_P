import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
import uuid

import filetype
from fastapi import FastAPI, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

ALLOWED = {"pdf", "docx", "xlsx", "pptx", "png", "jpg"}
MAX_MB = 20                        # 파일 1개당 최대 용량
MAX_BYTES = MAX_MB * 1024 * 1024
MAX_FILES = 1000                   # 한 번에 올릴 수 있는 파일 수

BASE_DIR = Path("uploads")
TMP_DIR = BASE_DIR / "tmp"         # 검사 중인 임시 파일
STORE_DIR = BASE_DIR / "files"     # 검사 통과 후 보관되는 파일
INDEX_FILE = BASE_DIR / "index.json"   # 보관된 증적 목록 (B 파트의 DB 연결 전 임시 장부)
TMP_DIR.mkdir(parents=True, exist_ok=True)
STORE_DIR.mkdir(parents=True, exist_ok=True)

lock = asyncio.Lock()


def error(status, code, message):
    return JSONResponse(
        status_code=status,
        content={"error": code, "message": message},
    )


def load_index() -> list:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return []


def save_index(items: list) -> None:
    INDEX_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def register(tmp_path: Path, ext: str, name: str, size: int, sha256: str) -> str:
    """검사 통과한 파일을 보관 폴더로 옮기고 목록에 기록한다. (B 파트가 DB 저장으로 대체할 부분)"""
    async with lock:
        items = load_index()
        n = max((int(i["evidence_id"][1:]) for i in items), default=0) + 1
        evidence_id = f"E{n:04d}"
        tmp_path.replace(STORE_DIR / f"{evidence_id}.{ext}")
        items.append({
            "evidence_id": evidence_id,
            "filename": name,
            "ext": ext,
            "size": size,
            "sha256": sha256,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        })
        save_index(items)
    return evidence_id


async def process_file(file: UploadFile) -> dict:
    name = file.filename

    def fail(code, message):
        return {"filename": name, "status": "error", "error": code, "message": message}

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
    kind = filetype.guess(head)
    if kind is None or kind.extension != ext:
        return fail("CONTENT_MISMATCH", "파일 내용이 확장자와 일치하지 않습니다.")

    # 4. 크기 검사 + 임시 저장 + 해시 계산
    tmp_path = TMP_DIR / f"{uuid.uuid4().hex}.{ext}"
    size = len(head)
    sha = hashlib.sha256()
    sha.update(head)
    too_large = False

    with open(tmp_path, "wb") as f:
        f.write(head)
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                too_large = True
                break
            f.write(chunk)
            sha.update(chunk)

    if too_large:
        tmp_path.unlink(missing_ok=True)
        return fail("FILE_TOO_LARGE", f"파일 크기가 {MAX_MB}MB를 초과합니다.")

    # 5. 보관 + 목록 기록
    evidence_id = await register(tmp_path, ext, name, size, sha.hexdigest())
    return {
        "filename": name,
        "status": "ok",
        "evidence_id": evidence_id,
        "ext": ext,
        "size": size,
    }


@app.post("/evidence/upload")
async def upload(files: list[UploadFile]):
    if not files:
        return error(400, "NO_FILES", "업로드된 파일이 없습니다.")
    if len(files) > MAX_FILES:
        return error(413, "TOO_MANY_FILES", f"한 번에 {MAX_FILES}개까지 업로드할 수 있습니다.")

    results = [await process_file(f) for f in files]
    ok = sum(1 for r in results if r["status"] == "ok")
    return {
        "total": len(results),
        "success": ok,
        "failed": len(results) - ok,
        "results": results,
    }


@app.get("/evidence")
async def list_evidence():
    items = load_index()
    return {"total": len(items), "items": items}


@app.delete("/evidence/{evidence_id}")
async def delete_evidence(evidence_id: str):
    async with lock:
        items = load_index()
        target = next((i for i in items if i["evidence_id"] == evidence_id), None)
        if target is None:
            return error(404, "NOT_FOUND", "해당 증적을 찾을 수 없습니다.")
        (STORE_DIR / f"{evidence_id}.{target['ext']}").unlink(missing_ok=True)
        save_index([i for i in items if i["evidence_id"] != evidence_id])
    return {"deleted": evidence_id}


@app.post("/analysis/start")
async def start_analysis():
    """보관된 증적 전체를 분석 대상으로 확정한다. (Phase 2 분석 모듈이 연결될 자리)"""
    items = load_index()
    if not items:
        return error(400, "NO_EVIDENCE", "분석할 증적이 없습니다. 먼저 파일을 업로드하세요.")

    analysis_id = "A" + datetime.now().strftime("%Y%m%d%H%M%S")
    # TODO: Phase 2 분석 함수 호출 -> run_analysis(analysis_id, items)
    return {
        "analysis_id": analysis_id,
        "status": "queued",
        "total": len(items),
        "evidence_ids": [i["evidence_id"] for i in items],
    }


PAGE = r"""
<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>증적 업로드</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f6fa;font-family:'Malgun Gothic',sans-serif;color:#1f2937}
.wrap{max-width:900px;margin:40px auto;padding:0 16px}
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
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
.badge.ok{background:#dcfce7;color:#166534}.badge.bad{background:#fee2e2;color:#991b1b}
.type{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;font-weight:600;background:#e0e7ff;color:#3730a3}
.err{color:#991b1b}.code{color:#9ca3af;font-size:12px}.muted{color:#6b7280;font-size:14px}
.stored{max-height:320px;overflow-y:auto}
</style></head>
<body><div class="wrap">
<h1>증적 파일 업로드</h1>
<div class="sub">PDF, DOCX, XLSX, PPTX, PNG, JPG / 파일당 20MB / 한 번에 1000개까지 / 여러 번 나눠 올려도 됩니다</div>

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
function esc(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function type(ext){
  return '<span class="type">' + esc(ext).toUpperCase() + '</span>';
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
    ? '<tr><td><span class="badge ok">성공</span></td><td>' + esc(r.filename) + '</td><td>' +
      type(r.ext) + ' ' + esc(r.evidence_id) + ' · ' + fmt(r.size) + '</td></tr>'
    : '<tr><td><span class="badge bad">실패</span></td><td>' + esc(r.filename) + '</td><td class="err">' +
      esc(r.message) + ' <span class="code">' + esc(r.error) + '</span></td></tr>').join('');
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
    if (!res.ok) showError(data.message || JSON.stringify(data));
    else { showResult(data); files = []; }
  } catch (e) {
    showError('서버에 연결할 수 없습니다.');
  }
  render();
  loadStored();
};

async function loadStored(){
  const res = await fetch('/evidence');
  const d = await res.json();
  count.textContent = '(' + d.total + '개)';
  go.disabled = d.total === 0;
  go.textContent = d.total ? d.total + '개 증적 분석 시작' : '분석 시작';
  stored.innerHTML = d.total === 0
    ? '<div class="muted">아직 보관된 증적이 없습니다.</div>'
    : '<table><tr><th>ID</th><th>종류</th><th>파일명</th><th>크기</th><th>업로드 시간</th><th></th></tr>' +
      d.items.map(i =>
        '<tr><td>' + esc(i.evidence_id) + '</td><td>' + type(i.ext) + '</td><td>' + esc(i.filename) +
        '</td><td>' + fmt(i.size) + '</td><td>' + esc(i.uploaded_at) +
        '</td><td><button class="x" data-id="' + esc(i.evidence_id) + '">✕</button></td></tr>').join('') +
      '</table>';
}

stored.onclick = async e => {
  const id = e.target.dataset.id;
  if (!id) return;
  if (!confirm(id + ' 증적을 삭제할까요?')) return;
  await fetch('/evidence/' + encodeURIComponent(id), {method: 'DELETE'});
  loadStored();
};

go.onclick = async () => {
  go.disabled = true; go.textContent = '분석 요청 중...';
  try {
    const res = await fetch('/analysis/start', {method: 'POST'});
    const d = await res.json();
    analysis.innerHTML = res.ok
      ? '<h2>분석 요청 완료</h2><div>분석 ID: <b>' + esc(d.analysis_id) + '</b></div>' +
        '<div>상태: ' + esc(d.status) + '</div><div>분석 대상: ' + d.total + '개 증적</div>' +
        '<div class="muted" style="margin-top:8px">' + esc(d.evidence_ids.join(', ')) + '</div>'
      : '<div class="err">' + esc(d.message || JSON.stringify(d)) + '</div>';
  } catch (e) {
    analysis.innerHTML = '<div class="err">서버에 연결할 수 없습니다.</div>';
  }
  analysis.style.display = 'block';
  loadStored();
};

loadStored();
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE