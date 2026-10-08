"""
phase1_mapping_store.py : Phase 1 매핑 결과 저장·조회

    save(phase1_result)              매핑 결과 한 증적분 저장
    backfill()                       results/*.json 을 전부 읽어 채운다
    by_control("2.5.1")              그 통제항목에 후보로 걸린 증적
    by_evidence("E0001")             그 증적이 걸린 통제항목
    candidates("2.5.1")              화면용 — PRIMARY 먼저, 점수 높은 순

원본·청크·판정은 DB 에 있는데 매핑만 phase1_통합/results/*.json 파일이었다.
그래서 "이 통제항목에 어떤 증적이 후보로 걸렸나" 를 조회로 못 했다.

orchestrator 는 안 고친다. 두 길이 있다.

    phase2_runner.run_evidence()  Phase 1 결과를 읽자마자 자동으로 올린다 (평소)
    backfill()                    이미 쌓인 옛 결과를 한 번에 올릴 때

PRIMARY 가 바뀌면 그 통제항목의 Phase 2 판정을 과거로 돌리고, 증적 단위 보류를
지우고, evidence.status 를 다시 연다. 전부 한 트랜잭션이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from .phase2_result_store import (_connect, _q, _rows, _now, _lock,
                                      _supersede_in_tx, _unexclude_in_tx,
                                      _reopen_in_tx, _evidence_version,
                                      ResultStoreError)
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from phase2_result_store import (_connect, _q, _rows, _now, _lock,
                                     _supersede_in_tx, _unexclude_in_tx,
                                     _reopen_in_tx, _evidence_version,
                                     ResultStoreError)

_HERE = Path(__file__).resolve().parent

RELATIONS = ("PRIMARY", "RELATED")
_RELATION_ORDER = {"PRIMARY": 0, "RELATED": 1}


def result_dir() -> Path:
    """phase1_통합/results 를 거슬러 올라가며 찾는다."""
    for base in [_HERE, *_HERE.parents]:
        p = base / "phase1_통합" / "results"
        if p.is_dir():
            return p
    raise ResultStoreError("RESULT_DIR_NOT_FOUND",
                           "phase1_통합/results 를 찾지 못했습니다.")


def _reasons(review):
    return [(r.get("code") if isinstance(r, dict) else str(r))
            for r in ((review or {}).get("reasons") or [])]


def _primary_in_tx(cur, evidence_id, version):
    cur.execute(_q("SELECT control_id FROM phase1_mapping "
                   "WHERE evidence_id = %s AND evidence_version = %s "
                   "AND relation = 'PRIMARY'"), (evidence_id, version))
    return {r["control_id"] for r in _rows(cur)}


def primary_ids(evidence_id: str, version: int):
    """그 버전에서 PRIMARY 로 잡힌 통제항목."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            return _primary_in_tx(cur, evidence_id, version)
    finally:
        conn.close()


