# Phase 2 인터페이스

Phase 1 결과를 Phase 2 가 받을 수 있는 모양으로 맞추고, Phase 2 가 내놓을 결과의
모양·상태 전이·오류 처리를 정한 곳. **판정 자체는 하지 않는다** (그건 `phase2_판단`).

상태 DRAFT.

---

## 폴더

```
phase2_인터페이스/
├── README.md
├── requirements.txt
│
├── phase2_input.schema.json      Phase 2 가 받는 입력 규격
├── phase2_output.schema.json     Phase 2 가 내는 출력 규격
├── phase2_input_sample.json      입력 예시
│
├── build_phase2_input.py         Phase 1 결과 → Phase 2 입력 조립
├── overall_result.py             문항별 판정 → 통제항목 종합 결론
├── phase2_status.py              상태 8개의 전이표
├── phase2_errors.py              오류 코드 · 검토 사유 · 재시도
│
└── test/
    ├── run_schema_rules.py       스키마가 틀린 모양을 거르는지 (폴더 단독)
    ├── run_status_check.py       상태 전이 검사
    ├── run_error_check.py        오류 · 검토 흐름 검사
    ├── run_output_validation.py  출력 규격 · 판정 규칙 검증
    └── run_integration.py        Phase 1 결과 연동 시험
```

루트의 `.py` 네 개는 **검사용이 아니라 본 코드**다. 판정 파이프라인이 import
해서 쓴다. `test/run_*.py` 다섯 개만 검사 스크립트다.

**폴더 이름을 바꾸면 안 된다.** `phase2_판단` 이 `phase2_인터페이스` 를 경로로
직접 적어둔 곳이 7군데다 (`contracts.py`, `structured_output_adapter.py`,
`judgment_harness.py`, `tests/`, `tools/`). `phase2_input_sample.json` 도
`phase2_판단/tests/test_validated_pipeline.py` 가 직접 읽으므로 지우면 안 된다.

## 돌리는 법

검사 스크립트는 **DB·LLM 에 붙지 않는다.** 파일만 읽으므로 몇 초 걸린다.

```bash
pip install jsonschema

cd phase2_인터페이스/test
python run_schema_rules.py          # 저장소 없이 이 폴더만으로 돈다
python run_status_check.py          # 아래 넷은 저장소를 알아서 찾는다
python run_error_check.py
python run_output_validation.py
python run_integration.py
```

저장소를 못 찾으면 위치를 직접 넘긴다. `python run_status_check.py C:\I_P\I_P-main`

`run_schema_rules.py` 만 저장소가 필요 없다. 스키마 파일을 받은 쪽이 바로
돌려볼 수 있는 유일한 검사다.

## 다른 파트에서 쓸 때

```python
import sys
sys.path.insert(0, "phase2_인터페이스")

from build_phase2_input import build, judge_targets
from overall_result import compute, is_critical, validate
from phase2_status import from_phase1, from_phase2, check, approve_review
from phase2_errors import decide_review, provisional_fields, failed_item, evidence_outcome
```

| 함수 | 하는 일 |
|---|---|
| `build(phase1_result, chunks, scope, version)` | Phase 1 결과 → Phase 2 입력 |
| `judge_targets(phase2_input)` | 실제로 판정할 통제항목만 골라낸다 |
| `compute(items, policy)` | 문항별 판정 → `overall_result` 와 집계 |
| `validate(output)` | 조립된 출력이 판정 규칙과 맞는지 역산 |
| `from_phase1(...)` / `from_phase2(...)` | 판정 결과 → `evidence.status` |
| `check(현재, 다음)` | 못 가는 상태 전이면 `ValueError` |
| `decide_review(items, errors, review_signals=...)` | 사람 검토가 필요한지 + 사유 코드 |
| `provisional_fields(review)` | 번역 못 한 검토 신호가 있으면 `provisional` 두 필드 |
| `failed_item(...)` | 실패한 문항을 `UNKNOWN` 으로 만든다 |
| `evidence_outcome(items, errors)` | 통제항목 전체의 결말 |

