# Phase 2 결과 저장 · 검토 이력

WBS 38행 `Phase 2 통합 / 결과 화면·수정 이력` 의 **데이터·상태 쪽** (A 파트).
화면은 B 파트가 만든다. 이 폴더는 그 화면에 넘길 값을 저장하고 꺼내준다.

```
phase2_schema.sql          표 3개 (CREATE TABLE IF NOT EXISTS — 데이터 보존)
phase2_result_store.py     판정 결과 저장·조회·종합·UI 응답 조립
phase2_review_store.py     승인·수정·반려와 이력
```

## 설치

```bash
mysql -u root -p < phase2_schema.sql
```

`evidence_db` 안에 표 세 개가 생긴다. 기존 세 표(`evidence`·`evidence_history`·`chunk`)는
건드리지 않는다. **별도 DB 파일은 생기지 않는다** — 데이터는 MySQL 안에 들어간다.
여러 번 돌려도 데이터가 안 지워진다. 완전히 새로 만들려면 `phase2_schema_reset.sql`.

**전에 만든 표가 이미 있으면** `CREATE TABLE IF NOT EXISTS` 가 구조를 안 바꾸므로
`phase2_migration_v1_to_v2.sql` 을 한 번 돌린다. 결과·이력·제외 기록 모두 안 지워진다
(`phase2_excluded` 는 `phase2_excluded_old` 로 이름만 바꾸고 내용을 새 표로 옮긴다.
옮긴 수를 확인한 뒤 손으로 지운다).

```bash
python test/run_gpt_review.py                  # MySQL 없이 돌아간다
python test/run_real_check.py <상세결과 폴더>    # 실제 시험 결과와 대조
```

## 쓰는 법

```python
from phase2_result_store import (save, exclude, summary_response,
                                 control_response, evidence_response)
from phase2_review_store import approve, modify, reject, history

save(output, row=row, audit=audit, checklist_approved=False)
exclude("E0012", "HOLD_PHASE1_INVALID")                  # 증적 전체 제외
exclude("E0006", "NO_CHECKLIST", control_id="2.5.5")     # 그 항목만 제외

summary_response()              # GET /api/review/summary
control_response("2.5.1")       # GET /api/review/control/{id}
evidence_response("E0001")      # GET /api/review/evidence/{id}

approve(result_id, actor="이승은", reason="확인함")
modify (result_id, actor="신혜진", target="2.5.1-Q03",
        value_after="NOT_MET", reason="인용이 원문과 다름")
modify (result_id, actor="신혜진", target="2.5.1-Q03.reason_codes",
        value_after="P2_NM_RULE_VIOLATED", reason="사유 코드 교체")
reject (result_id, actor="이승은", reason="최신본으로 다시 제출 요망")
```

## 표 세 개

| 표 | 내용 |
|---|---|
| `phase2_result` | 판정 결과. 증적 1개 × 통제항목 1개 당 한 줄 |
| `review_history` | 사람이 손댄 기록. 덮어쓰지 않고 쌓인다 |
| `phase2_excluded` | Phase 2 를 못 돌린 증적·통제항목과 사유 |

`phase2_excluded` 가 없으면 올린 증적 33건 중 25건만 화면에 보이고 **8건이 사라진다.**

## 검토 상태는 판정마다 따로 있다

한 증적이 통제항목 여러 개에 걸린다. 그래서 `evidence.status` 하나로 승인을
관리하면 **첫 항목을 승인한 순간 나머지를 처리할 수 없게 된다.**
그래서 `phase2_result.review_state` 를 따로 둔다.

| 상태 | 뜻 |
|---|---|
| `NOT_REQUIRED` | 검토가 필요 없다 |
| `PENDING` | 검토 대기 |
| `APPROVED` | 승인됨 |
| `REVALIDATING` | 사람이 고쳐서 다시 판정해야 한다 |
| `REJECTED` | 반려됨 |

`evidence.status` 는 그 증적의 **모든 현재 판정을 종합**해서 다시 계산한다.

```
하나라도 Phase 1 검토 미완료      → REVIEW_REQUIRED
하나라도 PENDING                 → REVIEW_REQUIRED
하나라도 REVALIDATING / REJECTED → VALIDATING
전부 APPROVED · NOT_REQUIRED     → COMPLETED
```

