"""자운 담당 항목별 LLM 하네스를 Ollama/Qwen3 환경으로 실행한다.

최종 Validator/Self-check/Human Review까지 실행하는 도구가 아니다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / "src")]

from checklist_adapter import compact_reason_codes, get_checklist_item, load_reason_code_catalog
from judgment_harness import run_item_judgment
from runtime_config import runtime_profile_from_env


def _read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--context", required=True, help="context_builder 결과 JSON")
    parser.add_argument("--model", help="Qwen3 Ollama tag. 생략하면 LLM_MODEL")
    parser.add_argument("--allow-draft", action="store_true")
    args = parser.parse_args()

    runtime = runtime_profile_from_env(model_override=args.model)
    item = get_checklist_item(args.item_id, allow_draft=args.allow_draft)
    reasons = compact_reason_codes(load_reason_code_catalog(allow_draft=args.allow_draft))
    result = run_item_judgment(
        item,
        _read(args.context),
        model=runtime.model,
        reason_codes=reasons,
    )
    print(json.dumps({
        "runtime": runtime.audit_dict(),
        "succeeded": result.succeeded,
        "attempts": result.attempts,
        "retry_count": result.retry_count,
        "final_error_code": result.final_error_code,
        "validation_messages": list(result.validation_messages),
        "output": result.parsed_output,
    }, ensure_ascii=False, indent=2))
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
