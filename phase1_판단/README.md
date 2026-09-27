# Phase 1 판단 파트 — 규격 · 규칙 · Validator · 골든셋 (v0.13)

작성: 판단팀 · 2026-09-24
상태: **초안. 팀 리뷰 전**

앞단(전처리·검색) 완성 여부와 무관하게 **단독으로 만들 수 있는 부분**만 담았다.
이 규격이 확정돼야 다른 팀이 무엇을 넘겨주고 무엇을 받는지가 정해진다.

> **PII 마스킹은 프로젝트 범위에서 빠졌다 (2026-09-24).**
> 증적 본문이 마스킹 없이 그대로 LLM에 가고, 그대로 로그에 남는다.
> 골든셋과 테스트 증적을 **전부 합성으로 만든 이유**가 이것이다.
> 실제 문서를 레포에 커밋하면 git 히스토리에 영구히 남는다.

---

## 확인 방법

```bash
pip install pydantic pytest
pytest                          # 269개
python src/check.py             # 스키마·정책 검증 + JSON Schema 생성
python tools/report_goldenset.py  # 검토 전환율과 임계값 시나리오
python tools/eval_goldenset.py --self-test   # 평가 지표 계산 점검
```

`schemas/*.json`과 `configs/thresholds.yaml`은 `check.py`가 **코드에서 자동 생성**한다.
손으로 고치지 않는다. 원본(모델·`Thresholds`)을 고치고 스크립트를 다시 돌린다.
**손으로 고치면 `check.py`가 FAIL을 낸다.**

---

## 구성

```
docs/
├── output_schema_v0.3.md          입출력 필드 정의표 ← 다른 팀은 이것만 보면 된다
├── mapping_rules_v0.7.md          판단 규칙 (1:N 매핑, control_id, Citation, 위반 처리)
├── human_review_policy_v0.1.md    검토 조건, 임계값, 상태 머신
├── error_codes_v0.1.md            오류코드, 재시도·Timeout 정책
├── goldenset_v0.1.md              골든셋 29건 구성과 측정 결과
├── human_review_storage_v0.1.md   검토 큐·수정 이력 저장 구조 (백엔드 합의 필요)
└── json_format_v0.1.md            JSON 형식 총정리 ← 다른 팀 공유용
                                   ※ mapping_rules 13장 = 프롬프트에 그대로 넣을 문구

src/
├── enums.py                       판단팀이 소유하는 공통 Enum (상태값·오류코드)
│                                  + EvidenceStatus (입력팀 소유. 우리는 변환만 한다)
├── models.py                      Pydantic 입출력 모델
├── kb.py                          통제항목 101개 ID·명칭 조회
├── output_parser.py               LLM 원본 응답 → 모델 (망가진 응답 처리)
├── validators.py                  Control ID · Citation · 판단 규칙 검증
├── review_policy.py               검토 전환 판단 + 상태 전이
├── review.py                      검토 큐·수정 이력·골든셋 초안 추출
├── metrics.py                     평가 지표 (Precision/Recall/F1 …)
├── service.py                     파싱 → 검증 → 검토 → 최종 결과 조립
└── check.py                       스키마·정책 검증 + JSON Schema 생성

tools/build_goldenset.py           골든셋 생성기 (JSON을 직접 고치지 않는다)
tools/report_goldenset.py          검토율·임계값 시나리오 보고
tools/eval_goldenset.py            LLM 결과로 정확도 평가
tests/                             pytest 269개
tests/fixtures/goldenset.json      골든셋 29건
schemas/                           모델에서 생성된 JSON Schema
samples/                           단일 / 다중 / NO_MATCH / 검토필요 예시
data/control_index.json            KB에서 뽑은 ID → 명칭 표
configs/thresholds.yaml            임계값 (review_policy.py에서 생성됨)
```

---

## 판단 한 건 처리하기

```python
from service import build_result

result = build_result(
    raw_response,      # LLM이 뱉은 문자열 그대로
    mapping_input,     # 청크 + Top-K 후보
    versions,          # 프롬프트·모델·규칙 버전
)
```

