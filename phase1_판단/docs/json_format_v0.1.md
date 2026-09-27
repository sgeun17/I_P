# ISMS-P 증적 도구 — JSON 형식 정리 (Phase 1 판단)

작성: 판단팀 · 2026-09-24
상태: **초안. 팀 합의 전**

판단 파트가 **받는 JSON**과 **내보내는 JSON**을 한 곳에 모았다.
다른 팀은 이 문서만 보면 된다. 규칙의 근거는 `mapping_rules_v0.7.md`에 있다.

기계가 읽을 형식은 `schemas/*.json`(JSON Schema)에 있고,
**Pydantic 모델에서 자동 생성한다.** 손으로 고치지 않는다.

---

## 0. 한눈에 — JSON이 4번 오간다

```
전처리팀 청크  ┐
              ├→ ① 입력 JSON ─→ LLM ─→ ② LLM 출력 JSON ─→ Validator
검색팀 Top-K  ┘                                              │
                                                             ↓
              ④ 검토 JSON ←─ 사람 ←─ Human Review ←─ ③ 최종 결과 JSON
                                                             │
                                                             ↓
                                                    백엔드 · Phase 2
```

| | JSON | 누가 만드나 | 누가 받나 |
|---|---|---|---|
| ① | `MappingInput` | 전처리팀 + 검색팀 | 판단팀 |
| ② | `LLMMappingOutput` | LLM | 판단팀 (Validator) |
| ③ | `Phase1MappingResult` | 판단팀 | 백엔드 · Phase 2 |
| ④ | `ReviewDecision` / `ReviewRecord` / `ReviewQueueItem` | 검토 화면 ↔ 판단팀 | 백엔드 |

---

## 1. 입력 JSON — `MappingInput`

검색팀이 판단팀에 넘기는 요청이다. 청크는 전처리팀 것을 그대로 싣는다.

