# Phase 1 판단 파트 입출력 스키마 v0.3

작성: 판단팀 · 2026-09-24
상태: **입력은 전처리팀·검색팀 실제 구현에 맞춤. 출력은 확정 초안**

원본은 `src/models.py`(Pydantic)다.
`schemas/*.json`은 거기서 **자동 생성**되므로 손으로 고치지 않는다.

```
models.py  ──(python src/check.py)──▶  schemas/*.json
```

| 파일 | 모델 | 누가 만드나 |
|---|---|---|
| `phase1_mapping_input.schema.json` | `MappingInput` | 전처리팀 + 검색팀 |
| `phase1_llm_output.schema.json` | `LLMMappingOutput` | LLM |
| `phase1_mapping_result.schema.json` | `Phase1MappingResult` | 판단팀 (최종 산출물) |

---

## 1. 입력 — `MappingInput`

> 전처리팀 `chunk_format.py`와 검색팀 ChromaDB 결과의 **실제 필드에 맞췄다.**
> 우리가 정한 이름이 아니므로 마음대로 바꾸지 않는다.

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `evidence_id` | string | O | 증적 번호. **문자열이다** (아래 주의) |
| `version` | int (1 이상) | O | 증적 버전. 재업로드하면 올라간다 |
| `chunks` | Chunk[] | O (1개 이상) | 전처리팀 청크 |
| `candidate_controls` | CandidateControl[] | O | 검색팀 Top-K 후보 |
| `top_k` | int | X | 몇 개로 검색했는지 |
| `kb_sha256` | string | X | 검색팀 메타데이터의 KB 해시 |
| `model_revision` | string | X | 임베딩 모델 revision |

> **`evidence_id`는 문자열로 다룬다.**
> 입력팀 JSON 규격 문서의 지적대로, 숫자로 저장하면 앞의 0이 사라져 DB와 대조가 안 된다.
> 다만 표기가 전처리팀 안에서 아직 갈려 있다 (`E0001` / `000001` / 정수 `1`).
> 자세한 내용과 우리 처리 방식은 `mapping_rules_v0.7.md` 12장에 있다.

### Chunk — 전처리팀 `CHUNK_KEYS` 그대로

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `chunk_id` | string | O | `{evidence_id}_v{version}_c{4자리}` |
| `evidence_id` | string | O | 상위 `evidence_id`와 같아야 한다 |
| `version` | int | O | |
| `chunk_index` | int (0부터) | O | 문서 내 순서 |
| `file_type` | pdf/docx/xlsx/pptx/txt/csv | O | |
| `source_file` | string | O | 원래 파일 이름 |
| `chunk_type` | text / table | O | 글과 표를 섞지 않는다 |
| `page_start` | int 또는 null | O | pdf=페이지, xlsx=시트, pptx=슬라이드 |
| `page_end` | int 또는 null | O | 청크가 두 페이지에 걸칠 수 있다 |
| `heading` | string 또는 null | O | 가장 최근 제목 |
| `text` | string | O | **800자 이하.** 이것이 인용 대조 기준 |
| `block_orders` | int[] | O | 오름차순. 표 청크는 정확히 1개 |
| `source` | parser / ocr | O | **품질 점수는 없다** |

**`chunk_id` 검증 — 표기가 아니라 관계를 본다**

```
chunk_id == f"{evidence_id}_v{version}_c{chunk_index:04d}"
```

`E0001`로 정해지든 `000001`로 정해지든 이 관계는 변하지 않는다.
표기를 고정하지 않았으므로 어느 쪽으로 합의돼도 코드를 고칠 필요가 없고,
**어긋난 조합은 그대로 잡힌다.**

한 증적의 입력에 다른 증적·버전의 청크가 섞이면 입력 단계에서 막는다.

**페이지 규칙**

```
pdf, xlsx, pptx  →  page_start, page_end 둘 다 1 이상, page_start <= page_end
docx, txt, csv   →  둘 다 null
```

