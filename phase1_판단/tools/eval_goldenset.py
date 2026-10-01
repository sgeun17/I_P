"""골든셋으로 판단 정확도를 잰다.

    # 1) 자기 점검 — 골든셋에 든 응답을 그대로 넣는다 (LLM 없이)
    python tools/eval_goldenset.py --self-test

    # 2) 실제 LLM 결과로 평가
    python tools/eval_goldenset.py --responses runs/qwen_v0.1.jsonl \\
        --model qwen2.5-14b-instruct --prompt phase1_mapping_v0.1

응답 파일 형식 (JSONL, 한 줄에 한 건)

    {"test_id": "G-SINGLE-01", "raw_response": "{...LLM이 뱉은 문자열 그대로...}"}
    {"test_id": "G-SINGLE-02", "raw_response": "...", "processing_time_ms": 4210}
    {"test_id": "G-MULTI-01",  "error_code": "E101"}      호출 자체가 실패한 경우

**LLM 응답은 가공하지 말고 그대로 넣는다.** 코드블록이든 앞뒤 설명이든
우리 파서가 처리한다. 미리 다듬으면 실제 파싱 실패율을 못 잰다.

`--out`으로 저장하는 결과에는 숫자만 넣지 않는다. 어느 응답 파일, 어느
골든셋(해시), 어느 임계값 프로파일로 잰 숫자인지 같이 적는다. 그게 없으면
나중에 두 측정을 비교할 때 무엇이 달라서 숫자가 달라졌는지 알 수 없다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from enums import ErrorCode  # noqa: E402
from metrics import compute, format_report, make_outcome  # noqa: E402
from models import MappingInput, VersionInfo  # noqa: E402
from review_policy import DEFAULT_THRESHOLDS  # noqa: E402
from service import build_result  # noqa: E402

GOLDENSET_PATH = ROOT / "tests" / "fixtures" / "goldenset.json"


def sha256_file(path: Path) -> str:
    """파일 내용의 해시. 어느 골든셋으로 잰 숫자인지 결과에 남기려고 쓴다.

    골든셋 라벨을 고쳐도 `version` 문자열("goldenset_v0.1")은 그대로 남는다.
    실제로 라벨 3건을 고친 뒤, 저장된 측정 결과만 보고는 수정 전인지 후인지
    구분할 수 없었다. 버전 문자열을 올리는 건 사람이 잊을 수 있지만 해시는
    잊을 수가 없다. `kb_sha256`을 쓰는 것과 같은 이유다.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


# 저장된 응답이 무효가 된 사례. 실제 LLM 응답으로 잴 때만 제외한다.
#
# 케이스의 후보 목록을 고치면, 그 전에 저장해 둔 응답은 "모델이 본 적도 없는
# 후보를 골랐어야 한다"고 채점하게 된다. 모델 잘못이 아닌 것을 오답으로 세면
# 정확도가 실제보다 낮게 나온다.
#
# **해당 모델로 다시 돌린 뒤에는 이 목록을 비운다.** 안 비우면 멀쩡한 사례가
# 조용히 측정에서 빠진 채로 남는다.
#
#   G-OCR-01: 후보 목록을 2.4.2 중심으로 다시 짰다(커밋 070f126). 그 전에 저장된
#             응답에는 2.4.2가 후보에 아예 없었다.
#
# `--self-test`에는 적용하지 않는다. 자기 점검은 골든셋이 들고 있는 응답을 쓰므로
# 후보를 고치면 응답도 같이 바뀌어 어긋날 일이 없다.
STALE_RESPONSES = {
    "G-OCR-01",
}