```json
{
  "evidence_id": "E0001",
  "version": 1,
  "chunks": [ ... ],
  "candidate_controls": [ ... ],
  "top_k": 5,
  "kb_sha256": "6421a840c5fd84d1...",
  "model_revision": "5617a9f61b028005..."
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `evidence_id` | string | ● | 증적 번호. **문자열이다** (2.1 참고) |
| `version` | int ≥ 1 | ● | 증적 버전. 재업로드하면 올라간다 |
| `chunks` | Chunk[] | ● | **1개 이상.** 빈 배열이면 판단하지 않는다 |
| `candidate_controls` | CandidateControl[] | ● | Top-K 후보. 빈 배열 가능 |
| `top_k` | int 1~101 | | 후보를 몇 개 뽑았는지 |
| `kb_sha256` | string | | KB 파일 해시. 결과에 그대로 옮긴다 |
| `model_revision` | string | | 임베딩 모델 리비전 |

### 1.1 `Chunk` — 전처리팀 `chunk_format.py`와 같은 이름

```json
{
  "chunk_id": "E0001_v1_c0000",
  "evidence_id": "E0001",
  "version": 1,
  "chunk_index": 0,
  "file_type": "pdf",
  "source_file": "계정관리지침서.pdf",
  "chunk_type": "text",
  "page_start": 1,
  "page_end": 1,
  "heading": "3. 계정 발급 절차",
  "text": "사용자 계정은 관리자 승인 후 생성한다. ...",
  "block_orders": [12, 13],
  "source": "parser"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `chunk_id` | string | ● | **`{evidence_id}_v{version}_c{4자리}` 꼴이어야 한다** |
| `evidence_id` | string | ● | 상위 `evidence_id`와 같아야 한다 |
| `version` | int ≥ 1 | ● | 상위 `version`과 같아야 한다 |
| `chunk_index` | int ≥ 0 | ● | 0부터 |
| `file_type` | enum | ● | `pdf` `docx` `xlsx` `pptx` `txt` `csv` |
| `source_file` | string | ● | 원본 파일명 |
| `chunk_type` | enum | ● | `text` / `table` |
| `page_start` | int ≥ 1 \| null | ● | pdf·xlsx·pptx는 **필수**, 나머지는 **null** |
| `page_end` | int ≥ 1 \| null | ● | `page_start` 이상 |
| `heading` | string \| null | | 소속 제목 |
| `text` | string | ● | **1~800자.** Citation 대조 대상이 이 값이다 |
| `block_orders` | int[] | ● | 1개 이상 |
| `source` | enum | | `parser`(기본) / `ocr` |

**`chunk_id` 관계 검사만 한다**

```
chunk_id == f"{evidence_id}_v{version}_c{chunk_index:04d}"
```

`E0001`이든 `000001`이든 표기 자체는 검사하지 않는다.
전처리팀 안에서 아직 갈려 있어서, **어느 쪽으로 정해져도 돌아가게** 관계만 본다.

**`page`의 의미는 파일 종류마다 다르다**

| `file_type` | `page`의 뜻 |
|---|---|
| pdf | 페이지 번호 |
| xlsx | 시트 번호 |
| pptx | 슬라이드 번호 |
| docx · txt · csv | `null` |

화면에 "3페이지"라고 쓰면 XLSX에서는 틀린 말이 된다. `file_type`을 같이 봐야 한다.

### 1.2 `CandidateControl` — 검색팀 ChromaDB 결과

```json
{
  "rank": 1,
  "control_id": "2.5.1",
  "control_name": "사용자 계정 관리",
  "similarity_score": 0.5833,
  "distance": 0.4167,
  "requirement": null,
  "source_chunk_ids": ["E0001_v1_c0000", "E0001_v1_c0002"]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `rank` | int ≥ 1 | ● | 1부터 |
| `control_id` | string | ● | `x.y.z` 형식. **끝에 점 없음** |
| `control_name` | string | ● | KB 명칭 그대로 |
| `similarity_score` | float −1.0~1.0 | ● | `1 - cosine_distance`. **코사인 유사도 그 자체** |
| `distance` | float | | ChromaDB 코사인 거리 |
| `requirement` | string \| null | | 요구사항 본문. 지금은 안 실려 온다 |
| `source_chunk_ids` | string[] | | 어느 청크 검색에서 나온 후보인지 |

> **후보에 같은 `control_id`가 두 번 오면 안 된다.**
> 검색이 청크 단위라 증적 하나에 Top-5 × N개의 결과가 나오고, 같은 통제항목이 여러 번 잡힌다.
> **증적 단위로 합칠 때 중복을 제거해야 한다.** 중복이 있으면 입력 단계에서 거부된다(`E003`).

> **`similarity_score`는 정답 확률이 아니다.**
> BGE-M3 한국어 문장에서는 관련 있어도 0.5~0.6에 몰리고,
> **완전히 무관한 항목도 0.4988이 나온다**(실측).
> 절대값으로 관련성을 판단하면 안 된다. 판단은 LLM이 원문을 읽고 한다.

---

## 2. LLM 출력 JSON — `LLMMappingOutput`

**LLM은 이 형태로만 답한다.** 프롬프트에 이 구조를 그대로 넣는다.
JSON 외의 설명 문장을 앞뒤에 붙이지 않는다.

```json
{
  "match_status": "MATCHED",
  "candidate_decisions": [
    {
      "control_id": "2.5.1",
      "decision": "RELATED",
      "llm_confidence": 0.88,
      "reason": "계정 생성·삭제 절차가 직접 기술된다.",
      "citations": [
        { "chunk_id": "E0001_v1_c0000", "page": 1,
          "quote": "사용자 계정은 관리자 승인 후 생성한다." }
      ]
    },
    {
      "control_id": "2.5.6",
      "decision": "NOT_RELATED",
      "llm_confidence": 0.86,
      "reason": "권한 검토 주기에 대한 언급이 없다.",
      "citations": []
    }
  ],
  "mapped_controls": [
    {
      "control_id": "2.5.1",
      "control_name": "사용자 계정 관리",
      "relation": "PRIMARY",
      "llm_confidence": 0.88,
      "reason": "계정 등록·해지 절차가 문서의 주제다.",
      "citations": [
        { "chunk_id": "E0001_v1_c0000", "page": 1,
          "quote": "사용자 계정은 관리자 승인 후 생성한다." }
      ]
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `match_status` | enum | ● | `MATCHED` / `NO_MATCH` |
| `candidate_decisions` | 배열 | ● | **후보 전부에 대해 하나씩.** 고른 것만 남기면 안 된다 |
| `mapped_controls` | 배열 | | 최종 매핑. `NO_MATCH`면 `[]` |

### 2.1 `candidate_decisions[]`

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `control_id` | string | ● | 후보 목록에 있던 ID |
| `decision` | enum | ● | `RELATED` / `NOT_RELATED` / `UNCERTAIN` |
| `llm_confidence` | float 0~1 | ● | **뜻은 `mapping_rules_v0.7.md` 2.2에서 정의한다.** 0.70 미만이면 검토 |
| `reason` | string | ● | 판단 이유. 작성 규칙은 2.4 |
| `citations` | 배열 | | `RELATED`면 **1개 이상 필수** |

### 2.2 `mapped_controls[]`

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `control_id` | string | ● | **후보 안에서만** 고른다 |
| `control_name` | string | ● | KB 명칭과 정확히 같아야 한다 |
| `relation` | enum | ● | `PRIMARY` / `RELATED` |
| `llm_confidence` | float 0~1 | ● | |
| `reason` | string | ● | |
| `citations` | 배열 | ● | **1개 이상 필수** |

### 2.3 `citations[]`

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `chunk_id` | string | ● | 입력 청크에 있는 ID |
| `page` | int \| null | | 청크의 `page_start`~`page_end` 범위 안 |
| `quote` | string | ● | **청크 `text` 그대로.** 요약·의역 금지 |

**인용 대조 방식**

연속된 공백·줄바꿈을 공백 하나로 합친 뒤, **포함 여부만** 본다.
유사도 비교나 구두점 제거는 하지 않는다. 느슨하게 만들수록 지어낸 문장이 통과한다.

| 인용 | 통과 | 이유 |
|---|:---:|---|
| 원문 한 문장 | ● | 그대로 있다 |
| 원문의 일부 | ● | 부분 문자열이다 |
| 여러 줄을 한 번에 | ● | `\n`이 공백이 되어 통과 |
| 두 문장을 이어 붙여 만든 문장 | ✕ | 원문에 없다 |
| 조사·어미를 바꾼 문장 | ✕ | 원문에 없다 |

### 2.4 `reason` 작성 규칙

| 규칙 | 어기면 |
|---|---|
| 최소 10자 | `E506` (경고) |
| 증적 원문을 그대로 옮기지 않는다 | `E507` (경고) |
| 통제항목 명칭만 되풀이하지 않는다 | `E507` (경고) |
| **적정/미흡/충족/위반 등 판정 표현 금지** | `E505` → **무조건 검토** |

Phase 1은 **연결까지만** 한다. "충족하는가"는 Phase 2의 일이다.

```
좋다   "계정 생성 시 관리자 승인 절차가 문서의 주제다."
좋다   "관련 있어 보이나 원문 근거가 불충분하다."     ← UNCERTAIN 설명. 판정이 아니다
나쁘다 "관련 있음"                                  → E506
나쁘다 "해당 통제항목을 적정하게 이행하고 있다."       → E505
```

### 2.5 판정 절차

```
1. 후보 전부에 RELATED / NOT_RELATED / UNCERTAIN을 매긴다
2. RELATED 0개  →  match_status = NO_MATCH, mapped_controls = []
3. RELATED 1개  →  그것이 PRIMARY
4. RELATED 2개 이상  →  주된 목적 하나를 PRIMARY, 나머지는 RELATED
```

| 규칙 | 값 |
|---|---|
| `MATCHED`일 때 `PRIMARY` | **정확히 1개** |
| `NO_MATCH`일 때 `mapped_controls` | **빈 배열** |
| `mapped_controls` 상한 | **3개** (넘어도 자르지 않고 검토로 보낸다) |
| 같은 `control_id` 중복 | 금지 |
| 후보 밖 `control_id` 생성 | 금지 |

> **`UNCERTAIN`만 남아도 `match_status`는 `NO_MATCH`다.**
> 다만 `R107`이 붙어서 반드시 사람에게 간다.
> 화면에서는 `R107`이 있으면 "관련 없음"이 아니라 **"판단 보류"**로 표시해야 한다.

---

## 3. 최종 결과 JSON — `Phase1MappingResult`

판단팀이 백엔드·Phase 2로 내보내는 결과다.
**어떤 입력이 와도 이 구조로 나온다.** 실패해도 결과 객체는 만든다.

```json
{
  "evidence_id": "E0002",
  "version": 1,
  "processing_status": "COMPLETED",
  "match_status": "MATCHED",
  "mapped_controls": [
    {
      "control_id": "2.5.1",
      "control_name": "사용자 계정 관리",
      "relation": "PRIMARY",
      "llm_confidence": 0.88,
      "reason": "계정 생성과 삭제 절차가 문서의 핵심 내용이다.",
      "citations": [
        { "chunk_id": "E0002_v1_c0001", "page": 1,
          "quote": "퇴직자 계정은 퇴직일 당일 삭제한다." }
      ],
      "similarity_score": 0.874
    }
  ],
  "candidate_decisions": [ ... ],
  "validation": {
    "passed": true,
    "schema_valid": true,
    "control_ids_valid": true,
    "citations_valid": true,
    "rules_valid": true,
    "issues": [],
    "warnings": []
  },
  "human_review": {
    "required": true,
    "status": "PENDING",
    "reasons": ["R205"],
    "threshold_profile": "thresholds_v0.4"
  },
  "versions": {
    "schema_version": "0.3.0",
    "prompt_version": "phase1_mapping_v0.1",
    "model_name": "qwen2.5-14b-instruct",
    "ruleset_version": "mapping_rules_v0.7",
    "kb_sha256": "6421a840c5fd84d1...",
    "embedding_model_revision": "5617a9f61b028005..."
  },
  "llm_raw_response_ref": "logs/llm/2026-09-21/E0002-c3d4.json",
  "trace_id": "5f2c9a1e-0000-4000-8000-000000000002",
  "created_at": "2026-09-21T14:07:52+09:00",
  "processing_time_ms": 5890
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `evidence_id` | string | ● | |
| `version` | int ≥ 1 | ● | 어느 버전 증적에 대한 판단인지 |
| `processing_status` | enum | ● | `PENDING` `PROCESSING` `COMPLETED` `FAILED` |
| `match_status` | enum | ● | `MATCHED` / `NO_MATCH` |
| `mapped_controls` | 배열 | ● | LLM 출력 + `similarity_score` |
| `candidate_decisions` | 배열 | ● | LLM 출력 그대로 |
| `validation` | 객체 | ● | 3.1 |
| `human_review` | 객체 | ● | 3.2 |
| `versions` | 객체 | ● | 3.3 |
| `llm_raw_response_ref` | string \| null | | 원본 응답 로그 위치 |
| `trace_id` | string \| null | | 추적용 |
| `created_at` | datetime | ● | ISO 8601 **+09:00** |
| `processing_time_ms` | int \| null | | |

### 3.1 `validation`

| 필드 | 타입 | 설명 |
|---|---|---|
| `passed` | bool | `issues`가 하나도 없는가 |
| `schema_valid` | bool | 형식 검사 통과 |
| `control_ids_valid` | bool | ID 검사 통과 |
| `citations_valid` | bool | 인용 검사 통과 |
| `rules_valid` | bool | 판단 규칙 검사 통과 |
| `issues` | 배열 | **결과가 틀렸다.** `passed`에 반영 |
| `warnings` | 배열 | **근거가 부실하다.** `passed`에 반영 안 함, 검토로도 안 보냄 |

`issues[]` / `warnings[]` 한 줄:

```json
{ "code": "E403", "message": "인용문이 청크 원문에 없다",
  "control_id": "2.5.1", "chunk_id": "E0002_v1_c0001", "field": null }
```

### 3.2 `human_review`

| 필드 | 타입 | 설명 |
|---|---|---|
| `required` | bool | 사람이 봐야 하는가 |
| `status` | enum | `NOT_REQUIRED` `PENDING` `APPROVED` `MODIFIED` `REJECTED` |
| `reasons` | 배열 | `R1xx`(무조건) / `R2xx`(조건부). `required`면 **1개 이상** |
| `threshold_profile` | string | 어느 임계값으로 판단했는지 |
| `reviewer_id` | string \| null | 검토 후 채워진다 |
| `reviewed_at` | datetime \| null | |
| `review_note` | string \| null | `REJECTED`면 필수 |

정합성 규칙 — 어기면 객체가 만들어지지 않는다.

```
required=true  → status ≠ NOT_REQUIRED,  reasons 1개 이상
required=false → status = NOT_REQUIRED
```

### 3.3 `versions`

없으면 "지난주 결과가 왜 달랐는지"를 영영 못 밝힌다.

| 필드 | 설명 |
|---|---|
| `schema_version` | 이 JSON 구조의 버전 (`0.3.0`) |
| `prompt_version` | 프롬프트 버전 |
| `model_name` | 사용한 LLM |
| `ruleset_version` | 판단 규칙 문서 버전 (`mapping_rules_v0.7`) |
| `kb_sha256` | KB 파일 해시 |
| `embedding_model_revision` | 임베딩 모델 리비전 |

### 3.4 `evidence.status`로 옮기는 규칙

입력팀 DB의 `evidence.status`는 **처리 상태와 검토 상태가 한 칸에 섞여 있다.**
우리는 나눠서 관리하고, 넘기는 순간에만 합친다.

| 판단 결과 | `evidence.status` |
|---|---|
| `processing_status = FAILED` | `FAILED` |
| `human_review.required = true` | `REVIEW_REQUIRED` |
| 그 외 | `COMPLETED` |

> **거꾸로는 복원할 수 없다.** 검토 사유와 오류코드는 `Phase1MappingResult`에만 있다.

---

## 4. 검토 JSON

### 4.1 `ReviewQueueItem` — 대기열 한 줄

```json
{
  "evidence_id": "E0002",
  "version": 1,
  "source_file": "계정관리지침서.pdf",
  "match_status": "MATCHED",
  "processing_status": "COMPLETED",
  "control_ids": ["2.5.1", "2.5.5"],
  "reasons": ["R205"],
  "priority": 2,
  "has_blocking_reason": false,
  "created_at": "2026-09-21T14:07:52+09:00"
}
```

| `priority` | 상황 |
|---:|---|
| 0 | 처리 실패 (`FAILED`) — 결과 자체가 없다 |
| 1 | 무조건 검토 사유(`R1xx`) — 오류가 있는 결과다 |
| 2 | 조건부 검토 사유(`R2xx`)만 있음 |

**사유 코드를 그대로 화면에 띄우지 않는다.** 사람 말로 바꿔야 한다.
대응표는 `human_review_policy_v0.1.md`에 있다.

### 4.2 `ReviewDecision` — 검토자가 내린 결정

```json
{
  "status": "MODIFIED",
  "reviewer_id": "reviewer-1",
  "note": "2.5.5가 맞다",
  "decided_at": "2026-09-22T10:11:00+09:00",
  "modified_match_status": "MATCHED",
  "modified_controls": [ ... ]
}
```

| 규칙 | |
|---|---|
| `status` | `APPROVED` / `MODIFIED` / `REJECTED` 중 하나 |
| `MODIFIED` | `modified_match_status`와 `modified_controls` **둘 다 필수** |
| `REJECTED` | `note` **필수** |
| `APPROVED` | 수정본을 붙이면 거부된다 |

사람이 고친 결과도 **구조 규칙은 지켜야 한다** (PRIMARY 정확히 1개, 중복 금지 등).
다만 **인용이 원문에 없는 것은 경고만** 하고 막지 않는다. 사람이 최종 권한을 갖는다.

### 4.3 `ReviewRecord` — 검토 이력

```json
{
  "record_id": "R-1",
  "evidence_id": "E0002",
  "version": 1,
  "decision": { ... },
  "previous_status": "PENDING",
  "ruleset_version": "mapping_rules_v0.7",
  "threshold_profile": "thresholds_v0.4"
}
```

**덧붙이기만 한다.** `UPDATE`도 `DELETE`도 하지 않는다.
검토자가 마음을 바꾸면 새 레코드를 쌓고, 마지막 레코드가 현재 상태다.

### 4.4 상태 전이

```
NOT_REQUIRED   (검토 불필요 — 자동 확정)
PENDING  ──┬─→ APPROVED    그대로 확정
           ├─→ MODIFIED    사람이 고쳐서 확정
           └─→ REJECTED    이 매핑은 틀렸다
```

확정된 상태에서는 더 바뀌지 않는다.
**Phase 2로 넘어가는 것은 `NOT_REQUIRED` / `APPROVED` / `MODIFIED`뿐이다.**

---

## 5. Enum 전체

판단팀이 소유한다. **같은 의미의 문자열을 각 팀이 따로 정의하지 않는다.**
`src/enums.py`를 import해서 쓴다.

| Enum | 값 | 소유 |
|---|---|---|
| `FileType` | `pdf` `docx` `xlsx` `pptx` `txt` `csv` | 전처리팀 |
| `ChunkType` | `text` `table` | 전처리팀 |
| `ChunkSource` | `parser` `ocr` | 전처리팀 |
| `MatchStatus` | `MATCHED` `NO_MATCH` | 판단팀 |
| `Relation` | `PRIMARY` `RELATED` | 판단팀 |
| `Decision` | `RELATED` `NOT_RELATED` `UNCERTAIN` | 판단팀 |
| `ProcessingStatus` | `PENDING` `PROCESSING` `COMPLETED` `FAILED` | 판단팀 |
| `ReviewStatus` | `NOT_REQUIRED` `PENDING` `APPROVED` `MODIFIED` `REJECTED` | 판단팀 |
| `EvidenceStatus` | 8개 값 | **입력팀.** 우리는 변환만 한다 |

---

## 6. 코드 체계

### 오류코드

| 대역 | 뜻 |
|---|---|
| `E0xx` | 입력 자체가 판단 불가 (LLM 호출 전) |
| `E1xx` | LLM 호출 실패 |
| `E2xx` | 출력 형식 오류 |
| `E3xx` | 통제항목 ID 오류 |
| `E4xx` | Citation 오류 |
| `E5xx` | 판단 규칙 위반 (`E506` `E507`은 경고) |

### 검토 사유 코드

| 대역 | 뜻 |
|---|---|
| `R1xx` | **무조건 검토.** 기계가 판단할 수 없다 |
| `R2xx` | **조건부 검토.** 임계값으로 정한다 |

전체 목록은 `error_codes_v0.1.md`와 `human_review_policy_v0.1.md`에 있다.

> **입력팀 API 오류코드와 섞지 않는다.**
> 입력팀 API는 `{success, data, error:{code, message}}` 봉투에 `INVALID_EXTENSION` 같은
> 자체 코드를 쓴다. 우리 `E1xx~E5xx`는 `validation.issues`에만 들어간다.

---

## 7. 자주 틀리는 것

| | |
|---|---|
| `evidence_id`를 숫자로 저장 | 앞의 0이 사라져 DB와 대조가 안 된다. **문자열이다** |
| `control_id`에 끝 점을 붙임 | `2.5.1`이지 `2.5.1.`이 아니다. 보정하지 않고 `E301`로 잡는다 |
| `control_id`를 문자열 정렬 | `2.10.1`이 `2.2.1`보다 앞으로 온다 |
| `similarity_score`를 "정답 확률"로 설명 | 확률이 아니다. 같은 검색 안에서의 순위용이다 |
| `llm_confidence`를 "정확도"로 표시 | 정확도는 골든셋 평가로만 계산한다 |
| 점수 4개를 하나로 합침 | 합치면 어디서 틀렸는지 추적이 안 된다 |
| `NO_MATCH`를 전부 "관련 없음"으로 표시 | `R107`이 붙었으면 **"판단 보류"**다 |
| `processing_status`와 검토 상태를 한 칸에 | "LLM은 끝났는데 사람이 아직 안 봄"을 표현할 수 없다 |
| 판단 결과를 덮어쓰기 | 모델이 뭘 틀렸는지 영영 못 밝힌다. 쌓는다 |

---

## 8. 파일 위치

```
schemas/phase1_mapping_input.schema.json    ① 입력
schemas/phase1_llm_output.schema.json       ② LLM 출력
schemas/phase1_mapping_result.schema.json   ③ 최종 결과
schemas/review_decision.schema.json         ④ 검토 결정
schemas/review_record.schema.json           ④ 검토 이력
schemas/review_queue_item.schema.json       ④ 대기열

samples/single_mapping.json                 1:1 예시
samples/multi_mapping.json                  1:N 예시
samples/no_match.json                       NO_MATCH 예시
samples/review_required.json                검토 필요 예시
```

JSON Schema는 `python src/check.py`가 Pydantic 모델에서 **자동 생성한다.**
손으로 고치지 않는다. 모델을 고치고 스크립트를 다시 돌린다.

---

## 9. 아직 안 정해진 것

- [ ] **청크별 검색 결과를 증적 단위로 어떻게 합칠지** (검색팀) — 후보 목록 자체가 여기서 정해진다
- [ ] `evidence_id` 표기 확정 (전처리팀) — `E0001` / `000001`
- [ ] 청크 `source`를 OCR일 때 `"ocr"`로 채울 수 있는지 (전처리팀)
- [ ] 후보에 `requirement` 본문을 실을지 (검색팀)
- [ ] `llm_confidence`의 정의 — 프롬프트에 써넣어야 임계값에 근거가 생긴다
- [ ] Citation 개수 상한 — 지금은 없다
- [ ] `schemas/`와 `enums.py` 변경 시 판단팀 리뷰를 거치는 규칙
