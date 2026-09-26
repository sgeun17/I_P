## 증적 장부 정리(B파트)

B파트가 하는 일은 한 줄로 **"올라온 증적 파일을 기록하고, 필요한 사람에게 꺼내주는 것"** 이에요.

| 팀에서 받은 할 일 | 한 곳 | 확인하는 방법 |
|---|---|---|
| 1. 장부(DB 표) 만들기 | `schema.sql` | `SHOW TABLES;` |
| 2. 파일 지문(해시) 계산 | `evidence_store.py` → `compute_hash()` | `practice/step2_hash.py` |
| 3. 같은 파일 확인, 새 증적이면 번호 발급, 수정본이면 버전 +1 | `save_evidence()`, `replace_evidence()` | `practice/step3_save.py`, `step5_replace.py` |
| 4. 저장된 파일을 조회하는 주소 | `get_file_path()`, `list_evidence()`, `get_evidence()`, `api.py` | `practice/step4_lookup.py`, 브라우저 | 

---

## 1. 폴더 구성

```
database/
├─ schema.sql          ① 표 2개 만들기
├─ evidence_store.py   ② 핵심 함수 전부 (다른 팀원이 가져다 쓰는 파일)
├─ api.py              ③ 조회 주소 /api/v1/evidence (위에서 말한 4번 파트)
├─ db.py               ④ MySQL 접속
├─ config.py           ⑤ 설정 (허용 형식, 번호 모양, 상태값, 폴더)
│
├─ requirements.txt    설치할 도구 목록
├─ .env.example        비밀번호 적는 파일의 예시
├─ .gitignore          Git에 안 올릴 것 (.env, data/)
├─ README.md           이 문서
│
└─ practice/           연습·확인용 (없어도 B파트는 동작함) (깃에는 안올림)
   ├─ step1_check_connection.py   파이썬 ↔ MySQL 연결 확인
   ├─ step2_hash.py               파일 지문 계산
   ├─ step3_save.py               새 증적 저장 + 중복 표시
   ├─ step4_lookup.py             조회, 상태 변경
   ├─ step5_replace.py            수정본으로 교체 (버전 +1)
   └─ sample_files.py             연습용 PDF 만드는 도구
```

---

## 2. 정해진 규칙

| 항목 | 정한 내용 |
|---|---|
| JSON 칸 이름 | 팀 규격: `evidence_id, version, file_name, file_type, file_size, file_hash, uploaded_at, status, error_code, error_message` |
| 허용 파일 | `pdf, docx, xlsx, pptx, txt, csv, png, jpg` (png·jpg 는 `ocr_parser.py` 가 처리) |
| 증적 번호 | **`E0001`** (`config.py` 의 `ID_PREFIX="E"`, `ID_DIGITS=4`). 2026-09-25 팀 확정 |
| 저장 파일 이름 | `data/evidence/E0001_v1.pdf` (원래 파일명은 DB에만 저장) |
| 같은 파일 재업로드 | 막지 않고 `is_duplicate: true`, `duplicate_of: E0001` 로 표시만 |
| 상태값 | `UPLOADED, PREPROCESSING, PREPROCESSED, MAPPING, VALIDATING, COMPLETED, REVIEW_REQUIRED, FAILED` 8개만 허용 |
| 버전 | **파일 단위.** 같은 증적을 수정본으로 바꿀 때마다 +1, 표 2개로 관리 |

### 버전 관리 (표 2개)

| 표 | 내용 |
|---|---|
| `evidence` | 지금 쓰는 증적. 증적 하나당 한 줄, 항상 최신본 |
| `evidence_history` | 교체되기 전 옛날 버전 보관함. 교체할 때마다 한 줄씩 쌓임 |
| `chunk` | 청킹 결과 (2026-09-26 추가). `chunk_schema.sql` 로 만들고 `chunk_store.py` 로 다룸 |

기존 → 수정 → 최종 수정, 이렇게 세 번 올리면

```
evidence          : E0001 | v3 | 계정관리_절차서_최종.pdf
evidence_history  : E0001 | v1 | 계정관리_절차서.pdf
                    E0001 | v2 | 계정관리_절차서_수정.pdf
파일              : E0001_v1.pdf, E0001_v2.pdf, E0001_v3.pdf
```

