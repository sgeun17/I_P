"""
text_parser.py : TXT·CSV 인코딩 판별 + 파싱 (Phase 1 입력 - 텍스트 담당)

하는 일
  TXT : 글자 인코딩을 알아내서 읽고, 빈 줄 기준으로 문단(paragraph)을 나눕니다.
  CSV : 글자 인코딩과 구분자(쉼표·탭·세미콜론·세로줄)를 알아내서 표(table)로 만듭니다.

팀 합의 규칙 (v2) - PDF/DOCX/XLSX/PPTX 와 같은 형식
  1. block_type : paragraph, table, heading 세 가지만
  2. order      : 문서 내 등장 순서, 1부터 연속 (block_id 없음)
  3. page       : TXT·CSV 는 페이지가 없어서 null (DOCX 와 같음). page_count 도 null
  4. level      : heading 일 때만 1, 2, 3 중 하나, 나머지는 null
  5. table      : block_type 이 table 일 때만 {"rows": [...]}, 아니면 null
  6. table.rows : 2차원 배열, 줄마다 칸 수를 맞춤 (모자라면 "" 로 채움)
  7. text       : paragraph/heading 일 때만 채우고, table 이면 ""
  8. 빈 블록은 버림 (빈 줄만 있는 문단, 빈 줄·빈 칸(열))
  9. 손상/빈 문서 : 예외를 던지지 않고 blocks: [] + errors 에 사유 코드
 10. errors     : 항상 리스트, 없으면 []

인코딩 (한국에서 실제로 나오는 순서대로 시도)
  ① BOM 표시가 있으면 그대로 (UTF-8 BOM, UTF-16)   ← 메모장 "유니코드"로 저장, 엑셀 "CSV UTF-8"
  ② UTF-8                                           ← 요즘 대부분
  ③ CP949 (EUC-KR 포함)                              ← 엑셀 "CSV(쉼표로 분리)", 옛날 윈도우 메모장
  모두 실패하거나, 글자 파일이 아닌 것(그림 등)이면 corrupted_file

이 파서가 정한 것 (★ 팀 확인 필요)
  ★ page / page_count = null (페이지 개념이 없음)
  ★ TXT 는 문단 안의 줄바꿈을 "\\n" 으로 남김 (로그·설정 파일은 한 줄 한 줄이 의미가 있어서)
  ★ TXT 문단이 MAX_LINES_PER_BLOCK 줄보다 길면 나눔 (빈 줄 없는 로그 파일 대비)
  ★ TXT 는 제목을 판단할 근거가 없어서 전부 paragraph
  ★ TXT 의 구분선(같은 기호 3개 이상만 있는 줄: *****, =====, ━━━ 등)은 빈 줄처럼 문단을 나누고 버림
  ★ CSV 맨 윗줄이 "한 칸만 채워진 제목 줄"이면 heading level 1 로 빼냄 (엑셀 파서와 같은 방식)
  ★ CSV 가 MAX_TABLE_ROWS 줄보다 길면 나누고, 나뉜 표마다 머리글 줄을 다시 붙임 (엑셀과 같음)
  ★ 0바이트·공백만 있는 파일은 empty_document (글자 파일은 "비어 있어도 열리는" 파일이라서)

errors 코드 (팀 합의: 두 가지만 사용)
  corrupted_file   파일을 열 수 없음 (없는 파일, 글자 파일이 아님, 어떤 인코딩으로도 안 읽힘)
  empty_document   열리긴 하는데 글자가 하나도 없음 (0바이트, 빈 줄·공백만 있음)

사용법
  from text_parser import parse_txt, parse_csv, parse_text
  result = parse_text("C:/team/database/data/evidence/000004_v1.csv")   # 확장자로 알아서 고름

  명령창에서 결과 보기:  python text_parser.py 파일경로.txt
  (파일 이름에 띄어쓰기가 있으면 "따옴표"로 감싸세요)
"""

import csv
import io
import json
import os
import logging

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)
import re
import sys
from pathlib import Path

MAX_LINES_PER_BLOCK = 50     # TXT 문단 한 블록의 최대 줄 수
MAX_TABLE_ROWS = 200         # CSV 표 한 블록의 최대 줄 수
REPEAT_HEADER = True         # 나눈 CSV 표마다 첫 줄(머리글)을 다시 붙일지
DELIMITERS = ",\t;|"         # CSV 구분자 후보

ENCODINGS = ["utf-8", "cp949"]    # BOM 이 없을 때 시도하는 순서
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   # 줄바꿈·탭 빼고 보이지 않는 제어 문자
# 구분선 : 같은 기호만 3개 이상 반복된 줄 (*****, =====, -----, ━━━━, ──── 등) → 빈 줄처럼 문단을 나누고 버림
SEPARATOR = re.compile(r"^\s*([-=*_#~+.·•─━═▬■□◆◇●○※])\1{2,}\s*$")


def parse_text(path):
    """확장자를 보고 parse_txt / parse_csv 중 하나를 부릅니다."""
    return parse_csv(path) if Path(path).suffix.lower() == ".csv" else parse_txt(path)


def parse_txt(path):
    """TXT 파일 하나를 읽어서 팀 합의 포맷의 딕셔너리로 돌려줍니다. 예외를 밖으로 던지지 않아요."""
    path = Path(path)
    result = _empty_result(path, "txt")

    text, error = _read_text(path)
    if error:
        result["errors"].append(error)
        return result

    blocks = []
    for paragraph in _paragraphs(text):
        blocks.append(_block(len(blocks) + 1, "paragraph", text=paragraph))

    if not blocks:
        result["errors"].append("empty_document")
        return result
    result["blocks"] = blocks
    return result


