# XLSX 시트·셀 텍스트 추출 (Phase 1 입력 - 엑셀 담당)

엑셀 파일을 시트 순서대로 읽어서 **시트 이름과 표를 뽑고, 몇 번째 시트였는지 기록**하는 파서예요.
PDF·DOCX 와 **똑같은 형식(팀 규칙 v2)** 으로 결과를 냅니다.

## 파일

| 파일 | 하는 일 |
|---|---|
| `xlsx_parser.py` | ★ 파서 본체. `parse_xlsx(경로)` 하나만 쓰면 돼요 |
| `test_xlsx_parser.py` | 팀 규칙 + 정상/예외 테스트 36개 (samples 폴더 사용 → **지우지 마세요**) |
| `samples/` | 테스트용 엑셀 13개 |
| `make_samples.py` | 샘플을 다시 만들 때만 사용 (보통은 실행 안 해도 됨) |
| `requirements.txt` | 필요한 도구 목록 (`openpyxl` 하나) |

## 실행 방법

```bash
cd C:\xlsx_part
python -m pip install -r requirements.txt

python test_xlsx_parser.py                     # "✅ 36개 통과, ❌ 0개 실패" 나오면 정상
python xlsx_parser.py samples/01_normal.xlsx   # 결과 JSON 직접 보기
```
파일 이름에 띄어쓰기가 있으면 `"따옴표"` 로 감싸세요.
```powershell
python xlsx_parser.py "samples2\계정 관리대장.xlsx"
```

코드에서 쓸 때:
```python
from xlsx_parser import parse_xlsx
result = parse_xlsx("C:/team/database/data/evidence/000002_v1.xlsx")
```

## 결과 예시

```json
{
  "source_file": "01_normal.xlsx",
  "file_type": "xlsx",
  "page_count": 1,
  "blocks": [
    { "order": 1, "block_type": "heading", "level": 1, "page": 1, "text": "계정관리대장", "table": null },
    { "order": 2, "block_type": "table", "level": null, "page": 1, "text": "",
      "table": { "rows": [["계정", "이름", "부서", "발급일"],
                          ["user01", "홍길동", "정보보호부", "2026-09-01"]] } }
  ],
  "errors": []
}
```

## 팀 규칙 v2 를 어떻게 지켰나

| 규칙 | 구현 |
|---|---|
| 1. block_type 3종 | heading(시트 이름) + table. paragraph 는 안 씀 |
| 2. order 1부터 연속, block_id 없음 | 시트 순서 → 시트 안에서 위에서 아래 |
| 3. page | **시트 번호(1부터)**, page_count 는 시트 개수 ★ |
| 4. level | 시트 이름 heading 은 항상 1 |
| 5. table | table 블록만 `{"rows": ...}`, 나머지 null |
| 6. rows 2차원, 병합 셀 "" | openpyxl 이 병합 칸 중 왼쪽 위에만 값을 줌 → 나머지는 "" |
| 7. text | table 이면 "" |
| 8. 빈 블록 버리기 | 빈 줄·빈 칸(열)·빈 시트는 버림 |
| 9. 손상/빈 문서 | 예외 없이 `blocks: []` + `errors` |
| 10. errors 항상 리스트 | 없으면 `[]` |
| 11. 일부 실패 | 시트 하나가 실패하면 그 시트만 건너뜀, `errors` 는 `[]` |

## 한셀(한글과컴퓨터)에서 만든 엑셀

한셀로 만든 엑셀은 스타일 정보(styles.xml) 안에 `<mc:AlternateContent>` 라는 특수 블록을 넣어요.
openpyxl 이 이 블록을 건너뛰어서 **스타일을 덜 읽고, 그 스타일을 쓰는 셀에서 `IndexError` 로 터져요.**
(실제 증적 3개가 전부 이 경우였고, 멀쩡한 파일이 `corrupted_file` 로 처리됐어요)

그래서 파서는 이렇게 해요.
1. 그냥 열어본다
2. 실패하면 → 그 특수 블록을 풀어낸 **사본을 메모리에** 만들어서 다시 열어본다 (원본 파일은 안 건드림)
3. 그래도 실패하면 `corrupted_file`

공공기관·금융권 증적에 한셀 파일이 많아서, **DOCX·PPTX 담당도 같은 문제를 겪을 수 있어요** (한글로 만든 docx, 한쇼로 만든 pptx).

테스트의 `check_rules()` 가 모든 결과에 대해 규칙 1~10 을 자동으로 검사해요.
칸 값이 전부 문자열인지(JSON 으로 안전한지)도 같이 봅니다.

## ★ 팀 확인이 필요한 결정 5가지

정답이 없어서 일단 이렇게 정해 뒀어요. 팀에서 다르게 정하면 파일 맨 위 설정값만 바꾸면 돼요.