- 표는 처음에 2개 만들면 끝이에요. 교체할 때마다 보관함에 **줄**이 늘어나요.
- 번호(E0001)는 그대로예요. 교체되면 상태가 `UPLOADED` 로 돌아가요 (새 파일이라 전처리부터 다시).
- 저장 방식(표 2개, 덮어쓰기 등)이 나중에 바뀌어도 `evidence_store.py` 와 `schema.sql` 만 고치면 돼요.

---


## 3. 제대로 되는지 확인 (연습 스크립트)
(깃에는 이 연습 스크립트 안올림)

database 폴더에서 순서대로 실행하세요. 각 결과가 이렇게 나오면 정상이에요.

| 명령 | 정상 결과 |
|---|---|
| `python practice/step1_check_connection.py` | `✅ 연결 성공!` 과 표 칸 목록 |
| `python practice/step2_hash.py` | 이름만 다르면 `True`, 한 글자 다르면 `False`, 지문 64글자 |
| `python practice/step3_save.py` | `E0001` 저장, 두 번째는 `000002` + `is_duplicate: True` |
| `python practice/step4_lookup.py` | 목록, 파일 경로, 상태 변경, 틀린 입력 2개가 `✅ 막힘` |
| `python practice/step5_replace.py` | `E0001` 이 v1 → v2, 버전 이력 2줄, 틀린 입력 2개가 `✅ 막힘` |

내 파일로 저장해보려면: `python practice/step3_save.py "C:\Users\나\Documents\파일.pdf"` (원본은 그대로 남아요)

MySQL에서 직접 보기:
```sql
SELECT evidence_id, version, file_name, status FROM evidence;
SELECT evidence_id, version, file_name, replaced_at FROM evidence_history;
```
한글이 `???` 로 보이면 `mysql -u root -p --default-character-set=utf8mb4` 로 접속하세요 (저장은 정상).



### 조회 주소 확인

```bash
uvicorn api:app --reload
```

브라우저에서 `http://127.0.0.1:8000/docs` → 항목 열고 "Try it out" → "Execute". 끄려면 터미널에서 `Ctrl + C`.

| 주소 | 내용 |
|---|---|
| `GET /api/v1/evidence?status=&page=1&size=20` | 목록 (최신본만) |
| `GET /api/v1/evidence/{evidence_id}` | 상세 + 버전 이력 |

응답은 팀 규격 모양이에요.
```json
{ "success": true,  "data": { ... }, "error": null }
{ "success": false, "data": null, "error": { "code": "EVIDENCE_NOT_FOUND", "message": "..." } }
```

---

## 5. 함수 사용법 (팀원에게 공유할 부분)

```python
from evidence_store import (
    save_evidence, replace_evidence,        # A파트가 씀
    get_file_path, update_status,           # 전처리·이후 단계가 씀
    list_evidence, get_evidence, delete_evidence,
    EvidenceError,
)
```

| 함수 | 누가 | 하는 일 | 돌려주는 것 |
|---|---|---|---|
| `save_evidence(임시파일경로, file_name, file_type)` | A파트 | 새 증적 등록 (v1) | `evidence_id, version, file_name, file_type, file_size, file_hash, uploaded_at, status, is_duplicate, duplicate_of` |
| `replace_evidence(evidence_id, 임시파일경로, file_name, file_type)` | A파트 | 수정본으로 교체 (v+1) | `evidence_id, version, previous_version, file_name, ...` |
| `get_file_path(evidence_id)` | 전처리 | 지금 버전 파일 경로. `get_file_path(id, 1)` 이면 v1 | 경로 문자열 |
| `update_status(evidence_id, status, error_code=None, error_message=None)` | 각 단계 | 상태 변경 (8개 값만) | 없음 |
| `list_evidence(status=None, page=1, size=20)` | 화면 | 목록 (최신본만, 최신 업로드 순) | `items, page, size, total` |
| `get_evidence(evidence_id)` | 화면 | 상세 + `versions` 이력 | 증적 정보 |
| `delete_evidence(evidence_id)` | 화면 | 모든 버전 **파일 먼저 → DB 나중** 삭제. 청크도 같이 지워짐 | 없음 |