def save(phase1_result: dict, invalidate_phase2: bool = True) -> dict:
    """
    매핑 결과 한 증적분을 저장한다. 같은 증적·버전·통제항목은 지우고 다시 넣는다.

    PRIMARY 가 바뀌면 그 버전의 Phase 2 현재 판정을 과거로 돌린다
    (invalidate_phase2=False 면 안 한다). 안 그러면 Phase 1 검토에서 매핑을
    고쳐도 옛 매핑으로 낸 Phase 2 판정이 현재 값으로 남는다.

        E0001 → PRIMARY 2.5.1   Phase 2 판정 생성
        검토자가 2.5.1 → 2.5.2 로 고침
        2.5.1 판정이 그대로 남아 있으면 틀린 결과가 화면에 뜬다

    돌려주는 값
        {saved, primary_before, primary_after, changed, superseded[]}
    """
    eid = phase1_result["evidence_id"]
    ver = int(phase1_result["version"])
    review = phase1_result.get("human_review") or {}
    required = 1 if review.get("required") else 0
    reasons = ",".join(_reasons(review))[:200] or None
    status = phase1_result.get("match_status")
    trace = phase1_result.get("trace_id")
    now = _now()

    conn = _connect()
    gone, before, after, changed = [], set(), set(), []
    reopened, closed = None, False
    try:
        with conn.cursor() as cur:
            # 매핑 저장과 Phase 2 무효화를 한 트랜잭션에서 한다.
            # 따로 하면 중간에 터졌을 때 새 매핑 + 옛 Phase 2 판정이 동시에
            # 현재로 남아서, 막으려던 그 상태가 그대로 생긴다.
            # 증적 줄을 먼저 잠근다. Phase 2 저장과 겹쳐도 한쪽이 기다린다.
            cur_ver = _evidence_version(cur, eid, lock=True)
            # 옛 버전 매핑(backfill 등)은 매핑만 적재한다. 현재 버전의 Phase 2 를
            # 건드리면 v1 을 백필했다는 이유로 v2 가 VALIDATING 으로 열린다.
            touch = invalidate_phase2 and ver == cur_ver
            before = _primary_in_tx(cur, eid, ver) if touch else set()
            # 인용부터 지운다. MySQL 은 ON DELETE CASCADE 가 처리하지만
            # SQLite 로 시험할 때는 안 걸려서 고아 줄이 남는다.
            cur.execute(_q("DELETE FROM phase1_mapping_citation WHERE mapping_id IN "
                           "(SELECT mapping_id FROM (SELECT mapping_id "
                           " FROM phase1_mapping WHERE evidence_id = %s "
                           " AND evidence_version = %s) t)"), (eid, ver))
            cur.execute(_q("DELETE FROM phase1_mapping WHERE evidence_id = %s "
                           "AND evidence_version = %s"), (eid, ver))
            n = 0
            for m in phase1_result.get("mapped_controls", []):
                relation = m.get("relation")
                if relation not in RELATIONS:
                    raise ResultStoreError(
                        "RELATION_INVALID",
                        f"모르는 relation: {relation} (허용: {', '.join(RELATIONS)})")
                cits = m.get("citations") or []
                cur.execute(_q(
                    "INSERT INTO phase1_mapping "
                    "(evidence_id, evidence_version, control_id, control_name, relation, "
                    " similarity_score, llm_confidence, reason, citation_count, "
                    " match_status, review_required, review_reasons, trace_id, noted_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                    (eid, ver, m["control_id"], m["control_name"], relation,
                     m.get("similarity_score"), m.get("llm_confidence"),
                     m.get("reason"), len(cits), status, required, reasons, trace, now))
                mid = cur.lastrowid
                for i, c in enumerate(cits, 1):
                    cur.execute(_q(
                        "INSERT INTO phase1_mapping_citation "
                        "(mapping_id, seq, chunk_id, page, quote, source_file) "
                        "VALUES (%s,%s,%s,%s,%s,%s)"),
                        (mid, i, c.get("chunk_id"), c.get("page"),
                         c.get("quote", ""), c.get("source_file")))
                n += 1

            after = _primary_in_tx(cur, eid, ver) if touch else set()
            changed = sorted(before ^ after)      # 빠졌거나 새로 생긴 PRIMARY
            removed = sorted(before - after)      # PRIMARY 에서 빠진 것
            # before 가 비었는지로 가르지 않는다. PRIMARY 가 없다가 생기는 경우
            # (SKIP_NO_MATCH 를 사람이 고친 경우)도 바뀐 것이다.
            if touch and changed:
                # 빠진 통제항목의 판정만 과거로 돌린다. 안 바뀐 통제항목의
                # 판정은 그대로 둔다 — 사람이 고쳐둔 값까지 날아가면 안 된다.
                if removed:
                    gone = _supersede_in_tx(cur, eid, ver, removed)
                    # 그 통제항목의 제외 기록도 지운다. 안 지우면 더 이상
                    # 대상도 아닌 통제항목이 "실패" 로 화면에 남는다.
                    _unexclude_in_tx(cur, eid, ver, removed)
                if after:
                    # 돌릴 게 남았다 — 증적 단위 보류를 지우고 상태를 다시 연다.
                    reopened = _reopen_in_tx(cur, eid, ver, bool(required))
                else:
                    # PRIMARY 가 전부 사라졌다. 다시 열지 않고 제외로 닫는다.
                    # 안 그러면 옛 판정이 현재로 남아 화면에 계속 뜬다.
                    cur.execute(_q("DELETE FROM phase2_excluded "
                                   "WHERE evidence_id = %s AND evidence_version = %s "
                                   "AND control_id = ''"), (eid, ver))
                    cur.execute(_q(
                        "INSERT INTO phase2_excluded (evidence_id, evidence_version, "
                        " control_id, reason_code, reason, noted_at) "
                        "VALUES (%s,%s,'','SKIP_NO_MATCH',%s,%s)"),
                        (eid, ver, "Phase 1 매핑에 PRIMARY 가 없어졌다", _now()))
                    cur.execute(_q("UPDATE evidence SET status = %s "
                                   "WHERE evidence_id = %s"),
                                ("REVIEW_REQUIRED" if required else "COMPLETED", eid))
                    closed = True
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"saved": n, "primary_before": sorted(before),
            "primary_after": sorted(after), "changed": changed,
            "removed_primary": sorted(before - after),
            "superseded": [{"result_id": r, "control_id": c} for r, c in gone],
            "reopened": reopened, "closed_no_primary": closed}


