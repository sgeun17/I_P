"""저장된 LLM 응답을 임계값만 바꿔 다시 판정한다.

### 왜 이 모듈이 있는가

임계값 하나 바꿀 때마다 LLM을 다시 돌리면 29건에 20분이 넘는다. 그런데 임계값은
판정 단계에서만 쓰이고 LLM 호출과는 전혀 무관하다. 응답을 한 번 저장해 두면
임계값 조합 여러 개를 몇 초 만에 비교할 수 있다.

    tools/threshold_scenario.py   임계값 조합별 검토율·유출을 표로 비교한다
    tools/why_caught.py           오답이 어떤 사유로 검토에 걸렸는지 본다

둘이 같은 불러오기 코드를 복사하고 있었다. 한쪽만 고치면 두 도구의 숫자가 조용히
달라지므로 여기로 합쳤다.

### 두 종류를 측정에서 뺀다 — 넣으면 숫자가 거짓이 된다

  retrieval_miss     정답이 후보 목록에 없던 케이스다. LLM이 맞힐 방법이 없으므로
                     판단 정확도에 넣으면 검색 성능을 판단 성능으로 착각하게 된다.
                     골든셋이 케이스마다 `gold.retrieval_miss`로 표시해 둔다.

  STALE_RESPONSES    후보 목록을 고친 뒤 저장된 응답이 무효가 된 케이스다. 모델이
                     본 적도 없는 후보를 골랐어야 한다고 채점하게 된다.
                     **그 모델로 다시 돌린 뒤에는 이 목록을 비운다.** 안 비우면
                     멀쩡한 케이스가 조용히 측정에서 빠진 채로 남는다.

제외한 건수는 두 도구가 항상 같이 출력한다. 모집단이 몇 건인지 모르고 보는
퍼센트는 아무 뜻이 없기 때문이다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import eval_goldenset as E  # noqa: E402
from eval_goldenset import STALE_RESPONSES  # noqa: E402  (목록의 원본은 그쪽이다)
from enums import ErrorCode  # noqa: E402
from models import MappingInput, VersionInfo  # noqa: E402
from review_policy import Thresholds  # noqa: E402
from service import build_result  # noqa: E402


# STALE_RESPONSES는 tools/eval_goldenset.py가 원본이다. 여기서 따로 들고 있으면
# 두 도구가 다른 모집단으로 세게 되고, 숫자가 어긋나도 아무도 모른다.


@dataclass(frozen=True)
class Replayed:
    """케이스 하나를 다시 판정한 결과."""

    test_id: str
    category: str
    case: dict
    result: object          # Phase1MappingResult
    gold: set[str]
    predicted: set[str]

    @property
    def correct(self) -> bool:
        """매핑이 정답과 정확히 일치하는가 (EM 기준).

        부분 일치를 정답으로 보지 않는다. 임계값을 고를 때는 이게 맞다 —
        "일부만 맞은 것"을 자동확정으로 넘기면 Phase 2가 빠진 항목을 영영
        검사하지 않기 때문이다. 부분 일치를 보려면 eval_goldenset.py의
        precision/recall을 쓴다.
        """
        return self.predicted == self.gold

    @property
    def review_required(self) -> bool:
        return self.result.human_review.required

    @property
    def reasons(self) -> list[str]:
        return [r.value for r in self.result.human_review.reasons]


@dataclass
class Population:
    """측정에 쓴 모집단과 뺀 것들. 퍼센트와 항상 같이 출력한다."""

    used: int = 0
    skipped_stale: int = 0
    skipped_retrieval_miss: int = 0
    missing_response: int = 0

    def summary(self) -> str:
        parts = [f"측정 {self.used}건"]
        if self.skipped_stale:
            parts.append(f"응답 무효 {self.skipped_stale}건 제외")
        if self.skipped_retrieval_miss:
            parts.append(f"검색 누락 {self.skipped_retrieval_miss}건 제외")
        if self.missing_response:
            parts.append(f"응답 없음 {self.missing_response}건")
        return " / ".join(parts)


def load_goldenset() -> dict:
    return json.loads(E.GOLDENSET_PATH.read_text(encoding="utf-8"))


def make_versions(run_path: Path, goldenset: dict) -> VersionInfo:
    """결과에 붙는 라벨.

    model_name·prompt_version은 판정에 쓰이지 않는다. 기록용 라벨이라서 응답
    파일 이름을 그대로 쓴다. 손으로 적으면 파일과 어긋나는 쪽이 더 위험하다.
    """
    return VersionInfo(
        prompt_version=run_path.stem,
        model_name=run_path.stem,
        ruleset_version=goldenset["ruleset_version"],
        kb_sha256=goldenset.get("kb_sha256"),
    )


def replay(
    run_path: Path,
    thresholds: Thresholds,
    *,
    goldenset: dict | None = None,
    skip: set[str] = STALE_RESPONSES,
) -> tuple[list[Replayed], Population]:
    """저장된 응답 전체를 주어진 임계값으로 다시 판정한다."""
    gs = goldenset if goldenset is not None else load_goldenset()
    rows = E.load_responses(run_path)
    versions = make_versions(run_path, gs)

    out: list[Replayed] = []
    pop = Population()

    for case in gs["cases"]:
        test_id = case["test_id"]

        if test_id in skip:
            pop.skipped_stale += 1
            continue
        if case["gold"].get("retrieval_miss"):
            pop.skipped_retrieval_miss += 1
            continue

        row = rows.get(test_id)
        if row is None:
            pop.missing_response += 1
            continue

        error_code = row.get("error_code")
        result = build_result(
            row.get("raw_response"),
            MappingInput.model_validate(case["input"]),
            versions,
            call_error=ErrorCode(error_code) if error_code else None,
            thresholds=thresholds,
        )

        out.append(Replayed(
            test_id=test_id,
            category=case["category"],
            case=case,
            result=result,
            gold={c["control_id"] for c in case["gold"]["controls"]},
            predicted={m.control_id for m in result.mapped_controls},
        ))
        pop.used += 1

    return out, pop


def run_path_from_argv(argv: list[str]) -> Path:
    """첫 번째 인자를 응답 파일 경로로 읽는다.

    경로를 소스에 박아두면 v0.6 프롬프트나 8b로 바꿀 때마다 도구를 고쳐야 한다.
    임계값을 YAML에서 코드로 옮긴 것과 같은 이유로, 측정 대상은 인자로 받는다.
    """
    if len(argv) < 2:
        runs = sorted((ROOT / "runs").glob("*.jsonl"))
        hint = "\n".join(f"  python {Path(argv[0]).name} runs/{p.name}" for p in runs)
        print(
            f"사용법: python tools/{Path(argv[0]).name} <응답 jsonl>\n"
            + (f"\n지금 있는 응답 파일:\n{hint}" if runs else ""),
            file=sys.stderr,
        )
        raise SystemExit(2)

    path = Path(argv[1])
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        print(f"응답 파일이 없다: {path}", file=sys.stderr)
        raise SystemExit(2)
    return path
