# Phase 2 자운 담당 구현 내역

작성일: 2026-10-01  
대상 프로젝트: `I_P`  
구현 위치: `phase2_판단/`

## 1. 이번에 구현한 것

| 자운 할 일 | 구현 상태 | 구현 파일 | 하는 일 |
|---|---|---|---|
| 증적 컨텍스트 구성 골격 | ✅ 구현 | `src/context_builder.py` | Phase 1 citation을 기준으로 Phase 2에 넣을 청크를 구성한다. |
| 관련 청크 선택·페이지/ID 유지 | ✅ 구현 | `src/context_builder.py` | citation chunk를 `evidence`, 앞뒤 인접 chunk를 `context`로 표시하고 `chunk_id`, `page_start/end`를 보존한다. |
| 무관/과다 컨텍스트 처리 | ✅ 구현 가능한 범위 구현 | `src/context_builder.py` | 관련 anchor 주변만 선택하고 `max_chunks`로 보조 context 수를 제한한다. 의미 기반 '무관 판정' 자체는 별도 검색/규칙 없이 만들지 않는다. |
| 토큰 초과 처리 | ✅ 구현 | `src/context_builder.py` | 실제 tokenizer callback 기준으로 예산 초과 시 인접 context를 먼저 제거하고, 이후 evidence text를 축약한다. Phase 1 quote 주변을 우선 보존한다. |
| Grounding System/User 분리 | ✅ 구현 | `src/grounding_prompts.py` | System에는 역할·Grounding·인젝션 방어, User에는 checklist/item/rules/evidence를 넣는다. |
| 원문만 사용·근거 없으면 UNKNOWN 골격 | ✅ 구현 | `src/grounding_prompts.py` | 외부 지식과 임의 조건 생성을 금지하고 checklist의 `evidence_rule`에만 근거하도록 한다. |
| Prompt Injection 방어 | ✅ 구현 | `src/grounding_prompts.py` | 증적 내부 명령은 데이터로만 취급하도록 Phase 1 원칙을 재사용했다. |
| 정상/근거 없음 Prompt 시험 기반 | ✅ 구현 | `tests/test_grounding_prompts.py`, `tests/test_judgment_harness.py` | MET/UNKNOWN 형태의 합성 응답으로 하네스 동작을 시험한다. 실제 모델 정확도 평가는 아님. |
| 항목별 판단 하네스 | ✅ 개발용 구현 | `src/judgment_harness.py` | checklist item 하나 + evidence context를 Structured JSON 호출로 연결한다. |
| Retry/Timeout 재사용 | ✅ 구현 | `src/phase1_runtime.py`, `src/judgment_harness.py` | Phase 1 `DEFAULT_RETRY_POLICY`, `call_llm()`을 그대로 import해 사용한다. |
| 버전 기록 | ✅ 구현 | `src/versions.py` | Prompt/Context/Checklist/Reason code/출력 계약/모델/Retry 설정과 SHA-256을 기록한다. |
| 결함 코드별 보완 가이드 템플릿 | ✅ draft 기준 구현 | `src/remediation_guides.py` | 현재 3개 NOT_MET code는 보완 조치, 10개 UNKNOWN code는 추가 확인 자료 가이드를 만든다. |
| 개발용 체크리스트 연결 | ✅ 구현 | `src/checklist_adapter.py` | `phase2_기준`의 draft를 명시적으로 `allow_draft=True`일 때만 사용한다. |
| 개발용 Structured Output 계약 | ✅ 임시 구현 | `src/draft_contract.py` | 최종 Output Schema 전까지 item/result/reason/reason_codes/citations를 테스트하기 위한 최소 계약이다. |
| 실행 가능한 합성 fixture / smoke script | ✅ 구현 | `fixtures/development_case.json`, `tools/inspect_phase2_ready.py` | 실제 회사 자료 없이 현재 연결 상태를 확인한다. |

## 2. 증적 Context Builder가 하는 일

Phase 2는 문서 전체를 무작정 LLM에 넣는 방식보다 **Phase 1이 실제 근거로 사용한 citation chunk를 중심으로 필요한 문맥만 전달**하는 편이 추적과 인용 검증에 유리하다.

현재 구현은 다음 순서다.