| # | 정한 것 | 왜 | 바꾸려면 |
|---|---|---|---|
| 1 | `page` = 시트 번호, `page_count` = 시트 개수 | 엑셀은 "페이지"가 없고 시트가 자연스러운 단위. 증적 위치를 "2번째 시트"로 말할 수 있음 | 팀이 null 로 정하면 코드 두 줄 수정 |
| 2 | 시트 이름을 heading(level 1) 블록으로 | 형식에 시트 이름 칸이 없음. 시트 이름 자체가 증적("2026 상반기 점검")일 때가 많음 | `SHEET_NAME_AS_HEADING = False` |
| 3 | 숨긴 시트·숨긴 행도 포함 | 증적에서 숨긴 내용이 중요할 수 있음 (빠뜨리면 놓침) | `SKIP_HIDDEN = True` |
| 4 | 수식은 계산된 값. 계산값이 없으면 수식 글자 | 엑셀로 한 번도 안 연 파일은 계산값이 저장돼 있지 않음. 빈 칸으로 두면 내용이 통째로 사라짐 | 코드의 fallback 부분 제거 |
| 6 | 표 맨 윗줄이 "한 칸만 채워진 제목 줄"이면 heading(level 2)으로 빼냄 | 엑셀 증적은 표 위에 병합된 제목 줄이 흔한데, 그대로 두면 청킹이 제목 줄을 머리글로 착각함 | `_peel_title_rows` 호출 제거 |
| 5 | 200줄 넘는 시트는 나누고, 나뉜 표마다 머리글 반복 | 접속기록처럼 수천 줄인 증적이 한 블록이면 청킹이 어려움 | `MAX_TABLE_ROWS`, `REPEAT_HEADER` |

## 테스트 결과 (36개 통과)

| 샘플 | 확인 내용 |
|---|---|
| 01 정상 | 시트 이름 heading → 표, 날짜가 `2026-09-01` 로 |
| 02 시트 2개 | page 가 1, 2 로 나뉨 |
| 03 병합 셀 | 병합 칸 `""` |
| 04 빈 엑셀 | `empty_document` |
| 05 빈 줄·빈 칸 | 가운데 빈 줄과 빈 열이 버려짐 |
| 06 수식 | 계산값이 없어도 내용이 남음 |
| 07 숨긴 시트·행 | 기본 설정에서 포함됨 |
| 08 긴 시트(251줄) | 표 2개로 나뉘고 머리글 반복, 시트 번호는 그대로 |
| 12 제목 줄 | 병합된 제목 줄이 heading level 2 로 빠지고 표는 머리글부터 시작 |
| 13 한셀 스타일 | openpyxl 단독으로는 못 여는 파일을 복구해서 읽음 |
| 09 손상 / 10 0바이트 / 11 암호·옛날 xls / 없는 파일 | `corrupted_file` |

## 실제 증적 6개로 확인한 결과

| 파일 | 결과 |
|---|---|
| 채용일정(7월 2주차) | 12칸 × 19줄, 빈 칸 7개·빈 줄 1개 정리됨, 긴 URL 온전 |
| 특수권한 신청·승인 대장 (한셀) | 시트 2개, 8×15 / 3×8 |
| 게시자료 게시이력 (한셀) | 시트 2개, 7×10 / 3×5 |
| 매체 관리실태 점검 내역 (한셀) | 시트 1개, 4×9 |
| 서버 보안설정 점검표 | 시트 2개, 61×8 / 6×8 |
| 웹 취약점 점검결과 | 시트 2개, 11×9 / 10×2 |
| 백신 업데이트 현황 | 시트 2개, 9×13 / 6×8 |

원본 시트 크기와 결과 표 크기가 모두 일치해요 (빠진 줄·칸 없음).

## 알아둘 한계

- **셀 색깔·메모·조건부 서식으로 표현된 정보는 사라져요.** (예: 빨간색 = 미조치)
- **차트·그림·도형 안의 글자는 안 나와요.** (캡처 화면을 붙여 넣은 증적은 OCR 이 필요)
- **표시 형식이 일부 사라져요.** `45.0%` → `0.45`, `₩1,000` → `1000` 처럼 값만 남아요.
- **옛날 `.xls`** 는 못 열어요 (`corrupted_file`). 업로드 허용 목록에도 `.xls` 는 없어요.
- **암호 걸린 엑셀**은 `corrupted_file` 이라, "깨진 파일"과 구분되지 않아요.
- 표가 시트 안에서 여러 개 떨어져 있어도 **시트 하나 = 표 하나**로 나와요.
- 매우 큰 파일(수십만 줄)은 메모리를 많이 써요. 필요해지면 `read_only` 방식으로 바꿔야 하는데, 그러면 병합 셀 정보를 잃어요.

## 다음 단계와 연결

```python
from evidence_store import get_file_path, update_status
from xlsx_parser import parse_xlsx

update_status(eid, "PREPROCESSING")
result = parse_xlsx(get_file_path(eid))
if result["blocks"]:
    update_status(eid, "PREPROCESSED")
else:
    update_status(eid, "FAILED", error_code="FILE_PARSE_FAILED",
                  error_message=", ".join(result["errors"]))
```
`evidence_id`, `version` 은 파서가 아니라 이 부분(파서를 부르는 쪽)에서 결과에 붙여요.

청킹할 때는 **table 블록의 `text` 가 `""`** 라서, `table.rows` 를 글자로 바꿔야 검색에 걸려요.
머리글 줄과 짝지어서 `"계정: user01 | 이름: 홍길동 | 부서: 정보보호부"` 처럼 만들면 됩니다.
