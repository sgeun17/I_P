# 입력 폴더 실행 방법

모든 명령은 `입력` 폴더에서 실행하세요.

## 1. 설치

```bash
pip install -r database/requirements.txt
```

## 2. 서버 실행

MySQL이 켜져 있고 `database/.env` 가 있어야 해요. (`database/.env.example` 참고)

```bash
uvicorn main:app --reload
```

브라우저에서 `http://127.0.0.1:8000` 을 열면 업로드 화면이 나와요. 끄려면 `Ctrl + C`.

## 3. DOCX 추출

```python
from docx_extractor import extract_docx

result = extract_docx(path, source_file="정책.docx")
```

- `path` : DOCX 파일 경로
- `source_file` : 사용자가 올린 원래 파일명 (안 넘기면 경로의 파일명)
- 예외를 던지지 않아요. 손상/빈 문서는 `result["blocks"] == []` 이고 `result["errors"]` 에 이유가 담겨요.

```python
if result["errors"]:
    print(result["errors"][0]["code"], result["errors"][0]["message"])
else:
    for block in result["blocks"]:
        print(block["order"], block["block_type"], block["text"] or block["table"]["rows"])
```

오류 코드: `FILE_NOT_FOUND`, `EMPTY_FILE`, `FILE_PARSE_FAILED`, `EMPTY_DOCUMENT`

## 4. 테스트 실행

```bash
python -m pytest tests -v
```

`22 passed` 가 나오면 정상이에요.
