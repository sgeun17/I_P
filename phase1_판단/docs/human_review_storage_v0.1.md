# Human Review 저장 구조 v0.1

작성: 판단팀 · 2026-09-24
상태: **제안. 백엔드 담당과 합의 필요**

코드: `src/review.py` · 테스트: `tests/test_review.py`

---

## 1. 원칙 — 판단 결과를 덮어쓰지 않는다

사람이 고쳐도 **원본 판단 결과는 그대로 남긴다.** 수정은 별도 레코드로 쌓고,
"지금 유효한 결과"는 원본 + 수정 이력을 합쳐서 만든다.

덮어쓰면 세 가지를 잃는다.

1. **모델이 무엇을 틀렸는지** — 평가 지표를 다시 계산할 수 없다.
   사람이 고친 값으로 Precision을 재면 항상 100%가 나온다
2. **사람이 무엇을 고쳤는지** — 이게 골든셋의 가장 좋은 재료다
3. **언제 왜 바뀌었는지** — 나중에 결과가 달라진 이유를 못 밝힌다

이력은 **덧붙이기만** 한다. 검토자가 마음을 바꾸면 새 레코드를 쌓고,
가장 마지막 레코드가 현재 상태다.

---

## 2. 테이블 제안

### `judgment_result` — 판단 결과 (불변)

```sql
CREATE TABLE judgment_result (
    result_id         TEXT PRIMARY KEY,
    evidence_id       TEXT NOT NULL,
    version           INTEGER NOT NULL,

    processing_status TEXT NOT NULL,      -- PENDING/PROCESSING/COMPLETED/FAILED
    match_status      TEXT NOT NULL,      -- MATCHED/NO_MATCH
    result_json       TEXT NOT NULL,      -- Phase1MappingResult 전체

    ruleset_version   TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    model_name        TEXT NOT NULL,
    threshold_profile TEXT,
    kb_sha256         TEXT,

    trace_id          TEXT,
    llm_raw_response_ref TEXT,            -- 원본 응답 로그 위치
    processing_time_ms   INTEGER,
    created_at        TEXT NOT NULL,      -- ISO 8601 +09:00

    UNIQUE (evidence_id, version, ruleset_version, prompt_version, model_name)
);
```

**한 번 넣으면 `UPDATE`하지 않는다.** 재처리하면 새 행을 넣는다.
같은 증적·버전에 대해 프롬프트나 모델이 바뀌면 행이 늘어나고,
그게 곧 "어떤 설정에서 무엇이 나왔는지"의 기록이 된다.

`result_json`을 통째로 넣는 이유는 필드가 계속 바뀌기 때문이다.
검색·정렬에 필요한 것만 컬럼으로 빼고 나머지는 JSON에 둔다.

### `review_record` — 검토 이력 (덧붙이기만)

```sql
CREATE TABLE review_record (
    record_id         TEXT PRIMARY KEY,
    result_id         TEXT NOT NULL REFERENCES judgment_result(result_id),
    evidence_id       TEXT NOT NULL,
    version           INTEGER NOT NULL,

    previous_status   TEXT NOT NULL,      -- 직전 검토 상태
    status            TEXT NOT NULL,      -- APPROVED/MODIFIED/REJECTED
    reviewer_id       TEXT NOT NULL,
    note              TEXT,               -- REJECTED면 필수
    decided_at        TEXT NOT NULL,

    modified_json     TEXT,               -- MODIFIED일 때 사람이 고친 매핑
    ruleset_version   TEXT NOT NULL,
    threshold_profile TEXT
);

CREATE INDEX idx_review_result ON review_record(result_id, decided_at);
```

**`UPDATE`도 `DELETE`도 하지 않는다.** 잘못 눌렀으면 새 레코드를 쌓는다.

### `review_queue` — 대기열 (뷰로 만들어도 된다)

```sql
CREATE VIEW review_queue AS
SELECT
    r.result_id, r.evidence_id, r.version,
    r.match_status, r.processing_status, r.created_at,
    CASE
        WHEN r.processing_status = 'FAILED' THEN 0    -- 처리 실패가 제일 급하다
        WHEN <무조건 검토 사유 있음>        THEN 1
        ELSE 2
    END AS priority
FROM judgment_result r
WHERE <검토 필요> AND <아직 확정 안 됨>;
```

우선순위는 `src/review.py`의 `make_queue_item()`과 같은 규칙이다.

| 순위 | 상황 |
|---:|---|
| 0 | 처리 실패 (`FAILED`) — 결과 자체가 없다 |
| 1 | 무조건 검토 사유(`R1xx`) — 오류가 있는 결과다 |
| 2 | 조건부 검토 사유(`R2xx`)만 있음 |

