"""
run_error_check.py : 오류·재시도·검토 흐름 검사 

쓰는 법
    python run_error_check.py                  저장소를 알아서 찾음
    python run_error_check.py C:\\I_P\\I_P-main

하는 일
    ① 판단팀 ErrorCode·ReviewReason 을 읽어서, 내 코드와 **번호가 겹치는지** 본다
    ② 재사용한다고 선언한 Phase 1 코드가 실제로 거기 있는지 본다
    ③ phase2_판단 하네스가 이미 쓰는 오류 문자열과 맞는지 본다
    ④ 모든 오류가 "검토로 보낸다" 또는 "기록만 한다" 중 하나에 들어있는지 본다
    ⑤ 실패 상황 9가지를 넣어서 검토 판정이 맞게 나오는지 본다
    ⑥ 통제항목 전체의 결말(FAILED / REVIEW_REQUIRED / COMPLETED)을 본다
    ⑦ 재시도 비용을 계산해서 배치 중단이 왜 필요한지 보여준다

LLM·DB 에 붙지 않는다.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import phase2_errors as pe        # noqa: E402
import phase2_status as st        # noqa: E402


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


def enum_values(path, class_name):
    """class X(StrEnum): 안의  NAME = "VALUE"  를 모은다. import 하지 않는다."""
    if not path:
        return {}
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"class {class_name}\(StrEnum\):", text)
    if not m:
        return {}
    tail = text[m.end():]
    cut = tail.find("\nclass ")
    block = tail if cut < 0 else tail[:cut]
    return {v: k for k, v in re.findall(r"^\s{4}([A-Z_]+)\s*=\s*\"([^\"]+)\"",
                                        block, re.M)}


def main():
    start = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    repo = find_repo(start)
    if repo is None:
        sys.exit(f"저장소를 못 찾았습니다: {start}")
    print(f"저장소: {repo}")
    print(f"규격:   {pe.SPEC_VERSION}\n")

    fails = []
    enums = pick(repo, "phase1_*", "src/enums.py")
    p1_err = enum_values(enums, "ErrorCode")
    p1_rev = enum_values(enums, "ReviewReason")

    # ── ① 번호 충돌 ────────────────────────────────────────────────────
    print("── ① 판단팀 코드와 번호가 겹치는가 ──")
    print(f"  판단팀 ErrorCode {len(p1_err)}개 / ReviewReason {len(p1_rev)}개")
    clash_e = set(pe.ERRORS) & set(p1_err)
    clash_r = set(pe.REVIEW_REASONS) & set(p1_rev)
    if clash_e or clash_r:
        print(f"  [X] 겹침  오류 {sorted(clash_e)}  검토사유 {sorted(clash_r)}")
        fails.append("번호 충돌")
    else:
        print(f"  [O] 안 겹침 — Phase 2 전용 오류 {len(pe.ERRORS)}개는 P2E, "
              f"검토사유 {len(pe.REVIEW_REASONS)}개는 P2R 접두사")

    # ── ② 재사용 선언이 실제로 있는가 ──────────────────────────────────
    print("\n── ② 재사용한다고 적은 Phase 1 코드가 실제로 있는가 ──")
    missing = [c for c in pe.REUSED_FROM_PHASE1 if c not in p1_err]
    wrong = [(c, n, p1_err.get(c)) for c, n in pe.REUSED_FROM_PHASE1.items()
             if c in p1_err and p1_err[c] != n]
    print(f"  재사용 선언 {len(pe.REUSED_FROM_PHASE1)}개")
    if missing:
        print(f"  [X] 판단팀에 없는 코드: {missing}")
        fails.append("없는 코드 재사용")
    elif wrong:
        print(f"  [X] 이름이 다름: {wrong}")
        fails.append("이름 불일치")
    else:
        print("  [O] 전부 실제로 있고 이름도 같음")

    # ── ③ phase2_판단 하네스가 쓰는 문자열 ────────────────────────────
    print("\n── ③ phase2_판단 하네스가 이미 쓰는 오류 문자열 ──")
    cb = pick(repo, "phase2_*", "src/context_builder.py")
    if cb:
        used = sorted(set(re.findall(r'Error\(\s*"([A-Z_]+)"', cb.read_text(encoding="utf-8"))))
        names = {v[0] for v in pe.ERRORS.values()}
        print(f"  하네스가 쓰는 값: {used}")
        covered = [u for u in used if u in names]
        print(f"  내 ERRORS 에 같은 이름이 있는 것: {covered or '-'}")
        print("  (나머지는 설정값 검증이라 Phase 2 결과 오류가 아님)")
    else:
        print("  ? context_builder.py 를 못 찾았습니다")

    # 재시도 정책이 공유되는지
    jh = pick(repo, "phase2_*", "src/judgment_harness.py")
    if jh:
        t = jh.read_text(encoding="utf-8")
        shared = "DEFAULT_RETRY_POLICY" in t and "ErrorCode" in t
        print(f"  하네스가 판단팀 RetryPolicy·ErrorCode 를 재사용: "
              f"{'[O] 그렇다' if shared else '[X] 아니다'}")
        if not shared:
            fails.append("하네스가 Phase 1 정책을 안 씀")

    # ── ④ 모든 오류가 분류돼 있는가 ───────────────────────────────────
    print("\n── ④ 모든 오류가 '검토' 또는 '기록만' 에 들어있는가 ──")
    allcodes = list(pe.ERRORS) + list(pe.REUSED_FROM_PHASE1)
    unclassified = [c for c in allcodes
                    if c not in pe.BLOCKING and c not in pe.RECORD_ONLY]
    print(f"  오류 {len(allcodes)}개 = 검토 {len(pe.BLOCKING)} + 기록만 {len(pe.RECORD_ONLY)}")
    if unclassified:
        print(f"  [X] 분류 안 된 것: {unclassified}")
        fails.append("미분류 오류")
    else:
        print("  [O] 전부 분류됨")
    # BLOCKING 이 가리키는 검토 사유가 실제로 있는가
    bad = [(k, v) for k, v in pe.BLOCKING.items() if v not in pe.REVIEW_REASONS]
    if bad:
        print(f"  [X] 없는 검토 사유를 가리킴: {bad}")
        fails.append("잘못된 검토 사유 참조")

    # ── ⑤ 실패 상황별 검토 판정 ───────────────────────────────────────
    print("\n── ⑤ 실패 상황별 검토 판정 ──")

    def it(iid, result, kind="procedure", reason="충분히 긴 판정 근거 문장입니다",
           critical=True, cites=None):
        return {"item_id": iid, "result": result, "check_kind": kind,
                "reason": reason, "critical": critical,
                "citations": cites or []}

    ok3 = [it("Q1", "MET"), it("Q2", "MET"), it("Q3", "MET")]
    cases = [
        ("정상 — 전부 충족", ok3, [], False, []),
        ("LLM 을 끝내 못 불렀다", [], ["E106"], True, ["P2R106", "P2R107"]),
        ("재시도까지 했는데 JSON 깨짐", [], ["E201"], True, ["P2R101", "P2R107"]),
        ("MET 인데 근거 없음", ok3, ["P2E501"], True, ["P2R105"]),
        ("인용이 청크 원문과 다름", ok3, ["E403"], True, ["P2R104"]),
        ("질문지에 없는 문항을 지어냄", ok3, ["P2E301"], True, ["P2R103"]),
        ("critical 문항 미충족",
         [it("Q1", "NOT_MET"), it("Q2", "MET"), it("Q3", "MET")], [], True, ["P2R201"]),
        ("절반 이상 UNKNOWN",
         [it("Q1", "UNKNOWN"), it("Q2", "UNKNOWN"), it("Q3", "MET")], [], True, ["P2R202"]),
        ("전부 UNKNOWN",
         [it("Q1", "UNKNOWN"), it("Q2", "UNKNOWN")], [], True, ["P2R204"]),
        ("OCR 청크를 인용",
         [it("Q1", "MET", cites=[{"chunk_id": "E1_v1_c0", "source": "ocr",
                                  "quote": "x"}])], [], True, ["P2R203"]),
        # 근거가 짧은 것은 검토로 보내지 않는다 (판단팀 E506 과 같은 취급). 기록만 한다.
        ("근거가 너무 짧음 (검토 아님)", [it("Q1", "MET", reason="맞음")], [], False, []),
        # 질문지·판정대상이 없는 것은 정상 결과 '증적 없음' 이다.
        # evidence_outcome() 이 review_required=False 를 내므로 여기도 False 여야 한다.
        # (전에는 True/P2R107 을 기대하게 돼 있어서 두 함수가 서로 다른 말을 했다.)
        ("질문지가 없어 판정 안 함 (검토 아님)", [], ["P2E002"], False, []),
        ("판정 대상이 없음 (검토 아님)", [], ["P2E001"], False, []),
        # 다른 이유로 문항이 비었으면 판정을 시도했다 못한 것이므로 검토다.
        ("청크가 없어 판정 못 함 (검토)", [], ["P2E003"], True, ["P2R107"]),
    ]
    for label, items, errors, want_req, want_codes in cases:
        r = pe.decide_review(items, errors)
        ok = r["required"] == want_req and all(c in r["reasons"] for c in want_codes)
        print(f"  [{'O' if ok else 'X'}] {label:32} 검토={str(r['required']):5} "
              f"{r['reasons'] or '-'}")
        if not ok:
            fails.append(label)

    # ── ⑥ 통제항목 전체의 결말 ────────────────────────────────────────
    print("\n── ⑥ 통제항목 전체의 결말 → 상태 ──")
    outcomes = [
        ("정상", ok3, []),
        ("critical 미충족 → 검토", [it("Q1", "NOT_MET")], []),
        ("입력이 규격 위반 → 실패", [], ["P2E005"]),
        ("배치 중단 → 실패", [], ["P2E901"]),
        ("판정 대상 없음 → 증적 없음 (실패 아님)", [], ["P2E001"]),
        ("질문지 없음 → 증적 없음 (실패 아님)", [], ["P2E002"]),
        ("문항 하나만 실패, 나머지는 됨",
         [pe.failed_item("Q1", "E106", "타임아웃"), it("Q2", "MET"), it("Q3", "MET")],
         ["E106"]),
    ]
    for label, items, errors in outcomes:
        o = pe.evidence_outcome(items, errors)
        status = st.from_phase2(o["processing_status"], o["review_required"],
                                phase1_review_open=False)
        print(f"  {label:36} → {status:16} {o['note']}")
        if status not in st.STATUSES:
            fails.append(label)

    # 핵심 규칙 확인 — 문항 하나 실패가 통제항목 실패가 되면 안 된다
    mixed = [pe.failed_item("Q1", "E106", "타임아웃"), it("Q2", "MET")]
    o = pe.evidence_outcome(mixed, ["E106"])
    if o["processing_status"] == "FAILED":
        print("  [X] 문항 하나 실패인데 통제항목이 FAILED 가 됐습니다")
        fails.append("문항 실패가 전체 실패로 번짐")
    else:
        print("  [O] 문항 하나가 실패해도 통제항목은 결과를 냅니다")
        f = mixed[0]
        print(f"      실패한 문항 → result={f['result']}  (NOT_MET 아님), "
              f"citations={f['citations']}")

    # ── ⑦ 재시도 비용 ────────────────────────────────────────────────
    print("\n── ⑦ 재시도 비용과 배치 중단 ──")
    print(f"  {pe.RETRY_NOTE}")
    items_n, per_call, timeout, retries = 647, 34, 60, 1
    print(f"\n  정상일 때   : {items_n}문항 × {per_call}초 = {items_n*per_call/3600:.1f}시간")
    worst = items_n * (1 + retries) * timeout
    print(f"  LLM 이 죽었을 때 (중단 없음) : "
          f"{items_n} × {1+retries}회 × {timeout}초 = {worst/3600:.1f}시간을 버림")
    with_abort = pe.BATCH_ABORT_AFTER * (1 + retries) * timeout
    print(f"  배치 중단 켜면 : 연속 {pe.BATCH_ABORT_AFTER}건에서 멈춤 = "
          f"{with_abort/60:.0f}분")
    print(f"  중단 조건 : {pe.BATCH_ABORT_ON} (서버가 죽은 신호일 때만)")

    print()
    if fails:
        print(f"실패 {len(fails)}건: {fails}")
        sys.exit(1)
    print("전부 통과.")


if __name__ == "__main__":
    main()
