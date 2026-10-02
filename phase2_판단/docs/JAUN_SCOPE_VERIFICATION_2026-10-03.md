# Phase 2 자운 담당 변경 검증 — 2026-10-03

## 결론

`phase2_판단_자운담당만_2026-10-03.zip`은 원본 `I_P(7).zip`을 다시 기준으로 만든 정리본이다.

이전 Ollama/Qwen3 수정본에서는 연동 과정에서 `self_check.py`, `validated_pipeline.py`,
`validation/contracts.py`, `validation/logic.py` 등 찬우 담당 경계 파일까지 일부 수정된 것이 확인되었다.

이번 정리본에서는 해당 변경을 전부 되돌리고, **자운 담당인 Context / Grounding Prompt /
항목별 LLM 하네스 / Retry 재사용 / 보완 가이드 / 런타임 / 버전 연결만 수정**하였다.

## 다른 담당 파일 원본 유지 검증

아래 파일은 원본 `I_P(7).zip`과 SHA-256이 동일하다.

- `src/self_check.py`
- `src/validated_pipeline.py`
- `validation/citations.py`
- `validation/contracts.py`
- `validation/logic.py`
- `validation/review.py`
- `tools/run_validated_phase2.py`
- `tests/fixtures/goldenset.json`
- `tests/test_citation_validator.py`
- `tests/test_dates.py`
- `tests/test_validation_boundaries.py`

## 이번에 자운 영역에서 추가/수정한 주요 파일

- `src/runtime_config.py`: Ollama / OpenAI-compatible / localhost / Qwen3 실행환경 확인
- `src/ollama_preflight.py`: 설치된 Ollama 모델 및 quantization 정보 확인
- `src/structured_output_adapter.py`: 찬우가 제공한 `phase2_output.schema.json`의 `ItemResult`를 읽어 LLM 요청에 사용
- `src/grounding_prompts.py`: 팀 제공 Structured Output 계약을 사용하는 Grounding Prompt
- `src/judgment_harness.py`: 항목별 LLM 호출 및 Phase 1 RetryPolicy 재사용
- `src/checklist_adapter.py`: 최신 Chapter 2 checklist/reason code draft 연결
- `src/versions.py`: 공식 Output Schema 버전/SHA-256 기록
- `src/phase1_runtime.py`: Phase 1 model env helper 재사용
- `configs/llm.env.example`
- `tools/check_ollama_runtime.py`
- `tools/run_item_judgment.py`
- 자운 영역 테스트 추가

## 테스트

자운 변경 관련 집중 테스트: **17 passed**

전체 `phase2_판단` 테스트는 정리본에서 **250 passed / 21 failed**였고,
원본 `I_P(7)`에서도 **243 passed / 21 failed**로 동일한 21개 실패가 존재한다.
기존 실패는 `phase2_인터페이스/test/overall_result.py` 경로 참조와 골든셋/Schema hash 불일치 등
원본 상태의 선행 문제이며, 다른 담당 영역을 침범하지 않기 위해 수정하지 않았다.

## GitHub 반영 시

이 ZIP의 `phase2_판단/` 폴더를 기준으로 반영하면 된다.
`self_check.py`, `validated_pipeline.py`, `validation/` 디렉터리의 기존 파일은 팀 원본 그대로 유지되어 있다.
