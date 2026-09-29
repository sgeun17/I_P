# LLM 하네스 구현 상태 v0.1

## 2026-09-30 Ollama 실측 + Citation 계약/Validator 정합성 수정

실제 `qwen3:4b` + Ollama 환경에서 smoke 3건과 골든셋 29건을 실행했다.

실측:

```text
Smoke 3건: 3/3 OK, retries=0
전체 29건: 29/29 OK, retries=0
총 약 16분
평균 약 34초/건
```

확인된 호출 계약:

- `reasoning_effort=none` 정상
- `<think>` 블록 미출력
- `response_format=json_schema` 정상
- `LLM_BASE_URL=http://localhost:11434/v1`
- `LLM_ENDPOINT=/chat/completions`
- 후보 밖 `control_id` 생성 없음
- NOT_RELATED의 `citations=[]` 규칙 준수

실측 과정에서 Citation 유효율이 16.1%, 검토 전환율이 96.3%로 보였으나,
quote를 청크 원문과 직접 대조하면 66건 중 65건(98.5%)이 일치했다.
원인은 모델이 아니라 **Citation.page 계약과 Validator 동작의 불일치**였다.

이번 수정:

- `Citation.page`는 **필수 필드 + nullable**로 변경
- 출력 Schema 버전 `0.3.0 → 0.3.1`
- `PROMPT_VERSION phase1_mapping_v0.4 → phase1_mapping_v0.5`
- 페이지형 청크에서는 page 정수를 강하게 요구
- 비페이지형 청크는 page=null
- 페이지형 청크에서 `page=null`이면 인용 무효(E404)가 아니라 **E407 warning**
- 실제 page 값이 범위를 벗어날 때만 E404 유지
- reason 내부에 원문을 따옴표로 재인용하는 사례를 E507 warning으로 탐지
- 실제 Ollama 모델 tag 예시는 `qwen3:8b` 형태로 정리
- Windows pip CP949 문제를 피하도록 `requirements*.txt`의 한글 주석 제거
- `requirements.txt`를 추가하고 `requirements-llm.txt`에서 포함

이번 수정은 매핑 판단 의미를 바꾸지 않으므로:

```text
Ruleset: mapping_rules_v0.7 유지
Prompt : phase1_mapping_v0.5
Schema : 0.3.1
```

검증 결과:

```text
pytest: 300 PASS
python src/check.py: 49 PASS / 0 FAIL
compileall: PASS
```

`G-MULTI-04`의 잘못된 chunk_id(`c0.0001`)는 기존 E402가 정상 탐지하므로 수정하지 않았다.
`G-SINGLE-01`의 2.5.1 vs 2.2.5 라벨은 도메인 해석이 필요한 골든셋 검토 사안이므로 자동 수정하지 않았다.

---

## 2026-09-29 Ollama 실제 호출부 구현 업데이트

Phase 1 실제 추론 환경을 **Ollama 단일 환경 + OpenAI-compatible API + localhost + Qwen3**로 확정한 뒤, LLM 담당 범위에서 지금 구현 가능한 호출 계층을 추가했다. 다른 팀의 Search/입력/Validator/Review/Phase 2 코드는 수정하지 않았다.

### 이번에 새로 구현한 파일

- `src/llm_config.py`
  - Ollama base URL/endpoint/timeout을 환경 설정으로 분리
  - Generation baseline: `temperature=0`, `max_tokens=4096`, `stream=false`, Thinking OFF
  - OpenAI-compatible thinking 제어를 `reasoning_effort=none`으로 변환
  - Context Budget Guard를 위한 설정/정확한 tokenizer callback 인터페이스 준비
- `src/llm_client.py`
  - `POST /v1/chat/completions` OpenAI-compatible 호출 구현
  - System/User messages + Pydantic JSON Schema `response_format` 전달
  - HTTP 응답에서 `choices[0].message.content` 추출
  - E101 Timeout / E102 연결 실패 / E103 5xx / E104 빈 응답 / E105 잘림 분류
  - HTTP 4xx는 공통 ErrorCode가 아직 없어 E103으로 위장하지 않고 별도 오류로 보존
- `src/llm_runner.py`
  - 실제 LLM 호출과 기존 `RetryPolicy`, `retry_prompt.py` 연결
  - E101~E105 및 정책이 허용한 E201/E202만 재출력 Prompt로 최대 1회 재시도
  - 호출 오류가 재시도 후에도 실패하면 E106 반환
- `configs/llm.env.example`
  - Ollama/OpenAI-compatible/Qwen3 8B Q4_K_M baseline 설정 예시
- `requirements-llm.txt`
  - HTTP transport용 `httpx` 의존성 분리
- `tests/test_llm_client.py`, `tests/test_llm_runner.py`
  - 실제 네트워크 없이 MockTransport로 request body, Structured Output, 오류 코드, Retry 연결을 자동 검증
- `tools/run_ollama_goldenset.py`
  - Ollama가 설치된 실제 개발 PC에서 1:1 / 1:N / NO_MATCH smoke 3건 또는 골든셋 29건을 호출하고 기존 `eval_goldenset.py`용 JSONL을 생성

