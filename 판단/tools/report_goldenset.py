"""골든셋을 돌려 검토 전환율을 본다.

    python tools/report_goldenset.py

임계값을 바꾸기 전에 이걸 돌려서 **검토율이 어떻게 변하는지 먼저 본다.**
숫자 없이 임계값을 고치면 근거가 없다.

주의: 여기 나오는 것은 LLM 정확도가 아니다. 정답대로 답했을 때
우리 정책이 몇 건을 사람에게 넘기는지다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from models import MappingInput, VersionInfo  # noqa: E402
from review_policy import DEFAULT_THRESHOLDS  # noqa: E402
from service import build_result  # noqa: E402

GOLDENSET = json.loads((ROOT / "tests" / "fixtures" / "goldenset.json").read_text(encoding="utf-8"))
CASES = GOLDENSET["cases"]
VERSIONS = VersionInfo(prompt_version="p", model_name="goldenset-ideal", ruleset_version="r")


def run_all(thresholds):
    rows = []
    for case in CASES:
        mapping_input = MappingInput.model_validate(case["input"])
        result = build_result(case["llm_response"], mapping_input, VERSIONS, thresholds=thresholds)
        scores = sorted((c.similarity_score for c in mapping_input.candidate_controls), reverse=True)
        rows.append(
            {
                "test_id": case["test_id"],
                "category": case["category"],
                "gap": scores[0] - scores[1] if len(scores) > 1 else None,
                "required": result.human_review.required,
                "reasons": sorted(r.value for r in result.human_review.reasons),
                "validation_passed": result.validation.passed,
            }
        )
    return rows


def main() -> None:
    rows = run_all(DEFAULT_THRESHOLDS)

    print(f"골든셋 {GOLDENSET['version']} — {len(rows)}건")
    print(f"임계값 프로필: {DEFAULT_THRESHOLDS.profile_name}\n")

    print(f"{'사례':18} {'1·2위차':>8}  {'검토':4} 사유")
    print("-" * 78)
    for r in rows:
        gap = f"{r['gap']:.4f}" if r["gap"] is not None else "—"
        mark = "검토" if r["required"] else "확정"
        print(f"{r['test_id']:18} {gap:>8}  {mark:4} {', '.join(r['reasons']) or '—'}")

    required = sum(1 for r in rows if r["required"])
    print(f"\n검토 전환율: {required}/{len(rows)} = {required / len(rows):.0%}")

    print("\n사유별 발생 횟수")
    counter = Counter(reason for r in rows for reason in r["reasons"])
    for reason, n in counter.most_common():
        alone = sum(1 for r in rows if r["reasons"] == [reason])
        note = "  ← 단독으로 결정한 적 없음" if alone == 0 else f"  (단독 {alone}건)"
        print(f"  {reason}  {n:2}건{note}")

    print("\n임계값을 바꾸면 검토율이 어떻게 되는가")
    scenarios = {
        f"현재 ({DEFAULT_THRESHOLDS.profile_name})": DEFAULT_THRESHOLDS,
        "NO_MATCH 검토 끔": replace(DEFAULT_THRESHOLDS, review_on_no_match=False),
        "1:N 검토 끔": replace(DEFAULT_THRESHOLDS, review_on_multi_mapping=False),
        "둘 다 끔": replace(DEFAULT_THRESHOLDS, review_on_no_match=False, review_on_multi_mapping=False),
        "위 + 점수차 조건 끔": replace(
            DEFAULT_THRESHOLDS, review_on_no_match=False, review_on_multi_mapping=False,
            narrow_score_gap=0.0,
        ),
    }
    for name, thresholds in scenarios.items():
        result_rows = run_all(thresholds)
        n = sum(1 for r in result_rows if r["required"])
        print(f"  {name:24} {n:2}/{len(result_rows)} = {n / len(result_rows):3.0%}")

    print("\n1·2위 점수차 임계값(narrow_score_gap)만 바꿔보면")
    for value in (0.0, 0.005, 0.01, 0.02, 0.03, 0.05):
        result_rows = run_all(replace(DEFAULT_THRESHOLDS, narrow_score_gap=value))
        n = sum(1 for r in result_rows if r["required"])
        fired = sum(1 for r in result_rows if "R202" in r["reasons"])
        alone = sum(1 for r in result_rows if r["reasons"] == ["R202"])
        print(f"  {value:.3f}  검토 {n:2}/{len(result_rows)} = {n / len(result_rows):3.0%}"
              f"   R202 발동 {fired:2}건 (단독 {alone}건)")

    print(
        "\n주의 1: 이 숫자는 정답대로 답했을 때의 검토율이다.\n"
        "        실제 LLM은 틀리기도 하므로 실제 검토율은 이보다 높다.\n"
        "주의 2: **골든셋의 similarity_score는 사람이 지어낸 값이다.**\n"
        "        실측 1건(1·2위 차 0.0553)에 맞춰 분포를 흉내 냈을 뿐이다.\n"
        "        임계값을 확정하려면 검색팀이 이 29건의 증적 본문으로\n"
        "        실제 Retriever를 돌려 진짜 점수를 뽑아줘야 한다."
    )


if __name__ == "__main__":
    main()
