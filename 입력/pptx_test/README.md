# PPTX 슬라이드 텍스트 추출 (Phase 1 입력 - 파워포인트 담당)

파워포인트를 슬라이드 순서대로 읽어서 **제목·글상자 내용·표를 뽑고, 몇 번째 슬라이드였는지 기록**하는 파서예요.
PDF·DOCX·XLSX 와 **똑같은 형식(팀 규칙 v2)** 으로 결과를 냅니다.

## 파일

| 파일 | 하는 일 |
|---|---|
| `pptx_parser.py` | ★ 파서 본체. `parse_pptx(경로)` 하나만 쓰면 돼요 |
| `test_pptx_parser.py` | 팀 규칙 + 정상/예외 테스트 36개 (samples 폴더 사용 → **지우지 마세요**) |
| `samples/` | 테스트용 파워포인트 15개 |
| `make_samples.py` | 샘플을 다시 만들 때만 사용 (보통은 실행 안 해도 됨) |
| `requirements.txt` | 필요한 도구 목록 (`python-pptx` 하나) |

## 실행 방법

```bash
cd C:\pptx_part
python -m pip install -r requirements.txt

python pptx_parser.py samples/01_normal.pptx   # 결과 JSON 직접 보기
```
파일 이름에 띄어쓰기가 있으면 `"따옴표"` 로 감싸세요.
```powershell
python pptx_parser.py "samples2\정보보호 교육자료.pptx"
```

코드에서 쓸 때:
```python
from pptx_parser import parse_pptx
result = parse_pptx("C:/team/database/data/evidence/000003_v1.pptx")
```

## 결과 예시

```json
{
  "source_file": "01_normal.pptx",
  "file_type": "pptx",
  "page_count": 2,
  "blocks": [
    { "order": 1, "block_type": "heading",   "level": 1,    "page": 1, "text": "정보보호 교육 결과 보고", "table": null },
    { "order": 2, "block_type": "paragraph", "level": null, "page": 1, "text": "교육일: 2026-09-01",   "table": null },
    { "order": 3, "block_type": "table",     "level": null, "page": 2, "text": "",
      "table": { "rows": [["부서", "대상", "이수"], ["정보보호부", "12", "12"]] } }
  ],
  "errors": []
}
```

## 팀 규칙 v2 를 어떻게 지켰나

| 규칙 | 구현 |
|---|---|
| 1. block_type 3종 | heading(슬라이드 제목) + paragraph(글상자 문단) + table |
| 2. order 1부터 연속, block_id 없음 | 슬라이드 순서 → 슬라이드 안에서 위에서 아래 |
| 3. page | **슬라이드 번호(1부터)**, page_count 는 슬라이드 개수 ★ |
| 4. level | 슬라이드 제목 heading 은 항상 1 |
| 5. table | table 블록만 `{"rows": ...}`, 나머지 null |
| 6. rows 2차원, 병합 셀 "" | 병합 칸 중 왼쪽 위에만 값, 나머지는 "" |
| 7. text | table 이면 "" |
| 8. 빈 블록 버리기 | 빈 글상자·빈 표·내용 없는 슬라이드는 버림 |
| 9. 손상/빈 문서 | 예외 없이 `blocks: []` + `errors` |
| 10. errors 항상 리스트 | 없으면 `[]` |
| 11. 일부 실패 | 슬라이드 하나가 실패하면 그 슬라이드만 건너뜀, `errors` 는 `[]` |

## PPT 만의 처리

- **도형 순서**: PPT 는 도형에 "읽는 순서"가 없어요. 그래서 화면 위치(위→아래, 같은 높이면 왼→오른쪽)로 정렬해요. 만든 순서대로 읽으면 뒤죽박죽이 돼요.
- **그룹 도형**: 묶인 도형 안까지 들어가서 글자를 뽑아요. 그룹 안 도형의 좌표는 "그룹 안 좌표"라서 슬라이드 좌표로 **환산**해요. 환산하지 않으면 순서가 뒤집혀요.
- **제목 틀이 없는 PPT**: 템플릿으로 만든 자료는 제목 틀 대신 글상자만 쓰는 경우가 많아요. 그때는 **슬라이드에서 가장 큰 글자**(2~60자, 숫자·기호만인 것 제외)를 제목으로 잡아요. "01", "02" 같은 장식 번호는 더 크더라도 제목이 아니에요.
- **차트**: 그림이 아니라 **표로** 바꿔요. `[["구분", "점검결과"], ["적합", "8"], ["미흡", "2"]]` 처럼요. 점검 결과 그래프의 숫자가 검색에 걸리게 하려고요.
- **스마트아트(도해)**: 글자가 슬라이드가 아닌 별도 파일에 들어 있어서, 그 파일에서 글자만 꺼내요.
- **발표자 노트**: 슬라이드 맨 뒤에 `[발표자 노트] …` 형태로 붙여요.
- **한쇼(한글과컴퓨터) 파일**: 도형이 `AlternateContent` 블록 안에 들어 있어서 **파일은 열리는데 내용이 비어 보일 수 있어요.** 그래서 내용이 하나도 없으면 그 블록을 풀어낸 사본을 메모리에 만들어 다시 읽어요. (엑셀 파서와 같은 방식, 원본 파일은 안 건드림)

## ★ 팀 확인이 필요한 결정 6가지

