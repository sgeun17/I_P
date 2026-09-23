# 청킹-B : 표 청크 + 청크 형식 (Phase 1 입력)

파서가 뽑은 **표 블록을 검색에 걸리는 글자로 바꿔서 표 청크로 만들고**,
청크의 **형식(chunk_id·page·text)을 고정·검사**하는 부분이에요.
글 청크는 청킹-A(`chunker.py` 의 `make_chunks`)가 만들어요.

## 파일

| 파일 | 하는 일 |
|---|---|
| `chunk_format.py` | ★ 청크 형식 고정. `make_chunk_id` · `finalize_chunks` · `validate_chunks`, 크기 설정 |
| `table_chunker.py` | ★ 표 블록 → 표 청크. `table_chunks` · `fill_continued_headers` |
| `chunk_links.py` | 청킹-A 와 잇는 연결 파일. A 는 `from chunk_links import ...` 만 하면 됨 |
| `chunker.py` | 청킹-A 본체. `make_chunks(parsed, evidence_id, version)` |
| `CHUNK_JSON.md` | 검색팀에 넘기는 청크 형식 문서 |

**필요한 도구 없음**. 파이썬 기본 기능만 써요 (`requirements.txt` 에 추가할 것 없음).

## 실행 방법

```python
from chunking.chunker import make_chunks
from chunking.chunk_format import validate_chunks

chunks = make_chunks(parsed, evidence_id, version)   # parsed = 파서 결과
problems = validate_chunks(chunks)                   # [] 이면 규칙 통과
```
테스트 파일(`test_*.py`)·샘플·`run_chunks.py` 는 저장소에 올리지 않았어요 (각자 로컬).

## 청킹-A 와 연결

```python
from chunk_links import CHUNK_MAX_CHARS, OVERLAP_CHARS, finalize_chunks, fill_continued_headers, table_chunks

def make_chunks(parsed, evidence_id, version):
    if parsed["errors"] or not parsed["blocks"]:
        return []
    blocks = fill_continued_headers(parsed["blocks"])   # ① 시작할 때 한 번 (PDF 페이지 넘김 표)
    parts, heading = [], None
    for b in blocks:
        if b["block_type"] == "heading":
            heading = b["text"]                          # ② 제목 기억 (글 청크 끊기 등은 A 담당)
        elif b["block_type"] == "table":
            # (모으던 글 청크를 먼저 끝내고)
            parts += table_chunks(b, heading, CHUNK_MAX_CHARS)   # ③ 표 청크 조각
        ...
    return finalize_chunks(parts, evidence_id, version,
                           parsed["file_type"], parsed["source_file"])     # ④ 번호·ID 붙이기
```

- `table_chunks()`가 돌려주는 **조각**에는 `chunk_type, page_start, page_end, heading, text, block_orders, source`가 들어 있어요.
- A도 글 청크를 **같은 모양의 조각**으로 만들어서 문서 순서대로 `parts`에 넣으면 돼요. 그러면 `finalize_chunks()`가 `chunk_index`, `chunk_id`, `evidence_id`, `version`, `file_type`, `source_file`을 붙여 줘요.
- `finalize_chunks()` 를 안 쓰고 직접 붙여도 돼요 (지금 `chunker.py` 방식). 칸 순서는 상관없어요.
- 다 만든 뒤 `validate_chunks(chunks)`가 `[]`이면 규칙을 지킨 거예요.

> ★ 공통 규칙에서 바뀐 점: **크기 설정(`CHUNK_MAX_CHARS`, `OVERLAP_CHARS`)은 `chunk_format.py` 에 있어요.**
> A 파일에 두면 A↔B 가 서로 import 하게 돼서 오류가 나요. A 는 여기서 import 해서 쓰면 돼요.
>
> ★ 청킹-A 에 맞춘 것 (09-22)
> - 연결 파일 이름 `chunk_links.py`
> - **청크 번호는 0부터** (`INDEX_START = 0`, 첫 청크 `000001_v1_c0000`)
> - **파일 이름은 `source_file` 칸**을 새로 둠. `source` 칸은 그대로 `"parser"` (나중에 OCR 글이면 `"ocr"`)

## 청크 형식

```json
{
  "chunk_id": "000001_v1_c0003",
  "evidence_id": "000001", "version": 1, "chunk_index": 3,
  "file_type": "pdf", "source_file": "계정발급대장.pdf", "chunk_type": "table",
  "page_start": 2, "page_end": 2,
  "heading": "계정 발급 대장",
  "text": "연번: 37 | 계정: user037 | 부서: 정보보호부 | 승인일: 2026-09-01\n…",
  "block_orders": [3],
  "source": "parser"
}
```