Citation 검증은 `page_start <= citation.page <= page_end` 범위 검사다.

**OCR**

`ocr_confidence` 같은 품질 점수는 없다. `source`가 `"parser"` / `"ocr"` 둘뿐이다.
따라서 검토 조건 `R207`은 **OCR 청크를 인용했으면 검토**로 단순화했다.

**겹침**

글 청크끼리 100자가 겹친다. 단 **표 청크, 페이지 경계, 이미 나뉜 긴 문단**은 겹치지 않는다.
페이지 경계에서 겹치지 않으므로 인용 페이지가 어긋날 일은 줄었지만,
같은 페이지 안에서는 여전히 겹친다.
`chunk_id`가 달라도 `quote`가 같으면 같은 근거로 센다.

### CandidateControl — 검색팀 ChromaDB 결과 그대로

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `rank` | int (1부터) | O | 검색 순위 |
| `control_id` | string | O | `x.y.z` |
| `control_name` | string | O | |
| `similarity_score` | float (-1 ~ 1) | O | `1 - cosine_distance`. **코사인 유사도 그 자체** |
| `distance` | float | X | ChromaDB 코사인 거리 |
| `requirement` | string | X | 요구사항 본문. **현재 검색 결과에 실리지 않는다** |
| `source_chunk_ids` | string[] | X | 어느 청크 검색에서 나왔는지 |

**점수를 관련성 판단에 쓰지 않는다**

BGE-M3 한국어 문장에서는 관련 있어도 0.5~0.6에 몰린다.
실측에서 계정 삭제 질의에 "백업 및 복구관리"가 0.4988로 5위에 들어왔다.
**0.5가 넘는다고 관련 있는 것이 아니다.**

**미해결 — 청크별 결과를 어떻게 합칠 것인가**

검색은 텍스트 한 덩어리를 받는다. 증적에 청크가 10개면 Top-5가 10벌 나온다.
`candidate_controls`는 증적 하나당 하나의 목록이므로 **누군가는 합쳐야 한다.**

검색팀이 합쳐주는 것이 맞다고 본다. 우리가 하면 검색 로직을 판단팀이 떠안는다.
우리가 하게 되면 규칙은 이렇게 잡는다.

> `control_id`별로 묶고, 최고 `similarity_score`를 대표값으로 삼아 상위 K개.
> 어느 청크에서 나왔는지는 `source_chunk_ids`에 남긴다.

---

## 2. LLM 출력 — `LLMMappingOutput`

프롬프트에 이 구조를 그대로 명시한다. **형식만 검사하고 판단 규칙은 검사하지 않는다.**

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `match_status` | `MATCHED` \| `NO_MATCH` | O | 관련 통제항목이 있는가 |
| `candidate_decisions` | CandidateDecision[] | O | **Top-K 후보 전부**에 대한 판단 |
| `mapped_controls` | LLMMappedControl[] | O | 최종 매핑. `NO_MATCH`면 빈 배열 |

### CandidateDecision

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `control_id` | string | O | 후보 ID |
| `decision` | `RELATED` \| `NOT_RELATED` \| `UNCERTAIN` | O | 판단값 |
| `llm_confidence` | float (0~1) | O | 모델이 매긴 신뢰도 |
| `reason` | string | O | 판단 이유 |
| `citations` | Citation[] | X | `NOT_RELATED`면 보통 빈 배열 |

고른 것만 남기지 않고 **후보 전부**를 기록하는 이유는, 나중에
"왜 이걸 놓쳤나"를 분석하려면 버린 후보의 판단 근거가 필요하기 때문이다.

### LLMMappedControl

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `control_id` | string | O | 통제항목 ID |
| `control_name` | string | O | 통제항목 명칭 |
| `relation` | `PRIMARY` \| `RELATED` | O | 관계. `MATCHED`면 PRIMARY 정확히 1개 |
| `llm_confidence` | float (0~1) | O | |
| `reason` | string | O | |
| `citations` | Citation[] | O (1개 이상) | **매핑됐으면 인용 필수** |