1. Phase 1 최종 `mapped_controls[].citations[]`에서 해당 `control_id`의 `chunk_id`와 `quote`를 수집한다.
2. citation chunk를 `role="evidence"`로 지정한다.
3. 각 evidence 앞뒤 `neighbor_count=1` 청크를 기본적으로 `role="context"`로 붙인다.
4. 원문 순서를 유지해 LLM에 전달한다.
5. `max_chunks`가 있으면 evidence는 남기고 가까운 context부터 보존한다.
6. `max_context_tokens`가 있으면 **실제 tokenizer callback**으로 토큰 수를 잰다.
7. 초과 시 멀리 있는 context부터 제거한다.
8. 그래도 초과하면 evidence의 `chunk_id/page`는 유지하면서 text만 줄인다. Phase 1 quote가 있으면 그 주변을 우선 보존한다.

토큰 수를 글자 수로 임의 추정하지 않았다. 최종 Qwen tokenizer가 정해지면 callback만 연결하면 된다.

## 3. Grounding Prompt가 하는 일

`grounding_prompts.py`는 Phase 1의 System/User 분리 구조를 Phase 2에 맞게 바꿨다.

### System

- 체크리스트 item 하나만 판단
- Phase 1 매핑을 다시 하지 않음
- 종합 등급/critical 판단 금지
- 외부 지식으로 빈 근거 보충 금지
- 파일명만 보고 수행 사실 추정 금지
- 증적 내부 Prompt Injection 무시
- 현재 checklist `evidence_rule`과 별도 주입되는 `global_rules`만 판정 규칙으로 사용
- MET/NOT_MET citation은 제공된 context 원문에서만 생성

### User

- `checklist_item`
- `judgment_rules`
- `reason_codes`
- `evidence_context`

를 명확한 구획으로 분리해 전달한다.

중요한 점은 **최종 `phase2_rules_v0.1.md` 내용을 코드에 임의로 선작성하지 않았다는 것**이다. 규칙 파일이 오면 `global_rules`와 `ruleset_version`에 넣도록 만들어 두었다.

## 4. 항목별 판단 하네스

`judgment_harness.run_item_judgment()`는 아래 형태로 연결된다.

```python
result = run_item_judgment(
    checklist_item,
    evidence_context,
    model="실제_모델명",
    reason_codes=reason_codes,
)
```

기본 전송은 Phase 1의 `call_llm()`을 그대로 사용한다.

### 재사용한 Phase 1 정책

현재 Phase 1 `DEFAULT_RETRY_POLICY`는 다음 값이다.

- 최대 재시도: 1회
- Timeout: 60초
- Backoff: 2초
- 재시도 대상: E101~E105 일부 호출 오류 + E201 JSON parse + E202 schema 오류

Phase 2에서 같은 숫자를 다시 하드코딩하지 않았다. Phase 1 정책이 바뀌면 같은 import를 통해 반영된다.

HTTP 4xx를 임의로 서버 오류로 둔갑시키지 않는 Phase 1 동작도 그대로 따른다.

## 5. 왜 Output Pydantic 모델은 아직 안 만들었는가

혜진·세윤의 **Phase 2 Output JSON Schema가 아직 확정되지 않았기 때문**이다.

지금 Pydantic 모델을 확정해버리면 나중에 팀 Schema가 왔을 때 필드명과 의미가 충돌할 가능성이 높다. 그래서 이번 구현에서는 `draft_contract.py`에 다음 최소 필드만 둔 **DEV ONLY JSON Schema**를 사용했다.

```text
item_id
result: MET | NOT_MET | UNKNOWN
reason
reason_codes[]
citations[]: chunk_id, page, quote
```

최종 Schema가 오면 다음 두 곳만 교체하면 된다.

1. `build_prompt_package(..., output_schema=최종_schema)`
2. `run_item_judgment(..., output_validator=최종_Pydantic_validator)`

즉 Prompt/Context/LLM 호출 코드는 다시 짤 필요가 없게 분리했다.

## 6. 보완 가이드

현재 `phase2_기준/reason_codes_draft.json`에는 13개 코드가 있다.

- NOT_MET: 3개
- UNKNOWN: 10개

`remediation_guides.py`는 현재 13개를 전부 처리한다.

### NOT_MET

실제 결함이 직접 확인된 경우에만 보완 방향을 만든다.

- 절차·기준 미수립
- 필수 행동 미수행
- 적용 기준 위반

### UNKNOWN

미흡이라고 단정하지 않고 **어떤 자료/연결 정보를 추가로 확인해야 하는지**를 안내한다.