`validate_chunks()` 가 검사하는 것:
- 칸 이름 (빠진 칸·없어야 할 칸. 칸 순서는 상관없음)
- `chunk_index` 0부터 빈 번호 없음, `chunk_id` 형식과 중복
- `evidence_id` 는 B파트 DB 가 주는 글자(`"000001"`) 또는 정수 둘 다 허용
- `source_file` 이 비어 있지 않고 한 문서 안에서 같음
- `file_type`, `chunk_type`, `source`(parser·ocr) 값
- page: PDF·XLSX·PPTX는 숫자이고 `start ≤ end`, DOCX·TXT·CSV는 null
- `heading`은 글자 또는 null
- `text`는 비어 있지 않고 800자 이하 (**겹침 포함**)
- `block_orders`는 오름차순이고, 표 청크는 블록 1개에서만 나옴
- 청크 순서가 문서 순서와 같음

## 표 → 글자 규칙

| 표 모양 | 결과 |
|---|---|
| 보통 표 (첫 줄 = 머리글) | `계정: user01 \| 승인자: 홍길동 \| 승인일: 2026-09-01` |
| 가로 병합 머리글 | `시스템 접근권한: 서버 관리자 \| 비고: 승인 필요` (왼쪽 머리글 이어받음) |
| 2칸 항목표 (서약서·신청서) | `성명: 홍길동` 한 줄씩. 항목 칸이 비었으면 위 항목 이어받음 |
| 줄 하나뿐인 표 | 칸을 ` \| ` 로 이음 |
| 빈 칸 | 건너뜀 |

- 줄마다 머리글이 붙어 있어서 **표가 여러 청크로 나뉘어도 조각 하나만 읽어도 뜻이 통해요.** 그래서 표 청크끼리는 겹치지 않아요.
- 표 한 줄은 자르지 않아요. **한 줄이 혼자서 800자를 넘을 때만** ` | ` 기준으로 자르고, 그래도 길면 글자 수로 잘라요.

### 2칸 표 구분 (항목표 vs 머리글 표)
아래 중 하나면 **머리글 표**, 아니면 **항목표**로 봐요.
- 왼쪽 칸 값의 절반 이상에 숫자가 있음 (user01, 2026-09-01, 연번)
- 오른쪽 칸이 첫 줄은 글자이고 나머지는 전부 숫자 (건수 / 12)

애매하면 항목표로 봐요. 항목표로 잘못 보면 `항목: 건수 / 계정 발급: 12` 처럼 그래도 읽히지만,
머리글 표로 잘못 보면 `성명: 소속 | 홍길동: 정보보호부` 처럼 뜻이 망가지기 때문이에요.

### PDF 페이지 넘김 표
PDF 는 표가 두 페이지에 걸치면 페이지마다 따로 table 블록이 나오고, 뒤 조각은 머리글 없이 시작해요.
`fill_continued_headers()` 가 **바로 앞 블록이 다음 페이지 직전의 표이고 칸 수가 같으면** 앞 표의 머리글을 붙여줘요.
- 블록을 합치지 않아서 **page 는 원래 페이지 그대로**예요 (인용할 때 페이지가 정확함)
- 머리글이 다시 인쇄된 표는 그대로 둠 (머리글 두 번 안 붙음)
- 첫 줄이 자기 머리글처럼 보이면(첫 줄엔 숫자 없고 둘째 줄엔 있음) **다른 표**로 보고 안 붙임
- 원본 blocks 는 바꾸지 않고 사본을 돌려줘요

## 테스트 결과 (74개 통과)

| 확인 내용 | 샘플 |
|---|---|
| chunk_id 형식 (0번 = c0000), 잘못된 값은 오류 | - |
| 모든 샘플이 `validate_chunks == []` | 11개 전부 |
| 보통 표 / 병합 머리글 / 빈 칸 | 01, 02, 07 |
| 페이지 넘김 표: 머리글 붙음·반복 머리글·다른 표 구분 | 03, 04, 05 |
| 2칸 표 구분, 시트 번호·이름, DOCX page null | 06, 09 |
| 250줄 CSV: 800자 이하, 줄 안 잘림, 빠짐·중복 없음 | 08 |
| 800자 넘는 한 줄만 잘림, 글자 안 사라짐 | 10 |
| 표 아닌 블록·빈 표 → `[]`, 차트 표 | 11 |
| 규칙 위반 20가지를 `validate_chunks` 가 잡아냄, 칸 순서만 다르면 통과 | - |

실제 파서(PDF 14개 + 페이지 넘김 PDF 2개, XLSX 13개, CSV 7개) 결과로도 돌려서 **오류 없이 규칙 위반 0건**이었어요.

## 알아둘 한계

- **머리글이 두 줄인 표**는 첫 줄만 머리글로 봐요. 둘째 머리글 줄은 데이터처럼 나와요.
- **4칸 이상 항목표** (`성명 | 홍길동 | 소속 | 정보보호부`)는 머리글 표로 잘못 봐요.
- **세로 병합 칸**(데이터 칸의 `""`)은 건너뛰어요. 2칸 항목표의 항목 칸만 위 값을 이어받아요.
- 페이지 넘김 판단은 추정이에요. 칸 수가 같은 다른 표가 연속 페이지에 머리글 없이 있으면 앞 표 머리글이 붙을 수 있어요.