첫 줄이 중요하다. Phase 2 가 깨끗해도 Phase 1 사람 검토가 남아 있으면
`COMPLETED` 로 가지 않는다. 그래서 `save()` 는 `row` 를 **필수**로 받고,
`phase1_review_required` 가 없으면 거부한다. 모르면 `None` 으로 넘기면
'검토 남음' 으로 저장된다. 빠뜨려서 `0` 으로 들어가면 자동 승인이 된다.

| 동작 | 그 판정의 상태 |
|---|---|
| 수정 | `PENDING` 그대로 — 여러 군데를 고치는 동안 유지 |
| 승인 (고친 것 있음) | `REVALIDATING` |
| 승인 (고친 것 없음) | `APPROVED` |
| 반려 | `REJECTED` |

이력 INSERT 와 상태 UPDATE 는 한 트랜잭션이다. 판정 줄을 `FOR UPDATE` 로 먼저
잠그므로 두 사람이 동시에 처리해도 `seq` 가 겹치지 않는다.

## 사람이 고친 값은 원본을 덮지 않는다

`modify()` 는 `payload` 를 바꾸지 않고 이력에만 쌓는다.
조회하면 `payload` + 이력을 순서대로 적용한 `effective` 가 같이 나온다.

```python
r = load("E0002", "2.5.6")
r["payload"]["overall_result"]      # '충족'   — LLM 원본
r["effective"]["overall_result"]    # '미충족' — 사람이 고친 뒤
r["overall_result_effective"]       # '미충족'
r["modified_by_human"]              # True
```

**Phase 1 검토가 나중에 끝나면** `refresh_phase1_review()` 를 불러야 한다.
저장된 판정에는 저장 시점의 Phase 1 상태가 복사돼 있어서, 안 부르면 그 검토가
끝나도 증적이 계속 `REVIEW_REQUIRED` 로 남는다.

```python
from phase2_result_store import refresh_phase1_review
refresh_phase1_review("E0002", required=False)              # 검토 끝남
refresh_phase1_review("E0002", required=True, reasons=["R205"])  # 다시 열림
```

**문항을 고치면 종합 판정이 다시 계산된다.** `overall_result.compute()` 를 쓴다.
안 하면 '문항은 미충족인데 종합은 충족' 인 모순이 남는다. 사람이 그 뒤에 종합을
직접 고치면 그 값이 이긴다 (`overall_recomputed` 로 구분한다).

고칠 수 있는 것은 셋이다.

```
overall_result               종합 판정 (5값만)
2.5.1-Q03                    문항 판정 (MET / NOT_MET / UNKNOWN 만)
2.5.1-Q03.reason_codes       사유 코드 (기준팀 카탈로그에 있는 것만)
```

없는 문항이나 허용되지 않은 값은 거부한다. 고치기 전 값은 호출자 말을 믿지 않고
**저장된 결과에서 읽어서** 기록한다.

**승인할 때 판정값과 사유 코드가 맞는지 검사한다.**

```
MET              reason_codes 가 비어야 한다
NOT_MET·UNKNOWN  reason_codes 가 1개 이상이어야 한다
```

어긋나면 `INCONSISTENT_RESULT` 로 승인을 거부한다. 그대로 내보내면 출력 스키마
검증에서 떨어지기 때문이다. `MET → NOT_MET` 으로 고치면 사유 코드도 같이 넣어야 한다.
`load()` 의 `consistency` 로 화면에서 미리 보여줄 수 있다.

**종합 판정이 문항 계산값과 다른 것은 막지 않는다.** 검토자 판단이 최종이고,
critical 정책이 아직 임시(`check_kind`)라 규칙이 항상 옳다고 볼 수 없다.
대신 `OVERALL_CONFLICTS_WITH_ITEMS` 경고와 `OVERALL_OVERRIDDEN` 보류 사유로 남긴다.

사유 코드를 고치려면 **기준팀 카탈로그를 먼저 읽어야 한다.** 안 읽으면 아무
문자열이나 사유 코드로 들어가므로 `REASON_CODE_CATALOG_NOT_LOADED` 로 거부한다.

```python
from phase2_review_store import load_reason_codes
load_reason_codes("phase2_기준/chapter2_reason_codes_draft.json")
```

## COMPLETED 는 운영 승인이 아니다