## 규격 버전

| | |
|---|---|
| 입력 | `phase2-input-0.2` |
| 출력 | `phase2-output-0.3` |
| 상태 | `phase2-status-0.1` |
| 오류 | `phase2-errors-0.1` |
| 종합 판정 규칙 | `overall-v0.1-checkkind` |

결과마다 `schema_version`·`rule_version`·`judge_policy`·`critical_policy` 를
찍어둔다. 규칙이 바뀌어도 어느 기준으로 나온 결과인지 구분되고, 값이 다른 결과는
섞어서 집계하지 않는다.

**주의** — 10/2 에 배포한 입력 `0.2` 와 지금 `0.2` 는 내용이 다르다. required 필드가
늘었는데 다른 팀 코드 변경을 줄이려고 번호를 올리지 않았다. 넘길 때 "이전 `0.2` 는
버리고 이 파일로 교체" 를 꼭 같이 전한다.

---

## 핵심 규칙 셋

**① 상태값을 새로 만들지 않는다.** 입력팀 `config.VALID_STATUSES` 8개 중
`MAPPING`·`VALIDATING` 이 정의만 돼 있고 아무도 안 쓰던 자리다. 거기에 들어간다.

**② 문항 하나가 실패해도 통제항목 전체를 실패시키지 않는다.** 실패한 문항만
`UNKNOWN` 으로 둔다 (`NOT_MET` 이 아니다 — 안 지켰다고 확인한 게 아니라
확인을 못 한 것이다). 판단팀이 Phase 1 에서 세운 원칙을 그대로 따른다.

**③ 판정 대상은 `PRIMARY` 만이다** (`judge_policy = primary_only`, 확정).
`RELATED` 는 판정하지 않고 참고 증적으로 연결만 한다. 그 항목에 `PRIMARY` 증적이
따로 생기면 `RELATED` 청크도 같이 근거로 쓴다. `PRIMARY` 가 끝까지 없으면
`증적 없음` 으로 낸다 — `판단 불가` 와 구분한다. `all_mapped` 로 넓히면 판정 대상이
약 1.5배가 된다. `enum` 에 값만 남겨뒀고, 바꾸려면 기본값 한 줄이다.

`PRIMARY` 가 **0개인 입력도 정상**이다 (`minContains: 0`). "PRIMARY 는 당연히
1개 이상" 이라고 가정해서 구현하면 `증적 없음` 케이스가 터진다.

---

# 상태 전이 (`phase2_status.py`)

## 상태 8개

입력팀 `database/config.py` 의 `VALID_STATUSES` 와 판단팀 `src/enums.py` 의
`EvidenceStatus` 가 이미 같은 8개다. 아래는 각 값에 Phase 2 를 얹은 뒤의 의미다.

| 상태 | 단계 | 뜻 | 누가 씀 |
|---|---|---|---|
| `UPLOADED` | upload | 파일만 올라왔다 | 입력팀 `main.py` |
| `PREPROCESSING` | preprocess | 파싱·청킹 중 | 입력팀 `pipeline.py` |
| `PREPROCESSED` | preprocess | 청크 적재 끝. Phase 1 대기 | 입력팀 `pipeline.py` |
| `MAPPING` | phase1 | **Phase 1 매핑 중** | *(아직 아무도)* |
| `VALIDATING` | phase2 | **Phase 2 판정 중 / 판정 대기** | *(아직 아무도)* |
| `COMPLETED` | done | **Phase 2 까지 끝났다** | 판단팀 `service.py` |
| `REVIEW_REQUIRED` | review | 사람 검토 대기 | 판단팀 `service.py` |
| `FAILED` | failed | 실패 | 입력팀·판단팀 |

WBS 에 적은 `ANALYZING` 은 **`MAPPING`** 을 가리킨다. 값을 새로 만들지 않는다.

## 전이표

표에 없는 전이는 거부한다 (`phase2_status.ALLOWED`).

