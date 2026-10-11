"""
setup_db.py : MySQL 준비를 한 번에 끝낸다.

    python tools\\setup_db.py

하는 일
    ① mysql 명령을 찾는다 (PATH 에 없으면 설치 폴더에서 찾는다)
    ② root 비밀번호를 한 번 물어본다
    ③ phase1_입력/database/.env 를 만든다 (이미 있으면 그대로 둔다)
    ④ SQL 을 순서대로 돌린다. **하나라도 실패하면 거기서 멈춘다**
    ⑤ 시험용 관리자 계정을 넣는다 (operator@isms-p.test / demo1234)
    ⑥ 표 13개·칸 8개를 확인하고 실제로 로그인까지 해 본다

MySQL 설치 자체는 사람이 한다 (dev.mysql.com/downloads/installer).
이 파일은 그 뒤를 맡는다.

    --no-seed      시험 계정을 안 넣는다
    --reset        증적 표까지 새로 만든다 (★ 올린 증적이 다 지워진다)
"""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / "phase1_입력").is_dir() else HERE
ENV = ROOT / "phase1_입력" / "database" / ".env"

# 돌리는 순서. (파일, DB 이름을 붙이나)
# schema.sql 은 DB 자체를 만들고 USE 를 하므로 DB 이름을 안 붙인다.
FILES = [
    ("phase1_입력/database/schema.sql", False, "증적 · 보관함"),
    ("phase1_입력/database/chunk_schema.sql", True, "청크"),
    ("phase2_통합/phase2_schema.sql", True, "판정 · 제외 · 검토 이력"),
    ("phase2_통합/phase1_mapping.sql", True, "매핑 · 인용"),
    ("auth/user_schema.sql", True, "계정 · 세션 · 기록"),
    ("auth/company_schema.sql", True, "회사 · 초대 + 칸 추가"),
]
SEED = ("auth/seed_demo_admin.sql", True, "시험 관리자 계정")

NEED_TABLES = ["app_user", "chunk", "company", "evidence", "evidence_history",
               "invite", "phase1_mapping", "phase1_mapping_citation",
               "phase2_excluded", "phase2_result", "review_history",
               "user_login_log", "user_session"]
NEED_COLS = [("app_user", "email"), ("app_user", "company_id"),
             ("app_user", "verified"), ("app_user", "is_super"),
             ("evidence", "company_id"), ("evidence", "uploaded_by"),
             ("evidence_history", "company_id"),
             ("evidence_history", "uploaded_by")]

DB = "evidence_db"


def die(msg, hint=""):
    print(f"\n[X] {msg}")
    if hint:
        print(f"    → {hint}")
    sys.exit(1)


def find_mysql() -> str:
    found = shutil.which("mysql")
    if found:
        return found
    pats = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MySQL",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "MySQL"]
    for base in pats:
        if base.is_dir():
            hits = sorted(base.glob("MySQL Server */bin/mysql.exe"))
            if hits:
                return str(hits[-1])
    die("mysql 명령을 못 찾았습니다.",
        "MySQL 을 먼저 설치하세요: dev.mysql.com/downloads/installer")


