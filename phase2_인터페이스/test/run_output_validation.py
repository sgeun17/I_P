"""
run_output_validation.py : 출력 규격 검증   (WBS 2-B 샘플 출력 스키마 검증)

쓰는 법
    python run_output_validation.py                  저장소를 알아서 찾음
    python run_output_validation.py C:\\I_P\\I_P-main

하는 일
    ① 기준팀 검토 사례 162건(review_examples.json)을 읽는다
       — 각 사례는 문항 하나의 기대 판정(MET/NOT_MET/UNKNOWN)이다
    ② 그걸 Phase 2 출력 모양으로 조립한다
    ③ 출력 규격(phase2_output.schema.json)에 맞는지 검사한다
    ④ 통제항목별로 모아 종합 판정(overall_result)을 계산한다
    ⑤ critical 정책을 바꿔가며 결과가 어떻게 달라지는지 비교한다

LLM 을 부르지 않는다. 기대값만으로 규격과 규칙을 검사한다.
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
import overall_result as orule  # noqa: E402


def find_repo(start):
    for base in [start, *start.parents]:
        if any(base.glob("phase1_*")) and any(base.glob("phase2_*")):
            return base
    return None


def pick(repo, pattern, name):
    for d in sorted(repo.glob(pattern)):
        hit = list(d.glob(name))
        if hit:
            return hit[0]
    return None


def main():
    start = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    repo = find_repo(start)
    if repo is None:
        sys.exit(f"저장소를 못 찾았습니다: {start}")
    print(f"저장소: {repo}")

    checklist_path = (pick(repo, "phase2_*", "chapter2_full_checklist_draft.json")
                      or pick(repo, "phase2_*", "checklist_draft.json"))
    examples_path = pick(repo, "phase2_*", "review_examples.json")
    if not checklist_path or not examples_path:
        sys.exit("체크리스트 또는 검토 사례 파일을 못 찾았습니다")

    checklist = json.load(open(checklist_path, encoding="utf-8"))
    examples = json.load(open(examples_path, encoding="utf-8"))
    schema = json.load(open(HERE.parent / "phase2_output.schema.json", encoding="utf-8"))

    # 문항 ID → (통제항목, check_kind)
    meta = {}
    for ctl in checklist["controls"]:
        for it in ctl.get("items", []):
            meta[it["item_id"]] = (ctl["control_id"], ctl.get("control_name", ""),
                                   it.get("check_kind"))

    cases = examples.get("cases", [])
    print(f"체크리스트: {checklist_path.name}  ({checklist.get('draft_version')})")
    print(f"검토 사례:  {len(cases)}건\n")

    # ① 사례를 통제항목별로 모은다
    by_control = collections.defaultdict(list)
    unknown_items = []
    for c in cases:
        iid = c.get("item_id")
        if iid not in meta:
            unknown_items.append(iid)
            continue
        cid, cname, kind = meta[iid]
        by_control[(cid, cname)].append({
            "item_id": iid,
            "result": c.get("expected_result_draft"),
            "reason": c.get("review_note") or "기준팀 검토 사례의 기대값",
            # 기준팀 질문지 citation_required_for = ["MET","NOT_MET"] 이므로 둘 다 인용을 단다.
            "citations": ([{"chunk_id": "E9999_v1_c0000", "page": None,
                            "quote": c["evidence_text"]}]
                          if c.get("evidence_text") and
                          c.get("expected_result_draft") in ("MET", "NOT_MET") else []),
            "check_kind": kind,
        })

    if unknown_items:
        print(f"  ⚠ 질문지에 없는 문항 {len(unknown_items)}건: {unknown_items[:5]}\n")

    # ②③ 출력 조립 + 규격 검사
    policy = orule.default_policy()
    ok = fail = 0
    verdicts = collections.Counter()
    outputs = []
    for (cid, cname), items in sorted(by_control.items()):
        computed = orule.compute(items, policy)
        out = {
            "schema_version": "phase2-output-0.2",
            "evidence_id": "E9999", "version": 1,
            "control_id": cid, "control_name": cname,
            "checklist_version": checklist.get("draft_version"),
            "items": [{**i, "critical": orule.is_critical(i, policy)} for i in items],
            **computed,
        }
        try:
            jsonschema.validate(out, schema)
            ok += 1
            verdicts[computed["overall_result"]] += 1
            outputs.append(out)
        except jsonschema.ValidationError as e:
            fail += 1
            print(f"  ✗ {cid}: {e.message[:80]}")

    print("── 규격 검사 ──")
    print(f"  통과 {ok}건 / 실패 {fail}건   (통제항목 단위)")

    print("\n── 종합 판정 분포 (기준팀 기대값 기준) ──")
    for k, v in sorted(verdicts.items(), key=lambda x: -x[1]):
        print(f"  {k:16} {v}건")

    # ⑤ critical 정책 비교
    print("\n── critical 정책을 바꾸면 ──")
    policies = {
        "procedure+implementation (임시 기본)": {"mode": "check_kind",
                                                "critical_kinds": ["procedure", "implementation"]},
        "procedure 만": {"mode": "check_kind", "critical_kinds": ["procedure"]},
        "전부 critical": {"mode": "all"},
    }
    print(f"  {'정책':34} {'미충족':>6} {'확인 필요':>8} {'보완 권고':>9} {'충족':>5}")
    for label, p in policies.items():
        v = collections.Counter()
        for (cid, cname), items in by_control.items():
            v[orule.compute(items, p)["overall_result"]] += 1
        print(f"  {label:34} {v['미충족']:>6} {v['확인 필요']:>8} "
              f"{v['충족(보완 권고)']:>9} {v['충족']:>5}")


    # ⑥ 규칙 자체 검사 — 162 사례는 문항마다 세 값을 하나씩 두어서
    #    어떤 정책을 써도 전부 미충족이 된다. 규칙이 갈리는지는 경우를 만들어 확인한다.
    print("\n── 종합 판정 규칙 검사 ──")
    def it(iid, result, kind):
        return {"item_id": iid, "result": result, "reason": "x", "citations": [], "check_kind": kind}

    scenarios = [
        ("전부 MET",
         [it("Q1","MET","procedure"), it("Q2","MET","record"), it("Q3","MET","implementation")],
         "충족"),
        ("기록만 미비 (record NOT_MET)",
         [it("Q1","MET","procedure"), it("Q2","NOT_MET","record"), it("Q3","MET","implementation")],
         "충족(보완 권고)"),
        ("기록만 확인 불가 (record UNKNOWN)",
         [it("Q1","MET","procedure"), it("Q2","UNKNOWN","record"), it("Q3","MET","implementation")],
         "충족(보완 권고)"),
        ("이행 확인 불가 (implementation UNKNOWN)",
         [it("Q1","MET","procedure"), it("Q2","MET","record"), it("Q3","UNKNOWN","implementation")],
         "확인 필요"),
        ("절차 미수립 (procedure NOT_MET)",
         [it("Q1","NOT_MET","procedure"), it("Q2","MET","record"), it("Q3","MET","implementation")],
         "미충족"),
        ("NOT_MET 과 UNKNOWN 이 같이 (나쁜 쪽이 이김)",
         [it("Q1","NOT_MET","procedure"), it("Q2","UNKNOWN","implementation")],
         "미충족"),
        ("문항 없음",
         [], "증적 없음"),
    ]
    rule_fail = 0
    for label, items, expect in scenarios:
        got = orule.compute(items, policy)["overall_result"]
        mark = "O" if got == expect else "X"
        if got != expect: rule_fail += 1
        print(f"  [{mark}] {label:38} → {got}")
    print(f"  {len(scenarios)-rule_fail}/{len(scenarios)} 통과")
    if rule_fail: sys.exit(1)

    # 샘플 하나 저장
    if outputs:
        sample = max(outputs, key=lambda o: len(o["items"]))
        out_path = HERE.parent / "phase2_output_sample.json"
        out_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n샘플 저장: {out_path.name}  "
              f"({sample['control_id']}, 문항 {len(sample['items'])}개, {sample['overall_result']})")

    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