돌려주는 것은 항상 `Phase1MappingResult`다.
**어떤 입력이 와도 예외를 던지지 않는다.** 증적 하나가 실패했다고 배치가 멈추면 안 된다.

실패하면 `processing_status = FAILED`, `validation.issues`에 오류코드,
`human_review.required = True`가 된다.

---

## 핵심 결정 10가지

**1. `match_status` + `relation` 2단 구조**

`ONE_TO_ONE` / `ONE_TO_MANY`는 저장하지 않는다. 배열 길이로 계산되는 값이라
따로 저장하면 어긋날 수 있다.

**2. PRIMARY는 MATCHED일 때 정확히 1개**

0개를 허용하면 Phase 2가 뭘 먼저 볼지 못 정하고, 2개를 허용하면 모델이 고민을 회피한다.
고르기 어려우면 사람에게 넘긴다.

**3. 점수 필드 4개를 절대 합치지 않는다**

`similarity_score`(검색) / `llm_confidence`(모델) / `validation.passed`(검증) /
`human_review.required`(정책). 합치면 어디서 틀렸는지 추적이 안 된다.
`llm_confidence`는 정확도가 아니다.

**4. 임계값은 코드 밖, 값은 전부 임시**

골든셋으로 `narrow_score_gap`은 0.03 → 0.01로 조정했다(근거는 `goldenset_v0.1.md`).
`low_confidence`(0.70)는 **실제 LLM을 붙이기 전에는 분포조차 알 수 없다.**

**5. `UNCERTAIN`만 남아도 `match_status`는 `NO_MATCH`다 (v0.6)**

값을 늘리면 Phase 2·화면·DB가 전부 바뀐다. 대신 `R107`로 구분하고
**평가할 때 따로 센다.** 안 그러면 "판단 못 한 것"이 "관련 없다고 맞힌 것"으로
집계돼서 `no_match_accuracy`가 부풀려진다.

**6. `issues`와 `warnings`를 나눈다 (v0.6)**

결과가 **틀린 것**은 `issues`, 근거가 **부실한 것**은 `warnings`다.
근거가 짧다고 사람을 부르면 검토량만 늘고 정확도는 안 오른다.
`warnings`는 프롬프트를 고칠 자료로 모은다.

**7. Phase 1에서 적정성 판정을 하면 잡아낸다 (v0.6)**

Phase 1은 **증적과 통제항목을 연결하는 것까지만** 한다.
모델이 "적정하게 이행하고 있다" 같은 판정을 내면 `E505`로 잡아 사람에게 보낸다.
검증되지 않은 판정이 그대로 보고서로 넘어가는 것이 인증 심사 도구에서 가장 위험하다.

`reason`만 검사하고 `quote`는 보지 않는다. 증적 원문에는 "미흡", "위반"이 얼마든지 나온다.

**8. 판단을 지우지 않는다 (v0.7)**

PRIMARY를 고르기 어려워도 `RELATED` 판단을 `UNCERTAIN`으로 내리지 않는다.
판단을 지우면 사람이 검토할 대상이 사라지고, 2개가 경쟁할 때는 `NO_MATCH`가 되어
관련 통제항목이 분명히 있는데도 "관련 없음"으로 나간다.

대신 판단을 남기고 `llm_confidence`를 낮게 매긴다. 그러면 `R201`로 알아서 사람에게 간다.
**모델은 판단을 남기고, 사람이 고친다.** 틀린 판단도 골든셋 재료가 된다.

**9. 판단 못 한 증적도 결과를 남긴다 (v0.7)**

청크가 0개면 `MappingInput`을 만들 수조차 없어서 결과 객체가 아예 안 생겼다.
그러면 **그 증적은 화면에서 사라지고 통계에서도 빠진다.**
`build_unjudgeable_result()`로 `FAILED` 결과를 만들어 `R110`으로 사람에게 보낸다.

**10. 임계값은 코드가 원본이다 (v0.8)**

v0.7까지는 "임계값은 `thresholds.yaml`에서 관리한다"고 써놨는데 **코드가 그 파일을
읽은 적이 없었다.** 값이 같아서 티가 안 났을 뿐이다(검색팀이 찾아줬다).

