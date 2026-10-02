# Phase 2 자운 담당 구현 내역 — 2026-10-03

## 1. 목적

확정된 실행환경인 **Ollama + OpenAI-compatible API + localhost + Qwen3**를 자운 담당 LLM 하네스에 연결했다.
Structured Output은 찬우 담당에서 제공한 Phase 2 Output Schema 산출물을 그대로 소비하며, 찬우의 Validator/Self-check/Human Review 구현은 수정하지 않는다.

## 2. 이번에 수정/추가한 자운 담당 파일

### 실행환경
- `src/runtime_config.py`
  - provider가 Ollama인지 확인
  - base URL이 localhost/loopback인지 확인
  - OpenAI-compatible `/v1/chat/completions` 규격 확인
  - model tag가 Qwen3 계열인지 확인
- `src/ollama_preflight.py`
  - Ollama `/v1/models`, `/api/show`를 이용해 실제 설치 모델과 quantization 정보를 점검
- `configs/llm.env.example`
  - 로컬 실행환경 예시
- `tools/check_ollama_runtime.py`
  - 위 preflight를 CLI로 실행

### Structured Output 연결
- `src/structured_output_adapter.py`
  - `phase2_인터페이스/phase2_output.schema.json`의 `$defs.ItemResult`를 그대로 읽음
  - 자운 코드가 Output Schema/Pydantic 모델을 새로 정의하지 않음
  - 최종 Citation/ID/ENUM 검증은 수행하지 않음
- `src/grounding_prompts.py`
  - System/User Prompt에서 팀 제공 Structured Output 계약을 사용
  - Prompt Injection 방어, 외부 지식 금지, 원문 citation 규칙 유지
- `src/judgment_harness.py`
  - 팀 제공 ItemResult Schema를 Ollama 호출에 전달
  - JSON parse와 재시도 판단에 필요한 최소 형태만 확인
  - 최종 Validator 역할은 수행하지 않음

### 체크리스트/버전
- `src/checklist_adapter.py`
  - 기본 checklist를 `chapter2_full_checklist_draft.json`으로 연결
  - 기본 reason code를 `chapter2_reason_codes_draft.json`으로 연결
  - 둘 다 미승인 Draft 상태를 그대로 존중
- `src/versions.py`
  - Prompt/Rules/Context/Checklist/Reason Code/Phase 1 Retry 외에
    공식 `phase2_output.schema.json` 버전과 SHA-256을 기록
- `src/phase1_runtime.py`
  - Phase 1의 기존 OpenAI-compatible 호출/Retry를 그대로 재사용하기 위해 model env helper만 노출

### 실행 도구/테스트
- `tools/run_item_judgment.py`
  - 자운 담당 문항별 판단까지만 실행하는 CLI
- `tests/test_runtime_config.py`
- `tests/test_ollama_preflight.py`
- `tests/test_structured_output_adapter.py`

## 3. 의도적으로 수정하지 않은 다른 담당 영역

아래 파일은 `I_P(7).zip`의 원본을 그대로 유지했다.

- `src/self_check.py`
- `src/validated_pipeline.py`
- `validation/citations.py`
- `validation/contracts.py`
- `validation/logic.py`
- `validation/review.py`
- `tools/run_validated_phase2.py`
- 기존 Citation Validator/Self-check/Human Review 관련 테스트와 문서

따라서 이번 자운 변경은 **LLM 입력 구성/Prompt/호출/보완 가이드/런타임/버전 연결 범위**에 한정된다.

## 4. 다른 팀 산출물 사용 방식

- 채은·유빈 checklist/reason code: **읽어서 사용만 함. 내용 결정/수정 안 함**
- 혜진·세윤 Input/Output Schema: **인터페이스로 사용만 함. 스키마 설계/변경 안 함**
- 찬우 Pydantic/Validator: **Structured Output 계약을 소비만 함. Validator/Self-check 수정 안 함**
- 승은 종합 결과/Human Review: **결정 로직 수정 안 함**

## 5. 양자화

세부 Qwen3 모델과 quantization은 장비 사양에 따라 선택한다. `ollama_preflight.py`는 Ollama가 제공하는
quantization 정보를 표시하고 낮은 비트 양자화에 주의를 주는 용도다. 최종 선택은 실제 골든셋 품질/속도/메모리 측정 후 결정한다.