def parse_csv(path):
    """CSV 파일 하나를 읽어서 팀 합의 포맷의 딕셔너리로 돌려줍니다. 예외를 밖으로 던지지 않아요."""
    path = Path(path)
    result = _empty_result(path, "csv")

    text, error = _read_text(path)
    if error:
        result["errors"].append(error)
        return result

    try:
        rows = _csv_rows(text)
    except Exception:
        logger.exception("CSV parsing failed: %s", path)
        result["errors"].append("corrupted_file")
        return result

    blocks = []
    rows, titles = _peel_title_rows(rows)
    for title in titles:
        blocks.append(_block(len(blocks) + 1, "heading", text=title, level=1))
    if rows:
        for part in _split_rows(rows):
            blocks.append(_block(len(blocks) + 1, "table", table={"rows": part}))

    if not blocks:
        result["errors"].append("empty_document")
        return result
    result["blocks"] = blocks
    return result


# ─────────────────────────────────────────────
# 내부 도우미 함수
# ─────────────────────────────────────────────
def _empty_result(path, file_type):
    return {
        "source_file": path.name,
        "file_type": file_type,
        "page_count": None,
        "blocks": [],
        "errors": [],
    }


def _block(order, block_type, text="", level=None, table=None):
    return {"order": order, "block_type": block_type, "level": level,
            "page": None, "text": text, "table": table}


def _read_text(path):
    """
    파일을 글자로 읽습니다. 돌려주는 값: (글자, 에러코드)
      - 없는 파일                              → corrupted_file
      - 0바이트                                → empty_document
      - 글자 파일이 아님(그림·압축 파일 등)     → corrupted_file
      - 어떤 인코딩으로도 안 읽힘              → corrupted_file
    """
    if not path.exists():
        return None, "corrupted_file"
    if os.path.getsize(path) == 0:
        return None, "empty_document"
    try:
        data = path.read_bytes()
    except Exception:
        logger.exception("File read failed: %s", path)
        return None, "corrupted_file"

    text = _decode(data)
    if text is None:
        return None, "corrupted_file"
    text = CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return text, None


def _decode(data):
    """BOM → UTF-8 → CP949 순서로 글자로 바꿔봅니다. 글자 파일이 아니면 None."""
    if data.startswith(b"\xef\xbb\xbf"):
        return _try(data, "utf-8-sig")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return _try(data, "utf-16")
    if b"\x00" in data[:8192]:            # BOM 없는데 NUL 이 있으면 글자 파일이 아님 (그림, 압축 등)
        return None
    for encoding in ENCODINGS:
        text = _try(data, encoding)
        if text is not None:
            return text
    return None


def _try(data, encoding):
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def _paragraphs(text):
    """
    빈 줄(또는 ***** ===== 같은 구분선)을 기준으로 문단을 나눕니다. 문단 안의 줄바꿈은 "\\n" 으로 남겨요.
    구분선 줄 자체는 내용이 없어서 버려요.
    한 문단이 MAX_LINES_PER_BLOCK 줄보다 길면 (빈 줄 없는 로그 파일 등) 그 줄 수마다 나눕니다.
    """
    out, current = [], []

    def flush():
        lines = [ln.rstrip() for ln in current]
        while lines and not lines[0].strip():
            lines.pop(0)
        for i in range(0, len(lines), MAX_LINES_PER_BLOCK):
            chunk = "\n".join(lines[i:i + MAX_LINES_PER_BLOCK]).strip("\n")
            if chunk.strip():
                out.append(chunk)
        current.clear()

    for line in text.split("\n"):
        if line.strip() and not SEPARATOR.match(line):
            current.append(line)
        else:
            flush()                        # 빈 줄 또는 구분선 → 문단 끝
    flush()
    return out


def _csv_rows(text):
    """구분자를 알아내서 CSV 를 2차원 배열로 만듭니다. 빈 줄·빈 칸(열)은 버리고 칸 수를 맞춰요."""
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=DELIMITERS)
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [[" ".join(cell.split()) for cell in row] for row in reader]

    rows = [row for row in rows if any(row)]                     # 빈 줄 버림
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]      # 칸 수 맞추기
    keep = [k for k in range(width) if any(row[k] for row in rows)]   # 빈 칸(열) 버림
    return [[row[k] for k in keep] for row in rows]


def _peel_title_rows(rows):
    """맨 윗줄이 "한 칸만 채워진 줄"이면 제목으로 빼냅니다. 칸이 1개뿐인 표는 건드리지 않아요."""
    titles = []
    while len(rows) > 1 and len(rows[0]) > 1 and sum(1 for c in rows[0] if c) == 1:
        titles.append(next(c for c in rows[0] if c))
        rows = rows[1:]
    return rows, titles


def _split_rows(rows):
    """아주 긴 표를 MAX_TABLE_ROWS 줄씩 나눕니다. 나뉜 표마다 머리글 줄을 다시 붙여요."""
    if len(rows) <= MAX_TABLE_ROWS:
        return [rows]
    header, body = rows[0], rows[1:]
    size = MAX_TABLE_ROWS - (1 if REPEAT_HEADER else 0)
    parts = [body[i:i + size] for i in range(0, len(body), size)]
    return [([header] + part if REPEAT_HEADER else part) for part in parts]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python text_parser.py 파일경로.txt (또는 .csv)")
        raise SystemExit(1)
    print(json.dumps(parse_text(sys.argv[1]), ensure_ascii=False, indent=2))
