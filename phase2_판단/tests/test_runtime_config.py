import pytest

from phase1_runtime import GenerationConfig, LLMClientConfig
from runtime_config import Phase2RuntimeConfigError, validate_phase2_runtime


def test_accepts_local_ollama_openai_qwen3():
    profile = validate_phase2_runtime(
        LLMClientConfig(base_url="http://localhost:11434/v1", endpoint="/chat/completions"),
        "qwen3:8b",
        generation=GenerationConfig(temperature=0, max_tokens=2048),
    )
    assert profile.provider == "ollama"
    assert profile.api_mode == "openai-compatible"
    assert profile.access_mode == "localhost"
    assert profile.model == "qwen3:8b"


def test_rejects_remote_endpoint():
    with pytest.raises(Phase2RuntimeConfigError):
        validate_phase2_runtime(
            LLMClientConfig(base_url="http://10.0.0.3:11434/v1"),
            "qwen3:8b",
        )


def test_rejects_non_qwen3_model():
    with pytest.raises(Phase2RuntimeConfigError):
        validate_phase2_runtime(LLMClientConfig(), "llama3.2:8b")


def test_rejects_wrong_openai_endpoint():
    with pytest.raises(Phase2RuntimeConfigError):
        validate_phase2_runtime(
            LLMClientConfig(endpoint="/responses"),
            "qwen3:8b",
        )
