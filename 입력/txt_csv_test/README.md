# TXT·CSV 인코딩/파싱 (Phase 1 입력 - 텍스트 담당)

TXT·CSV 파일의 **글자 인코딩을 알아내서 읽고**, TXT 는 문단으로, CSV 는 표로 뽑는 파서예요.
PDF·DOCX·XLSX·PPTX 와 **똑같은 형식(팀 규칙 v2)** 으로 결과를 냅니다.

> ⚠️ **지금은 업로드 단계에서 txt·csv 가 막혀 있어요.** 파서는 혼자 돌아가지만, 실제로 쓰려면
> A파트(업로드 검사)와 B파트(DB 저장)의 허용 목록을 바꿔야 해요. 아래 "업로드와 연결하려면" 참고.

## 파일

| 파일 | 하는 일 |
|---|---|
| `text_parser.py` | ★ 파서 본체. `parse_text(경로)` 하나면 확장자 보고 알아서 골라요 |
| `test_text_parser.py` | 팀 규칙 + 정상/예외 테스트 36개 (samples 폴더 사용 → **지우지 마세요**) |
| `samples/` | 테스트용 파일 16개 |
| `make_samples.py` | 샘플을 다시 만들 때만 사용 |

**필요한 도구 없음** — 파이썬 기본 기능만 써요 (`requirements.txt` 추가할 것 없음).

## 실행 방법

```bash
python test_text_parser.py                        # "✅ 36개 통과, ❌ 0개 실패" 나오면 정상
python text_parser.py samples/05_cp949.csv        # 결과 JSON 직접 보기
python text_parser.py "samples2\서버 접속 로그.txt"   # 띄어쓰기 있으면 따옴표
```

코드에서 쓸 때:
```python
from text_parser import parse_text          # 확장자로 알아서 (txt → parse_txt, csv → parse_csv)
result = parse_text("C:/team/database/data/evidence/000005_v1.csv")
```

## 결과 예시

TXT (빈 줄 기준 문단, 문단 안 줄바꿈은 `\n` 으로 남김)
```json
{ "source_file": "01_utf8.txt", "file_type": "txt", "page_count": null,
  "blocks": [
    { "order": 1, "block_type": "paragraph", "level": null, "page": null, "text": "정보보호 정책 점검 결과", "table": null },
    { "order": 2, "block_type": "paragraph", "level": null, "page": null,
      "text": "점검일: 2026-09-01\n점검자: 정보보호부 홍길동", "table": null } ],
  "errors": [] }
```

CSV (표 하나)
```json
{ "source_file": "05_cp949.csv", "file_type": "csv", "page_count": null,
  "blocks": [
    { "order": 1, "block_type": "table", "level": null, "page": null, "text": "",
      "table": { "rows": [["계정", "이름", "부서", "발급일"], ["user01", "홍길동", "정보보호부", "2026-09-01"]] } } ],
  "errors": [] }
```

## 인코딩 판별

한국에서 실제로 나오는 순서대로 시도해요.

| 순서 | 인코딩 | 어디서 나오나 |
|---|---|---|
| ① | BOM 표시가 있으면 그대로 (UTF-8 BOM, UTF-16) | 엑셀 "CSV UTF-8", 메모장 "유니코드" |
| ② | UTF-8 | 요즘 대부분의 프로그램, 리눅스 로그 |
| ③ | CP949 (EUC-KR 포함) | **한국 엑셀 기본 "CSV(쉼표로 분리)"**, 옛날 메모장 "ANSI" |

모두 실패하거나, 파일 안에 글자 파일에는 없는 값(NUL)이 있으면 → `corrupted_file` (이름만 .txt 인 그림 파일 등)

## CSV 구분자

쉼표(`,`) · 탭 · 세미콜론(`;`) · 세로줄(`|`) 을 자동으로 알아내요. 못 알아내면 쉼표로 봐요.

## ★ 팀 확인이 필요한 결정 7가지

| # | 정한 것 | 왜 | 바꾸려면 |
|---|---|---|---|
| 1 | `page`, `page_count` = null | 페이지 개념이 없음 (DOCX 와 같음) | - |
| 2 | TXT 문단 안 줄바꿈은 `\n` 으로 남김 | 로그·설정 파일은 한 줄 한 줄이 의미가 있음. PDF 는 공백으로 합치는 것과 다름 | `_paragraphs` 수정 |
| 3 | TXT 문단이 50줄 넘으면 나눔 | 빈 줄 없는 로그 파일이 블록 하나가 되는 것 방지 | `MAX_LINES_PER_BLOCK` |
| 4 | TXT 는 전부 paragraph (제목 없음) | 제목을 판단할 근거(글자 크기·스타일)가 없음 | - |
| 4-2 | TXT 구분선(`*****` `=====` `-----` `━━━` 등 같은 기호 3개 이상만 있는 줄)은 빈 줄처럼 문단을 나누고 버림 | 점검 스크립트 결과·로그에 흔함. 안 그러면 구분선이 제목과 한 문단으로 붙음 | `SEPARATOR` |
| 5 | CSV 맨 윗줄이 한 칸만 채워진 제목 줄이면 heading level 1 | 엑셀 파서와 같은 방식 | `_peel_title_rows` 호출 제거 |
| 6 | CSV 200줄 넘으면 나누고 머리글 반복 | 엑셀 파서와 같음 | `MAX_TABLE_ROWS`, `REPEAT_HEADER` |
| 7 | 0바이트·공백만 있는 파일 = `empty_document` | 글자 파일은 비어 있어도 "열리는" 파일이라서. **다른 파서는 0바이트를 `corrupted_file` 로 봐요** (업로드 단계에서 빈 파일은 이미 막히니 실제로는 거의 안 나옴) | `_read_text` 수정 |