| 현재 | 갈 수 있는 곳 |
|---|---|
| `UPLOADED` | `PREPROCESSING` · `FAILED` |
| `PREPROCESSING` | `PREPROCESSED` · `FAILED` |
| `PREPROCESSED` | `MAPPING` · `FAILED` |
| `MAPPING` | `VALIDATING` · `REVIEW_REQUIRED` · `FAILED` |
| `VALIDATING` | `COMPLETED` · `REVIEW_REQUIRED` · `FAILED` |
| `REVIEW_REQUIRED` | `MAPPING` · `VALIDATING` · `COMPLETED` · `FAILED` |
| `FAILED` | `PREPROCESSING` · `MAPPING` · `VALIDATING` |
| `COMPLETED` | *(없음 — 끝)* |

```
UPLOADED → PREPROCESSING → PREPROCESSED → MAPPING → VALIDATING → COMPLETED
                                            │           │
                                            └───────────┴──→ REVIEW_REQUIRED
                                                                   │
                                            ┌──────────────────────┘
                                            └──→ 멈췄던 자리로
```

재시도는 처음부터가 아니라 **터진 단계부터** 한다. `COMPLETED` 는 끝이고, 같은
증적의 파일을 다시 올리면 `version` 이 올라가고 `UPLOADED` 로 새로 시작한다.

## Phase 결과 → `evidence.status`

```
from_phase1(processing_status, review_required, phase2_enabled=True)
    FAILED                  → FAILED
    review_required         → REVIEW_REQUIRED
    그 외, Phase 2 를 돌릴 것 → VALIDATING      ← 판단팀 원본은 COMPLETED
    그 외                    → COMPLETED

from_phase2(processing_status, review_required, phase1_review_open)
    FAILED                  → FAILED
    review_required         → REVIEW_REQUIRED
    phase1_review_open      → REVIEW_REQUIRED
    그 외                    → COMPLETED
```

`phase1_review_open` 에는 **기본값이 없다.** Phase 2 가 깨끗하게 끝났다고
`COMPLETED` 로 덮으면 사람이 아직 안 본 Phase 1 검토가 조용히 사라진다.
상태 칸이 하나뿐이라 덮어쓰면 끝이다. 안 넘기면 `TypeError` 가 나서 바로 안다.
호출하는 쪽은 Phase 1 결과의 `human_review.required` 를 그대로 넘긴다.

## 검토가 끝났을 때

```
approve_review(phase, modified)
  phase1 검토, 그대로 승인 → VALIDATING   (판정으로 넘어간다)
  phase1 검토, 사람이 고침 → MAPPING      (매핑부터 다시)
  phase2 검토, 그대로 승인 → COMPLETED    (끝)
  phase2 검토, 사람이 고침 → VALIDATING   (판정부터 다시)
```

`REVIEW_REQUIRED` 와 `FAILED` 는 Phase 1 에서도 Phase 2 에서도 오는데
`evidence.status` 는 칸이 하나뿐이라 어느 단계에서 멈췄는지 적을 자리가 없다.
`evidence_history` 테이블은 **파일 교체 이력**이고 상태 전이 이력이 아니다.
그래서 호출하는 쪽이 `phase` 를 넘겨준다 (DB 변경 없음). 전이가 꼬이는 사고가
실제로 나면 그때 `evidence_status_history` 테이블을 추가한다.

## Phase 1 코드 수정 여부 — 없다

- 입력팀 `config.VALID_STATUSES` 8개 그대로. 전이표와 완전히 일치
- 판단팀 `enums.EvidenceStatus` 8개 그대로. 완전히 일치
- `update_status()` 는 이미 8개 밖의 값을 `ValueError` 로 막는다
- `MAPPING`·`VALIDATING` 은 정의만 있고 미사용 — 그 자리를 쓰는 것이므로 추가 없음
- `schema.sql` 의 `status VARCHAR(20)` — `REVIEW_REQUIRED`(15자)가 들어가므로 충분

---

# 오류 · 검토 (`phase2_errors.py`)

