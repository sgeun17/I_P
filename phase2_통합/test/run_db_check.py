"""
run_db_check.py : 실제 MySQL 에서 DB 장애·중복 실행 점검

    python test/run_db_check.py

메모리 SQLite 가 아니라 **진짜 MySQL** 에 붙어서 돈다. 지금까지의 시험은
전부 SQLite 라 MySQL 전용 문법(GENERATED ALWAYS AS ... VIRTUAL, FOR UPDATE,
FOREIGN KEY)이 실제로 도는지 확인한 적이 없다.

가상 증적(E9001~E9003)만 만들고 끝나면 지운다. 기존 데이터는 안 건드린다.
중간에 끊겨도 지워지도록 마지막에 정리한다.

보는 것
    ① 표가 다 있고 칸이 맞는가
    ② 같은 증적·통제항목을 두 번 저장하면 한 줄만 남는가 (current_key UNIQUE)
    ③ 두 연결이 동시에 승인하면 한쪽이 기다렸다가 처리되는가 (FOR UPDATE)
    ④ 중간에 터지면 이력과 상태가 같이 롤백되는가
    ⑤ 없는 증적으로 저장하면 거부되는가 (FOREIGN KEY)
    ⑥ 같은 제외를 두 번 적으면 한 줄만 남는가
    ⑦ 증적을 지우면 판정·이력·제외가 같이 지워지는가 (ON DELETE CASCADE)
    ⑧ 한글이 안 깨지는가 (utf8mb4)
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import phase2_result_store as rs            # noqa: E402
import phase2_review_store as rv            # noqa: E402

ok = fail = 0
notes = []


def ck(name, cond, got=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [O] {name}" + (f"   {got}" if got else ""))
    else:
        fail += 1
        print(f"  [X] {name}   {got}")


EV = ("E9001", "E9002", "E9003")
ROW = {"phase1_review_required": False, "phase1_review_reasons": []}


def out(eid, cid, verdict="충족", review=False, item="MET", codes=None, ver=1):
    return {"schema_version": "phase2-output-0.3", "rule_version": "v0.4",
            "evidence_id": eid, "version": ver, "control_id": cid,
            "control_name": "가상 통제항목 — 홍길동 임꺽정",
            "overall_result": verdict,
            "items": [{"item_id": f"{cid}-Q01", "result": item,
                       "reason_codes": codes or [], "citations": []}],
            "human_review": {"required": review,
                             "reasons": [{"code": "R201"}] if review else []}}


def cleanup():
    conn = rs._connect()
    try:
        with conn.cursor() as cur:
            for e in EV:
                cur.execute("DELETE FROM evidence WHERE evidence_id = %s", (e,))
        conn.commit()
    except Exception as exc:
        print(f"  (정리 실패: {exc})")
    finally:
        conn.close()


def main():
    print("=" * 68)
    print("DB 장애·중복 실행 점검 (실제 MySQL)")
    print("=" * 68)

    try:
        conn = rs._connect()
    except Exception as exc:
        print(f"\nMySQL 에 못 붙었습니다: {type(exc).__name__} {exc}")
        print("phase1_입력/database/.env 의 DB_HOST·DB_USER·DB_PASSWORD 를 확인하세요.")
        return 2
    conn.close()
    cleanup()

    print("\n── ① 표와 칸 ──")
    want = {
        "phase2_result": ("result_id", "evidence_id", "evidence_version", "control_id",
                          "overall_result", "review_state", "phase1_review_required",
                          "phase1_review_reasons", "error_codes", "freshness_status",
                          "format_status", "citation_count", "citation_with_source",
                          "checklist_approved", "payload", "superseded_at", "current_key"),
        "review_history": ("history_id", "result_id", "seq", "action", "actor",
                           "state_before", "state_after", "status_before", "status_after"),
        "phase2_excluded": ("excluded_id", "evidence_id", "evidence_version",
                            "control_id", "reason_code"),
    }
    conn = rs._connect()
    try:
        with conn.cursor() as cur:
            for table, cols in want.items():
                cur.execute(f"SHOW COLUMNS FROM {table}")
                have = {r["Field"] if isinstance(r, dict) else r[0] for r in cur.fetchall()}
                missing = [c for c in cols if c not in have]
                ck(f"{table} 칸", not missing, f"빠진 것 {missing}" if missing else "")
            cur.execute("SHOW COLUMNS FROM phase2_result LIKE 'current_key'")
            r = cur.fetchall()
            extra = (r[0]["Extra"] if isinstance(r[0], dict) else r[0][5]) if r else ""
            # VIRTUAL 이어야 맞다. STORED 면 FK 가 안 걸려서 표가 아예 안 만들어진다.
            ck("current_key 가 생성 칸이다",
               "GENERATED" in str(extra).upper(), str(extra))
            ck("VIRTUAL 이다 (STORED 면 ON DELETE CASCADE 와 못 쓴다)",
               "VIRTUAL" in str(extra).upper(), str(extra))
            cur.execute("SELECT TABLE_COLLATION FROM information_schema.TABLES "
                        "WHERE TABLE_NAME = 'phase2_result' AND TABLE_SCHEMA = DATABASE()")
            col = cur.fetchall()
            col = (col[0]["TABLE_COLLATION"] if isinstance(col[0], dict) else col[0][0]) if col else ""
            ck("utf8mb4 로 되어 있다", "utf8mb4" in str(col), str(col))
    finally:
        conn.close()

    # 가상 증적. file_size·file_hash·uploaded_at 는 NOT NULL 이라 다 채운다.
    conn = rs._connect()
    with conn.cursor() as cur:
        for i, e in enumerate(EV):
            cur.execute(
                "INSERT INTO evidence (evidence_id, version, file_name, file_type, "
                " file_size, file_hash, uploaded_at, status) "
                "VALUES (%s, 1, %s, 'pdf', %s, %s, %s, 'COMPLETED')",
                (e, f"{e}_가상증적.pdf", 1024, f"{i:064d}", rs._now()))
    conn.commit()
    conn.close()

    print("\n── ② 같은 증적·통제항목 두 번 저장 ──")
    r1 = rs.save(out("E9001", "2.5.1"), row=ROW)
    r2 = rs.save(out("E9001", "2.5.1", "미충족", item="NOT_MET",
                     codes=["P2_NM_RULE_VIOLATED"]), row=ROW)
    ck("새 줄이 생긴다", r1 != r2, f"{r1} → {r2}")
    cur_rows = rs.load("E9001", "2.5.1", include_superseded=True)
    ck("현재 줄은 하나뿐이다",
       sum(1 for r in cur_rows if r["superseded_at"] is None) == 1,
       f"전체 {len(cur_rows)}줄")
    ck("옛 줄은 지워지지 않고 남는다", len(cur_rows) == 2)
    ck("현재 값은 나중 것이다", rs.load("E9001", "2.5.1")["overall_result"] == "미충족")

    print("\n── ③ 두 연결이 동시에 승인 ──")
    rs.save(out("E9002", "2.5.2", review=True), row=ROW)
    rid = rs.load("E9002", "2.5.2")["result_id"]
    res, errs = [], []

    def approve(tag):
        try:
            time.sleep(0.01)
            res.append((tag, rv.approve(rid, f"동시시험{tag}")))
        except Exception as exc:
            errs.append((tag, type(exc).__name__, str(exc)[:60]))

    ts = [threading.Thread(target=approve, args=(i,)) for i in (1, 2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    ck("둘 중 하나만 승인된다", len(res) == 1 and len(errs) == 1,
       f"성공 {len(res)} / 거부 {len(errs)} {errs}")
    ck("나머지는 NOT_IN_REVIEW 로 거부된다",
       any(e[1] == "ReviewError" for e in errs), str(errs))
    hist = rv.history(rid)
    ck("이력이 한 줄만 생긴다", len(hist) == 1, f"{len(hist)}줄")
    ck("seq 가 겹치지 않는다", len({h["seq"] for h in hist}) == len(hist))

    print("\n── ④ 중간에 터지면 롤백 ──")
    rs.save(out("E9003", "2.5.3", review=True), row=ROW)
    rid3 = rs.load("E9003", "2.5.3")["result_id"]
    before_state = rs.load("E9003", "2.5.3")["review_state"]

    # ④-1 값 검사 단계에서 거부 — DB 를 건드리기 전에 막힌다
    try:
        rv.modify(rid3, "혜진", "없는문항-Q99", "NOT_MET", "일부러 틀린 대상")
    except Exception:
        pass
    ck("거부된 수정은 이력에 안 남는다", len(rv.history(rid3)) == 0,
       f"{len(rv.history(rid3))}줄")
    ck("판정 상태가 그대로다", rs.load("E9003", "2.5.3")["review_state"] == before_state)

    # ④-2 진짜 중간 장애 — UPDATE 는 됐는데 INSERT 직전에 터뜨린다.
    #     앞의 UPDATE 까지 같이 롤백돼야 한다.
    real_q = rs._q
    calls = {"n": 0}

    def boom(sql):
        s = real_q(sql)
        if "INSERT INTO review_history" in s:
            calls["n"] += 1
            raise RuntimeError("일부러 터뜨림 — UPDATE 는 끝났고 INSERT 직전")
        return s

    rs._q = boom
    rv._q = boom
    try:
        rv.approve(rid3, "장애시험")
    except Exception as exc:
        note = type(exc).__name__
    finally:
        rs._q = real_q
        rv._q = real_q
    ck("INSERT 직전에 터뜨렸다", calls["n"] == 1, f"{calls['n']}회")
    after = rs.load("E9003", "2.5.3")
    ck("앞서 끝난 UPDATE 도 같이 롤백된다",
       after["review_state"] == before_state, after["review_state"])
    ck("이력도 안 남는다", len(rv.history(rid3)) == 0, f"{len(rv.history(rid3))}줄")
    st_now = None
    conn = rs._connect()
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM evidence WHERE evidence_id = %s", ("E9003",))
        st_now = rs._rows(cur)[0]["status"]
    conn.close()
    ck("증적 상태도 안 바뀐다", st_now == "REVIEW_REQUIRED", str(st_now))

    print("\n── ⑤ 없는 증적으로 저장 ──")
    try:
        rs.save(out("E9999_없는증적", "2.5.1"), row=ROW)
        ck("없는 증적은 거부된다", False, "들어가 버렸다 — FOREIGN KEY 가 없다")
    except Exception as exc:
        ck("없는 증적은 거부된다", True, type(exc).__name__)

    print("\n── ⑥ 같은 제외를 두 번 ──")
    rs.exclude("E9001", "NO_CHECKLIST", "질문지 없음", control_id="1.1.1")
    rs.exclude("E9001", "NO_CHECKLIST", "질문지 없음 (다시)", control_id="1.1.1")
    mine = [x for x in rs.excluded()
            if x["evidence_id"] == "E9001" and x["control_id"] == "1.1.1"]
    ck("한 줄만 남는다", len(mine) == 1, f"{len(mine)}줄")
    ck("나중 사유로 덮인다", mine and "다시" in (mine[0]["reason"] or ""))

    print("\n── ⑦ 증적을 지우면 같이 지워지는가 ──")
    # 지우기 전에 이력을 하나 만들어 둔다. 이력이 없으면 CASCADE 가 안 걸려도
    # 0줄이라 통과해 버린다.
    rs.save(out("E9001", "2.5.8", review=True), row=ROW)
    rid8 = rs.load("E9001", "2.5.8")["result_id"]
    rv.approve(rid8, "삭제시험")
    conn = rs._connect()
    with conn.cursor() as cur:
        cur.execute("SELECT result_id FROM phase2_result WHERE evidence_id = %s",
                    ("E9001",))
        rids = [r["result_id"] for r in rs._rows(cur)]
        marks = ", ".join(["%s"] * len(rids))
        cur.execute(f"SELECT COUNT(*) AS n FROM review_history "
                    f"WHERE result_id IN ({marks})", tuple(rids))
        hist_before = int(rs._rows(cur)[0]["n"])
    conn.commit()
    ck("지우기 전에 이력이 있다", hist_before > 0, f"{hist_before}줄")

    with conn.cursor() as cur:
        cur.execute("DELETE FROM evidence WHERE evidence_id = %s", ("E9001",))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM phase2_result WHERE evidence_id = %s",
                    ("E9001",))
        left = int(rs._rows(cur)[0]["n"])
        cur.execute("SELECT COUNT(*) AS n FROM phase2_excluded WHERE evidence_id = %s",
                    ("E9001",))
        left_x = int(rs._rows(cur)[0]["n"])
        cur.execute(f"SELECT COUNT(*) AS n FROM review_history "
                    f"WHERE result_id IN ({marks})", tuple(rids))
        left_h = int(rs._rows(cur)[0]["n"])
    conn.close()
    ck("판정이 같이 지워진다", left == 0, f"{left}줄 남음")
    ck("제외도 같이 지워진다", left_x == 0, f"{left_x}줄 남음")
    ck("검토 이력도 같이 지워진다", left_h == 0, f"{left_h}줄 남음")

    print("\n── ⑧ 한글 ──")
    one = rs.load("E9002", "2.5.2")
    ck("통제항목 이름이 안 깨진다", one["control_name"] == "가상 통제항목 — 홍길동 임꺽정",
       one["control_name"])
    ck("판정값이 안 깨진다", one["overall_result"] in rs.OVERALL_VALUES,
       one["overall_result"])

    cleanup()
    print("\n" + "=" * 68)
    print(f"{ok} 통과 / {fail} 실패")
    print("=" * 68)
    if notes:
        print("\n".join(notes))
    return 1 if fail else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n중간에 터졌습니다: {type(exc).__name__} {exc}")
        cleanup()          # 가상 증적을 남기지 않는다
        raise
