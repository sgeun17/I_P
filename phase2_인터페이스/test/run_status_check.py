"""
run_status_check.py : 상태 전이 검사   (WBS 3-A 점검)

쓰는 법
    python run_status_check.py                  저장소를 알아서 찾음
    python run_status_check.py C:\\I_P\\I_P-main

하는 일
    ① 입력팀 config.py 의 VALID_STATUSES 를 읽어서, 내 전이표가 쓰는 값이
       그 8개와 정확히 같은지 본다 — 새 상태값을 만들지 않았는지 확인
    ② 판단팀 enums.py 의 EvidenceStatus 와도 맞는지 본다
    ③ 지금 저장소 코드가 실제로 쓰는 상태값을 세어서, 안 쓰이는 값을 보여준다
    ④ 실제로 생길 경로 7가지를 전이표로 통과시켜 본다
    ⑤ 일어나면 안 되는 전이 6가지가 막히는지 본다
    ⑥ Phase 1 이 끝난 증적의 상태를 판단팀 함수와 비교한다 (★ 미합의 지점)

DB 에 붙지 않는다. 코드와 전이표만 읽는다.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import phase2_status as st  # noqa: E402


def find_repo(start):
    for base in [start, *start.parents]:
        if any(base.glob("phase1_*")) and any(base.glob("phase2_*")):
            return base
    return None


def pick(repo, pattern, inner):
    for d in sorted(repo.glob(pattern)):
        hit = list(d.glob(inner))
        if hit:
            return hit[0]
    return None


def read_block(path, start_pat, end="}"):
    """파일에서 { ... } 블록 하나를 글자로 떠낸다. import 하지 않으려고 이렇게 한다."""
    text = path.read_text(encoding="utf-8")
    m = re.search(start_pat, text)
    if not m:
        return None
    tail = text[m.end():]
    cut = tail.find(end)
    return tail if cut < 0 else tail[:cut]


def main():
    start = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    repo = find_repo(start)
    if repo is None:
        sys.exit(f"저장소를 못 찾았습니다: {start}")
    print(f"저장소: {repo}")
    print(f"규격:   {st.SPEC_VERSION}\n")

    fails = []

    # ① 입력팀 VALID_STATUSES 와 대조
    cfg = pick(repo, "phase1_*", "database/config.py") or pick(repo, "phase1_*", "config.py")
    print("── ① 입력팀 config.VALID_STATUSES 와 대조 ──")
    if not cfg:
        print("  ? config.py 를 못 찾았습니다")
    else:
        block = read_block(cfg, r"VALID_STATUSES\s*=\s*\{")
        theirs = set(re.findall(r'"([A-Z_]+)"', block or ""))
        mine = set(st.STATUSES)
        print(f"  입력팀 {len(theirs)}개 / 전이표 {len(mine)}개")
        if theirs == mine:
            print("  [O] 완전히 같음 — 새 상태값을 만들지 않았습니다")
        else:
            print(f"  [X] 전이표에만: {sorted(mine - theirs) or '-'}")
            print(f"      입력팀에만: {sorted(theirs - mine) or '-'}")
            fails.append("VALID_STATUSES 불일치")

    # ② 판단팀 EvidenceStatus 와 대조
    enums = pick(repo, "phase1_*", "src/enums.py")
    print("\n── ② 판단팀 EvidenceStatus 와 대조 ──")
    if not enums:
        print("  ? enums.py 를 못 찾았습니다")
    else:
        block = read_block(enums, r"class EvidenceStatus\(StrEnum\):", "\nclass ")
        theirs = set(re.findall(r'=\s*"([A-Z_]+)"', block or ""))
        if theirs == set(st.STATUSES):
            print("  [O] 완전히 같음")
        else:
            print(f"  [X] 차이: {sorted(set(st.STATUSES) ^ theirs)}")
            fails.append("EvidenceStatus 불일치")

    # ③ 지금 코드가 실제로 쓰는 값
    #    config.py·enums.py 는 "값을 정의한 곳"이라 빼고 센다. 실제로 쓰는 곳만 본다.
    print("\n── ③ 저장소 코드가 실제로 쓰는 상태값 (정의 파일 제외) ──")
    SKIP = ("config.py", "enums.py")
    used = {s: 0 for s in st.STATUSES}
    for py in repo.rglob("*.py"):
        if ("phase2_spec" in str(py) or "__pycache__" in str(py)
                or py.name in SKIP):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:
            continue
        # 주석·docstring 안의 언급은 세지 않으려고, 따옴표로 감싼 것만 센다
        for s in st.STATUSES:
            used[s] += len(re.findall(rf'["\']{s}["\']', text))
    for s in st.STATUSES:
        mark = " " if used[s] else "←"
        print(f"  {s:17} {used[s]:3}곳  {mark} {'아무도 안 씀' if not used[s] else ''}")
    unused = [s for s in st.STATUSES if not used[s]]
    print(f"\n  안 쓰이는 값 {len(unused)}개: {unused}")
    print("  → Phase 2 가 이 자리에 들어갑니다 (새로 만들 값 없음)")

    # ④ 실제로 생길 경로
    print("\n── ④ 정상 경로 검사 ──")
    paths = [
        ("순조롭게 끝",
         ["UPLOADED", "PREPROCESSING", "PREPROCESSED", "MAPPING", "VALIDATING", "COMPLETED"]),
        ("파싱 실패 → 재시도 성공",
         ["UPLOADED", "PREPROCESSING", "FAILED", "PREPROCESSING", "PREPROCESSED", "MAPPING"]),
        ("Phase 1 에서 검토 (NO_MATCH 등) → 승인 → 판정",
         ["PREPROCESSED", "MAPPING", "REVIEW_REQUIRED", "VALIDATING", "COMPLETED"]),
        ("Phase 1 검토에서 사람이 고침 → 매핑 다시",
         ["MAPPING", "REVIEW_REQUIRED", "MAPPING", "VALIDATING", "COMPLETED"]),
        ("Phase 2 에서 검토 → 승인하고 끝",
         ["MAPPING", "VALIDATING", "REVIEW_REQUIRED", "COMPLETED"]),
        ("Phase 2 LLM 실패 → 판정만 재시도",
         ["MAPPING", "VALIDATING", "FAILED", "VALIDATING", "COMPLETED"]),
        ("청크가 0개라 판단 불가 → 검토",
         ["PREPROCESSED", "MAPPING", "REVIEW_REQUIRED", "FAILED"]),
    ]
    for label, seq in paths:
        try:
            st.validate_path(seq)
            print(f"  [O] {label}")
        except ValueError as e:
            print(f"  [X] {label}  — {e}")
            fails.append(label)

    # ⑤ 막혀야 하는 전이
    print("\n── ⑤ 막혀야 하는 전이 검사 ──")
    bad = [
        ("업로드만 하고 바로 판정", "UPLOADED", "VALIDATING"),
        ("청킹 안 하고 매핑", "UPLOADED", "MAPPING"),
        ("Phase 1 건너뛰고 판정", "PREPROCESSED", "VALIDATING"),
        ("매핑하다 바로 완료 (★ 지금 판단팀이 하는 것)", "MAPPING", "COMPLETED"),
        ("끝난 증적을 되돌림", "COMPLETED", "VALIDATING"),
        ("실패에서 바로 완료", "FAILED", "COMPLETED"),
    ]
    for label, a, b in bad:
        if st.can(a, b):
            print(f"  [X] {label:42} {a} → {b}  통과돼 버림")
            fails.append(label)
        else:
            print(f"  [O] {label:42} {a} → {b}  막힘")

    # ⑥ Phase 1 끝난 뒤의 상태 — 판단팀 함수와 비교
    print("\n── ⑥ Phase 1 이 끝난 증적의 상태 (★ 미합의) ──")
    svc = pick(repo, "phase1_*", "src/service.py")
    theirs = None
    if svc:
        block = read_block(svc, r"def to_evidence_status", "\ndef ")
        if block and "EvidenceStatus.COMPLETED" in block:
            theirs = "COMPLETED"
    print(f"  판단팀 to_evidence_status()  → {theirs or '?'}")
    print(f"  이 전이표 from_phase1()      → {st.from_phase1('COMPLETED', False)}")
    if theirs == "COMPLETED":
        print("  [!] 다릅니다. 판단팀 함수는 Phase 2 가 없던 때 쓴 것이라 "
              "Phase 1 만 끝나도 COMPLETED 입니다.")
        print("      그러면 VALIDATING 으로 갈 수 없고 Phase 2 가 돌 자리가 없어집니다.")
        print("      판단팀 코드는 고치지 않고, 호출하는 쪽에서 from_phase1() 을 쓰면 됩니다.")

    # ⑥-2 통합 계층이 DB 에 실제로 쓰는 값
    print("\n── ⑥-2 통합 계층이 DB 에 실제로 쓰는 값 ──")
    orch = pick(repo, "phase1_*", "orchestrator.py")
    if not orch:
        print("  통합 폴더가 아직 없습니다 (phase1_통합/orchestrator.py)")
    else:
        text = orch.read_text(encoding="utf-8")
        calls = re.findall(r"update_status\(([^)]*)\)", text)
        print(f"  {orch.parent.name}/{orch.name} 의 update_status 호출 {len(calls)}건")
        for c in calls:
            print(f"    update_status({c.strip()})")
        # 판단팀 결과를 그대로 넘기고 있는가
        if any("evidence_status" in c for c in calls):
            print("  [!] 판단팀 to_evidence_status() 결과를 그대로 DB 에 씁니다.")
            print("      그 값은 Phase 1 매핑만 끝나도 COMPLETED 라, Phase 2 가 돌 자리가 없어집니다.")
            print("      고칠 곳은 여기 한 줄입니다:")
            print("        update_status(evidence_id, evidence_status)")
            print("            ↓")
            print("        update_status(evidence_id, from_phase1(")
            print("            judged.mapping_result.processing_status,")
            print("            judged.mapping_result.human_review.required))")
            print("      판단팀 파일은 건드리지 않습니다.")

    print("\n  세 경우:")
    for ps, rv, p2 in [("COMPLETED", False, True), ("COMPLETED", True, True),
                       ("FAILED", False, True), ("COMPLETED", False, False)]:
        print(f"    processing={ps:9} review={str(rv):5} phase2={str(p2):5}"
              f" → {st.from_phase1(ps, rv, p2)}")

    # 검토 끝난 뒤
    print("\n  검토 끝난 뒤 (approve_review):")
    for phase in ("phase1", "phase2"):
        for mod in (False, True):
            print(f"    {phase} 검토, 고침={str(mod):5} → {st.approve_review(phase, mod)}")

    print()
    if fails:
        print(f"실패 {len(fails)}건: {fails}")
        sys.exit(1)
    print("전부 통과.")


if __name__ == "__main__":
    main()