원본을 `review_policy.py`로 모으고, YAML은 `check.py`가 생성한다.
`schemas/*.json`과 같은 방식이다. **YAML을 손으로 고치면 `check.py`가 FAIL을 낸다.**

임계값을 바꾸면 검토 대상이 크게 달라진다. 조용히 일어나면 안 되는 변경이므로
코드에 두고 PR 리뷰를 거치게 한다. 근거 주석도 값 바로 옆에 남는다.

---

## 평가 지표와 검토 저장 구조

늦게 붙인 둘이라 따로 적는다.

**`src/metrics.py` — 정확도를 재는 계산기**

발표 자료와 코드가 다른 숫자를 말하는 일이 제일 흔한 사고다.
계산은 전부 이 모듈을 거치고, 문서는 이 정의를 인용한다.

| | 기본 동작 | 이유 |
|---|---|---|
| LLM 호출·파싱 실패 | **분모에 포함** | 실패도 못 맞힌 것이다 |
| 정답이 후보 밖 | **제외** | 판단이 아니라 검색 Recall 문제다 |
| `UNCERTAIN`이 섞인 `NO_MATCH` | **따로 센다** | "관련 없음"과 "판단 못 함"은 다르다 |

`tools/eval_goldenset.py`가 실제 LLM 결과 파일을 받아 이 지표를 계산한다.

**`src/review.py` + `docs/human_review_storage_v0.1.md` — 검토 결과를 쌓는 구조**

원칙은 하나다. **판단 결과를 덮어쓰지 않는다.**
사람이 고쳐도 원본은 그대로 두고 수정은 별도 레코드로 쌓는다.

덮어쓰면 ① 모델이 뭘 틀렸는지 ② 사람이 뭘 고쳤는지 ③ 언제 왜 바뀌었는지를 잃는다.
특히 ②는 **골든셋의 가장 좋은 재료**다. `to_goldenset_draft()`가 그걸 뽑아낸다.

사람이 고칠 때는 **구조는 강제, 인용은 경고만** 한다.
Phase 2가 받는 모양은 같아야 하지만, 내용의 최종 권한은 사람에게 있다.

---

## 모델을 두 단계로 나눈 이유

| 모델 | 용도 | 엄격함 |
|---|---|---|
| `LLMMappingOutput` | LLM이 방금 뱉은 것 | 형식만 검사 |
| `Phase1MappingResult` | 저장·전달되는 최종 결과 | 판단 규칙까지 검사 |

LLM 출력 모델에 규칙까지 강제하면 **규칙을 어긴 응답이 객체로 만들어지지도 않아서
무엇이 잘못됐는지 기록할 수가 없다.** 규칙 검사는 Validator가 따로 하고,
위반은 오류코드로 남긴 뒤 Human Review로 보낸다.

---

## 다른 팀에 요청할 것

이 규격이 성립하려면 아래가 필요하다.

**전처리팀** — 청크 형식은 `chunk_format.py`와 JSON 규격 문서에 맞췄다.
- ~~`evidence_id`를 정수로 받는 문제~~ → **전처리팀이 문자열로 바꾸기로 했다.** 반영되면 다시 확인한다.
  우리 쪽은 표기를 고정하지 않고 `chunk_id` **관계만** 검사하므로 어느 쪽이든 그대로 돈다
- **청크의 `source`를 OCR일 때 `"ocr"`로 채워줄 수 있는가.** 파서 블록에 `source` 칸이 없어서
  지금 구조면 `R207`이 한 번도 발동하지 않는다. **OCR은 아직 미구현이라 나중에 다시 본다**
- 스캔 PDF는 지금 `empty_document` → 청크 `[]` → `FAILED`가 된다.
  **시연용 증적은 텍스트 레이어가 있는 파일이어야 한다**
- Citation 원문 대조를 하므로, **LLM에 넘어가는 텍스트와 저장된 청크 `text`가 같아야 한다**

**검색팀** — 결과 형식은 ChromaDB 구현에 맞췄다. Top-K는 **5로 진행**한다.
- **청크별 검색 결과를 증적 단위로 합쳐서 달라.** 지금 검색이 청크 단위라 증적 하나에
  후보가 5×N개 나오는데 **합치는 주체가 정해지지 않았다.**
  후보 목록 자체가 여기서 정해지므로, 남은 것 중 제일 급하다