def backfill(directory=None, verbose: bool = True) -> dict:
    """
    phase1_통합/results/*.json 을 전부 읽어 표를 채운다.
    orchestrator 를 안 고치고 매핑을 DB 에 올리는 길이다. 여러 번 돌려도 된다.
    """
    d = Path(directory) if directory else result_dir()
    files = sorted(d.glob("*_v*.json"))
    done, errors, total = 0, [], 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            total += save(data)["saved"]
            done += 1
            if verbose:
                print(f"  {f.name}  통제항목 {len(data.get('mapped_controls', []))}건")
        except Exception as exc:
            errors.append({"file": f.name, "error": type(exc).__name__,
                           "message": str(exc)})
            if verbose:
                print(f"  X {f.name}: {type(exc).__name__} {exc}")
    return {"files": len(files), "saved": done, "mappings": total, "errors": errors}


# ── 조회 ──────────────────────────────────────────────────────────────
_COLS = ("mapping_id, evidence_id, evidence_version, control_id, control_name, "
         "relation, similarity_score, llm_confidence, reason, citation_count, "
         "match_status, review_required, review_reasons, trace_id, noted_at")


def _fetch(where, params):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(f"SELECT {_COLS} FROM phase1_mapping WHERE {where} "
                           f"ORDER BY control_id, evidence_id"), params)
            rows = _rows(cur)
            for r in rows:
                cur.execute(_q("SELECT seq, chunk_id, page, quote, source_file "
                               "FROM phase1_mapping_citation WHERE mapping_id = %s "
                               "ORDER BY seq"), (r["mapping_id"],))
                r["citations"] = _rows(cur)
                r["review_required"] = bool(r["review_required"])
                r["review_reasons"] = (r["review_reasons"].split(",")
                                       if r["review_reasons"] else [])
                for k in ("similarity_score", "llm_confidence"):
                    if r[k] is not None:
                        r[k] = float(r[k])
    finally:
        conn.close()
    return rows


def by_control(control_id: str, include_history: bool = False):
    """
    그 통제항목에 후보로 걸린 증적 (PRIMARY + RELATED).

    기본은 각 증적의 **지금 버전**만 돌려준다. 재업로드한 증적의 옛 버전까지
    같이 내보내면 화면에 E0001 v1·v2·v3 가 다 뜬다.
    include_history=True 면 지난 버전도 같이 본다.
    """
    if include_history:
        return _fetch("control_id = %s", (control_id,))
    return _fetch(
        "control_id = %s AND evidence_version = "
        "(SELECT version FROM evidence e WHERE e.evidence_id = "
        " phase1_mapping.evidence_id)", (control_id,))


def by_evidence(evidence_id: str, version: int | None = None,
                include_history: bool = False):
    """
    그 증적이 걸린 통제항목.

    기본은 **지금 버전**만. by_control·candidates·summary 와 같은 규칙이다.
    version 을 주면 그 버전, include_history=True 면 전 버전.
    """
    if version is not None:
        return _fetch("evidence_id = %s AND evidence_version = %s",
                      (evidence_id, version))
    if include_history:
        return _fetch("evidence_id = %s", (evidence_id,))
    return _fetch(
        "evidence_id = %s AND evidence_version = "
        "(SELECT version FROM evidence e WHERE e.evidence_id = "
        " phase1_mapping.evidence_id)", (evidence_id,))


