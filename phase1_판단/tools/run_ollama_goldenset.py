"""Ollama에 실제 골든셋 입력을 보내 raw response JSONL을 만든다.

기본 사용:
    # 환경변수 예시는 configs/llm.env.example 참고
    set LLM_MODEL=qwen3:8b        # Windows cmd
    python tools/run_ollama_goldenset.py --smoke

전체:
    python tools/run_ollama_goldenset.py --out runs/qwen3-8b-v07.jsonl

중단 후 이어서:
    python tools/run_ollama_goldenset.py --out runs/qwen3-8b-v07.jsonl --resume

평가:
    python tools/eval_goldenset.py \
        --responses runs/qwen3-8b-v07.jsonl \
        --model qwen3:8b \
        --prompt phase1_mapping_v0.7

이 스크립트는 실제 기업 증적이 아닌 저장소의 합성 골든셋만 사용한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_config import (  # noqa: E402
    GenerationConfig,
    LLMClientConfig,
    LLMConfigError,
    get_model_name_from_env,
)
from llm_runner import run_mapping_llm  # noqa: E402
from models import MappingInput  # noqa: E402
from prompts import PROMPT_VERSION  # noqa: E402

GOLDENSET_PATH = ROOT / "tests" / "fixtures" / "goldenset.json"
SMOKE_IDS = {"G-SINGLE-01", "G-MULTI-01", "G-NOMATCH-01"}


def _load_resume_rows(path: Path, *, model: str, prompt_version: str) -> tuple[list[dict], set[str]]:
    """기존 JSONL을 읽고 완료된 test_id를 반환한다.

    절전/강제 종료로 마지막 줄만 반쯤 기록된 경우에는 그 마지막 줄만 버리고
    정상 행으로 파일을 복구한다. 중간 줄이 깨졌다면 결과 파일 자체가 손상된
    것이므로 조용히 넘어가지 않고 오류를 낸다.
    """
    if not path.exists():
        return [], set()

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict] = []
    malformed_last = False

    for index, line in enumerate(raw_lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            is_last_nonempty = not any(x.strip() for x in raw_lines[index + 1 :])
            if not is_last_nonempty:
                raise SystemExit(
                    f"--resume 실패: {path}의 {index + 1}번째 줄이 손상됨"
                ) from exc
            malformed_last = True
            break

        if not isinstance(row, dict) or not isinstance(row.get("test_id"), str):
            raise SystemExit(
                f"--resume 실패: {path}의 {index + 1}번째 줄에 test_id가 없음"
            )
        if row.get("model") != model or row.get("prompt_version") != prompt_version:
            raise SystemExit(
                "--resume 실패: 기존 결과의 model/prompt_version이 현재 실행과 다름. "
                "v0.6 재평가는 새 --out 파일로 시작하세요."
            )
        rows.append(row)

    if malformed_last:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        print(f"경고: 중단 중 깨진 마지막 JSONL 행을 제거하고 {path}를 복구했습니다.")

    return rows, {row["test_id"] for row in rows}


def _append_row(path: Path, row: dict) -> None:
    """한 건이 끝날 때마다 즉시 디스크에 기록한다."""
    with path.open("a", encoding="utf-8", newline="\n") as fp:
        fp.write(json.dumps(row, ensure_ascii=False) + "\n")
        fp.flush()
        os.fsync(fp.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", help="Ollama model identifier. 없으면 LLM_MODEL 사용")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "runs" / "ollama_responses.jsonl",
    )
    parser.add_argument("--smoke", action="store_true", help="1:1, 1:N, NO_MATCH 대표 3건만 실행")
    parser.add_argument("--test-id", action="append", default=[], help="특정 test_id만 실행 (반복 가능)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="기존 JSONL의 완료 test_id를 건너뛰고 이어서 실행",
    )
    args = parser.parse_args()

    try:
        model = args.model or get_model_name_from_env(required=True)
        client_config = LLMClientConfig.from_env()
        generation = GenerationConfig.from_env()
    except LLMConfigError as exc:
        raise SystemExit(f"LLM 설정 오류: {exc}") from exc

    goldenset = json.loads(GOLDENSET_PATH.read_text(encoding="utf-8"))
    cases = goldenset["cases"]

    selected_ids = set(args.test_id)
    if args.smoke:
        selected_ids |= SMOKE_IDS
    if selected_ids:
        cases = [case for case in cases if case["test_id"] in selected_ids]
        missing = selected_ids - {case["test_id"] for case in cases}
        if missing:
            raise SystemExit("없는 test_id: " + ", ".join(sorted(missing)))

    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.resume:
        _, completed_ids = _load_resume_rows(
            args.out,
            model=model,
            prompt_version=PROMPT_VERSION,
        )
        before = len(cases)
        cases = [case for case in cases if case["test_id"] not in completed_ids]
        skipped = before - len(cases)
        print(f"Resume          : ON (기존 완료 {skipped}건 건너뜀)")
    else:
        # 새 실행은 이전 결과와 섞이지 않도록 시작 전에 한 번만 비운다.
        args.out.write_text("", encoding="utf-8")
        print("Resume          : OFF (출력 파일 새로 시작)")

    print(f"Ollama endpoint : {client_config.url}")
    print(f"Model           : {model}")
    print(f"Prompt          : {PROMPT_VERSION}")
    print(f"Cases           : {len(cases)}")
    print(f"Thinking        : {generation.thinking} ({generation.reasoning_effort})")
    print(f"Max tokens      : {generation.max_tokens}")
    print()

    if not cases:
        print("실행할 미완료 케이스가 없습니다.")
        print(f"응답 저장: {args.out}")
        return 0

    total = len(cases)
    for index, case in enumerate(cases, 1):
        mapping_input = MappingInput.model_validate(case["input"])
        started = time.perf_counter()
        run = run_mapping_llm(
            mapping_input,
            model=model,
            client_config=client_config,
            generation=generation,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        if run.has_unmapped_request_error:
            # 앞선 성공 건들은 이미 JSONL에 저장되어 있으므로 이후 --resume 가능하다.
            raise SystemExit(
                f"{case['test_id']}: HTTP {run.request_error_status} 요청 오류 — "
                f"{run.request_error_message}"
            )

        row = {
            "test_id": case["test_id"],
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "processing_time_ms": elapsed_ms,
            "attempts": run.attempts,
            "retry_count": run.retry_count,
        }
        if run.call_error is not None:
            row["error_code"] = run.call_error.value
        else:
            # raw_response는 match_status가 런타임에서 채워진 평가용 canonical 응답이다.
            # provider_raw_response는 모델이 실제로 반환한 content를 그대로 보존한다.
            row["raw_response"] = run.raw_response
            row["provider_raw_response"] = run.provider_raw_response

        # 각 케이스 직후 즉시 저장한다. 절전/중단돼도 앞선 결과는 남는다.
        _append_row(args.out, row)

        status = run.call_error.value if run.call_error else "OK"
        print(
            f"[{index:02d}/{total:02d}] {case['test_id']:<18} "
            f"{status:<5} {elapsed_ms:>6}ms retries={run.retry_count}"
        )

    print(f"\n응답 저장: {args.out}")
    print(
        "평가 예시:\n"
        f"  python tools/eval_goldenset.py --responses \"{args.out}\" "
        f"--model \"{model}\" --prompt \"{PROMPT_VERSION}\""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
