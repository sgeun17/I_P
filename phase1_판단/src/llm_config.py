"""Phase 1 LLM 런타임 설정.

현재 Phase 1의 실행 기준은 Ollama 단일 환경 + OpenAI-compatible API다.
Prompt/Ruleset과 서빙 설정을 분리하기 위해 모델/endpoint/generation 값을 이 모듈에서 관리한다.

환경변수는 선택사항이다. 코드에 모델 식별자를 하드코딩하지 않고 실행 환경에서 주입할 수 있게 한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from review_policy import DEFAULT_RETRY_POLICY


class LLMConfigError(ValueError):
    """LLM 런타임 설정이 잘못되었을 때 발생한다."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise LLMConfigError(f"{name} must be true/false, got {raw!r}")


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class LLMClientConfig:
    """Ollama OpenAI-compatible HTTP 연결 설정.

    기본 주소는 Ollama의 로컬 OpenAI-compatible endpoint다. 실제 배포 환경에서는
    환경변수로 덮어쓸 수 있다. API key는 로컬 Ollama raw HTTP 호출에는 보통 필요하지
    않으므로 기본값을 None으로 둔다.
    """

    provider: str = "ollama"
    base_url: str = "http://localhost:11434/v1"
    endpoint: str = "/chat/completions"
    api_key: str | None = None
    timeout_seconds: float = float(DEFAULT_RETRY_POLICY.timeout_seconds)

    def __post_init__(self) -> None:
        if self.provider != "ollama":
            raise LLMConfigError(
                f"Phase 1 currently supports only provider='ollama', got {self.provider!r}"
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise LLMConfigError("LLM base_url must start with http:// or https://")
        if not self.endpoint.startswith("/"):
            raise LLMConfigError("LLM endpoint must start with '/'")
        if self.timeout_seconds <= 0:
            raise LLMConfigError("timeout_seconds must be > 0")

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.endpoint.lstrip("/")

    @classmethod
    def from_env(cls) -> "LLMClientConfig":
        return cls(
            provider=os.getenv("LLM_PROVIDER", "ollama").strip().lower(),
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").strip(),
            endpoint=os.getenv("LLM_ENDPOINT", "/chat/completions").strip(),
            api_key=(os.getenv("LLM_API_KEY") or None),
            timeout_seconds=_env_float(
                "LLM_TIMEOUT_SECONDS", float(DEFAULT_RETRY_POLICY.timeout_seconds)
            ),
        )


@dataclass(frozen=True)
class GenerationConfig:
    """Phase 1 판단용 generation baseline.

    `thinking=False`는 Ollama OpenAI-compatible API에서 `reasoning_effort='none'`으로
    변환한다. Thinking을 켤 때는 `thinking_effort`를 low/medium/high/max 중 하나로 둔다.
    """

    temperature: float = 0.0
    max_tokens: int = 4096
    stream: bool = False
    thinking: bool = False
    thinking_effort: str = "medium"
    seed: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.temperature <= 2.0:
            raise LLMConfigError("temperature must be between 0 and 2")
        if self.max_tokens < 1:
            raise LLMConfigError("max_tokens must be >= 1")
        if self.stream:
            raise LLMConfigError(
                "Phase 1 structured-output parser requires stream=false"
            )
        if self.thinking_effort not in {"low", "medium", "high", "max"}:
            raise LLMConfigError(
                "thinking_effort must be one of: low, medium, high, max"
            )

    @property
    def reasoning_effort(self) -> str:
        return self.thinking_effort if self.thinking else "none"

    @classmethod
    def from_env(cls) -> "GenerationConfig":
        return cls(
            temperature=_env_float("LLM_TEMPERATURE", 0.0),
            max_tokens=_env_int("LLM_MAX_OUTPUT_TOKENS", 4096) or 4096,
            stream=_env_bool("LLM_STREAM", False),
            thinking=_env_bool("LLM_THINKING", False),
            thinking_effort=os.getenv("LLM_THINKING_EFFORT", "medium").strip().lower(),
            seed=_env_int("LLM_SEED", None),
        )


@dataclass(frozen=True)
class ContextBudgetConfig:
    """Context Budget Guard 설정.

    최종 Qwen3 tokenizer가 연결되기 전까지는 `context_window=None`으로 두어 비활성화한다.
    exact token counter가 준비되면 같은 코드에서 바로 활성화할 수 있다.
    """

    context_window: int | None = None
    safety_margin_tokens: int = 512

    def __post_init__(self) -> None:
        if self.context_window is not None and self.context_window < 1:
            raise LLMConfigError("context_window must be >= 1 when configured")
        if self.safety_margin_tokens < 0:
            raise LLMConfigError("safety_margin_tokens must be >= 0")

    @classmethod
    def from_env(cls) -> "ContextBudgetConfig":
        return cls(
            context_window=_env_int("LLM_CONTEXT_WINDOW", None),
            safety_margin_tokens=_env_int("LLM_CONTEXT_SAFETY_MARGIN", 512) or 0,
        )


TokenCounter = Callable[[str], int]


def get_model_name_from_env(*, required: bool = True) -> str | None:
    """실행 모델명을 환경변수에서 읽는다.

    코드에는 특정 Qwen3 tag를 고정하지 않는다. 개발 baseline은 example env에 제시한다.
    """
    value = (os.getenv("LLM_MODEL") or "").strip()
    if value:
        return value
    if required:
        raise LLMConfigError("LLM_MODEL is required for an actual LLM call")
    return None
