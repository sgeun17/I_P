"""로컬 Ollama/Qwen3 런타임 preflight와 양자화 정보 점검.

실제 판정 정확도를 보장하는 검사가 아니다. 실행 전에 다음 운영 실수를 잡는다.
- localhost가 아닌 endpoint
- Qwen3가 아닌 모델 tag
- Ollama에 해당 model이 설치되지 않음
- 지나치게 공격적인 저비트 양자화(휴리스틱 경고)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from urllib.parse import urlparse

import httpx

from phase1_runtime import LLMClientConfig
from runtime_config import validate_phase2_runtime


PREFLIGHT_VERSION = "phase2_ollama_preflight_v0.1"


@dataclass(frozen=True)
class QuantizationAssessment:
    level: str | None
    status: str
    note: str


def assess_quantization(level: str | None) -> QuantizationAssessment:
    if not level:
        return QuantizationAssessment(None, "UNKNOWN", "Ollama가 quantization_level을 반환하지 않아 확인할 수 없습니다.")
    normalized = level.upper().replace("-", "_")
    match = re.search(r"Q(\d+)", normalized)
    if match:
        bits = int(match.group(1))
        if bits <= 3:
            return QuantizationAssessment(level, "HIGH_RISK", "Q2/Q3 계열은 구조화 판정 품질 저하 가능성이 커 별도 정확도 검증이 필요합니다.")
        if bits == 4:
            return QuantizationAssessment(level, "CAUTION", "Q4는 로컬 baseline 후보지만 원본/상위 정밀도 모델과 골든셋 비교가 필요합니다.")
        return QuantizationAssessment(level, "OK_WITH_EVAL", "Q5 이상도 정확도를 보장하지 않으므로 골든셋 실측은 필요합니다.")
    if any(token in normalized for token in ("F16", "FP16", "BF16", "F32", "FP32")):
        return QuantizationAssessment(level, "OK_WITH_EVAL", "고정밀 표현이지만 실제 판정 정확도는 별도 골든셋으로 검증해야 합니다.")
    return QuantizationAssessment(level, "UNKNOWN", "알 수 없는 양자화 표기입니다. 모델 메타데이터를 직접 확인하세요.")


def _native_ollama_base(config: LLMClientConfig) -> str:
    parsed = urlparse(config.base_url)
    host = parsed.hostname or "localhost"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def inspect_ollama_runtime(
    *,
    model: str,
    client_config: LLMClientConfig | None = None,
    http_client: httpx.Client | None = None,
) -> dict:
    config = client_config or LLMClientConfig.from_env()
    profile = validate_phase2_runtime(config, model)
    owned = http_client is None
    client = http_client or httpx.Client(timeout=config.timeout_seconds)
    try:
        models_url = config.base_url.rstrip("/") + "/models"
        models_response = client.get(models_url)
        models_response.raise_for_status()
        models_payload = models_response.json()
        listed = []
        if isinstance(models_payload, dict) and isinstance(models_payload.get("data"), list):
            listed = [row.get("id") for row in models_payload["data"] if isinstance(row, dict) and row.get("id")]

        native_base = _native_ollama_base(config)
        show_response = client.post(native_base + "/api/show", json={"model": model})
        show_response.raise_for_status()
        show = show_response.json()
        details = show.get("details") if isinstance(show, dict) and isinstance(show.get("details"), dict) else {}
        model_info = show.get("model_info") if isinstance(show, dict) and isinstance(show.get("model_info"), dict) else {}
        quant = assess_quantization(details.get("quantization_level"))
        context_values = {
            key: value for key, value in model_info.items()
            if isinstance(key, str) and key.endswith(".context_length")
        }
        return {
            "preflight_version": PREFLIGHT_VERSION,
            "ready": model in listed,
            "runtime": profile.audit_dict(),
            "openai_compatible_models_url": models_url,
            "model_listed": model in listed,
            "available_models": listed,
            "ollama_show": {
                "family": details.get("family"),
                "families": details.get("families"),
                "parameter_size": details.get("parameter_size"),
                "quantization_level": details.get("quantization_level"),
                "context_length_fields": context_values,
            },
            "quantization": asdict(quant),
        }
    finally:
        if owned:
            client.close()
