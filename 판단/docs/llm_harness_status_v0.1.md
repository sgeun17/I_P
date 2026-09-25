# LLM 하네스 구현 상태 v0.1

기준: 2026-09-24 저장소 실제 코드/문서만 사용.

## 이번에 최종 구현한 항목

| 업무 | 상태 | 근거 |
|---|---|---|
| 0 공통 계약 확인 | 완료(현재 저장소 기준) | `MappingInput`, `LLMMappingOutput`, retriever-0.2 실제 출력, 오류/재시도 정책을 교차 확인 |
| 1 System/User Prompt 분리 | 완료 | `src/prompts.py` |
| 2 1:1·1:N·NO_MATCH 유도 | 완료 | `mapping_rules_v0.6` 반영. 별도 mapping_type은 저장하지 않음 |
| 3 인젝션 방어 | 완료 | 증적을 데이터로 한정 + `service.detect_injection`과 계층형 방어 |
| 4 후보·청크 Prompt 조립 | 완료 | `src/retrieval_adapter.py`가 실제 `Search/examples/docx_output.json`을 `MappingInput`으로 변환 |
| 8 재출력 Prompt | 완료 | `src/retry_prompt.py`, 현재 `RetryPolicy`의 재시도 대상만 허용 |
| 9 버전 관리 구조 | 완료 | `src/versions.py`; 모델명은 런타임에서 명시적으로 받음 |

## 부분 완료 / 아직 완료 처리하지 않은 항목

### 5 JSON 강제 출력 — Schema/Prompt 준비 완료, 서버 강제 적용은 미완료

`src/prompts.py`는 `LLMMappingOutput.model_json_schema()`를 그대로 `output_schema`로 제공하고
System Prompt에도 동일 스키마를 삽입한다. 따라서 스키마 drift는 막았다.

하지만 실제 모델 서버에서 이 스키마를 `format=...`, `response_format=...` 등으로 강제하는 방법은
사용할 LLM 서빙 환경이 정해져야 한다. 이 부분은 6번과 함께 마무리한다.

### 6 LLM 호출 인터페이스 — 보류

저장소에서 실제 LLM 서버 구현/endpoint/provider가 확인되지 않았다. 필요한 정보:

- Ollama / vLLM / llama.cpp / OpenAI-compatible 중 무엇인지
- endpoint 및 request/response 형식
- 실제 model identifier
- 인증 필요 여부
- Structured Output 전달 파라미터

샘플 JSON에 적힌 `qwen2.5-14b-instruct`는 실행 서버 계약으로 간주하지 않았다.

### 7 생성 설정값 — 보류

재시도 정책의 timeout=60초, backoff=2초는 판단팀 코드에 있으나 다음이 없다.

- 실제 모델의 context window
- max output token 지원/권장값
- 서버별 temperature/max token 파라미터명

따라서 숫자를 추측해 최종 Config로 고정하지 않았다.

## 확인된 팀 간 변환

검색팀 `retriever-0.2`는 문서 Top-K를 이미 `max_chunk_similarity`로 집계한다.
판단팀은 `retrieval.candidates`(rank/score)와 `candidate_controls`(requirement)를 control_id로 join하여
`MappingInput.candidate_controls`로 받는다.

검색팀 결과의 `matched_chunk_ids`는 판단 모델의 `source_chunk_ids`로 이름만 바꾼다.
검색팀의 `embedding_cache_key`는 model revision이 아니므로 `model_revision`으로 위조하지 않는다.

## 테스트

`tests/test_llm_harness_contract.py`는 실제 검색팀 샘플을 읽어 다음을 검사한다.

- Search -> MappingInput 변환
- requirement 포함 및 검색 점수 Prompt 미노출
- Prompt JSON Schema == 검증팀 체크인 Schema
- v0.6 판단 규칙/llm_confidence 정의 포함
- RetryPolicy와 재출력 Prompt 일치
- VersionInfo의 팀별 버전 연결
