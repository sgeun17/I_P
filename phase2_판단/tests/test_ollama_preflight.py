import json

import httpx

from ollama_preflight import assess_quantization, inspect_ollama_runtime
from phase1_runtime import LLMClientConfig


def test_quantization_assessment_boundaries():
    assert assess_quantization("Q3_K_M").status == "HIGH_RISK"
    assert assess_quantization("Q4_K_M").status == "CAUTION"
    assert assess_quantization("Q5_K_M").status == "OK_WITH_EVAL"
    assert assess_quantization(None).status == "UNKNOWN"


def test_preflight_reads_openai_models_and_ollama_show():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]})
        if request.url.path == "/api/show":
            body = json.loads(request.content)
            assert body["model"] == "qwen3:8b"
            return httpx.Response(200, json={
                "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
                "model_info": {"qwen3.context_length": 40960},
            })
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = inspect_ollama_runtime(
        model="qwen3:8b",
        client_config=LLMClientConfig(),
        http_client=client,
    )
    assert report["ready"] is True
    assert report["ollama_show"]["quantization_level"] == "Q4_K_M"
    assert report["quantization"]["status"] == "CAUTION"
    client.close()
