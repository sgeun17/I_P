# PDF 텍스트/페이지 추출 (Phase 1 입력 - PDF 담당)

PDF 파일을 위에서 아래로 읽어서 **제목·문단·표를 순서대로 뽑고, 몇 페이지에 있었는지 기록**하는 파서예요.
팀 합의 규칙 **v2** 를 따라요.

## 파일

| 파일 | 하는 일 |
|---|---|
| `pdf_parser.py` | ★ 파서 본체. `parse_pdf(경로)` 하나만 쓰면 돼요 |
| `test_pdf_parser.py` | 팀 규칙 + 정상/예외 테스트 34개 (samples 폴더 사용 → **지우지 마세요**) |
| `samples/` | 테스트용 PDF 13개 |
| `make_samples.py` | 샘플 PDF를 다시 만들 때만 사용 (보통은 실행 안 해도 됨) |
| `requirements.txt` | 필요한 도구 목록 (`pdfplumber` 하나) |

## 실행 방법

```bash
cd C:\pdf_part
python -m pip install -r requirements.txt

python pdf_parser.py samples/01_normal.pdf   # 결과 JSON 직접 보기
```

내 가상 증적으로 해보기 (samples 는 그대로 두고 폴더를 따로 만드세요):
```bash
python pdf_parser.py my_test\가상증적1.pdf
python pdf_parser.py my_test\가상증적1.pdf | Out-File -Encoding utf8 my_test\결과1.json   # 파일로 저장
```
PowerShell 에서 `>` 로 저장하면 한글이 깨질 수 있어서 `Out-File -Encoding utf8` 을 써요.

코드에서 쓸 때:
```python
from pdf_parser import parse_pdf
result = parse_pdf("C:/team/database/data/evidence/000001_v1.pdf")
```

## 결과 예시

```json
{
  "source_file": "01_normal.pdf",
  "file_type": "pdf",
  "page_count": 2,
  "blocks": [
    { "order": 1, "block_type": "heading",   "level": 1,    "page": 1, "text": "계정관리 절차서", "table": null },
    { "order": 2, "block_type": "heading",   "level": 2,    "page": 1, "text": "제1장 총칙",     "table": null },
    { "order": 3, "block_type": "paragraph", "level": null, "page": 1, "text": "제1조 (목적) 본 절차는 ...", "table": null },
    { "order": 4, "block_type": "table",     "level": null, "page": 1, "text": "",
      "table": { "rows": [["계정", "승인자"], ["user01", "홍길동"]] } }
  ],
  "errors": []
}
```

## 팀 규칙 v2 를 어떻게 지켰나

| 규칙 | 구현 |
|---|---|
| 1. block_type 3종 | paragraph, table, heading |
| 2. order 1부터 연속, block_id 없음 | 페이지 순서 → 페이지 안에서 위에서 아래 |
| 3. page | 실제 페이지 번호 |
| 4. level | heading 만 1·2·3, 나머지 null |
| 5. table | table 블록만 `{"rows": ...}`, 나머지 null |
| 6. rows 2차원, 병합 셀 "" | pdfplumber 가 병합 칸을 None 으로 주면 "" 로 바꿈 |
| 7. text | table 이면 "" |
| 8. 빈 블록 버리기 | 글자 없는 줄, 모든 칸이 빈 표는 버림 (**팀 확인 필요 항목**) |
| 9. 손상/빈 문서 | 예외 없이 `blocks: []` + `errors` |
| 10. errors 항상 리스트 | 없으면 `[]` |

테스트의 `check_rules()` 가 모든 결과에 대해 규칙 1~10 을 자동으로 검사해요.

## PDF에서 제목(heading)을 찾는 방법

PDF 파일 안에는 "이 줄이 제목"이라는 정보가 없어요 (DOCX 는 스타일로 알 수 있음). 그래서 **글자 크기**로 판단해요.

1. 문서에서 **가장 많이 쓰인 글자 크기 = 본문 크기**
2. 본문보다 **15% 이상 크고**, **100자 이하**, **2줄 이하**면 제목
3. 제목 크기가 **큰 것부터 level 1, 2, 3** (4단계 이상은 모두 3)

조정하고 싶으면 `pdf_parser.py` 맨 위 숫자(`HEADING_SIZE_RATIO` 등)만 바꾸면 돼요.

**한계:** 제목을 본문과 같은 크기에 **굵게만** 한 문서는 제목으로 못 잡고 paragraph 로 나와요. 반대로 본문 중간에 큰 글씨로 강조한 짧은 문구는 제목으로 잡힐 수 있어요.

## errors 코드 (팀 합의: 두 가지만)

| 코드 | 언제 | blocks |
|---|---|---|
| `corrupted_file` | 파일을 열 수 없음 — 깨진 파일, PDF가 아닌 파일, 잘린 파일, 0바이트, **비밀번호 걸린 파일**, 없는 파일 | `[]` |
| `empty_document` | 열리는데 뽑을 글자·표가 없음 — 빈 문서, **글자 없이 그림만 있는 스캔본** | `[]` |

- 에러가 나면 `blocks` 는 `[]` 이고 `errors` 에는 코드가 **딱 하나** 들어가요.
- **일부 페이지만** 비었거나 읽다 실패하면, 그 페이지만 건너뛰고 나머지를 뽑아요. 이때 `errors` 는 `[]` 예요.
- 알아둘 점: 스캔본도 `empty_document` 로 나와서, 나중에 OCR 분기를 할 때는 "진짜 빈 문서"와 구분이 안 돼요.
  필요해지면 `no_text_layer` 같은 코드를 팀과 다시 합의해서 추가하면 돼요.