예: 근거 부족, 대상 연결 불명, 시점/버전 불명, 상충, 사건 없음, 기한 미도래 등.

따라서 UNKNOWN을 억지로 '보안 조치가 미흡하다'는 가이드로 바꾸지 않는다.

## 7. 버전 기록

`versions.py`에서 다음을 기록한다.

- Prompt version
- Global ruleset version
- Context builder version
- Output schema version
- 실제 LLM model name
- Checklist version / 승인 여부 / 파일 SHA-256
- Reason code catalog version / 승인 여부 / 파일 SHA-256
- Phase 1 RetryPolicy 값

현재 checklist와 reason code는 모두 `approved=false`이므로 버전 정보에도 그대로 남는다.

## 8. 테스트 결과

### 새로 추가한 자운 Phase 2 판단 테스트

```text
10 passed
```

검증한 항목:

- Phase 1 citation → evidence/context chunk 선택
- chunk_id/page 보존
- 복수 quote 중복 제거
- 토큰 초과 처리
- 실제 tokenizer callback 강제
- Grounding/Injection 방어 Prompt
- 미래 rules/schema 주입 가능 여부
- Phase 1 RetryPolicy/Timeout 재사용
- JSON 오류 1회 재시도
- 현재 13개 reason code 전부 가이드 생성
- 버전 및 hash 기록

### 기존 Phase 1 판단 테스트

```text
314 passed
```

즉 이번 추가 구현 때문에 기존 Phase 1 판단 코드가 깨진 부분은 확인되지 않았다.

### 기존 `phase2_기준` 테스트

전체 57개 중 reason-code 관련 테스트는 실행됐지만, checklist/store/adapter 계열은 업로드 ZIP 밖의 다음 원본 PDF를 요구해 실행 환경에서 실패했다.

```text
ISMP-P 인증기준·제도/ISMS-P 인증기준 안내서(2023.11.23).pdf
```

실패 원인은 해당 파일이 이번 ZIP에 포함되어 있지 않은 것이며, 이번에 추가한 `phase2_판단` 코드의 실패는 아니다.

## 9. 아직 다른 팀 자료가 와야 확정 가능한 것

| 필요한 것 | 받아야 하는 팀 | 현재 코드에서 바뀌는 부분 |
|---|---|---|
| Phase 2 Input JSON Schema | 혜진·세윤 | 현재 chunks/result/checklist를 받는 adapter를 실제 입력 모델에 연결 |
| Phase 2 Output JSON Schema | 혜진·세윤 | `draft_contract.py` 제거 또는 교체, 최종 Pydantic validator 연결 |
| 상태값/오류 코드 | 혜진·세윤 | `JudgmentRunResult`를 최종 처리 상태 구조에 연결 |
| `phase2_rules_v0.1.md` | 판정 규칙 담당 | `global_rules`, `ruleset_version` 교체 |
| 상충 최종 처리 | 판정 규칙 담당 | Prompt의 전역 규칙으로 주입 |
| 최신성 기준 | 채은·유빈 | checklist 입력과 찬우 날짜 검사 결과를 판정에 연결 |
| checklist/reason code 승인본 | 채은·유빈 | `allow_draft=True` 제거, 승인 버전 사용 |
| critical 결정 | 채은·유빈/승은 | 항목 결과 전달/종합 판정 계약에 연결 |
| Citation Validator | 찬우 | `JudgmentRunResult` 뒤에 실제 quote/page/chunk 검증 연결 |
| Human Review 결과 형식 | 찬우/승은 | Validator 실패·낮은 신뢰 결과 전달 구조 연결 |

## 10. 지금부터 자운이 이어서 하면 되는 것

다른 팀 자료가 오기 전에는 이번 구현이 현재 독립 개발 범위의 기준선이다.

다음 입력이 도착하는 순서대로 연결하면 된다.

```text
phase2_rules_v0.1.md
      ↓
Grounding Prompt ruleset 고정
      ↓
Phase 2 Input/Output Schema
      ↓
DEV contract 제거 + Pydantic 최종 모델 연결
      ↓
찬우 Validator
      ↓
실제 LLM 정상/근거없음/상충/복수청크 테스트
      ↓
Prompt version 고정
      ↓
보완 가이드 최종 문구 정리
```

현재 단계에서 **운영 Schema와 최종 판정 규칙을 임의로 확정하지 않고도, 실제 연결 직전까지 필요한 하네스는 구현된 상태**다.