- 문제가 있으면 `EvidenceError` 가 나요. `e.code`(예: `EVIDENCE_NOT_FOUND`, `FILE_UPLOAD_FAILED`, `INVALID_FILE_TYPE`, `FILE_NOT_FOUND`, `FILE_DELETE_FAILED`)와 `e.message` 로 이유를 알 수 있어요.
- `save_evidence`, `replace_evidence` 가 성공하면 임시 파일은 보관 폴더로 **옮겨져서** 원래 자리에 없어요.
- 저장 중 하나라도 실패하면 DB 기록과 파일을 전부 되돌리고 `FILE_UPLOAD_FAILED` 를 내요.

**전처리 담당 예시**
```python
update_status(evidence_id, "PREPROCESSING")
path = get_file_path(evidence_id)
try:
    ...  # 파싱, 청킹
    update_status(evidence_id, "PREPROCESSED")
except Exception:
    update_status(evidence_id, "FAILED", error_code="FILE_PARSE_FAILED",
                  error_message="PDF 파일에서 텍스트를 추출하지 못했습니다.")
```

### 팀 규칙으로 정해둘 것

1. **증적 정보는 이 함수들로만 가져가기.** 표를 직접 SQL로 조회하면, 나중에 버전 저장 방식이 바뀔 때 그 사람 코드까지 고쳐야 해요.
2. **각자 PC에서 `schema.sql` 다시 실행하기.** `evidence_history` 표가 없으면 새 증적 업로드부터 에러가 나요.
3. **청크·매핑 결과를 저장할 때 `version` 도 같이 저장하기.** 교체되면 v1 결과와 v2 결과가 섞이지 않게.

## 저장소에 없는 파일

`api.py` 와 `practice/` 는 아직 저장소에 올리지 않았어요 (로컬 확인용). 위 문서의 해당 부분은 참고용이에요.

---

## 청크 표 (2026-09-26 추가)

청킹 결과를 담는 표예요. 증적 정보(`evidence`)와 달리 **파일 안의 글자**가 들어갑니다.

### 만들기

```bash
mysql -u root -p evidence_db < database/schema.sql          # 먼저
mysql -u root -p evidence_db < database/chunk_schema.sql    # 그 다음
```

`evidence` 표가 있어야 외래키가 걸려서, 순서를 지켜야 해요.

### 표 모양

청크 JSON 13칸을 그대로 담고 `created_at` 하나를 더 둡니다.

| 칸 | 내용 |
|---|---|
| `chunk_id` | `E0001_v1_c0000` (기본키) |
| `evidence_id`, `version`, `chunk_index` | 어떤 증적의 몇 번째 청크인지 (셋이 유니크) |
| `file_type`, `source_file`, `chunk_type` | 형식·원래 파일명·`text`/`table` |
| `page_start`, `page_end` | PDF=페이지, XLSX=시트, PPTX=슬라이드. 나머지는 NULL |
| `heading`, `text` | 제목·본문 (본문 800자 이하) |
| `block_orders` | 원본 블록 번호. `"[1, 2]"` 형태의 글자로 저장 |
| `source` | `parser` 또는 `ocr` |
| `created_at` | 청크를 만든 시각 |

**증적을 지우면 그 청크도 자동으로 지워집니다** (`ON DELETE CASCADE`). 증적과 청크가 어긋나지 않게 하려는 것이에요.

### 쓰는 법 (`chunk_store.py`)

```python
from chunk_store import save_chunks, get_chunks, delete_chunks, count_chunks

save_chunks(chunks)              # 저장. 같은 증적의 옛 청크는 지우고 새로 넣음
rows = get_chunks("E0001")       # 청크 JSON 13칸 그대로, chunk_index 순서
delete_chunks("E0001")           # 지우기
count_chunks("E0001")            # 개수
```

- `save_chunks()` 는 **같은 증적의 모든 버전 청크를 지우고** 새로 넣는 게 기본이에요
  (새 버전이 올라왔을 때 옛 청크가 검색에 남지 않게). 남기려면 `save_chunks(chunks, replace=False)`.
- 없는 증적에 저장하면 `ChunkError("EVIDENCE_NOT_FOUND")` 가 납니다.
- ⚠ **마스킹을 하지 않아서 `text` 칸에 개인정보가 그대로 들어갑니다.** DB 권한·백업 관리에 주의하세요.

### 정해야 할 것

- 새 버전이 올라왔을 때 **이전 버전 청크를 지울지** (지금은 지우는 쪽이 기본) — 검색팀과 합의 필요
- 청크를 검색팀 ChromaDB 로 넘기는 방법 (함수 호출 / JSON / DB 직접 읽기)
