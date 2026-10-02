"""Check the local Phase 2 Ollama/Qwen3 serving environment without running a judgment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / "src")]

from ollama_preflight import inspect_ollama_runtime
from phase1_runtime import LLMClientConfig
from runtime_config import runtime_profile_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Ollama Qwen3 tag. 생략하면 LLM_MODEL 사용")
    args = parser.parse_args()

    profile = runtime_profile_from_env(model_override=args.model)
    report = inspect_ollama_runtime(model=profile.model, client_config=LLMClientConfig.from_env())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