### Citation

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `chunk_id` | string | O | 입력 청크에 실제로 존재해야 한다 |
| `page` | int \| null | X | 청크의 페이지와 일치해야 한다 |
| `quote` | string | O | **원문 그대로.** 요약·의역 금지 |

`quote`는 정규화(연속 공백 압축) 후 청크 본문에 포함되는지 검사한다.
유사 문자열 비교는 쓰지 않는다. LLM이 원문에 없는 문장을 비슷하게 지어낼 수 있기 때문이다.

---

## 3. 최종 출력 — `Phase1MappingResult`

**판단팀이 소유한다.** 변경하려면 판단팀 리뷰가 필요하다.

| 필드 | 타입 | 필수 | 의미 |
|---|---|:---:|---|
| `evidence_id` | string | O | 입력의 값 그대로 |
| `version` | int | O | 증적 버전 |
| `processing_status` | `PENDING` \| `PROCESSING` \| `COMPLETED` \| `FAILED` | O | 기계적 처리 상태 |
| `match_status` | `MATCHED` \| `NO_MATCH` | O | |
| `mapped_controls` | MappedControl[] | O | 검증 통과한 최종 매핑 |
| `candidate_decisions` | CandidateDecision[] | O | |
| `validation` | ValidationResult | O | |
| `human_review` | HumanReview | O | |
| `versions` | VersionInfo | O | 재현용 |
| `llm_raw_response_ref` | string | X | 원본 응답 로그 위치 |
| `trace_id` | string | X | 처리 추적용 |
| `created_at` | datetime | O | ISO 8601 **+09:00** (입력팀 `uploaded_at`과 같은 기준) |
| `processing_time_ms` | int | X | 성능 지표 |

`MappedControl`은 `LLMMappedControl`에 `similarity_score`가 추가된 것이다.
LLM은 검색 점수를 모르므로 판단팀이 붙여준다.

### ValidationResult

| 필드 | 타입 | 의미 |
|---|---|---|
| `passed` | bool | 전체 통과 여부 |
| `schema_valid` | bool | 형식 |
| `control_ids_valid` | bool | ID 대조 |
| `citations_valid` | bool | 인용 원문 대조 |
| `rules_valid` | bool | 판단 규칙 |
| `issues` | ValidationIssue[] | 실패 내역 |

`passed` 하나로 합치지 않는 이유는 **어느 단계에서 걸렸는지 알아야**
프롬프트를 고칠지 Validator를 고칠지 판단할 수 있기 때문이다.

`ValidationIssue`는 `code`(오류코드), `message`, `control_id`, `chunk_id`, `field`로 구성된다.
오류코드 목록은 `docs/error_codes_v0.1.md` 참고.

### HumanReview

| 필드 | 타입 | 의미 |
|---|---|---|
| `required` | bool | 사람이 봐야 하는가 |
| `status` | `NOT_REQUIRED` \| `PENDING` \| `APPROVED` \| `MODIFIED` \| `REJECTED` | 검토 상태 |
| `reasons` | ReviewReason[] | 검토 사유 코드 |
| `threshold_profile` | string | 적용된 임계값 묶음 이름 |
| `reviewer_id` | string | |
| `reviewed_at` | datetime | |
| `review_note` | string | |

`required=True`인데 `status=NOT_REQUIRED`이거나, 사유가 비어 있으면 **객체 생성 자체가 거부된다.**

### VersionInfo

| 필드 | 의미 |
|---|---|
| `schema_version` | 이 스키마 버전 (현재 0.2.0) |
| `prompt_version` | 사용한 프롬프트 파일 버전 |
| `model_name` | 모델명 |
| `ruleset_version` | 판단 규칙 문서 버전 |
| `kb_sha256` | 검색팀 메타데이터의 KB 해시 |
| `embedding_model_revision` | BGE-M3 revision |