---

## 3. 상태 전이

```
NOT_REQUIRED   (검토 불필요 — 자동 확정)
PENDING  ──┬─→ APPROVED    그대로 확정
           ├─→ MODIFIED    사람이 고쳐서 확정
           └─→ REJECTED    이 매핑은 틀렸다
```

- 확정된 상태(`APPROVED`/`MODIFIED`/`REJECTED`)에서는 **더 바뀌지 않는다.**
  다시 봐야 하면 새 판단을 만들고 이전 것은 이력으로 남긴다
- `NOT_REQUIRED`에서 바로 `APPROVED`로 갈 수 없다. 검토 대상이 아니었기 때문이다
- **Phase 2로 넘어가는 것은 `NOT_REQUIRED` / `APPROVED` / `MODIFIED`뿐이다.**
  `REJECTED`는 "이 매핑은 틀렸다"는 뜻이므로 넘기지 않는다

---

## 4. 사람이 고칠 때의 규칙

### 구조는 강제, 인용은 경고

| 검사 | 사람 수정본에 | 이유 |
|---|---|---|
| `NO_MATCH`면 매핑 없음 | **강제** | Phase 2가 받는 모양이 달라지면 안 된다 |
| `MATCHED`면 PRIMARY 정확히 1개 | **강제** | 〃 |
| 같은 통제항목 중복 금지 | **강제** | 〃 |
| 인용이 청크 원문에 있는가 | **경고만** | 사람이 최종 권한을 갖는다 |
| 후보 목록 안에 있는가 | **경고만** | 검색이 놓친 항목을 사람이 넣을 수 있다 |

구조 규칙을 어기면 저장이 거부된다. 검토 화면에서 막아야 한다.
인용 경고는 "이 문장을 원문에서 못 찾았습니다"로 띄우고, 그래도 저장은 되게 한다.

`validate_modification()`이 경고 목록을 돌려준다.

### 사람이 고치면 실패 상태가 풀린다

`processing_status = FAILED`였어도, 사람이 직접 매핑을 채웠으면 `COMPLETED`가 된다.
"판단 못 함"이 더 이상 아니기 때문이다.

---

## 5. 수정 이력은 골든셋 재료다

**사람이 고친 것이 곧 정답이다.** 모델이 틀린 사례를 골든셋에 넣으면,
다음 프롬프트나 모델이 같은 실수를 반복하는지 회귀로 잡을 수 있다.

`to_goldenset_draft()`가 `MODIFIED` 레코드를 골든셋 케이스 초안으로 뽑는다.

```json
{
  "model_said": { "controls": [{"control_id": "2.5.1", "relation": "PRIMARY"}] },
  "human_said": { "controls": [{"control_id": "2.5.5", "relation": "PRIMARY"}] },
  "input": { ... },
  "warning": "증적 본문이 실제 문서라면 합성으로 바꾼 뒤 골든셋에 넣을 것"
}
```

> **그대로 커밋하면 안 된다.** 실제 증적 본문이 들어 있으면 합성으로 바꿔야 한다.
> 레포에 커밋되면 git 히스토리에 영구히 남는다.

---

## 6. 백엔드 담당에게 물어볼 것

- [ ] `result_json`을 통째로 넣는 방식이 괜찮은가, 아니면 컬럼으로 펼칠까
- [ ] `review_record`를 append-only로 둘 수 있는가 (ORM이 `UPDATE`를 안 쓰게)
- [ ] 대기열을 뷰로 만들지, 별도 테이블로 만들지
- [ ] **LLM 원본 응답 로그의 보관 위치·기간·접근 권한** — PII 마스킹을 뺐으므로
      증적 본문이 그대로 로그에 남는다
- [ ] 검토자 계정을 어떻게 식별하는가 (`reviewer_id` 형식)
- [ ] 동시에 두 사람이 같은 건을 검토하면 어떻게 할지 (지금은 1명 전제)

---

## 7. 화면 담당에게

대기열 한 줄에 필요한 것은 `ReviewQueueItem`에 정리해 뒀다.

```
evidence_id · version · source_file
match_status · processing_status
control_ids          매핑된 통제항목
reasons              검토 사유 코드 (R101 …)
priority             0 급함 → 2
has_blocking_reason  무조건 검토인가
created_at
```

**사유 코드는 그대로 보여주지 말고 사람 말로 바꿔야 한다.**
대응표는 `human_review_policy_v0.1.md`에 있다. 예를 들어 `R104`는
"인용문이 원문에서 확인되지 않음"이다.

전체 판단 결과는 `evidence_id`로 따로 읽는다. 대기열에 다 넣지 않는다.
