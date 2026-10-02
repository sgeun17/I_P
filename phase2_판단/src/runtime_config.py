"""Phase 2의 확정 LLM 서빙 프로필 검증.

확정 조건
- provider: Ollama
- API: 기존 Phase 1 OpenAI-compatible /v1/chat/completions
- access: localhost/loopback only
- model family: Qwen3

HTTP 전송 구현 자체는 복제하지 않고 phase1_runtime의 LLMClientConfig/call_llm을 재사용한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from phase1_runtime import GenerationConfig, LLMClientConfig, get_model_name_from_env


RUNTIME_PROFILE_VERSION = "phase2_runtime_ollama_openai_qwen3_v0.1"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Phase2RuntimeConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Phase2RuntimeProfile:
    version: str
    provider: str
    api_mode: str
    access_mode: str
    base_url: str
    endpoint: str
    model: str
    authentication: str
    generation: dict

    def audit_dict(self) -> dict:
        return asdict(self)


def _is_qwen3(model: str) -> bool:
    """Ollama tag/namespace를 허용하되 Qwen3 계열임을 명시적으로 확인한다."""
    return "qwen3" in model.strip().lower()


def validate_phase2_runtime(
    client_config: LLMClientConfig,
    model: str,
    *,
    generation: GenerationConfig | None = None,
) -> Phase2RuntimeProfile:
    if client_config.provider != "ollama":
        raise Phase2RuntimeConfigError("Phase 2 provider는 ollama로 고정되어 있습니다.")

    parsed = urlparse(client_config.base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK_HOSTS:
        raise Phase2RuntimeConfigError(
            "Phase 2 LLM 접속은 localhost/loopback만 허용합니다: "
            f"{client_config.base_url!r}"
        )
    if parsed.path.rstrip("/") != "/v1":
        raise Phase2RuntimeConfigError(
            "OpenAI-compatible base URL은 /v1을 사용해야 합니다: "
            f"{client_config.base_url!r}"
        )
    if client_config.endpoint.rstrip("/") != "/chat/completions":
        raise Phase2RuntimeConfigError(
            "기존 LLM API 규격에 따라 endpoint는 /chat/completions여야 합니다."
        )
    if not isinstance(model, str) or not model.strip():
        raise Phase2RuntimeConfigError("Qwen3 Ollama model tag가 필요합니다.")
    if not _is_qwen3(model):
        raise Phase2RuntimeConfigError(
            f"Phase 2 모델은 Qwen3 시리즈로 제한합니다. 현재 model={model!r}"
        )

    generation = generation or GenerationConfig.from_env()
    return Phase2RuntimeProfile(
        version=RUNTIME_PROFILE_VERSION,
        provider="ollama",
        api_mode="openai-compatible",
        access_mode="localhost",
        base_url=client_config.base_url.rstrip("/"),
        endpoint=client_config.endpoint,
        model=model.strip(),
        authentication="local-no-auth" if not client_config.api_key else "local-bearer-configured",
        generation={
            "temperature": generation.temperature,
            "max_tokens": generation.max_tokens,
            "stream": generation.stream,
            "reasoning_effort": generation.reasoning_effort,
            "seed": generation.seed,
        },
    )


def runtime_profile_from_env(*, model_override: str | None = None) -> Phase2RuntimeProfile:
    client = LLMClientConfig.from_env()
    generation = GenerationConfig.from_env()
    model = model_override.strip() if isinstance(model_override, str) and model_override.strip() else get_model_name_from_env(required=True)
    return validate_phase2_runtime(client, model, generation=generation)