`summary()` 가 `approval_blockers` 를 같이 내려준다. 화면은 이걸 반드시 같이 보여준다.

| 사유 | 뜻 |
|---|---|
| `PHASE1_REVIEW_OPEN` | Phase 1 사람 검토가 안 끝났다 |
| `PHASE1_REVIEW_OPEN_BUT_COMPLETED` | 그런데 증적 상태는 COMPLETED 다 |
| `CHECKLIST_NOT_APPROVED` | 미승인 체크리스트로 판정했다 |
| `POLICY_MISSING` | 최신성·형식 정책이 `OK` 가 아니다 |
| `INCONSISTENT_RESULT` | 판정값과 사유 코드가 맞지 않는다 (승인 차단) |
| `OVERALL_OVERRIDDEN` | 사람이 정한 종합이 문항 계산값과 다르다 (경고) |
| `PROVISIONAL` | 아직 최종 결과가 아니다 |

정책 상태는 **`OK` 인 것만 통과**시킨다. `audit` 를 안 넘겨 검사를 아예 못 한
결과는 `NOT_EVALUATED` 로 저장되고 보류 사유가 붙는다. `NULL` 로 두면
'정책 문제 없음' 과 구분되지 않아 운영 승인으로 새어 나간다.

`operationally_approved` 는 보류 사유가 하나도 없는 결과 수다. 정책 주입과
체크리스트 승인이 끝나기 전에는 **0 이 정상이다.**

## 집계 단위 세 개를 섞지 않는다

```
evidence_total         33   올린 증적 전체
judged_evidence_total  25   판정이 나온 증적
judged_result_total    25   판정 건수 (증적 × 통제항목)
```

한 증적이 통제항목 3개에 걸리면 `judged_evidence_total=1`, `judged_result_total=3` 이다.
비율을 낼 때 분모를 섞으면 안 된다. 문항 단위는 따로 있다.

```
review_required                    PENDING 만
unresolved_review_total            PENDING + REVALIDATING + REJECTED
verdicts / verdicts_wbs            사람이 고친 값 반영 — 화면이 쓰는 기본값
verdicts_original                  LLM 원본
item_results_effective             문항 단위, 사람이 고친 뒤
item_results                       문항 단위, 원본
```

상세 화면과 종합 화면이 다른 숫자를 보여주면 안 되므로 `verdicts` 도
`effective` 기준으로 센다.

## 실제 시험 결과와 대조 (2026-10-05, `3ba73a0`)

```
python test/run_real_check.py <phase2-current-3ba73a0-reviewed-20261005/상세결과>
```

| | 결과 |
|---|---:|
| 전체 증적 | 33건 = 판정 25 + 제외·보류 8 |
| 제외 사유 | `SKIP_NO_MATCH` 6 · `HOLD_PHASE1_INVALID` 2 |
| 판정 | 충족 1 · 충족(보완 권고) 5 · 확인 필요 19 |
| WBS 4값 | 적정 1 · 부분적정 5 · 부적정 0 · 판단불가 19 |
| 문항 | MET 129 · NOT_MET 0 · UNKNOWN 126 (합계 255) |
| 판정 문서 상태 | REVIEW_REQUIRED **22** · COMPLETED **3** |
| 검토 상태 | PENDING 19 · NOT_REQUIRED 6 |
| Phase 1 검토 미완료 | 16건 |
| 그중 증적 상태가 COMPLETED | **0건** — 전에는 3건(E0002·E0017·E0029)이었다 |
| 미승인 체크리스트 / POLICY_MISSING | 25/25건 |
| 운영 승인 | **0건** |
| 인용 | 797개 중 `source` 있는 것 **0개** |

13건 대조 모두 통과. 문항 수(255)와 판정 분포는 보고서와 같다.

**상태 3건이 보고서와 다른 건 의도한 것이다.** 실행기는 Phase 2 만 보고
E0002·E0017·E0029 를 `COMPLETED` 로 뒀는데, 셋 다 Phase 1 사람 검토가 열려 있다.
저장 모듈은 그걸 `REVIEW_REQUIRED` 로 돌린다 (19+3=22 / 6−3=3).
`PHASE1_REVIEW_OPEN_BUT_COMPLETED` 가 0 이 된 것도 같은 이유다 — 이제 그 조합이
구조적으로 생기지 않는다.
