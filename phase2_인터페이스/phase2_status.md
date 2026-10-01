# Phase 2 상태 전이 규격 (초안)

- 규격 버전 `phase2-status-0.1`
- 상태 DRAFT, 미승인
- 대조 기준 저장소 `2026-10-01` 판 / 체크리스트 `...-2026-10-01-r3`

---

## 0. 한 줄 요약

**새 상태값을 만들지 않는다.** 입력팀이 정한 8개 중 `MAPPING`·`VALIDATING` 두 개가
정의만 돼 있고 아무도 안 쓰고 있어서, Phase 1 분석과 Phase 2 판정이 그 자리에 들어간다.

---

## 1. 상태 8개

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

### 지금 코드가 실제로 쓰는 값

`run_status_check.py` 로 저장소 전체를 센 결과 (값을 정의한 `config.py`·`enums.py` 제외):

```
UPLOADED         7곳
PREPROCESSING    2곳
PREPROCESSED     1곳
MAPPING          0곳   ← 아무도 안 씀
VALIDATING       0곳   ← 아무도 안 씀
COMPLETED        8곳
REVIEW_REQUIRED  1곳
FAILED          12곳
```

---

## 2. 전이표

표에 없는 전이는 거부한다. (`phase2_status.ALLOWED`)

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

정상 경로:

```
UPLOADED → PREPROCESSING → PREPROCESSED → MAPPING → VALIDATING → COMPLETED
                                            │           │
                                            └───────────┴──→ REVIEW_REQUIRED
                                                                   │
                                            ┌──────────────────────┘
                                            └──→ 멈췄던 자리로 (아래 4절)
```

재시도는 처음부터가 아니라 **터진 단계부터** 한다. `FAILED → PREPROCESSING`
(파싱 실패), `FAILED → MAPPING` (매핑 실패), `FAILED → VALIDATING` (판정 실패).

`COMPLETED` 는 끝이다. 같은 증적의 파일을 다시 올리면 `version` 이 올라가고
`UPLOADED` 로 새로 시작한다 (입력팀 기존 규칙 그대로).

---

## 3. ★ 합의 필요 — Phase 1 이 끝난 증적의 상태

**이게 3-A 에서 유일하게 팀과 맞춰야 하는 건이다. 그리고 이미 돌아가는 코드다.**

`phase1_통합/orchestrator.py:190` 이 판단팀 결과를 그대로 DB 에 쓴다.

```python
update_status(evidence_id, evidence_status)   # evidence_status = COMPLETED
```

따라가면 이렇다.

```
judgment_pipeline.run_judgment()
  └ JudgmentPipelineResult(evidence_status = to_evidence_status(mapping_result))
       ↓
orchestrator.py:190  →  DB 에 COMPLETED
```

**지금 통합을 돌리면 모든 증적이 COMPLETED 가 되어 Phase 2 가 영원히 돌지 않는다.**

판단팀 `service.to_evidence_status()` 는 이렇게 돼 있다.

```python
if result.processing_status == ProcessingStatus.FAILED:
    return EvidenceStatus.FAILED
if result.human_review.required:
    return EvidenceStatus.REVIEW_REQUIRED
return EvidenceStatus.COMPLETED      # ← Phase 1 매핑만 끝났는데 COMPLETED
```

Phase 2 가 없던 때 쓴 코드라 **잘못된 게 아니다.** 다만 Phase 2 가 생긴 뒤에는
"매핑이 끝났다"가 "다 끝났다"가 아니다. 이대로 두면 `MAPPING → COMPLETED` 로
건너뛰어서 `VALIDATING` 으로 갈 자리가 없어진다.

### 제안

**판단팀 코드는 고치지 않는다.** 그 함수는 순수 함수이고, DB 에 쓰는 건
호출하는 쪽(통합·백엔드)이다. 호출하는 쪽이 `phase2_status.from_phase1()` 을 쓰면 된다.

```python
from_phase1(processing_status, review_required, phase2_enabled=True)

    processing_status = FAILED   → FAILED
    review_required = True      → REVIEW_REQUIRED
    그 외, Phase 2 를 돌릴 것     → VALIDATING     ← 판단팀 원본과 다른 지점
    그 외, Phase 2 를 안 돌릴 것   → COMPLETED
```

`phase2_enabled=False` 를 남겨둔 이유: Phase 2 가 아직 안 붙은 동안에도
화면이 깨지지 않아야 하고, 통합 데모에서 Phase 1 까지만 보여줄 수도 있다.

### 고칠 곳은 통합 파일 한 줄이다

```python
# phase1_통합/orchestrator.py:190
update_status(evidence_id, evidence_status)
        ↓
update_status(evidence_id, from_phase1(
    judged.mapping_result.processing_status,
    judged.mapping_result.human_review.required))
```

`JudgmentPipelineResult.mapping_result` 가 `Phase1MappingResult` 라 저 두 필드를
들고 있다. **판단팀 파일은 건드리지 않는다.**

`test/run_status_check.py` 의 ⑥-2 가 이 호출을 직접 찾아서 알려준다.

### 물어볼 것

1. Phase 1 이 끝난 증적을 `VALIDATING` 으로 두는 데 동의하는지 (판단팀)
2. 그 전환을 **누가** 하는지 — 통합 쪽 호출자인지, 판단팀이 함수에 인자를 받는지
3. `phase2_enabled` 를 설정값으로 둘지, 통합 완료 후 없앨지