- 후보에 `requirement` 본문을 실어달라. KB에 이미 있다

**자운 (LLM 하네스·프롬프트)** — 프롬프트 `phase1_mapping_v0.2.1` 검토 결과를 v0.7에 반영했다.
**`mapping_rules_v0.7.md` 13장에 프롬프트에 그대로 넣을 문구를 정리해 뒀다.** 13장만 보면 된다.
- ~~`llm_confidence` 정의~~ → **판단팀이 정했다 (2.2).** 13.1을 프롬프트에 넣어달라
- `reason` 작성 규칙 (2.1) → 13.2
- **PRIMARY 경쟁 시 `UNCERTAIN`으로 내리지 말 것** (4.4) → 13.3.
  지금 프롬프트 F절대로면 RELATED 2개가 경쟁할 때 `NO_MATCH`가 되어 판단이 사라진다
- **후보 중복은 adapter에서 제거해야 한다** (4.8). 검색이 청크 단위라 실제로 잘 생긴다
- `PromptInputError`가 나면 예외를 던지지 말고 `build_unjudgeable_result()`로 결과를 만들어달라 (8.1) → 13.4
- `RULESET_VERSION`을 `mapping_rules_v0.7`로
- 실제 LLM으로 골든셋 29건을 돌려야 임계값을 확정할 수 있다

**KB 담당**
- ~~`control_id` 표기 규칙~~ → **확정: `x.y.z`, 점 없음, 101개** (KB 파일 확인 완료)
- `kb_version` 문자열 형식

**백엔드 담당** — 저장 구조 제안은 `human_review_storage_v0.1.md`에 있다.
- **LLM 원본 응답 로그의 보관 위치·기간·접근 권한.** PII 마스킹을 뺐으므로
  증적 본문이 그대로 로그에 남는다
- 판단 결과와 검토 이력을 **덮어쓰지 않고 쌓는** 구조가 가능한가
- 검토자 계정 식별 방식 (`reviewer_id` 형식)

---

## 합의가 필요한 것 (단독으로 못 정함)

**정해진 것**

- [x] `control_id` 표기 → **`x.y.z`, 점 없음, 101개** (KB 파일 확인 완료)
- [x] `similarity_score`를 정규화하지 않는다 → 실측 분포에 맞춰 임계값을 잡았다
- [x] `NO_MATCH`와 1:N을 초기에 전부 검토로 보낸다 → **켜고 시작 (2026-09-24)**.
      켜면 83%, 끄면 48%. 정확도가 안정되면 `R204`부터 끈다
- [x] `UNCERTAIN`만 남았을 때 `match_status` → **`NO_MATCH` 유지 + `R107`로 구분 (2026-09-24)**
- [x] Top-K → **5로 진행**
- [x] `llm_confidence`의 정의 → **판단팀이 확정 (2026-09-24, `mapping_rules_v0.7.md` 2.2)**
- [x] PRIMARY를 못 고를 때 → **판단을 남기고 confidence를 낮게 (2026-09-24)**. `UNCERTAIN`으로 내리지 않는다
- [x] 후보 중복 → **입력 단계에서 거부(`E003`).** 합치는 쪽에서 제거한다

**아직 못 정한 것**

- [ ] `UNCERTAIN` 판단값을 쓸지 — 지금은 쓰는 것으로 작성됨
- [ ] 1:N 상한 3개 — 골든셋 정답 분포를 보고 확정
- [ ] Citation 개수 상한 — 지금은 없다
- [ ] `reason` 최소 길이 10자 — 실제 응답 분포를 보고 확정
- [ ] 적정성 판정 표현 목록 — 실제 응답을 보고 늘리거나 줄인다
- [ ] 증적 재업로드 시 이전 판단을 승계할지, 다시 판단할지
- [ ] Phase 1 Review와 Phase 2 Review가 같은 모듈을 쓸지
- [ ] `schemas/`와 공통 Enum 변경은 판단팀 리뷰를 거치는 규칙