### 세부 모델 현재 기준

- 1차 baseline: `qwen3:8b`, Thinking OFF
- 품질 비교 후보: `qwen3:14b`, Thinking OFF
- 최종 운영 모델/양자화/Thinking ON 여부는 실제 골든셋 평가 후 결정한다.
- 모델명은 코드에 고정하지 않고 `LLM_MODEL` 또는 실행 인자로 주입한다.

### Context Budget Guard 상태

구조는 구현했다. 다만 현재는 Qwen3의 정확한 runtime tokenizer를 프로젝트 의존성으로 연결하지 않았으므로 **토큰 계산 callback을 주입했을 때만 활성화**한다. 글자 수를 토큰 수처럼 추정하는 휴리스틱은 사용하지 않는다. 따라서 이 항목은 "구조 구현 완료 / 실제 tokenizer 연동 대기" 상태다.

### HTTP 4xx 상태

기존 공통 ErrorCode는 E101~E106까지이며 E103은 명시적으로 모델 서버 5xx다. 따라서 400/404/422 같은 요청 오류를 E103으로 잘못 기록하지 않았다. `LLMRequestRejectedError` 및 `request_error_status`로 별도 보존하며, 공통 ErrorCode 추가 여부는 Validator/공통 계약 담당과 합의가 필요하다.

### 테스트 결과

- 판단팀 `pytest`: **300 PASS**
- `python src/check.py`: **49 PASS / 0 FAIL**
- `python -m compileall src tests`: **PASS**
- 실제 Ollama 서버 호출: **미수행** — 현재 검증 환경의 `localhost:11434`에 Ollama listener가 없어서 실제 inference는 실행하지 못했다.

### 현재 Task 19~34

| # | Task | 현재 상태 | 구현/결정 내용 | 남은 일 |
|---|---|---|---|---|
| 19 | LLM 서버 종류 결정 | ✅ 완료 | Ollama 단일 환경 | 실제 개발 PC에서 Ollama 설치/기동 |
| 20 | 실제 모델 결정 | 🟡 평가 후보 확정 | 8B Q4_K_M baseline / 14B 비교 | 골든셋 후 최종 선택 |
| 21 | endpoint/API 규격 | ✅ 완료 | OpenAI-compatible, localhost, `/v1/chat/completions` | 실제 환경에서 접속 확인 |
| 22 | `llm_client.py` | ✅ 완료 | httpx 기반 Ollama client 구현 | 실제 서버 smoke |
| 23 | `call_llm()` | ✅ 완료 | messages+model+schema 전송, content 반환 | 실제 서버 smoke |
| 24 | Generation Config | ✅ 초기 구현 완료 | temp=0, max_tokens=4096, stream=false, thinking=false | 실측 후 최종값 조정 |
| 25 | Context Budget Guard | 🟡 구조 구현 완료 | 정확한 token counter 주입식 guard | tokenizer 확정/연결 |
| 26 | Structured Output 실제 강제 | ✅ 코드 구현 완료 | Pydantic Schema→`response_format.json_schema` | 실제 Ollama에서 동작 확인 |
| 27 | LLM 호출 오류 처리 | 🟡 E101~E106 구현 완료 | 호출/Retry 연결 완료 | HTTP 4xx 공통 오류코드 합의 |
| 28 | 실제 1:1 테스트 | 🟡 실행 도구 준비 | smoke case 준비 | Ollama 실제 실행 |
| 29 | 실제 1:N 테스트 | 🟡 실행 도구 준비 | smoke case 준비 | Ollama 실제 실행 |
| 30 | 실제 NO_MATCH 테스트 | 🟡 실행 도구 준비 | smoke case 준비 | Ollama 실제 실행 |
| 31 | reason/confidence/Citation 실출력 | 🟡 실행 경로 준비 | 실제 raw response 저장/Validator 연결 가능 | Ollama 실제 실행 후 확인 |
| 32 | 골든셋 inference 평가 | 🟡 실행 도구 준비 | 29건 runner + 기존 evaluator 연결 | 8B/14B 실제 실행 |
| 33 | 모델·Prompt 비교/튜닝 | ⏸ 대기 | 비교 기준/도구 준비 | 골든셋 결과 필요 |
| 34 | Prompt 최종 Freeze | ⏸ 대기 | 현재 v0.4 유지 | 평가·튜닝 완료 후 |


## 2026-09-29 현재 GitHub 연동 재검토

검토 범위는 LLM 담당이 직접 만든 6개 파일(`prompts.py`, `retrieval_adapter.py`, `retry_prompt.py`, `versions.py`, `test_llm_harness_contract.py`, `llm_harness_status_v0.1.md`)로 한정했다. 검색팀·입력팀·Validator/Review·Phase 2 코드는 수정하지 않았다.

현재 다른 팀 코드와 다시 맞춰 본 결과는 다음과 같다.