없으면 "지난주 결과가 왜 달랐는지"를 영영 못 밝힌다. 전부 저장한다.

---

## 4. 점수 필드를 4개로 나눈 이유

| 필드 | 만드는 주체 | 의미 |
|---|---|---|
| `similarity_score` | 검색팀 | 표현이 얼마나 닮았는가 |
| `llm_confidence` | LLM | 모델이 자기 판단에 매긴 값 |
| `validation.passed` | Validator | 형식·근거가 맞는가 |
| `human_review.required` | Review Policy | 사람이 봐야 하는가 |

성격이 전부 다르다. 하나의 `confidence`로 합치자는 얘기가 나오면 반대한다.
합치는 순간 **검색이 틀린 건지, 모델이 틀린 건지, 형식이 깨진 건지 구분할 수 없다.**

> `llm_confidence = 0.91`은 정확도 91%가 아니다.
> 실제 정확도는 골든셋 평가로만 계산한다. 화면에도 "정확도"라고 쓰지 않는다.

---

## 5. `mapping_type`을 두지 않은 이유

1:1인지 1:N인지는 **배열 길이로 계산되는 값**이다.

```
len(mapped_controls) == 1   →  1:1
len(mapped_controls) >= 2   →  1:N
match_status == NO_MATCH    →  매핑 없음
```

따로 저장하면 배열과 어긋난 값이 생기고, Validator가 매번 일치 검사를 해야 한다.
화면에 "1:N"으로 표시할 일이 있으면 그때 계산해서 보여준다.

---

## 6. 샘플

| 파일 | 내용 |
|---|---|
| `samples/single_mapping.json` | 단일 매핑, 검토 불필요 |
| `samples/multi_mapping.json` | 1:N 매핑, 검토 필요(`R205`) |
| `samples/no_match.json` | 매핑 없음, 검토 필요(`R204`) |
| `samples/review_required.json` | 낮은 confidence + UNCERTAIN + OCR 품질 |

```bash
python src/check.py
```

샘플이 모델로 파싱되는지, 규칙 위반이 실제로 차단되는지 한 번에 확인한다.

---

## 6.5 백엔드로 넘길 때 — `evidence.status`

입력팀 DB의 `evidence.status`는 처리 상태와 검토 상태를 한 칸에 담는다.
우리는 둘을 나눠 관리하고, 넘기는 순간에만 합친다.

| 판단 결과 | `evidence.status` |
|---|---|
| `processing_status = FAILED` | `FAILED` |
| `human_review.required = true` | `REVIEW_REQUIRED` |
| 그 외 | `COMPLETED` |

코드는 `service.to_evidence_status()`다.
**거꾸로는 복원되지 않는다.** 검토 사유와 오류코드는 우리 결과에만 있다.

---

## 7. 미결정

- [x] ~~입력 필드명~~ → 전처리팀·검색팀 실제 구현에 맞춤
- [x] ~~`control_id` 표기~~ → `x.y.z`, 점 없음
- [ ] **`evidence_id` 표기** — `E0001` / `000001` / 정수 중 무엇인지 (전처리팀 내부에서 갈려 있다)
- [x] ~~OCR 품질 점수~~ → 없음. `source == "ocr"`로 대체
- [ ] **청크의 `source`가 실제로 `"ocr"`로 채워지는가** — 파서 블록에 `source` 칸이 아직 없어서
      `R207`이 한 번도 발동하지 않을 수 있다
- [ ] **청크별 검색 결과를 누가 합치는가** (검색팀) — 가장 급하다
- [ ] 후보에 `requirement`를 실을지 (검색팀) — KB에 이미 있다
- [ ] Top-K를 5로 고정할지 (검색팀)
- [ ] 증적에 청크가 여러 개일 때 한 번에 판단하는가, 청크별로 판단하고 합치는가
- [ ] `llm_raw_response_ref` 로그 보관 위치·기간 (백엔드 담당)
