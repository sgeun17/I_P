# 오류코드 · 실패 처리 정책 v0.1

작성: 판단팀 · 2026-09-21

코드 원본은 `src/enums.py`의 `ErrorCode`다. 이 문서는 설명용이고,
**코드를 추가할 때는 Enum을 먼저 고친다.**

---

## 1. 코드 체계

| 대역 | 분류 | 재시도 |
|---|---|---|
| `E1xx` | LLM 호출 실패 | O |
| `E2xx` | 출력 형식 오류 | O (1회) |
| `E3xx` | 통제항목 ID 오류 | X |
| `E4xx` | Citation 오류 | X |
| `E5xx` | 판단 규칙 위반 | X |

**E3xx 이상은 재시도하지 않는다.** 같은 프롬프트로 다시 불러도 같은 실수를 반복할 가능성이 크고,
"후보 밖 ID를 만들었다"는 건 모델이 지시를 안 따른 것이라 사람이 봐야 한다.

---

## 2. 전체 목록

### E1xx — LLM 호출

| 코드 | 이름 | 상황 |
|---|---|---|
| `E101` | LLM_TIMEOUT | 응답 시간 초과 |
| `E102` | LLM_CONNECTION_FAILED | 서버 연결 실패 |
| `E103` | LLM_SERVER_ERROR | 모델 서버 5xx |
| `E104` | LLM_EMPTY_RESPONSE | 빈 응답 |
| `E105` | LLM_TRUNCATED_RESPONSE | 응답이 중간에 잘림 (max_tokens 부족 의심) |
| `E106` | LLM_RETRY_EXHAUSTED | 재시도까지 모두 실패 |

### E2xx — 출력 형식

| 코드 | 이름 | 상황 |
|---|---|---|
| `E201` | JSON_PARSE_FAILED | JSON으로 읽히지 않음 |
| `E202` | SCHEMA_INVALID | 스키마 위반 |
| `E203` | REQUIRED_FIELD_MISSING | 필수 필드 없음 |
| `E204` | INVALID_ENUM_VALUE | 정의되지 않은 Enum 값 |
| `E205` | CONFIDENCE_OUT_OF_RANGE | confidence가 0~1 밖 |

### E3xx — 통제항목 ID

| 코드 | 이름 | 상황 |
|---|---|---|
| `E301` | CONTROL_ID_NOT_IN_KB | KB에 없는 ID |
| `E302` | CONTROL_ID_NOT_IN_CANDIDATES | 후보 목록 밖의 ID를 생성 |
| `E303` | CONTROL_NAME_MISMATCH | ID와 통제항목명이 불일치 |
| `E304` | DUPLICATE_CONTROL_ID | 같은 ID가 두 번 |

### E4xx — Citation

| 코드 | 이름 | 상황 |
|---|---|---|
| `E401` | CITATION_MISSING | 매핑했는데 인용이 없음 |
| `E402` | CITATION_CHUNK_NOT_FOUND | 입력에 없는 `chunk_id` |
| `E403` | CITATION_QUOTE_NOT_IN_SOURCE | 인용문이 원문에 없음 (지어냄) |
| `E404` | CITATION_PAGE_MISMATCH | 페이지가 청크와 다름 |
| `E405` | CITATION_EMPTY_QUOTE | 빈 인용문 |
| `E406` | CITATION_FOREIGN_EVIDENCE | 다른 증적의 청크를 참조 |

### E5xx — 판단 규칙

| 코드 | 이름 | 상황 |
|---|---|---|
| `E501` | PRIMARY_COUNT_INVALID | MATCHED인데 PRIMARY가 1개가 아님 |
| `E502` | TOO_MANY_MAPPED_CONTROLS | 매핑이 상한(3개) 초과 |
| `E503` | NO_MATCH_WITH_CONTROLS | NO_MATCH인데 매핑이 있음 |
| `E504` | MATCHED_WITHOUT_CONTROLS | MATCHED인데 매핑이 없음 |
| `E505` | ADEQUACY_JUDGMENT_DETECTED | Phase 1에서 적정성 판정을 생성 (→ `R102`) |