**새로 만들지 않고 Phase 1 것을 쓴다.** `phase2_판단/src/judgment_harness.py` 가
이미 판단팀 `ErrorCode` 와 `RetryPolicy` 를 import 해서 쓰고 있다.

## 오류 코드

### Phase 1 에서 그대로 쓰는 것 — 17개

| 범위 | 내용 |
|---|---|
| `E101`~`E106` | LLM 호출 (타임아웃·연결 실패·서버 오류·빈 응답·잘린 응답·재시도 소진) |
| `E201`~`E204` | 출력 형식 (JSON 파싱·규격 위반·필수 필드 누락·잘못된 값) |
| `E401`~`E407` | 인용 (없음·청크 못 찾음·원문과 다름·페이지 불일치·빈 인용 등) |

**값은 판단팀이 소유한다.** 여기서 다시 정의하지 않는다.

### Phase 2 에만 있는 것 — `P2E` 19개

| 코드 | 이름 | 비고 |
|---|---|---|
| `P2E001` | `NO_JUDGE_TARGET` | `judge=true` 인 항목이 없음. **실패가 아니라 '증적 없음'** |
| `P2E002` | `CHECKLIST_SCOPE_MISSING` | 질문지가 아직 없음 |
| `P2E003` | `NO_CHUNKS` | `phase2_판단` `ContextBuildError` 와 같은 이름 |
| `P2E004` | `INVALID_CHUNK` | 〃 |
| `P2E005` | `INPUT_MALFORMED` | 입력 스키마 위반. **통제항목 전체 `FAILED`** |
| `P2E006` | `CHECKLIST_NOT_APPROVED` | 미승인 체크리스트. 지금은 전건 해당 → 기록만 |
| `P2E007` | `DUPLICATE_CHUNK_ID` | |
| `P2E008` | `NO_EVIDENCE_ANCHOR` | Phase 1 citation 이 가리키는 청크가 없음 |
| `P2E009` | `ANCHOR_CHUNK_NOT_FOUND` | **증적 version 이 올라갔는데 옛 Phase 1 결과를 씀** |
| `P2E301` | `ITEM_ID_NOT_IN_CHECKLIST` | 질문지에 없는 문항을 지어냄 |
| `P2E302` | `ITEM_COUNT_MISMATCH` | 문항 수가 다름 |
| `P2E303` | `DUPLICATE_ITEM_ID` | |
| `P2E501` | `CITATION_REQUIRED_MISSING` | MET·NOT_MET 인데 인용 없음 |
| `P2E502` | `REASON_CODE_MISMATCH` | `result` 와 `reason_codes` 접두사가 안 맞음 |
| `P2E503` | `CRITICAL_UNDECIDED` | |
| `P2E504` | `OVERALL_RULE_CONFLICT` | 종합 판정이 규칙 계산값과 다름 |
| `P2E505` | `ADEQUACY_WITHOUT_EVIDENCE` | MET 인데 인용이 지어낸 문장 |
| `P2E506` | `REASON_TOO_SHORT` | 10자 미만. **검토로 안 보냄** |
| `P2E901` | `BATCH_ABORTED` | **통제항목 전체 `FAILED`** |

## 검토 사유 코드 — `P2R` 18개

Phase 1 의 `R1xx`·`R2xx` 와 겹치지 않게 `P2R` 접두사. 번호가 하나도 안 겹치는 것을
확인했다.

### `P2R1xx` 무조건 검토 — 13개

| 코드 | 언제 |
|---|---|
| `P2R101` | 재시도까지 했는데 JSON 이 안 나옴 |
| `P2R102` | 출력이 규격에 안 맞음 |
| `P2R103` | 질문지에 없는 문항이거나 문항 수가 다름 |
| `P2R104` | 인용이 청크 원문과 다름 |
| `P2R105` | MET·NOT_MET 인데 근거가 없음 |
| `P2R106` | LLM 을 끝내 못 부름 |
| `P2R107` | 청크·질문지가 없어 판정을 시도하지 못함 |
| `P2R108` | 증적 본문에 지시문처럼 보이는 내용 |
| `P2R109` | 종합 판정이 규칙과 어긋남 |
| `P2R110` | Phase 1 결과가 낡음 — 증적 version 이 올라감 |
| `P2R111` | Self-check 가 그 판정을 뒷받침하지 못함 |
| `P2R112` | Self-check 가 근거끼리 어긋난다고 봄 |
| `P2R113` | **번역표에 없는 검토 신호가 옴 — `provisional=true` 로 낸다** |

