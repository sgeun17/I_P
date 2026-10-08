# Phase 2 통합 — 결과 저장 · 검토 이력 · 커버리지

Phase 2 에서 **DB 를 쓰는 쪽**. 이 폴더는 화면에 넘길 값을 저장하고 꺼내준다.
DB 를 안 쓰는 순수 함수는 `phase2_인터페이스/` 에 있다.

```
phase2_runner.py           Phase 1 이 끝난 증적을 Phase 2 로 넘긴다
phase2_result_store.py     판정 저장·조회·종합·커버리지·UI 응답 조립
phase2_review_store.py     승인·수정·반려와 이력
phase1_mapping_store.py    Phase 1 매핑 결과 저장·조회
phase2_report.py           항목별 근거 리포트 (HTML·CSV)
phase2_schema.sql          표 3개      phase1_mapping.sql   매핑 표 2개
test/run_db_check.py       실제 MySQL 점검 28건
test/run_e2e_demo.py       Phase 1→2 종단 시연
```

---

## 설치

**입력팀 `schema.sql` · `chunk_schema.sql` 을 먼저 돌려야 한다.** `phase2_result` 가
`evidence` 를 외래 키로 참조해서, 그 표가 없으면 `ERROR 1215 Cannot add foreign key
constraint` 가 난다.

```bash
mysql -u root -p evidence_db < phase2_schema.sql
mysql -u root -p evidence_db < phase1_mapping.sql
python test/run_db_check.py          # 28 통과 / 0 실패여야 한다
```

`CREATE TABLE IF NOT EXISTS` 라 여러 번 돌려도 데이터가 안 지워진다. 반대로
**전에 만든 표가 있으면 구조도 안 바꾼다** — `run_db_check.py` 에서
`evidence_version` 이 없다고 나오면 신혜진에게 마이그레이션 SQL 을 받으면 된다.

### `current_key` 는 VIRTUAL 이어야 한다

```sql
current_key VARCHAR(45) GENERATED ALWAYS AS
  (IF(superseded_at IS NULL, CONCAT(evidence_id, '#', control_id), NULL)) VIRTUAL
```

"같은 증적·통제항목의 현재 줄은 하나뿐" 을 DB 가 보장하는 칸이다.
**`STORED` 로 두면 표가 아예 안 만들어진다** — MySQL 은 STORED 생성 컬럼의 재료가
되는 칸에 `ON DELETE CASCADE` 를 못 걸게 하는데 `evidence_id` 가 여기 재료이고 FK 가
CASCADE 다. `VIRTUAL` 이면 그 제약이 없고 UNIQUE 인덱스도 그대로 걸린다.

### 표 다섯 개

`phase2_result`(판정) · `review_history`(사람이 손댄 기록) · `phase2_excluded`(못 돌린
증적과 사유) · `phase1_mapping`(Phase 1 이 고른 통제항목) · `phase1_mapping_citation`.

`phase2_excluded` 가 없으면 올린 증적 33건 중 25건만 화면에 보이고 **8건이 사라진다.**

---

## 쓰는 법

```python
from phase2_runner import run_evidence, run_pending
from phase2_result_store import (save, exclude, unexclude, load, summary_response,
                                 coverage_response, control_response,
                                 evidence_response, review_queue)
from phase2_review_store import approve, modify, reject, history
from phase2_report import to_html, to_csv, reconcile

run_evidence("E0001")           # 증적 하나를 Phase 2 까지
run_pending()                   # 대기 중인 증적 전부

save(output, row=row, audit=audit, checklist_approved=False)
exclude("E0012", "HOLD_PHASE1_INVALID")                  # 증적 전체
exclude("E0006", "NO_CHECKLIST", control_id="2.5.5")     # 그 항목만
unexclude("E0006", "2.5.5")                              # 다시 돌려 성공했을 때

summary_response()              # GET /api/review/summary
coverage_response()             # GET /api/review/coverage
control_response("2.5.1")       # GET /api/review/control/{id}
evidence_response("E0001")      # GET /api/review/evidence/{id}
review_queue()                  # 안 끝난 검토 목록

approve(result_id, actor="이승은", reason="확인함")
modify (result_id, actor="신혜진", target="2.5.1-Q03",
        value_after="NOT_MET", reason="인용이 원문과 다름")
reject (result_id, actor="이승은", reason="최신본으로 다시 제출 요망")
```

응답의 칸 하나하나는 `API_response_spec.md` 에 있다.