| # | 정한 것 | 왜 | 바꾸려면 |
|---|---|---|---|
| 1 | `page` = 슬라이드 번호, `page_count` = 슬라이드 개수 | "3번째 슬라이드에 있음"으로 증적 위치를 말할 수 있음 | 코드 두 줄 |
| 2 | 슬라이드 제목 = heading level 1. 제목 틀이 없으면 가장 큰 글자로 추정 | 템플릿 자료는 제목 틀을 안 쓰는 경우가 많음 | `TITLE_AS_HEADING = False` |
| 3 | 발표자 노트 포함 + `[발표자 노트]` 표시 | 노트에 설명이 들어 있는 증적이 있음. 표시가 없으면 본문과 섞임 | `INCLUDE_NOTES = False` |
| 4 | 숨긴 슬라이드도 포함 | 증적에서 숨긴 내용이 중요할 수 있음 | `SKIP_HIDDEN = True` |
| 5 | 글상자 안 문단 = 각각 한 블록 | 글머리표 한 줄이 한 블록. 청킹에서 합치기는 쉬워도 나누기는 어려움 | 코드 수정 필요 |
| 6 | 차트를 표로 변환 | 그래프의 숫자를 살리기 위해 | `_chart_rows` 호출 제거 |

## 테스트 결과 (36개 통과)

| 샘플 | 확인 내용 |
|---|---|
| 01 정상 2장 | 제목 heading level 1, 글머리표가 한 줄씩 |
| 02 표 | 3줄 × 3칸 |
| 03 병합 셀 | 병합 칸 `""` |
| 04 도형 순서 | 만든 순서가 아니라 화면 위치 순서로 |
| 05 발표자 노트 | 맨 뒤에 `[발표자 노트]` 표시와 함께 |
| 06 그림만 있는 슬라이드 | `empty_document` |
| 07 숨긴 슬라이드 | 기본 설정에서 포함, page 2 |
| 08 빈 PPT | `empty_document` |
| 12 한쇼 파일 | python-pptx 단독으로는 안 보이는 내용을 읽어냄 |
| 13 그룹 + 차트 | 그룹 안 도형 나옴, 차트가 표로 |
| 14 제목 틀 없는 PPT | 가장 큰 글자가 heading, 장식 번호 01 은 문단 |
| 15 그룹 좌표 | 환산 안 하면 뒤집히는 순서가 바로 나옴 |
| (스마트아트) | 도해 파일에서 글자 추출, 깨진 XML 이면 빈 목록 |
| 09 손상 / 10 0바이트 / 11 암호·옛날 ppt / 없는 파일 | `corrupted_file` |

## 실제 자료로 확인한 결과 (20장짜리 발표 자료)

- 20장에서 **제목 20개가 모두** 잡혔어요 (제목 틀이 하나도 없는 파일이었음).
- 목차 슬라이드의 "01 + 항목명" 짝이 화면 순서대로 나와요 (그룹 좌표 환산 전에는 뒤엉켰음).
- 본문·표 글자는 빠짐없이 나왔고 `errors` 는 `[]` 였어요.
- 슬라이드 원본에 글상자가 겹쳐 있으면(그림자 효과 등) 같은 글이 두 번 나와요. 파일 자체가 그런 것이라 파서에서는 구분할 수 없어요.

## 알아둘 한계

- **캡처 화면·그림 속 글자는 못 읽어요.** PPT 증적은 화면 캡처가 많아서 영향이 커요. 그림만 있는 슬라이드는 `empty_document` 가 됩니다. OCR 을 붙이면 해결돼요.
- **글머리표 단계(1단계·2단계·3단계)는 사라져요.** 전부 같은 paragraph 로 나와요.
- **도형의 색·모양으로 표현된 의미는 사라져요.** (예: 빨간 화살표 = 문제 구간)
- **애니메이션·슬라이드 마스터·머리글 바닥글**은 뽑지 않아요.
- **좌우 2단 구성**은 좌우를 번갈아 읽어요 (왼쪽 칸 전부 → 오른쪽 칸 전부가 아님). 2단 자료가 많으면 칸을 먼저 나누는 방식으로 바꿔야 해요.
- **겹쳐 놓은 글상자**(그림자 효과)는 같은 글이 두 번 나와요.
- **옛날 `.ppt`** 는 못 열어요 (`corrupted_file`). 업로드 허용 목록에도 `.ppt` 는 없어요.
- **암호 걸린 파일**은 `corrupted_file` 이라 "깨진 파일"과 구분되지 않아요.

## 다음 단계와 연결

```python
from evidence_store import get_file_path, update_status
from pptx_parser import parse_pptx

update_status(eid, "PREPROCESSING")
result = parse_pptx(get_file_path(eid))
if result["blocks"]:
    update_status(eid, "PREPROCESSED")
else:
    update_status(eid, "FAILED", error_code="FILE_PARSE_FAILED",
                  error_message=", ".join(result["errors"]))
```
`evidence_id`, `version` 은 파서가 아니라 이 부분(파서를 부르는 쪽)에서 결과에 붙여요.

청킹할 때는 **table 블록의 `text` 가 `""`** 라서, `table.rows` 를 글자로 바꿔야 검색에 걸려요.
그리고 PPT 는 문단이 잘게 쪼개져 있으니, **같은 슬라이드(page)의 블록을 하나로 묶어서** 조각을 만드는 게 좋아요.

## 저장소에 없는 파일

테스트 파일(`test_*.py`), 샘플 폴더(`samples/`), `make_samples.py`, `requirements.txt` 는
저장소에 올리지 않았어요 (각자 로컬에만 있음). 필요한 도구는 `database/requirements.txt` 에 모아 두었어요.