### `P2R2xx` 조건부 검토 — 5개

| 코드 | 조건 | 임계 |
|---|---|---|
| `P2R201` | critical 문항이 미충족 | 켜짐 |
| `P2R202` | UNKNOWN 비율이 높음 | **0.5** |
| `P2R203` | OCR 청크를 인용 | 켜짐 |
| `P2R204` | 문항이 전부 UNKNOWN | 켜짐 |
| `P2R205` | Self-check 가 확정 판정을 보류 | 켜짐 |

`P2R203` 은 Phase 1 `R207` 과 같은 이유다. 전처리팀이 OCR 품질 점수를 주지 않아
글자가 맞는지 알 방법이 없다. **작동하려면 판단팀이 인용에 `citations[].source` 를
같이 실어야 한다** (`text` / `ocr` / `table` — 입력 `chunks[].source` 그대로).

## Self-check 신호 → 검토 사유 코드

판단팀 `validated_pipeline.py` 가 `audit.review_signals` 에 `"SELF_CHECK_" + verdict`
형태로 신호를 넣는다. 번역표는 `phase2_errors.SELF_CHECK_SIGNALS` 다.

```python
review = decide_review(items, errors, review_signals=audit["review_signals"])
out["human_review"] = review
out.update(provisional_fields(review))     # 모르는 신호가 없으면 아무것도 안 붙는다
```

| 신호 | 코드 |
|---|---|
| `SELF_CHECK_UNSUPPORTED` | `P2R111` |
| `SELF_CHECK_CONFLICT` | `P2R112` |
| `SELF_CHECK_UNCERTAIN` · `SELF_CHECK_LOW_CONFIDENCE` | `P2R205` |
| `SELF_CHECK_NOT_RUN` | `P2R106` |
| *표에 없는 신호* | `P2R113` + `provisional=true` |

표에 없는 신호가 오면 `decide_review()` 가 `unmapped_signals` 키를 같이 돌려주고,
`provisional_fields()` 가 그것으로 `provisional` 두 필드를 만든다.

```python
{"required": True, "reasons": ["P2R113"],
 "unmapped_signals": ["SELF_CHECK_BRAND_NEW"]}
→ {"provisional": True,
   "provisional_reason": "UNMAPPED_REVIEW_SIGNAL: SELF_CHECK_BRAND_NEW"}
```

경고만 내면 결과 JSON 에 흔적이 안 남아서 받는 쪽이 "공식 코드가 없다" 며 최종
출력을 보류한다. 그래서 결과 자체에 남긴다. 번역표에 그 신호를 더하면 `P2R113` 과
`provisional` 은 둘 다 사라진다.

`unmapped_signals` 는 값이 있을 때만 생기는 키다. 정상 실행에서는 기존과 글자까지
같은 dict 가 나오므로 받는 쪽 코드는 안 바뀐다.

## 검토로 보내지 않고 기록만 하는 것 — 7개

`P2E001` `P2E002` `P2E006` `E407` `P2E503` `P2E506` `P2E901`

**틀린 결과가 아니기 때문이다.**

- `P2E001`·`P2E002` — 판정 대상이나 질문지가 없는 건 정상 결과(`증적 없음`)다
- `P2E006` — 미승인 체크리스트는 지금 전건에 해당해서 검토 신호가 못 된다
- `P2E506` — 판단팀이 `E506` 을 검토로 안 보내는 것과 맞췄다. 근거가 부실한 것이지
  틀린 게 아니다. 임계값도 판단팀 `validators.REASON_MIN_CHARS = 10` 과 같다
