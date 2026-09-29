"""Ollama에 실제 골든셋 입력을 보내 raw response JSONL을 만든다.

기본 사용:
    # 환경변수 예시는 configs/llm.env.example 참고
    set LLM_MODEL=qwen3:8b-q4_K_M        # Windows cmd
    python tools/run_ollama_goldenset.py --smoke

전체 29건:
    python tools/run_ollama_goldenset.py --out runs/qwen3-8b-q4.jsonl

평가:
    python tools/eval_goldenset.py \
        --responses runs/qwen3-8b-q4.jsonl \
        --model qwen3:8b-q4_K_M \
        --prompt phase1_mapping_v0.4

이 스크립트는 실제 기업 증적이 아닌 저장소의 합성 골든셋만 사용한다.
"""
from __future__ import annotations

import argparse
import json
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="Ollama model identifier. 없으면 LLM_MODEL 사용")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "ollama_responses.jsonl")
    parser.add_argument("--smoke", action="store_true", help="1:1, 1:N, NO_MATCH 대표 3건만 실행")
    parser.add_argument("--test-id", action="append", default=[], help="특정 test_id만 실행 (반복 가능)")
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
    rows: list[dict] = []

    print(f"Ollama endpoint : {client_config.url}")
    print(f"Model           : {model}")
    print(f"Prompt          : {PROMPT_VERSION}")
    print(f"Cases           : {len(cases)}")
    print(f"Thinking        : {generation.thinking} ({generation.reasoning_effort})")
    print(f"Max tokens      : {generation.max_tokens}")
    print()

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
            # 4xx는 모델명/endpoint/body 등 실행 계약 문제일 가능성이 높다.
            # 공통 ErrorCode가 합의되기 전에는 데이터셋 전체를 잘못된 코드로 기록하지 않고 중단한다.
            raise SystemExit(
                f"{case['test_id']}: HTTP {run.request_error_status} 요청 오류 — "
                f"{run.request_error_message}"
            )

        row = {
            "test_id": case["test_id"],
            "processing_time_ms": elapsed_ms,
            "attempts": run.attempts,
            "retry_count": run.retry_count,
        }
        if run.call_error is not None:
            row["error_code"] = run.call_error.value
        else:
            row["raw_response"] = run.raw_response
        rows.append(row)

        status = run.call_error.value if run.call_error else "OK"
        print(
            f"[{index:02d}/{len(cases):02d}] {case['test_id']:<18} "
            f"{status:<5} {elapsed_ms:>6}ms retries={run.retry_count}"
        )

    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
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
