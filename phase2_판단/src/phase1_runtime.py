"""Phase 1에서 이미 검증한 LLM 전송부/RetryPolicy를 Phase 2에서 재사용한다.

복사 구현하지 않고 Phase 1 소스를 import한다. 따라서 Timeout/재시도 기본값이 두 Phase에서
어긋나는 것을 막는다. Phase 2의 판정 규칙/Prompt는 별도 모듈에서 관리한다.

Phase 1은 현재 평면 import 구조라 import 순간에만 phase1_판단/src를 sys.path 선두에 둔다.
필요 모듈을 모두 읽은 뒤 원래 sys.path로 복구해 Phase 2의 versions.py 같은 동명 모듈을 가리지 않는다.
"""
from __future__ import annotations

from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
PHASE1_SRC = HERE.parents[1] / "phase1_판단" / "src"
_phase1_path = str(PHASE1_SRC)
_added = _phase1_path not in sys.path
if _added:
    sys.path.insert(0, _phase1_path)
try:
    from enums import ErrorCode  # type: ignore
    from llm_client import (  # type: ignore
        LLMCallError,
        LLMRequestRejectedError,
        call_llm,
    )
    from llm_config import (  # type: ignore
        ContextBudgetConfig,
        GenerationConfig,
        LLMClientConfig,
        TokenCounter,
        get_model_name_from_env,
    )
    from review_policy import DEFAULT_RETRY_POLICY, RetryPolicy  # type: ignore
finally:
    if _added:
        sys.path.remove(_phase1_path)


def retry_policy_snapshot(policy: RetryPolicy = DEFAULT_RETRY_POLICY) -> dict:
    return {
        "max_retries": policy.max_retries,
        "timeout_seconds": policy.timeout_seconds,
        "backoff_seconds": policy.backoff_seconds,
        "retry_on": sorted(code.value for code in policy.retry_on),
    }