- `P2E901` — 배치 중단은 결과 하나의 문제가 아니라 운영 사건이다

## 문항 하나가 끝내 실패하면

```python
{ "item_id": "2.5.1-Q11",
  "result": "UNKNOWN",          # ← NOT_MET 이 아니다
  "reason": "판정하지 못했습니다 (LLM_TIMEOUT): ...",
  "citations": [],              # UNKNOWN 은 비워도 되는 유일한 값
  "error_code": "E101" }
```

`NOT_MET` 으로 두면 **요건을 안 지켰다고 확인한 것**이 된다. 확인을 못 한 것과 다르다.
통제항목은 나머지 문항으로 계속 가고, 검토 사유만 붙는다.

### 통제항목 전체가 `FAILED` 인 경우는 둘뿐

| 상황 | 결말 |
|---|---|
| 입력이 규격 위반 (`P2E005`) | `FAILED` |
| 배치 중단 (`P2E901`) | `FAILED` |
| 판정 대상·질문지 없음 (`P2E001`·`P2E002`) | `COMPLETED` — '증적 없음' |
| 문항 일부 실패 | `COMPLETED` 또는 `REVIEW_REQUIRED` |

## 재시도

**판단팀 `DEFAULT_RETRY_POLICY` 를 그대로 쓴다.**

```
max_retries  1 / timeout  60초 / backoff  2.0초
retry_on     LLM_TIMEOUT, LLM_CONNECTION_FAILED, LLM_SERVER_ERROR,
             LLM_EMPTY_RESPONSE, LLM_TRUNCATED_RESPONSE,
             JSON_PARSE_FAILED, SCHEMA_INVALID
```

**배치 중단은 구현하지 않는다.** Phase 1 은 증적 하나에 LLM 을 한 번 부르지만
Phase 2 는 문항마다 부른다. LLM 이 죽은 채로 끝까지 돌면 시간을 크게 버리지만,
문항마다 결과가 찍히므로 전부 실패하면 몇 분 안에 눈에 띄고, 멈추고 싶으면
`Ctrl+C` 로 된다. **시간만 버릴 뿐 데이터가 깨지지 않는다** — 실패한 문항은
`UNKNOWN` 으로 남는다. 7명이 손으로 돌리는 도구라 무인 실행이 없다.

상수만 남겨뒀고 아무도 호출하지 않으면 아무 일도 일어나지 않는다.

```python
BATCH_ABORT_AFTER = 5
BATCH_ABORT_ON = ("E101", "E102", "E103")   # 서버가 죽은 신호일 때만
```

---

## 종합 판정 (`overall_result.py`)

문항별 `MET`/`NOT_MET`/`UNKNOWN` 을 통제항목 하나의 결론으로 모은다.

| 결론 | 조건 |
|---|---|
| `미충족` | critical 문항 중 `NOT_MET` 이 있음 |
| `확인 필요` | critical 문항 중 `UNKNOWN` 이 있음 (`NOT_MET` 은 없음) |
| `충족(보완 권고)` | critical 은 전부 `MET`, 비critical 에 `NOT_MET`·`UNKNOWN` 이 있음 |
| `충족` | 전부 `MET` |
| `증적 없음` | 판정할 증적이 없음 (`items` 가 빔) |

나쁜 쪽이 이긴다. `NOT_MET` 과 `UNKNOWN` 이 같이 있으면 `미충족` 이다.

**critical 은 `check_kind` 로 정한다** (`critical_policy.mode = check_kind`).
`procedure`·`implementation` 이 critical, `record` 는 비critical 이다. 체크리스트의
`critical` 필드가 전부 `UNDECIDED` 라 쓸 값이 없어서다. 기준팀이 채우면
`mode` 를 `explicit` 으로 바꾼다.

`validate(output)` 이 조립된 출력을 역산해서 규칙과 어긋나면 `P2E504` 를 낸다.
스키마만으로는 이 의미 검사를 할 수 없다.

---