```bash
python phase2_report.py --out reports/
#   control_report.html / .csv   통제항목별 판정·문항·근거 원문
#   coverage.csv                 통제항목 101개 제출/제외/미제출
#   reconcile.txt                집계와 상세 대조 — 25 일치 / 0 어긋남 이어야 한다
```

---

## Phase 1 → Phase 2 연결

### 통합팀이 넣을 한 줄

`phase1_통합/orchestrator.py` 의 `inspect_evidence()`, `_persist(result)` 바로 다음.

```python
        result_path = _persist(result)
        from phase2_runner import run_evidence                      # ← 추가
        phase2 = run_evidence(evidence_id, phase1_result=result)    # ← 추가
```

그 자리가 `with _lock:` **안**이라 판정이 끝날 때까지 락을 잡는다. 업로드 화면이
기다리면 안 되면 orchestrator 를 안 건드리고 뒤에서 몰아 돌린다.

```bash
python phase2_runner.py              # 대기 중인 증적 전부
python phase2_runner.py E0001        # 한 건만
```

### 기본값

모델 `qwen3:14b` · 체크리스트 `phase2_기준/full_checklist_draft.json` · 사유 코드
`phase2_기준/full_reason_codes_draft.json` · 통제항목 이름 `phase1_검색/controls.json`
· `allow_draft=True` · 매핑 저장 실패 시 멈춘다(`REQUIRE_MAPPING = True`).

### 알아둘 것

**청크를 그대로 넘기면 판정이 전부 FAILED 난다.** `chunk_store.get_chunks()` 는 13칸을
주는데 Phase 2 입력 스키마의 청크는 10칸 + `additionalProperties: false` 고,
`run_control_judgment()` 는 입력 오류가 하나라도 있으면 `output: None` 으로 끝낸다.
runner 가 `CHUNK_FIELDS` 로 걸러 넘기고, 루프 전에 `validate_input()` 을 한 번 부른다.

**`VALIDATING` 은 아직 아무도 안 넣는다.** 판단팀 `to_evidence_status()` 는
`COMPLETED`/`REVIEW_REQUIRED`/`FAILED` 만 쓴다. 그래서 `run_pending()` 이 대기 상태를
셋 다 본다. orchestrator 190행이 `phase2_status.from_phase1(..., phase2_enabled=True)`
를 쓰면 `VALIDATING` 이 남고, 그러면 뒤의 둘은 빼도 된다.

**PRIMARY 는 1개까지다.** 아예 없으면 `SKIP_NO_MATCH` 로 적고 끝낸다.

**검토에서 돌아온 건 자동으로 다시 안 돌린다.** 사람이 고친 값이 최종이라
`REVALIDATING`·`REJECTED` 를 runner 가 덮으면 그 값이 과거가 된다. LLM 으로 다시
판정해야 하면 `rejudge(evidence_id)` 를 사람이 직접 부른다.

---

## 검토 상태는 판정마다 따로 있다

한 증적이 통제항목 여러 개에 걸린다. `evidence.status` 하나로 승인을 관리하면
**첫 항목을 승인한 순간 나머지를 처리할 수 없다.** 그래서
`phase2_result.review_state` 를 따로 둔다 —
`NOT_REQUIRED` / `PENDING` / `APPROVED` / `REVALIDATING` / `REJECTED`.

`evidence.status` 는 그 증적의 **모든 현재 판정을 종합**해서 다시 계산한다.

```
하나라도 Phase 1 검토 미완료      → REVIEW_REQUIRED
하나라도 PENDING                 → REVIEW_REQUIRED
하나라도 REVALIDATING / REJECTED → VALIDATING
전부 APPROVED · NOT_REQUIRED     → COMPLETED
```

첫 줄이 중요하다. Phase 2 가 깨끗해도 Phase 1 사람 검토가 남아 있으면 `COMPLETED`
로 가지 않는다. 그래서 `save()` 는 `row` 를 **필수**로 받고 `phase1_review_required`
가 없으면 거부한다. 모르면 `None` — '검토 남음' 으로 저장된다. 빠뜨려서 `0` 으로
들어가면 자동 승인이 된다. 그 검토가 나중에 끝나면
`refresh_phase1_review("E0002", required=False)` 를 불러야 상태가 풀린다.

```
저장 → PENDING
수정 → PENDING          (고친 값은 이력에 쌓이고 조회하면 반영돼 나온다)
승인 → REVALIDATING     "고친 값 맞는지 한 번 더 보라"
승인 → APPROVED         끝. 이 뒤로는 수정도 막힌다 (NOT_IN_REVIEW)
```