def recorded_path(path: Path | None) -> str | None:
    """결과 파일에 재현 가능한 프로젝트 상대 경로를 기록한다."""
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_responses(path: Path) -> dict[str, dict]:
    responses: dict[str, dict] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no} JSON이 아닙니다: {exc}") from exc
        if "test_id" not in row:
            raise SystemExit(f"{path}:{line_no} test_id가 없습니다")
        responses[row["test_id"]] = row
    return responses


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--responses", type=Path, help="LLM 응답 JSONL")
    parser.add_argument("--self-test", action="store_true", help="골든셋에 든 응답으로 자기 점검")
    parser.add_argument("--model", default="unknown", help="모델 이름 (결과에 기록)")
    parser.add_argument("--prompt", default="unknown", help="프롬프트 버전 (결과에 기록)")
    parser.add_argument("--include-retrieval-miss", action="store_true",
                        help="정답이 후보 밖인 사례도 분모에 포함")
    parser.add_argument("--include-stale", action="store_true",
                        help=f"응답이 무효가 된 사례도 분모에 포함 ({', '.join(sorted(STALE_RESPONSES))})")
    parser.add_argument("--out", type=Path, help="결과를 JSON으로 저장할 경로")
    args = parser.parse_args()

    if not args.responses and not args.self_test:
        parser.error("--responses 또는 --self-test 중 하나가 필요합니다")

    goldenset = json.loads(GOLDENSET_PATH.read_text(encoding="utf-8"))
    cases = goldenset["cases"]

    # 판정에 쓰는 임계값을 변수로 묶어 둔다. 결과에 기록하는 프로파일 이름과
    # 실제로 판정에 쓴 값이 어긋나는 것을 막으려는 것이다.
    thresholds = DEFAULT_THRESHOLDS
    skip_stale = set() if (args.self_test or args.include_stale) else STALE_RESPONSES

    responses = load_responses(args.responses) if args.responses else {}
    versions = VersionInfo(
        prompt_version=args.prompt,
        model_name=args.model,
        ruleset_version=goldenset["ruleset_version"],
        kb_sha256=goldenset.get("kb_sha256"),
    )

    outcomes = []
    missing: list[str] = []
    stale: list[str] = []

    for case in cases:
        if args.self_test:
            row = {"raw_response": case["llm_response"]}
        else:
            if case["test_id"] in skip_stale:
                stale.append(case["test_id"])
                continue
            row = responses.get(case["test_id"])
            if row is None:
                missing.append(case["test_id"])
                continue

        mapping_input = MappingInput.model_validate(case["input"])
        error_code = row.get("error_code")
        result = build_result(
            row.get("raw_response"),
            mapping_input,
            versions,
            call_error=ErrorCode(error_code) if error_code else None,
            thresholds=thresholds,
            processing_time_ms=row.get("processing_time_ms"),
        )
        outcomes.append(make_outcome(case, result))

    if missing:
        print(f"경고: 응답이 없는 사례 {len(missing)}건 — {', '.join(missing)}\n", file=sys.stderr)
    if stale:
        print(f"제외: 저장된 응답이 무효가 된 사례 {len(stale)}건 — {', '.join(stale)}\n"
              f"      (후보 목록을 고친 뒤라 모델이 정답을 볼 수 없었다. "
              f"다시 돌리면 STALE_RESPONSES를 비운다. 포함하려면 --include-stale)\n",
              file=sys.stderr)

    metrics = compute(outcomes, exclude_retrieval_miss=not args.include_retrieval_miss)

    title = (f"Phase 1 판단 평가 — {args.model} / {args.prompt}"
             f" / {thresholds.profile_name}")
    print(format_report(metrics, title))

    if args.self_test:
        print(
            "\n"
            "※ --self-test는 골든셋에 든 응답을 그대로 넣은 것이다.\n"
            "  ideal 23건은 정답 그대로이므로 높게 나오는 게 정상이고,\n"
            "  faulty 6건은 일부러 틀린 응답이라 낮게 나오는 게 정상이다.\n"
            "  **이 숫자는 LLM 정확도가 아니다.** 지표 계산이 맞게 도는지 확인하는 용도다."
        )

    # 틀린 사례를 이름으로 보여준다. 숫자만 보면 무엇을 고쳐야 할지 모른다.
    wrong = [o for o in outcomes if not o.exact_match]
    if wrong:
        print(f"\n## 정답과 다른 사례 {len(wrong)}건\n")
        for o in wrong:
            miss = " [검색 Recall 실패]" if o.gold.retrieval_miss else ""
            print(f"  {o.test_id:18} 정답 {sorted(o.gold.control_ids) or '없음'}"
                  f" → 예측 {sorted(o.predicted_ids) or '없음'}{miss}")

    if args.out:
        payload = {
            "model": args.model,
            "prompt_version": args.prompt,
            "threshold_profile": thresholds.profile_name,
            "response_file": recorded_path(args.responses),
            "goldenset_version": goldenset["version"],
            "goldenset_sha256": sha256_file(GOLDENSET_PATH),
            "ruleset_version": goldenset["ruleset_version"],
            "excluded_stale": sorted(stale),
            "metrics": {k: v for k, v in metrics.__dict__.items()},
            "cases": [
                {
                    "test_id": o.test_id,
                    "category": o.category,
                    "gold": sorted(o.gold.control_ids),
                    "predicted": sorted(o.predicted_ids),
                    "exact_match": o.exact_match,
                    "review_required": o.review_required,
                    "processing_status": o.processing_status,
                }
                for o in outcomes
            ],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n결과 저장: {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