- 입력팀은 `png`/`jpg` OCR 청크를 지원하며 이미지 청크의 page는 null이다. 현재 `prompts.py`의 `phase1_mapping_v0.5`가 이 규칙을 이미 반영한다.
- 검색팀의 공식 `judgment_adapter.py`는 검색 결과를 판단 입력으로 넘기기 전에 KB hash, 후보 순서, 유사도 순서, `matched_chunk_ids`, `best_chunk_id`, ID/명칭 등을 엄격하게 확인한다.
- 기존 `retrieval_adapter.py`는 정상 입력에서는 같은 `MappingInput`을 만들었지만 일부 비정상 입력(오래된 KB hash, 뒤섞인 candidate_controls 순서, 유사도 순서 오류, 잘못된 best_chunk_id 등)을 검색팀 어댑터보다 느슨하게 받을 수 있었다. 이번 수정에서 판단팀 어댑터도 같은 경계 조건을 추가 확인하도록 보강했다.
- `mapping_rules_v0.7`의 핵심인 "RELATED 판단 보존 + PRIMARY 불확실성은 confidence/reason으로 표현" 규칙은 유지한다. `PROMPT_VERSION=phase1_mapping_v0.5`, `RULESET_VERSION=mapping_rules_v0.7`이다.
- `retry_prompt.py`, `versions.py`, `prompts.py`는 현재 다른 팀 계약과 충돌이 없어 이번 재검토에서 코드 변경하지 않았다.

재검토 결과:

- 판단팀 pytest: **278 PASS**
- `src/check.py`: **49 PASS / 0 FAIL**
- 검색팀의 판단 연동·이미지 계약 단위 테스트: **10 PASS**
- 검색팀의 `verify_judgment_integration.py`: **PASS**, 실제 LLM 호출 0회
- 9/27 pre-LLM 검색 산출물 11건을 수정된 `retrieval_adapter.py`로 다시 변환: **11/11 PASS**

전체 검색팀 테스트를 이 환경에서 실행하면 `test_public_function_rejects_preprocessing_failure_without_worker` 1건이 `ENVIRONMENT_ERROR`와 `PREPROCESSING_FAILED` 처리 순서 차이로 실패한다. 이는 `phase1_검색/chunk_retriever.py` 영역이며 LLM 담당 파일에는 손대지 않았다. 또한 실제 BGE/Chroma 전체 재실행은 ZIP에 `data/chroma_kb/chroma.sqlite3`가 없어 수행하지 못했다.

## 2026-09-27 연결 전 검증 업데이트

최신 파일 파서·청커→실제 로컬 검색→두 팀의 입력 변환→프롬프트 생성→고정 응답 검증·검토 전환을 확인했다. [실행 결과](../../phase1_검색/reports/pre_llm_2026-09-27/summary.md), [연결 준비 및 변경 사항](../../phase1_검색/handoff/9월27일_LLM연결전_검증.md)을 참고한다. 실제 LLM 호출은 0회다.

PNG/JPG 입력 Enum과 생성 스키마를 최신 입력팀 규격에 맞췄다. 이미지 인용의 page=null 안내를 추가하면서 `PROMPT_VERSION`을 `phase1_mapping_v0.5`로 올렸다. OCR source 보존 수정 후 R207 검토 전환을 확인했다. 원시 OCR confidence는 기존 계약에 추가하지 않았다.

사용자 참고안은 Ollama 우선(vLLM 차순위), OpenAI-compatible API, 사양에 따른 Qwen3 모델, Pydantic JSON Schema, localhost다. **아직 확정된 연결 설정이 아니다.** 구체 모델·양자화·포트/경로·생성 설정·인증 여부·Schema 전달 방식은 미정이다. 기존 규격을 사용한다는 방향은 받았지만 저장소에서 실행용 LLM HTTP 계약은 확인하지 못했다. 임의 서버 설치·모델 다운로드·endpoint 고정은 하지 않았다.

아래는 9/24 작성 기록이다. 호출 인터페이스·서버 Schema 강제 적용·실제 모델 설정은 여전히 미완료다.

기준: 2026-09-24 저장소 실제 코드/문서만 사용.

## 이번에 최종 구현한 항목

| 업무 | 상태 | 근거 |
|---|---|---|
| 0 공통 계약 확인 | 완료(현재 저장소 기준) | `MappingInput`, `LLMMappingOutput`, retriever-0.2 실제 출력, 오류/재시도 정책을 교차 확인 |
| 1 System/User Prompt 분리 | 완료 | `src/prompts.py` |
| 2 1:1·1:N·NO_MATCH 유도 | 완료 | `mapping_rules_v0.7` 반영. 별도 mapping_type은 저장하지 않음 |
| 3 인젝션 방어 | 완료 | 증적을 데이터로 한정 + `service.detect_injection`과 계층형 방어 |
| 4 후보·청크 Prompt 조립 | 완료 | `src/retrieval_adapter.py`가 실제 `phase1_검색/examples/docx_output.json`을 `MappingInput`으로 변환 |
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
- v0.7 판단 규칙/llm_confidence 정의 포함
- `phase1_mapping_v0.5` 이미지 OCR 인용(page=null) 안내 포함
- 검색팀 계약 drift(KB hash/후보 순서/점수 순서/best chunk 등) 거부
- RetryPolicy와 재출력 Prompt 일치
- VersionInfo의 팀별 버전 연결