**"고친 것" 은 마지막 승인 뒤에 고친 것만 센다.** 이력 전체를 보면 한 번 고친 판정은
몇 번을 승인해도 계속 `REVALIDATING` 이라 `APPROVED` 에 영영 못 가고, 그 증적도 영영
`VALIDATING` 에 묶인다.

`NOT_REQUIRED` 판정도 **사람이 고치거나 반려할 수 있다.** 막아두면 자동 통과한 오판을
바로잡을 길이 없다. 고치면 `PENDING` 으로 올라가 승인을 받아야 끝나고, 그때 증적이
`COMPLETED` → `REVIEW_REQUIRED` 로 다시 열린다 (전이표에서 그 한 줄만 열어뒀다).

**지금 판정이 아닌 것은 손댈 수 없다.** 화면을 열어둔 채 증적이 재업로드되면 옛 v1
결과를 승인해서 v2 를 `COMPLETED` 로 만들 수 있다. 버전을 보고 아니면
`RESULT_NOT_CURRENT` 로 막는다.

---

## 사람이 고친 값은 원본을 덮지 않는다

`modify()` 는 `payload` 를 바꾸지 않고 이력에만 쌓는다. 조회하면 `payload` + 이력을
적용한 `effective` 가 같이 나온다.

```python
r = load("E0002", "2.5.6")
r["payload"]["overall_result"]      # '충족'   — LLM 원본
r["effective"]["overall_result"]    # '미충족' — 사람이 고친 뒤
r["modified_by_human"]              # True
```

고칠 수 있는 것은 `overall_result`(5값) · `2.5.1-Q03`(MET/NOT_MET/UNKNOWN) ·
`2.5.1-Q03.reason_codes` 셋이다. 사유 코드를 고치려면 **기준팀 카탈로그를 먼저
읽어야 한다** — 안 읽으면 `REASON_CODE_CATALOG_NOT_LOADED` 로 거부한다.

```python
from phase2_review_store import load_reason_codes
load_reason_codes("phase2_기준/full_reason_codes_draft.json")
```

문항을 고치면 종합 판정이 다시 계산된다(`overall_result.compute()`). 사람이 그 뒤에
종합을 직접 고치면 그 값이 이긴다.

**승인할 때 판정값과 사유 코드가 맞는지 검사한다** — `MET` 은 `reason_codes` 가 비어야
하고 `NOT_MET`·`UNKNOWN` 은 1개 이상이어야 한다. 어긋나면 `INCONSISTENT_RESULT` 로
승인을 거부한다. 반면 **종합이 문항 계산값과 다른 것은 막지 않는다** (검토자 판단이
최종) — `OVERALL_OVERRIDDEN` 보류 사유로만 남긴다.

---

## COMPLETED 는 운영 승인이 아니다

`summary()` 가 `approval_blockers` 를 같이 내려준다. 화면은 이걸 **반드시 같이**
보여준다.

| 사유 | 뜻 |
|---|---|
| `PHASE1_REVIEW_OPEN` | Phase 1 사람 검토가 안 끝났다 |
| `PHASE1_REVIEW_OPEN_BUT_COMPLETED` | 그런데 증적 상태는 COMPLETED 다 |
| `CHECKLIST_NOT_APPROVED` | 미승인 체크리스트로 판정했다 |
| `POLICY_MISSING` | 최신성·형식 정책이 `OK` 가 아니다 |
| `INCONSISTENT_RESULT` | 판정값과 사유 코드가 안 맞는다 (승인 차단) |
| `OVERALL_OVERRIDDEN` | 사람이 정한 종합이 문항 계산값과 다르다 (경고) |
| `PROVISIONAL` | 아직 최종 결과가 아니다 |

정책 상태는 `OK` 인 것만 통과시킨다. `audit` 를 안 넘겨 검사를 못 한 결과는
`NOT_EVALUATED` 로 저장되고 보류 사유가 붙는다 — `NULL` 로 두면 '문제 없음' 과 구분이
안 돼 운영 승인으로 새어 나간다. `operationally_approved` 는 정책 주입과 체크리스트
승인이 끝나기 전에는 **0 이 정상이다.**

---

## 집계 단위를 섞지 않는다

```
evidence_total         33   올린 증적 전체
judged_evidence_total  25   판정이 나온 증적
judged_result_total    25   판정 건수 (증적 × 통제항목)
```

한 증적이 통제항목 3개에 걸리면 `judged_evidence_total=1`, `judged_result_total=3`
이다. 비율을 낼 때 분모를 섞으면 안 된다.

