"""임계값 조합을 바꿔 검토율과 유출을 비교한다.

    python tools/threshold_scenario.py runs/qwen3-4b-v05.jsonl

### 이 표로 뭘 정하는가

임계값은 "검토율을 낮추는 것"이 목표가 아니다. 먼저 **유출 0건**을 제약으로
박고, 그 안에서 검토율을 최소화한다. 순서를 바꾸면 검토율은 예쁘지만 틀린
매핑이 Phase 2로 넘어간다.

열의 뜻:

  검토        사람이 봐야 하는 건수. 검토율 상한은 60% (human_review_policy 참고)
  자동확정    검토 없이 Phase 2로 넘어가는 건수
  유출        **오답인데 자동확정된 건수. 이게 0이 아닌 설정은 쓰지 않는다.**
  검토중오답  오답이지만 검토에 걸린 건수. 사람이 고칠 기회가 있으므로 유출이 아니다

### 읽을 때 주의할 것

검토율도 유출도 같은 설정이 둘 나오면, 그 둘은 **지금 이 응답에서만** 같다.
어느 사유가 오답을 잡고 있는지는 tools/why_caught.py로 따로 봐야 한다.

실제로 겪은 일: low_confidence 0.90은 이 표에서 아무것도 더 잡지 못하는 것처럼
보였다. 그런데 오답을 잡고 있던 E505·E401이 프롬프트 수정으로 사라지면 방어선을
이어받는 값이었다. 표만 보고 "쓸모없다"고 지우면 안 된다.

정답 기준은 EM(정확히 일치)이다. 이유는 replay.Replayed.correct 참고.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay import load_goldenset, replay, run_path_from_argv  # noqa: E402
from review_policy import DEFAULT_THRESHOLDS, Thresholds  # noqa: E402

# 비교할 설정. 현재 확정값(DEFAULT_THRESHOLDS)을 맨 위에 두고, 하나씩만 되돌려
# 무엇이 어떤 효과를 내는지 분리해서 본다. 두 개를 동시에 바꾼 줄은 그 둘의
# 상호작용을 보려는 것이다.
CONFIGS: list[tuple[str, Thresholds]] = [
    ("현재 (thresholds.yaml)", DEFAULT_THRESHOLDS),
    ("R204 ON으로 되돌림", Thresholds(review_on_no_match=True)),
    ("low_conf 0.70으로 되돌림", Thresholds(low_confidence=0.70)),
    ("둘 다 되돌림 (v0.4)", Thresholds(low_confidence=0.70, review_on_no_match=True)),
    ("+ R205(1:N)까지 끔", Thresholds(review_on_multi_mapping=False)),
]


def main() -> int:
    run_path = run_path_from_argv(sys.argv)
    goldenset = load_goldenset()

    print(f"응답: {run_path.name}")
    print(f"골든셋: {goldenset['version']} / {goldenset['ruleset_version']}\n")

    print(f"{'설정':28} {'검토':>12} {'자동확정':>9} {'유출':>6} {'검토중오답':>11}")
    print("-" * 74)

    population = None
    for name, thresholds in CONFIGS:
        rows, population = replay(run_path, thresholds, goldenset=goldenset)
        n = len(rows)
        if n == 0:
            print("측정할 응답이 없다.", file=sys.stderr)
            return 1

        review = [r for r in rows if r.review_required]
        auto = [r for r in rows if not r.review_required]
        leak = [r for r in auto if not r.correct]
        caught = [r for r in review if not r.correct]

        flag = "  <- 유출" if leak else ""
        print(f"{name:28} {len(review):3d}/{n:<3d}({len(review) / n * 100:3.0f}%) "
              f"{len(auto):9d} {len(leak):6d} {len(caught):11d}{flag}")

    print("-" * 74)
    print(population.summary())
    print(f"임계값 프로파일: {DEFAULT_THRESHOLDS.profile_name}")
    print("\n방어선이 어디에 몰려 있는지는 tools/why_caught.py로 같이 본다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
