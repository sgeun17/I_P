"""오답이 어떤 사유로 검토에 걸렸는지, 그 사유가 사라지면 뭐가 남는지 본다.

    python tools/why_caught.py runs/qwen3-4b-v05.jsonl

### 왜 이걸 따로 봐야 하는가

tools/threshold_scenario.py는 "유출 0건"만 보여준다. 그런데 **무엇이 막아주고
있는지**는 안 보여준다. 둘은 다른 질문이다.

실제로 겪은 일: 오답 5건이 전부 검토에 걸려 있었는데, 걸린 사유가 E505(적정성
판정)와 E401(인용 누락)이었다. 둘 다 LLM 프롬프트 문제라 하네스팀이 고치는
중이었다. 즉 **고치면 사라지는 방어선**이었고, 사라진 뒤에는 유출이 생길
상황이었다. 표만 보고 있었으면 못 봤다.

그래서 임계값을 바꾸기 전에 항상 이걸 같이 돌린다.

### 두 번째 표 — "사라진 뒤에 남는 방어선"

프롬프트를 고치면 없어질 오류 코드(PROMPT_FIXABLE_CODES)를 뺀 뒤, 각 오답에
어떤 사유가 남는지 다시 센다. 남는 사유가 **하나뿐인 케이스**가 다음에 유출될
후보다. 거기에 걸린 임계값이 유일한 방어선이기 때문이다.

이 목록은 판단이다. 측정값이 아니다. 하네스팀이 실제로 고친 걸 확인하면 그
코드를 목록에서 빼고 다시 돌린다. 안 빼면 이미 사라진 방어선을 계속 "사라질
것"으로 세게 되고, 표가 실제보다 비관적으로 나온다.

계산은 근사다. 사유를 다시 판정하는 게 아니라, 그 사유를 만든 오류 코드가
전부 '고쳐질 것' 목록에 있으면 그 사유를 뺀다. 같은 사유를 다른 오류 코드가
같이 만들고 있으면 남는다.

정답 기준은 EM(정확히 일치)이다. 부분 일치도 오답으로 본다 — 빠진 항목은
Phase 2가 영영 검사하지 않기 때문이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay import load_goldenset, replay, run_path_from_argv  # noqa: E402
from review_policy import BLOCKING_ERROR_CODES, DEFAULT_THRESHOLDS  # noqa: E402

# 프롬프트를 고치면 안 나올 오류 코드. 고쳐진 걸 확인하면 여기서 뺀다.
#
#   E505  적정성 판정 — 모델이 "적절히 수행하고 있다" 같은 판정을 내놓는다.
#         Phase 1은 매핑만 한다. 프롬프트가 역할을 분명히 하면 사라진다.
#   E401  인용 누락 — 매핑은 했는데 근거 인용이 없다. 인용을 필수로 못 박으면
#         사라진다. (인용 형식 문제는 이미 고쳐져서 유효율 16.1% -> 89.3%)
PROMPT_FIXABLE_CODES = {"E505", "E401"}


def surviving_reasons(replayed) -> list[str]:
    """프롬프트가 고쳐진 뒤에도 남을 검토 사유."""
    issues = replayed.result.validation.issues
    fixable, other = set(), set()
    for issue in issues:
        reason = BLOCKING_ERROR_CODES.get(issue.code)
        if reason is None:
            continue
        (fixable if issue.code.value in PROMPT_FIXABLE_CODES else other).add(reason.value)
    return [r for r in replayed.reasons if r not in fixable or r in other]


def main() -> int:
    run_path = run_path_from_argv(sys.argv)
    goldenset = load_goldenset()
    rows, population = replay(run_path, DEFAULT_THRESHOLDS, goldenset=goldenset)

    wrong = [r for r in rows if not r.correct]

    print(f"응답: {run_path.name} / 임계값: {DEFAULT_THRESHOLDS.profile_name}")
    print(f"{population.summary()} / 오답 {len(wrong)}건\n")

    if not wrong:
        print("오답이 없다. 임계값이 뭘 막고 있는지 볼 것도 없다.")
        return 0

    leaked = []
    for r in sorted(wrong, key=lambda r: r.test_id):
        codes = [i.code.value for i in r.result.validation.issues]
        print(f"  {r.test_id:18} {r.category}")
        print(f"    정답 {sorted(r.gold) or '없음'} / 예측 {sorted(r.predicted) or '없음'}")

        if not r.review_required:
            print("    검토 안 걸림  <- 유출. 이 오답은 Phase 2로 그대로 넘어간다")
            leaked.append(r)
        else:
            alone = "  (유일한 방어선)" if len(r.reasons) == 1 else ""
            print(f"    검토 사유 {r.reasons}{alone}")
        if codes:
            print(f"    오류 코드 {codes}")
        print()

    print("오답을 막고 있는 사유 (중복 포함):")
    tally: dict[str, int] = {}
    for r in wrong:
        for reason in r.reasons:
            tally[reason] = tally.get(reason, 0) + 1
    for reason, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {reason}  {count}건")

    # --- 프롬프트가 고쳐진 뒤 ---------------------------------------------
    print(f"\n{'=' * 70}")
    print(f"프롬프트 수정으로 {sorted(PROMPT_FIXABLE_CODES)}가 사라진 뒤 남는 방어선")
    print("=" * 70)

    thin, future_leak = [], []
    for r in sorted(wrong, key=lambda r: r.test_id):
        left = surviving_reasons(r)
        if not left:
            mark, note = "  <- 유출", "막는 게 아무것도 없다"
            future_leak.append(r)
        elif len(left) == 1:
            mark, note = "  <- 얇다", f"{left[0]} 하나가 유일한 방어선"
            thin.append((r, left[0]))
        else:
            mark, note = "", ""
        print(f"  {r.test_id:18} {left or '없음'}{mark}")
        if note:
            print(f"    {note}")

    if future_leak:
        print(f"\n유출 예정 {len(future_leak)}건 — 프롬프트 수정 전에 임계값을 먼저 올려야 한다: "
              f"{', '.join(r.test_id for r in future_leak)}")
    if thin:
        print("\n방어선이 하나뿐인 케이스 — 프롬프트 수정이 이 값까지 건드리는지 확인한다:")
        for r, reason in thin:
            confidences = [round(m.llm_confidence, 2) for m in r.result.mapped_controls]
            print(f"  {r.test_id:18} {reason}  (confidence {confidences or '없음'}, "
                  f"임계값 {DEFAULT_THRESHOLDS.low_confidence})")

    if leaked:
        print(f"\n지금 당장 유출 {len(leaked)}건 — 이 임계값은 쓰면 안 된다: "
              f"{', '.join(r.test_id for r in leaked)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