## 테스트 결과 (36개 통과)

| 샘플 | 확인 내용 |
|---|---|
| 01~03 TXT (UTF-8 / CP949 / UTF-16) | 세 인코딩 모두 같은 문단 3개 |
| 04~07 CSV (UTF-8 BOM / CP949 / 탭 / 세미콜론) | 네 가지 모두 같은 3줄 × 4칸 표 |
| 08 들쭉날쭉 CSV | 칸 수 맞춤, 빈 줄·빈 칸 버림, 따옴표 안 줄바꿈은 공백 |
| 09 제목 줄 CSV | 제목은 heading, 표는 머리글부터 |
| 10 긴 CSV (251줄) | 표 2개, 머리글 반복, 250줄 모두 있음 |
| 11 긴 로그 (120줄, 빈 줄 없음) | 50 + 50 + 20 줄로 나뉨 |
| 16 구분선 TXT (점검 스크립트 결과 모양) | 구분선에서 문단이 나뉘고 구분선은 사라짐 |
| 12 공백만 / 13 0바이트 | `empty_document` |
| 14 이름만 txt 인 그림 / 15 안 읽히는 파일 / 없는 파일 | `corrupted_file` |

## 실제 파일로 확인한 결과

- `chocolate_cake.txt`, `fried_rice.txt` : 빈 줄 기준 문단 4개, 들여쓰기·번호 목록 줄바꿈 유지
- `fried_rice.txt` 맨 위의 `*****` 장식 줄이 제목과 한 문단으로 붙던 문제 → 구분선 처리로 해결
- CSV 는 실제 파일로 아직 못 돌려봄 (샘플 9개로만 확인)

## 알아둘 한계

- **CP949 는 거의 모든 값을 "읽어버려요."** 일본어·중국어 인코딩 파일처럼 UTF-8 도 CP949 도 아닌 파일은 에러 없이 **깨진 글자로** 나올 수 있어요. 한국 증적에서는 거의 없는 경우예요.
- **TXT 의 제목·목록 구조는 알 수 없어요.** 전부 paragraph 예요.
- **CSV 에는 병합 셀·수식·서식이 없어요.** 엑셀에서 CSV 로 저장하면 이미 사라진 상태예요. 엑셀 증적은 xlsx 원본이 더 정확해요.
- **확장자가 .txt 인데 내용이 CSV** 인 경우는 문단으로 나와요 (표로 안 바뀜).

## 업로드와 연결하려면 (★ 팀 합의 후)

지금은 두 군데에서 txt·csv 를 막고 있어요.

**A파트 `main.py`** (팀원 코드)
- 27번째 줄 `ALLOWED` 목록에 `"txt"`, `"csv"` 추가
- 106~109번째 줄 "실제 파일 종류 검사(매직 넘버)" : `filetype` 이 txt·csv 를 판별하지 못해서(`None`)
  **목록에 추가만 하면 `CONTENT_MISMATCH` 로 거절돼요.** txt·csv 는 이 검사를 건너뛰고 다른 방식으로 확인해야 해요.
- 268번째 줄 화면 안내 문구에 TXT, CSV 추가

**B파트 `database/config.py`** (본인 코드)
- 48번째 줄 `ALLOWED_FILE_TYPES` 에 `"txt"`, `"csv"` 추가 (45~46번째 줄 주석도 정리)

## 다음 단계와 연결

```python
from evidence_store import get_file_path, update_status
from text_parser import parse_text

update_status(eid, "PREPROCESSING")
result = parse_text(get_file_path(eid))
if result["blocks"]:
    update_status(eid, "PREPROCESSED")
else:
    update_status(eid, "FAILED", error_code="FILE_PARSE_FAILED",
                  error_message=", ".join(result["errors"]))
```
`evidence_id`, `version` 은 파서가 아니라 이 부분(파서를 부르는 쪽)에서 결과에 붙여요.

## 저장소에 없는 파일

테스트 파일(`test_*.py`), 샘플 폴더(`samples/`), `make_samples.py`, `requirements.txt` 는
저장소에 올리지 않았어요 (각자 로컬에만 있음). 필요한 도구는 `database/requirements.txt` 에 모아 두었어요.