`verdicts`·`verdicts_wbs` 는 **사람이 고친 값 기준**(`effective`)이다 — 상세 화면과
종합 화면이 다른 숫자를 보여주면 안 되니까. LLM 원본은 `verdicts_original`, 문항
단위는 `item_results_effective` / `item_results`. `review_required` 는 `PENDING` 만,
`unresolved_review_total` 은 `PENDING`+`REVALIDATING`+`REJECTED`.

**커버리지는 분모가 또 다르다.** `summary()` 는 판정이 나온 건수가 분모라서 "101개
중 어디가 비었나" 를 알 수 없다 — 증적이 없는 통제항목은 줄 자체가 없다.
`coverage()` 는 `controls.json` 전체(101개)를 분모로 둔다.

```
control_total 101  /  submitted  /  excluded_only  /  not_submitted
```

통제항목 하나에 증적이 여럿이면 **제일 나쁜 판정**을 그 통제항목 값으로 쓴다
(`미충족 > 증적 없음 > 확인 필요 > 충족(보완 권고) > 충족`). 합의된 규칙은 아니다
(`ROLLUP_RANK`). 그래서 `summary()["verdicts"]` 와 `coverage()["verdicts_by_control"]`
은 합이 다르다 — **화면에서 둘을 같은 표에 놓지 않는다.**

---

## 버전과 "현재"

```
현재 = 과거로 안 내려갔고 + 그 증적의 지금 버전인 것
```

`phase2_result_store._is_current()` 한 군데에서만 정하고, 조회·집계가 전부 이 함수를
지난다. **`WHERE superseded_at IS NULL` 을 손으로 쓰지 말 것** — `evidence_version`
비교를 빼먹으면 재업로드한 증적에서 v1 결과가 v2 와 같이 현재로 남아 집계가 두 배로
잡힌다. 일부러 안 보는 자리에는 `# current-raw:` 표시와 이유를 달아뒀다.

옛 버전을 보려면 `include_history=True`, 옛 버전을 다시 판정하려면
`save(..., historical=True)`. 그냥 저장하면 `VERSION_NOT_CURRENT` 로 거부한다.

**제외 기록도 버전마다 따로 쌓인다** — `(evidence_id, evidence_version, control_id)`.
버전이 없으면 v1 에서 제외한 게 v2 재업로드까지 막는다. `control_id = ''` 는 증적
전체, 값이 있으면 그 통제항목만. `FAILED` 와 `ALL_TARGETS_UNRESOLVED` 는 고치면 다시
될 수 있는 사유라 `run_pending(retry_failed=True)` 로 다시 집는다.

### Phase 1 매핑이 바뀌면 Phase 2 를 무효화한다

```
E0001 → PRIMARY 2.5.1 로 판정 생성  →  검토자가 2.5.1 을 2.5.2 로 고침
  ↓ phase1_mapping_store.save() 가 PRIMARY 바뀐 걸 알아챔
  ↓ 빠진 통제항목의 판정을 과거로, 그 제외 기록도 지움, status 다시 엶
  ↓ run_pending() 이 다시 집어 새 매핑으로 재판정
```

안 하면 옛 매핑으로 낸 판정이 **현재 값으로 화면에 남는다.** 전부 한 트랜잭션이다 —
따로 하면 중간에 터졌을 때 새 매핑 + 옛 판정이 동시에 현재로 남는다.
**빠진 통제항목만** 과거로 돌리고 안 바뀐 것의 판정(사람이 고쳐둔 값 포함)은 그대로
둔다. `run_pending()` 은 `only_missing=True` 로 돌아 이미 있는 판정을 LLM 이 덮지
않는다. PRIMARY 가 전부 없어지면 다시 열지 않고 `SKIP_NO_MATCH` 로 닫는다.

---

## 시험

```
test/run_db_check.py    실제 MySQL      28 통과 / 0 실패 (2026-10-08)
test/run_e2e_demo.py    종단 시연       실제 LLM 으로는 아직 안 돌렸다
(깃에 없음)             메모리 366건    설계 검증 13개 · 0 실패. 필요하면 신혜진에게
```

`run_db_check.py` 는 표와 칸, `current_key` 가 VIRTUAL 인지, utf8mb4, 같은 증적·
통제항목 두 번 저장, **두 연결이 동시에 승인**(`FOR UPDATE`), 중간에 터졌을 때 롤백,
FK, 증적 삭제 시 CASCADE, 한글을 본다. 가상 증적 `E9001~E9003` 만 만들고 끝나면
지운다.

```bash
python test/run_e2e_demo.py --evidence-id E0001 --approve 혜진
```

맨 아래 "종단 경로" 두 칸이 다 `O` 여야 WBS 29·40 을 체크한다.