### E506 · E507 — 경고 (v0.6 신설)

**이 둘만 `issues`가 아니라 `validation.warnings`에 들어간다.**
`validation.passed`를 깨지 않고, 검토로도 보내지 않는다.

| 코드 | 이름 | 상황 |
|---|---|---|
| `E506` | REASON_TOO_SHORT | 판단 근거가 10자 미만 |
| `E507` | REASON_NOT_SPECIFIC | 근거가 증적 원문 복붙이거나 통제항목 명칭 반복 |

근거가 부실한 것은 결과가 틀린 것과 다르다. 이걸로 사람을 부르면 검토량만 늘고
정확도는 안 오른다. 모아 두고 **프롬프트를 고칠 자료로 쓴다.**
작성 규칙은 `mapping_rules_v0.6.md` 2.1에 있다.

---

## 3. 재시도 정책

| 항목 | 값 | 근거 |
|---|---|---|
| 최대 재시도 | **1회** | 2회차도 깨지면 프롬프트·모델 문제다. 더 돌려도 같은 결과에 시간만 쓴다 |
| Timeout | **60초** | 임시값. 로컬 LLM 실측 후 조정 |
| 재시도 간격 | 2초 | |
| 총 소요 상한 | 약 122초 | 60 + 2 + 60 |

### 재시도 대상

```
E101 E102 E103 E104 E105   LLM 호출 실패
E201 E202                  깨진 JSON, 스키마 위반
```

### 재시도하지 않음

```
E3xx E4xx E5xx             내용 자체가 잘못된 경우 → 바로 Human Review
```

### 재시도할 때

같은 프롬프트를 그대로 보내지 않는다. **"직전 응답이 이 규칙을 어겼다"는 내용을 덧붙인다.**
그냥 다시 보내면 같은 답이 나온다.

재시도 횟수는 결과에 기록한다. 몇 번 만에 성공했는지가 모델 안정성 지표가 된다.

---

## 4. 실패해도 멈추지 않는다

판단 파트는 **어떤 경우에도 예외를 밖으로 던지지 않는다.** 증적 하나가 실패했다고
전체 배치가 멈추면 안 된다.

실패해도 결과 객체는 반드시 만든다.

```json
{
  "evidence_id": "EVID-2026-000009",
  "processing_status": "FAILED",
  "match_status": "NO_MATCH",
  "mapped_controls": [],
  "validation": {
    "passed": false,
    "schema_valid": false,
    "control_ids_valid": false,
    "citations_valid": false,
    "rules_valid": false,
    "issues": [
      { "code": "E106", "message": "재시도 1회 후에도 JSON 파싱 실패" }
    ]
  },
  "human_review": {
    "required": true,
    "status": "PENDING",
    "reasons": ["R108"]
  }
}
```

`processing_status = FAILED`이면 `match_status`는 `NO_MATCH`로 두되,
**이건 "관련 항목 없음"이 아니라 "판단 못 함"이다.** 화면에서 둘을 구분해 표시해야 하고,
평가 지표를 계산할 때도 FAILED는 제외한다.

---

## 5. 로그

실패든 성공이든 남긴다. 없으면 원인 분석이 불가능하다.

| 항목 | 이유 |
|---|---|
| `trace_id` | 증적 하나의 처리를 끝까지 추적 |
| LLM 원본 응답 | 파싱 실패 원인을 보려면 원문이 필요 |
| 프롬프트 버전·모델명 | 어제와 결과가 다른 이유 |
| 재시도 횟수 | 모델 안정성 |
| 소요 시간 | 성능 지표 |

원본 응답에는 마스킹된 증적이 들어간다. **로그 보관 기간과 접근 권한은 백엔드 담당과 합의해야 한다.**
