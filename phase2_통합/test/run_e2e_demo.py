"""
run_e2e_demo.py : Phase 1 → Phase 2 종단 시연

    python test/run_e2e_demo.py --evidence-id E0001
    python test/run_e2e_demo.py --limit 3
    python test/run_e2e_demo.py --evidence-id E0001 --approve 혜진

업로드된 증적 하나를 Phase 1 → Phase 2 → 검토 → 커버리지까지 한 번에 돌리고,
각 단계에서 무엇이 바뀌었는지 찍는다. 발표 때 화면에 띄우고 읽으면 된다.

실제 MySQL 과 Ollama 가 있어야 돈다. 끝나면 reports/ 에 리포트까지 만든다.

Phase 1 은 phase1_통합/orchestrator.inspect_evidence() 를 부른다.
**그게 실패하면 거기서 멈춘다** — 저장된 옛 결과로 Phase 2 만 돌리면 Phase 1 이
깨져 있어도 시연은 끝까지 가버려서 종단 시연이 아니게 된다.
그걸 정말 보려면 --skip-phase1 을 명시한다.

Phase 2 는 run_evidence 를 직접 부르지 않고 run_pending() 으로 돈다 —
runner 가 스스로 그 증적을 고르는지까지 봐야 종단 연결이다.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import phase2_result_store as rs            # noqa: E402
import phase2_review_store as rv            # noqa: E402
import phase2_runner as pr                  # noqa: E402

LINE = "─" * 68

# 증적마다 어느 경로로 갔는지. 마지막 판정에 쓴다.
PATH = []


def step(n, title):
    print(f"\n{LINE}\n{n}. {title}\n{LINE}")


def status_of(evidence_id):
    conn = rs._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(rs._q("SELECT status, version FROM evidence "
                              "WHERE evidence_id = %s"), (evidence_id,))
            rows = rs._rows(cur)
    finally:
        conn.close()
    if not rows:
        return None, None
    return rows[0]["status"], rows[0]["version"]


def run_phase1(evidence_id, model=None):
    """orchestrator 로 Phase 1 을 돌린다. 안 되면 None 을 돌려준다 (호출자가 판단)."""
    sys.path.insert(0, str(pr.ROOT / "phase1_통합"))
    try:
        import orchestrator
    except Exception as exc:
        print(f"  orchestrator 를 못 읽었다 ({type(exc).__name__}: {exc})")
        return None
    if model:
        os.environ["PHASE1_JUDGMENT_MODEL"] = model
    try:
        t = time.time()
        r = orchestrator.inspect_evidence(evidence_id)
        print(f"  Phase 1 완료  {time.time() - t:.1f}초")
        s = r.get("judgment", {})
        print(f"  match_status      {s.get('match_status')}")
        print(f"  매핑 통제항목      {len(s.get('mapped_controls') or [])}건")
        print(f"  사람 검토          {(s.get('human_review') or {}).get('required')}")
        return r
    except Exception as exc:
        print(f"  Phase 1 실패 ({type(exc).__name__}: {exc})")
        return None


def demo_one(evidence_id, *, model=None, approve_as=None, skip_phase1=False,
             require_discovery=True, strict=False, direct=False):
    print(f"\n{'=' * 68}\n증적 {evidence_id} 종단 시연\n{'=' * 68}")

    step(1, "시작 상태")
    st, ver = status_of(evidence_id)
    if st is None:
        print(f"  그런 증적이 없다: {evidence_id}")
        PATH.append({"evidence_id": evidence_id, "via_validating": False,
                     "via_runner": False})
        return 1
    print(f"  evidence.status = {st}   version = {ver}")
    before = len(rs.by_evidence(evidence_id))
    print(f"  Phase 2 판정 {before}건")

    if not skip_phase1:
        step(2, "Phase 1 — 전처리 → 검색 → 매핑")
        p1 = run_phase1(evidence_id, model)
        if p1 is None:
            # 여기서 멈춘다. 예전 Phase 1 결과로 Phase 2 만 돌리면 Phase 1 이
            # 깨져 있어도 시연은 끝까지 가버린다 — 종단 시연이 아니게 된다.
            print("\n  Phase 1 이 실패했다. 종단 시연을 여기서 멈춘다.")
            print("  저장된 Phase 1 결과로 Phase 2 만 보려면 --skip-phase1 를 주면 된다.")
            PATH.append({"evidence_id": evidence_id, "via_validating": False,
                         "via_runner": False})
            return 1
        st, ver = status_of(evidence_id)
        print(f"  evidence.status = {st}   version = {ver}")
        # 정규 흐름이면 여기서 VALIDATING 이어야 한다.
        # COMPLETED 면 orchestrator 가 아직 from_phase1(phase2_enabled=True) 를
        # 안 쓰는 것이고, runner 가 호환 때문에 주워서 돌 뿐이다.
        if st == "VALIDATING":
            print("  정규 흐름 — Phase 1 이 VALIDATING 을 남겼다.")
            via_validating = True
        else:
            print(f"  ※ VALIDATING 이 아니라 {st} 다. runner 가 호환으로 주워 돈다.")
            print("     orchestrator 190행이 phase2_status.from_phase1(..., "
                  "phase2_enabled=True) 를 쓰면 VALIDATING 이 된다.")
            via_validating = False
            if strict:
                print("\n  --strict 라서 여기서 멈춘다. (WBS 29·40 은 이 경로가 핵심이다)")
                PATH.append({"evidence_id": evidence_id, "via_validating": False,
                             "via_runner": False})
                return 1
    else:
        step(2, "Phase 1 — 건너뜀 (저장된 결과를 쓴다)")
        via_validating = False
        print("  ※ Phase 1 을 안 돌렸으므로 이번 실행은 종단 시연이 아니다.")
        require_discovery = False

    step(3, "Phase 2 — runner 가 이 증적을 스스로 찾는가")
    pending = pr.pending_evidence_ids()
    found = evidence_id in pending
    print(f"  pending_evidence_ids() → {len(pending)}건" +
          (f"  (앞 10건 {pending[:10]})" if pending else ""))
    if found:
        print(f"  {evidence_id} 를 찾았다. 자동 연결이 실제로 된다.")
    else:
        print(f"  {evidence_id} 를 못 찾았다.")
        st_now, _ = status_of(evidence_id)
        print(f"    지금 상태 {st_now}  /  runner 가 보는 상태 {pr.PENDING_STATUS}")
        print("    이미 이 버전 판정이 있거나, 증적 전체가 제외됐거나, 상태가 다르다.")
        if require_discovery:
            print("\n  자동 발견이 안 되면 종단 연결이 된 게 아니다. 여기서 멈춘다.")
            print("  그래도 판정만 보려면 --no-discovery 를 주면 된다.")
            PATH.append({"evidence_id": evidence_id,
                         "via_validating": via_validating, "via_runner": False})
            return 1

    step(4, "Phase 2 — 통제항목별 판정")
    show = lambda n, tot, e, c: print(f"   [{n}/{tot}] {c}")      # noqa: E731
    t = time.time()
    if direct or not found:
        # 자동 발견을 못 했거나 --direct 면 그 증적만 직접 돌린다.
        print("  run_evidence() 를 직접 부른다 (자동 경로 아님)")
        r = pr.run_evidence(evidence_id, model=model or pr.DEFAULT_MODEL,
                            progress=show)
        via_runner = False
    else:
        # 진짜 자동 경로 — runner 가 스스로 고른 것을 돌린다.
        others = [e for e in pending if e != evidence_id]
        if others:
            print(f"  대기 중인 다른 증적 {len(others)}건은 이번에 안 돈다 "
                  f"(evidence_ids 로 좁힘)")
        print("  run_pending() 으로 돈다 — 찾는 건 runner 가 하고 "
              "실행만 이 증적으로 좁힌다")
        batch = pr.run_pending(model=model or pr.DEFAULT_MODEL,
                               evidence_ids=[evidence_id], progress=show)
        r = next((x for x in batch["results"] if x["evidence_id"] == evidence_id), None)
        via_runner = r is not None
        if r is None:
            err = next((x for x in batch["errors"]
                        if x["evidence_id"] == evidence_id), None)
            print(f"  run_pending 이 이 증적을 못 돌렸다: {err}")
            PATH.append({"evidence_id": evidence_id,
                         "via_validating": via_validating, "via_runner": False})
            return 1
    print(f"  {time.time() - t:.1f}초")
    if r.get("mapping") and not r["mapping"]["saved"]:
        print(f"  ※ 매핑 DB 저장 실패 — {r['mapping']['error']}")
    elif r.get("mapping", {}).get("superseded"):
        print(f"  매핑이 바뀌어 기존 판정 {len(r['mapping']['superseded'])}건을 과거로 돌렸다")
    print(f"  판정 {r['judged']} / 제외 {r['excluded']} / 실패 {r['failed']}")
    for x in r["results"]:
        print(f"    {x['control_id']:<10} {x.get('overall_result') or x['processing_status']}")
    for x in r["skipped"]:
        print(f"    {str(x['control_id'] or '증적 전체'):<10} 제외 — {x['reason_code']}")
    st, _ = status_of(evidence_id)
    print(f"  evidence.status = {st}")

    PATH.append({"evidence_id": evidence_id, "via_validating": via_validating,
                 "via_runner": via_runner})

    step(5, "검토 대기 목록")
    q = [x for x in rs.review_queue() if x["evidence_id"] == evidence_id]
    print(f"  {len(q)}건")
    for x in q:
        print(f"    {x['control_id']:<10} {x['review_state']:<14} {x['overall_result']}")
        for b in x["approval_blockers"]:
            print(f"        보류 — {b['message']}")

    if approve_as and q:
        step(6, f"승인 — {approve_as}")
        for x in q:
            try:
                a = rv.approve(x["result_id"], approve_as, reason="종단 시연")
                print(f"    {x['control_id']:<10} {a['state_before']} → {a['state_after']}"
                      f"   증적 {a['status_before']} → {a['status_after']}")
            except Exception as exc:
                print(f"    {x['control_id']:<10} 승인 거부 — {exc}")
        st, _ = status_of(evidence_id)
        print(f"  evidence.status = {st}")

    step(7, "이 증적의 결과")
    for one in rs.evidence_response(evidence_id)["controls"]:
        print(f"  {one['control_id']:<10} {one['overall_result']:<14} "
              f"{one['overall_result_wbs']:<8} {one['review_state']}")
        for it in one["items"]:
            cit = len(it.get("citations") or [])
            print(f"      {it['item_id']:<16} {it['result']:<8} 근거 {cit}건"
                  + (f"   {','.join(it.get('reason_codes') or [])}" if it.get("reason_codes") else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 1 → Phase 2 종단 시연")
    ap.add_argument("--evidence-id", help="없으면 대기 중인 증적을 --limit 만큼")
    ap.add_argument("--limit", type=int, default=3,
                    help="--evidence-id 를 안 줬을 때 몇 건까지 (기본 3)")
    ap.add_argument("--model", help="없으면 PHASE1_JUDGMENT_MODEL / qwen3:14b")
    ap.add_argument("--approve", help="승인자 이름. 주면 승인까지 한다")
    ap.add_argument("--skip-phase1", action="store_true",
                    help="Phase 1 은 이미 돌렸다. 저장된 결과로 Phase 2 만 "
                         "(종단 시연으로는 안 친다)")
    ap.add_argument("--no-discovery", action="store_true",
                    help="runner 가 자동으로 못 찾아도 그냥 판정까지 간다")
    ap.add_argument("--strict", action="store_true",
                    help="Phase 1 이 VALIDATING 을 안 남기면 실패로 본다")
    ap.add_argument("--direct", action="store_true",
                    help="run_pending 대신 그 증적만 직접 돌린다 (자동 경로 아님)")
    ap.add_argument("--reports", default="reports", help="리포트 폴더")
    a = ap.parse_args(argv)

    try:
        rs._connect().close()
    except Exception as exc:
        print(f"MySQL 에 못 붙었습니다: {type(exc).__name__} {exc}")
        return 2

    ids = ([a.evidence_id] if a.evidence_id
           else pr.pending_evidence_ids()[:a.limit])
    if not ids:
        print("돌릴 증적이 없습니다. 업로드 후 다시 해보세요.")
        return 1

    bad = 0
    for eid in ids:
        bad += demo_one(eid, model=a.model, approve_as=a.approve,
                        skip_phase1=a.skip_phase1,
                        require_discovery=not a.no_discovery,
                        strict=a.strict, direct=a.direct) or 0

    step(8, "전체 커버리지")
    cov = rs.coverage()
    print(f"  통제항목 {cov['control_total']}개 중 제출 {cov['submitted']}개 "
          f"({cov['coverage_rate']}%)  제외만 {cov['excluded_only']}  "
          f"미제출 {cov['not_submitted']}")
    print(f"  {cov['verdicts_by_control_wbs']}")
    s = rs.summary()
    print(f"  판정 {s['judged_result_total']}건 / 증적 {s['judged_evidence_total']}건 "
          f"/ 검토 대기 {s['review_required']}건")

    step(9, "리포트 출력")
    try:
        import phase2_report as rp
        out = Path(a.reports)
        print("  CSV      ", rp.to_csv(out / "control_report.csv"))
        print("  HTML     ", rp.to_html(out / "control_report.html"))
        print("  커버리지  ", rp.coverage_csv(out / "coverage.csv")[0])
        text, rec = rp.reconcile_text(out / "reconcile.txt")
        print(f"  대조      {rec['통과']} 일치 / {rec['실패']} 어긋남")
        for b in rec["어긋난 것"]:
            print(f"    어긋남 — {b['항목']}  집계 {b['집계']} / 상세 {b['상세']}")
    except Exception as exc:
        print(f"  리포트 실패 ({type(exc).__name__}: {exc})")

    print(f"\n{LINE}")
    v = bool(PATH) and all(x["via_validating"] for x in PATH)
    rn = bool(PATH) and all(x["via_runner"] for x in PATH)
    if bad:
        print(f"증적 {bad}건이 종단까지 못 갔다.")
    elif a.skip_phase1:
        print("Phase 2 까지는 갔다. Phase 1 을 건너뛰었으므로 종단 시연은 아니다.")
    elif v and rn:
        print("종단 시연 끝 — Phase 1 이 VALIDATING 을 남기고 runner 가 스스로 주워 돌았다.")
        print("WBS 29(ANALYZING→VALIDATING) · 40(Phase 1→2 종단 시연) 체크 가능.")
    else:
        print("Phase 1 부터 리포트까지 돌긴 했는데 정규 경로는 아니다.")
        if not v:
            print("  - Phase 1 이 VALIDATING 을 안 남긴다 (orchestrator 190행)")
        if not rn:
            print("  - runner 가 스스로 고른 게 아니라 직접 불렀다")
        print("  WBS 29·40 은 아직 체크하지 말 것.")
    for x in PATH:
        print(f"    {x['evidence_id']}  VALIDATING {'O' if x['via_validating'] else 'X'}"
              f"  자동선택 {'O' if x['via_runner'] else 'X'}")
    print(LINE)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