---

## 4. ★ 합의 필요 — 검토·실패에서 어디로 돌아가나

`REVIEW_REQUIRED` 와 `FAILED` 는 **Phase 1 에서도 Phase 2 에서도** 온다.
그런데 `evidence.status` 는 칸이 하나뿐이라 **어느 단계에서 멈췄는지 적을 자리가 없다.**

`evidence_history` 테이블이 있지만 그건 **파일 교체 이력**이고 (`status` = 교체되기
직전 상태), 상태 전이 이력이 아니다. 즉 지금 스키마로는 복구 지점을 알 수 없다.

### 선택지

| | 방법 | DB 변경 | 비고 |
|---|---|---|---|
| **(a)** | 결과물 유무로 판별 — Phase 2 출력 파일이 있으면 Phase 2 에서 멈춘 것 | 없음 | 지금 기본. 호출자가 `phase` 를 넘긴다 |
| **(b)** | `evidence_status_history` 테이블 추가 | 있음 | 전이 이력이 남아 감사·디버깅에 유리 |

**(a) 로 간다 (입력 파트 결정 2026-10-02).** 스키마를 안 건드리는 쪽이고,
호출하는 쪽이 `phase` 를 넘겨주면 된다. 상태 전이가 꼬이는 사고가 실제로 나면
그때 (b) 를 추가한다. DB 는 입력 파트 담당이라 나중에 올려도 비용이 크지 않다.

### 검토가 끝났을 때

```
approve_review(phase, modified)

  phase1 검토, 그대로 승인  → VALIDATING   (판정으로 넘어간다)
  phase1 검토, 사람이 고침  → MAPPING      (매핑부터 다시)
  phase2 검토, 그대로 승인  → COMPLETED    (끝)
  phase2 검토, 사람이 고침  → VALIDATING   (판정부터 다시)
```

---

## 5. Phase 2 결과 → 상태

```
from_phase2(processing_status, review_required)

    processing_status = FAILED   → FAILED
    review_required = True      → REVIEW_REQUIRED
    그 외                        → COMPLETED
```

Phase 1 의 `to_evidence_status()` 와 **같은 모양**으로 맞췄다. 두 단계가 서로 다른
규칙으로 상태를 쓰면 화면에서 구분이 안 된다.

---

## 6. Phase 1 코드 수정 여부

**없다.** 확인한 것:

- 입력팀 `config.VALID_STATUSES` — 8개 그대로. 전이표와 완전히 일치
- 판단팀 `enums.EvidenceStatus` — 8개 그대로. 완전히 일치
- `update_status()` 는 이미 8개 밖의 값을 `ValueError` 로 막는다 — 그대로 둔다
- `MAPPING`·`VALIDATING` 은 정의만 있고 미사용 — 그 자리를 쓰는 것이므로 추가 없음
- `schema.sql` 의 `status VARCHAR(20)` — `REVIEW_REQUIRED`(15자)가 들어가므로 충분

3절의 `COMPLETED → VALIDATING` 만 **호출하는 쪽**에서 갈아끼운다. 판단팀 파일은 안 건드린다.

---

## 7. 검증

`test/run_status_check.py` 가 DB 없이 전이표만으로 검사한다.

```
① 입력팀 VALID_STATUSES 와 대조        [O] 완전히 같음 (새 값 없음)
② 판단팀 EvidenceStatus 와 대조        [O] 완전히 같음
③ 실제 사용 집계                       MAPPING 0곳 / VALIDATING 0곳
④ 정상 경로 7가지                      7/7 통과
⑤ 막혀야 하는 전이 6가지                6/6 막힘
⑥ Phase 1 종료 상태 비교               판단팀 COMPLETED vs 전이표 VALIDATING  (★ 미합의)
```

④ 에 넣은 경로:

1. 순조롭게 끝
2. 파싱 실패 → 재시도 성공
3. Phase 1 에서 검토(`NO_MATCH` 등) → 승인 → 판정
4. Phase 1 검토에서 사람이 고침 → 매핑 다시
5. Phase 2 에서 검토 → 승인하고 끝
6. Phase 2 LLM 실패 → 판정만 재시도
7. 청크가 0개라 판단 불가 → 검토

⑤ 에 넣은, 막혀야 하는 전이:

1. `UPLOADED → VALIDATING` 업로드만 하고 바로 판정
2. `UPLOADED → MAPPING` 청킹 안 하고 매핑
3. `PREPROCESSED → VALIDATING` Phase 1 건너뛰고 판정
4. `MAPPING → COMPLETED` 매핑하다 바로 완료 — **지금 판단팀 함수가 하는 것**
5. `COMPLETED → VALIDATING` 끝난 증적 되돌림
6. `FAILED → COMPLETED` 실패에서 바로 완료

---

## 8. 남은 것

- 3절·4절 합의 (판단팀·통합)
- 3-B 로 이어짐: `FAILED` 로 갈 때 `error_code` 에 무엇을 적을지, 재시도를 몇 번 할지,
  `REVIEW_REQUIRED` 사유 코드를 Phase 1 의 `R1xx`·`R2xx` 와 어떻게 구분할지
