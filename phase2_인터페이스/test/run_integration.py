"""
run_integration.py : Phase 1 결과 → Phase 2 입력 조립 → 규격 검사   (WBS 1-B 연동 시험)

쓰는 법
    python run_integration.py                  저장소를 알아서 찾음
    python run_integration.py C:\\I_P\\I_P-main  저장소 위치를 직접 지정

하는 일
    ① 기준팀 체크리스트에서 "질문지가 있는 통제항목" 목록을 읽는다
    ② 판단팀의 실제 LLM 실행 기록(runs/*.jsonl)을 한 줄씩 읽는다
    ③ 각 기록을 Phase 2 입력 모양으로 조립한다 (build_phase2_input.py)
    ④ 조립한 것이 규격(phase2_input.schema.json)에 맞는지 검사한다
    ⑤ 판정 대상 / 질문지 없음 을 세어서 보여준다

LLM 을 부르지 않는다. 이미 저장된 기록만 읽으므로 몇 초면 끝난다.
"""
import json
import sys
import collections
from pathlib import Path

try:
    import jsonschema
except ImportError:
    sys.exit("jsonschema 가 없습니다.  pip install jsonschema")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))   # build_phase2_input.py 는 폴더 위(제품 코드)에 있다
from build_phase2_input import build, judge_targets, load_checklist_scope  # noqa: E402


def find_repo(start):
    """저장소 뿌리를 찾는다. phase1_* 과 phase2_* 폴더가 같이 있는 곳."""
    for base in [start, *start.parents]:
        if any(base.glob("phase1_*")) and any(base.glob("phase2_*")):
            return base
    return None


def pick(repo, pattern, inner):
    """폴더 이름이 한글이든 깨진 형태든 상관없이 찾는다."""
    for d in sorted(repo.glob(pattern)):
        hit = list(d.glob(inner))
        if hit:
            return d, hit
    return None, []


def main():
    start = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    repo = find_repo(start)
    if repo is None:
        sys.exit(f"저장소를 못 찾았습니다: {start}\n"
                 f"  phase1_* 과 phase2_* 폴더가 있는 위치를 인자로 넘겨주세요.")
    print(f"저장소: {repo}")

    # ① 체크리스트 — 2장 전체본이 있으면 그걸, 없으면 기본 초안을
    _, found = pick(repo, "phase2_*", "chapter2_full_checklist_draft.json")
    if not found:
        _, found = pick(repo, "phase2_*", "checklist_draft.json")
    if not found:
        sys.exit("체크리스트 파일을 못 찾았습니다 (chapter2_full_checklist_draft.json / checklist_draft.json)")
    checklist = found[0]
    scope, cl_ver = load_checklist_scope(checklist)
    print(f"체크리스트: {checklist.name}  ({cl_ver})  통제항목 {len(scope)}개")

    # ② 판단팀 실행 기록
    _, runs = pick(repo, "phase1_*", "runs/*.jsonl")
    if not runs:
        sys.exit("판단팀 실행 기록(runs/*.jsonl)을 못 찾았습니다")
    print(f"실행 기록: {len(runs)}개 파일\n")

    schema = json.load(open(HERE.parent / "phase2_input.schema.json", encoding="utf-8"))

    stat = collections.Counter()
    no_scope = collections.Counter()
    rows = []

    for runfile in sorted(runs):
        for line in open(runfile, encoding="utf-8"):
            d = json.loads(line)
            raw = d.get("raw_response")
            try:
                r = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                stat["기록 파싱 실패"] += 1
                continue
            if not isinstance(r, dict) or r.get("match_status") != "MATCHED":
                stat["MATCHED 아님(건너뜀)"] += 1
                continue

            # 실행 기록에는 mapped_controls 가 없어 RELATED 판정 후보로 대신한다.
            # 실제 Phase 1 결과를 쓸 때는 이 블록이 필요 없다.
            rel = [c for c in r.get("candidate_decisions", []) if c.get("decision") == "RELATED"]
            if not rel:
                stat["RELATED 후보 없음"] += 1
                continue
            mapped = []
            for i, c in enumerate(rel):
                q = (c.get("citations") or [{}])[0].get("quote")
                mapped.append({**c,
                               "relation": "PRIMARY" if i == 0 else "RELATED",
                               "citations": [{"chunk_id": "E9999_v1_c0000", "page": None,
                                              "quote": q}] if q else []})

            phase1_result = {"evidence_id": "E9999", "version": 1,
                             "mapped_controls": mapped,
                             "versions": {"kb_sha256": "0" * 64},
                             "trace_id": d.get("test_id")}
            chunks = [{"chunk_id": "E9999_v1_c0000", "chunk_index": 0,
                       "chunk_type": "text", "text": "(실제 청크 본문 자리)",
                       "source": "parser"}]

            # ③ 조립
            inp = build(phase1_result, chunks, scope, cl_ver)

            # ④ 규격 검사
            try:
                jsonschema.validate(inp, schema)
                stat["규격 통과"] += 1
            except jsonschema.ValidationError as e:
                stat["규격 실패"] += 1
                print(f"  ✗ {d.get('test_id')}: {e.message[:70]}")
                continue

            # ⑤ 집계
            jt = judge_targets(inp)
            if jt:
                stat["판정 진행"] += 1
            else:
                stat["질문지 없음"] += 1
                for t in inp["targets"]:
                    if t["judge"] and not t["checklist_in_scope"]:
                        no_scope[t["control_id"]] += 1
            rows.append((d.get("test_id"),
                         [t["control_id"] for t in jt],
                         [t["control_id"] for t in inp["targets"] if not t["judge"]]))

    print("── 결과 ──")
    for k, v in stat.items():
        print(f"  {k:22} {v}건")
    if no_scope:
        print(f"\n  질문지 없는 PRIMARY 항목: {dict(no_scope)}")

    print("\n── 건별 (앞 8건) ──")
    for tid, judged, ref in rows[:8]:
        print(f"  {str(tid):28} 판정={judged or '-'}   참고연결={ref or '-'}")

    if stat["규격 실패"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