def run_sql(mysql, pw, path: Path, with_db: bool):
    """mysql 로 파일 하나를 돌린다. (성공?, 출력) 을 돌려준다."""
    cmd = [mysql, "-u", "root", "--default-character-set=utf8mb4"]
    if with_db:
        cmd.append(DB)
    env = {**os.environ, "MYSQL_PWD": pw}
    with open(path, "rb") as f:
        p = subprocess.run(cmd, stdin=f, env=env,
                           capture_output=True, text=True, errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    out = "\n".join(l for l in out.splitlines()
                    if "Using a password" not in l)
    return p.returncode == 0, out.strip()


def query(mysql, pw, sql, with_db=True):
    cmd = [mysql, "-u", "root", "--default-character-set=utf8mb4", "-N", "-B"]
    if with_db:
        cmd.append(DB)
    cmd += ["-e", sql]
    env = {**os.environ, "MYSQL_PWD": pw}
    p = subprocess.run(cmd, env=env, capture_output=True, text=True,
                       errors="replace")
    if p.returncode != 0:
        return None
    return [l.split("\t") for l in p.stdout.splitlines() if l.strip()]


ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--no-seed", action="store_true", help="시험 계정을 안 넣는다")
ap.add_argument("--reset", action="store_true",
                help="증적 표까지 새로 만든다 (올린 증적이 지워진다)")
args = ap.parse_args()

print("=" * 60)
print("  ISMS-P 증적 도구 — DB 준비")
print("=" * 60)

mysql = find_mysql()
print(f"\nmysql : {mysql}")

# ── 비밀번호 ──────────────────────────────────────────────────────────────
pw = None
if ENV.is_file():
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("DB_PASSWORD="):
            pw = line.split("=", 1)[1].strip()
    if pw:
        print(f".env  : {ENV}  (비밀번호를 여기서 읽었습니다)")

if pw is None:
    print("\nMySQL 을 설치할 때 정한 root 비밀번호를 넣으세요. 화면에 안 보입니다.")
    pw = getpass.getpass("  root 비밀번호: ")

if query(mysql, pw, "SELECT 1", with_db=False) is None:
    die("MySQL 에 못 붙었습니다.",
        "비밀번호가 다르거나 MySQL 이 꺼져 있습니다. "
        "services.msc 에서 MySQL 서비스를 확인하세요.")
print("[O] MySQL 접속 확인")

# ── .env ─────────────────────────────────────────────────────────────────
if ENV.is_file():
    print(f"[O] .env 가 이미 있습니다 — 그대로 씁니다")
else:
    ENV.parent.mkdir(parents=True, exist_ok=True)
    ENV.write_text(
        f"DB_HOST=localhost\nDB_PORT=3306\nDB_USER=root\n"
        f"DB_PASSWORD={pw}\nDB_NAME={DB}\n", encoding="utf-8")
    print(f"[O] .env 를 만들었습니다 — {ENV}")

# ── SQL ──────────────────────────────────────────────────────────────────
have = {r[0] for r in (query(mysql, pw, f"SHOW TABLES FROM {DB}",
                             with_db=False) or [])}
print(f"\n지금 표 {len(have)}개 있습니다.")

print("\n" + "─" * 60)
todo = list(FILES)
if "evidence" in have and not args.reset:
    print("evidence 표가 이미 있어 schema.sql 은 건너뜁니다 "
          "(증적을 지우지 않으려고). 다시 만들려면 --reset")
    todo = todo[1:]

for rel, with_db, what in todo:
    path = ROOT / rel
    if not path.is_file():
        die(f"파일이 없습니다: {path}")
    print(f"  {rel:<42} {what} … ", end="", flush=True)
    ok, out = run_sql(mysql, pw, path, with_db)
    if not ok:
        print("실패")
        die(f"{rel} 에서 멈췄습니다.\n\n{out}",
            "위 메시지를 그대로 보내주세요. 여기서 멈췄으니 "
            "뒤 파일은 돌지 않았습니다.")
    print("완료")

if not args.no_seed:
    rel, with_db, what = SEED
    path = ROOT / rel
    if path.is_file():
        print(f"  {rel:<42} {what} … ", end="", flush=True)
        ok, out = run_sql(mysql, pw, path, with_db)
        print("완료" if ok else "실패")
        if not ok:
            print(f"    {out[:300]}")
    else:
        print(f"  {rel} 가 없어 시험 계정은 건너뜁니다.")

# ── 확인 ─────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("확인")

have = {r[0] for r in (query(mysql, pw, f"SHOW TABLES FROM {DB}",
                             with_db=False) or [])}
miss = [t for t in NEED_TABLES if t not in have]
print(f"  표   {len(NEED_TABLES) - len(miss)}/{len(NEED_TABLES)}"
      + (f"   없음: {', '.join(miss)}" if miss else "   전부 있습니다"))

cols = {(r[0], r[1]) for r in (query(mysql, pw,
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DB}'") or [])}
cmiss = [f"{t}.{c}" for t, c in NEED_COLS if (t, c) not in cols]
print(f"  칸   {len(NEED_COLS) - len(cmiss)}/{len(NEED_COLS)}"
      + (f"   없음: {', '.join(cmiss)}" if cmiss else "   전부 있습니다"))

ok_login = False
if not args.no_seed and not miss and not cmiss:
    for p in (ROOT / "auth", ROOT / "phase1_입력" / "database"):
        if p.is_dir():
            sys.path.insert(0, str(p))
    try:
        import company_store as cs
        import user_store as us
        token, info = cs.login("operator@isms-p.test", "demo1234")
        us.logout(token)
        ok_login = True
        print(f"  로그인 성공 — {info['name']} · {info['company']} "
              f"· {info['role']}")
    except Exception as e:                                    # noqa: BLE001
        print(f"  로그인 확인 실패: {getattr(e, 'code', type(e).__name__)} "
              f"{getattr(e, 'message', e)}")

print("\n" + "=" * 60)
if miss or cmiss:
    print("아직 덜 됐습니다. 위의 '없음' 을 보고 다시 돌려 보세요.")
    sys.exit(1)
print("DB 준비 끝.")
if ok_login:
    print("\n  아이디   operator@isms-p.test")
    print("  비밀번호 demo1234")
print("\n서버를 띄우세요 (phase1_통합 폴더에서):")
print("  python -m uvicorn api:app --port 8000")
print("그다음 브라우저로 http://127.0.0.1:8000")
print("=" * 60)