## 테스트 결과 (34개 통과)

| 샘플 | 확인 내용 |
|---|---|
| 01 정상 2페이지 | 제목 level 1·2·2·3, 문단 4개, 두 줄 문단 합쳐짐, 페이지 번호 |
| 02 문단→표→문단 | 순서 유지, 표 3×3, 표 글자 중복 없음 |
| 03 병합 셀 | 병합 칸 `""` |
| 04 빈 PDF | `empty_document` |
| 05 스캔본 | `empty_document` |
| 06 2페이지만 빔 | 1·3쪽 뽑힘, `errors: []` |
| 11 제목 4단계 | level 1, 2, 3, **3** |
| 12 워드/한글식 표 | 칸 배경색 상자 때문에 쪼개지던 칸이 3×3 으로 정상, 두 줄 칸은 한 칸 |
| 13 흐름도+표, 넓은 줄 간격 | 붙어 있는 흐름도 줄과 표가 따로 나옴, 줄바꿈된 문장은 한 문단, ● 항목은 각각 한 문단 |
| (URL) | 주소 중간 줄바꿈은 붙이고, 주소 두 개·주소 뒤 한글은 띄어쓰기 유지 |
| 07 손상 / 08 잘림 / 10 0바이트 | `corrupted_file` |
| 09 암호 | `corrupted_file` |
| 없는 파일 | `corrupted_file` |

## 워드/한글에서 만든 PDF 의 표

워드·한글로 만든 PDF 는 칸마다 **배경색 상자, 안쪽 여백 상자**를 따로 그려요.
pdfplumber 는 이 상자 가장자리도 표 선으로 봐서 한 칸을 `"", 값, ""` 처럼 가는 칸 3개로 쪼개고,
머리글 줄이 한 칸씩 밀리거나, 두 줄짜리 칸이 두 줄(row)로 갈라졌어요.
그래서 파서가 이렇게 처리해요.

1. 테두리 없이 색만 칠한 넓은 상자(= 배경색)는 표 선으로 보지 않음
2. 그래도 남은 경계선 중 `CELL_MERGE_TOL`(6pt) 안에 붙어 있는 것은 한 선으로 합쳐서 진짜 칸을 다시 만듦
3. 끝까지 모든 줄이 빈 칸(열)은 버림
4. 표 두 개가 딱 붙어 있으면(예: 흐름도 줄 바로 밑에 절차 표) 하나로 잡히는데,
   위아래 줄의 칸 경계가 절반 이상 다르면(`SPLIT_TABLE_RATIO`) 거기서 잘라 두 표로 냄

## 주소(URL) 줄바꿈

칸 너비 때문에 **주소(https://…) 중간에서 줄이 바뀌면** 띄어쓰기 없이 이어 붙여요 (링크가 깨지지 않게).

## 문단 나누기

1. 글자 크기가 바뀌면 새 문단
2. 줄 간격이 좁으면(글자 높이 × `PARAGRAPH_GAP_RATIO` 0.8 이하) 같은 문단
3. 줄 간격이 넓은 문서: 윗줄이 오른쪽 끝까지 차 있으면(자동 줄바꿈) 간격이 글자 높이 × 1.3 까지는 같은 문단
4. 단, 아랫줄이 목록 기호(● • ※ - ㅇ / 1. / 가. / (1) / 제3조)로 시작하면 항상 새 문단

## 알아둘 한계

- 머리글·바닥글(매 페이지 반복되는 회사명, 페이지 번호)은 문단으로 같이 나와요.
- 여러 페이지에 걸친 표는 페이지마다 따로 table 블록으로 나와요 (머리글 줄도 페이지마다 반복).
- 줄 간격이 아주 넓은 문서에서, 오른쪽 끝에서 6글자보다 일찍 끝난 줄은 문단 끝으로 봐서 가끔 한 문장이 둘로 나뉠 수 있어요.
- 원본에 비어 있는 페이지는 머리글·바닥글만 나와요 (정상).
- 선이 없는 표(공백으로만 칸을 맞춘 표)는 표로 인식되지 않을 수 있어요.
- **한글(HWP)로 만든 "양옆 세로선 없는 표"는 표로 못 잡거나 일부 칸만 잡혀요.** 못 잡은 칸의 글자는 문단으로 뭉쳐서 나와요 (글자 자체는 빠지지 않음).
- 취소선(삭제 표시)은 구분하지 못하고 일반 글자로 나와요.
- **엑셀에서 저장한 PDF 는 넓은 표가 "아래로 먼저, 그다음 옆으로" 잘려서 한 줄의 내용이 여러 표로 흩어져 나와요.** 엑셀 증적은 PDF 로 바꾸지 말고 xlsx 원본으로 올리는 게 맞아요.
- 표 칸 너비 때문에 한글 단어 중간에서 줄바꿈되면 "진 단", "보유 자" 처럼 공백이 생길 수 있어요.
- 두 칼럼 문서는 좌우 글자가 섞일 수 있어요.

## 다음 단계와 연결

```python
from evidence_store import get_file_path, update_status
from pdf_parser import parse_pdf

update_status(eid, "PREPROCESSING")
result = parse_pdf(get_file_path(eid))
if result["blocks"]:
    update_status(eid, "PREPROCESSED")
else:
    update_status(eid, "FAILED", error_code="FILE_PARSE_FAILED",
                  error_message=", ".join(result["errors"]))
```
`evidence_id`, `version` 은 파서가 아니라 이 부분(파서를 부르는 쪽)에서 결과에 붙이기로 팀과 맞추면 돼요.