def candidates(control_id: str, include_history: bool = False):
    """
    화면용 매핑 후보 목록. PRIMARY 를 먼저, 그다음 점수 높은 순.
    GET /api/review/control/{id}/candidates 가 그대로 내보낼 dict.

    기본은 각 증적의 지금 버전만 나온다. 지난 버전까지 보려면
    include_history=True.
    """
    rows = by_control(control_id, include_history)
    rows.sort(key=lambda r: (_RELATION_ORDER.get(r["relation"], 9),
                             -(r["llm_confidence"] or 0),
                             -(r["similarity_score"] or 0)))
    return {
        "control_id": control_id,
        "control_name": rows[0]["control_name"] if rows else None,
        "include_history": include_history,
        "candidate_count": len(rows),
        "primary_count": sum(1 for r in rows if r["relation"] == "PRIMARY"),
        "candidates": [{
            "evidence_id": r["evidence_id"],
            "version": r["evidence_version"],
            "relation": r["relation"],
            "judged": r["relation"] == "PRIMARY",
            "similarity_score": r["similarity_score"],
            "llm_confidence": r["llm_confidence"],
            "reason": r["reason"],
            "phase1_review_required": r["review_required"],
            "phase1_review_reasons": r["review_reasons"],
            "citations": r["citations"],
        } for r in rows],
    }


def summary(include_history: bool = False) -> dict:
    """
    매핑 집계. 기본은 각 증적의 지금 버전만 센다.
    옛 버전까지 세면 재업로드가 쌓일수록 매핑 수가 부풀어 보인다.
    """
    cur_only = ("" if include_history else
                " WHERE evidence_version = (SELECT version FROM evidence e "
                " WHERE e.evidence_id = phase1_mapping.evidence_id)")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_q(f"SELECT relation, COUNT(*) AS n FROM phase1_mapping"
                           f"{cur_only} GROUP BY relation"))
            by_rel = {r["relation"]: int(r["n"]) for r in _rows(cur)}
            cur.execute(_q(f"SELECT COUNT(DISTINCT evidence_id) AS n "
                           f"FROM phase1_mapping{cur_only}"))
            ev = int(_rows(cur)[0]["n"])
            cur.execute(_q(f"SELECT COUNT(DISTINCT control_id) AS n "
                           f"FROM phase1_mapping{cur_only}"))
            ctl = int(_rows(cur)[0]["n"])
            cur.execute(_q(
                "SELECT COUNT(*) AS n FROM phase1_mapping_citation c "
                "WHERE EXISTS (SELECT 1 FROM phase1_mapping m "
                " WHERE m.mapping_id = c.mapping_id" +
                ("" if include_history else
                 " AND m.evidence_version = (SELECT version FROM evidence e "
                 " WHERE e.evidence_id = m.evidence_id)") + ")"))
            cit = int(_rows(cur)[0]["n"])
    finally:
        conn.close()
    return {"mapping_total": sum(by_rel.values()), "by_relation": by_rel,
            "evidence_total": ev, "control_total": ctl, "citation_total": cit,
            "include_history": include_history}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Phase 1 매핑 결과를 DB 에 올린다")
    ap.add_argument("--dir", help="phase1_통합/results 경로")
    ap.add_argument("--control-id", help="이 통제항목의 후보만 보여준다")
    ap.add_argument("--history", action="store_true", help="지난 버전도 같이")
    a = ap.parse_args(argv)
    if a.control_id:
        c = candidates(a.control_id, a.history)
        print(f"{c['control_id']} {c['control_name'] or ''}  후보 {c['candidate_count']}건 "
              f"(PRIMARY {c['primary_count']})")
        for x in c["candidates"]:
            print(f"  {x['relation']:<8} {x['evidence_id']} v{x['version']}  "
                  f"conf {x['llm_confidence']}  sim {x['similarity_score']}  "
                  f"인용 {len(x['citations'])}")
        return 0
    r = backfill(a.dir)
    print(f"\n파일 {r['files']}개 / 저장 {r['saved']}개 / 매핑 {r['mappings']}건 "
          f"/ 오류 {len(r['errors'])}건")
    print(summary())
    return 1 if r["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
