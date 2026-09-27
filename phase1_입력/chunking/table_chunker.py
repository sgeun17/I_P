"""
table_chunker.py : 표 블록 → 표 청크 (Phase 1 입력 - 청킹-B)

하는 일
  파서가 뽑은 table 블록(text 가 "" 이고 table.rows 에 2차원 배열)을
  검색에 걸리는 글자로 바꾸고, 크기 한도에 맞게 나눠서 "표 청크 조각"으로 돌려줍니다.

청킹-A 와 연결되는 함수 (★ 모양을 바꿀 때는 먼저 말하기)
  table_chunks(block, heading, max_chars) -> list[dict]   표 블록 하나 → 표 청크 조각 목록
  fill_continued_headers(blocks) -> list[dict]            청킹 시작 전에 한 번 호출 (PDF 페이지 넘김 표)

글자로 바꾸는 규칙
  ① 보통 표 : 첫 줄 = 머리글. 나머지 줄마다  "머리글: 값 | 머리글: 값"
       계정: user01 | 승인자: 홍길동 | 승인일: 2026-09-01
  ② 2칸 "항목/내용" 표 (신청서·서약서의 성명/소속 같은 표) : 줄마다  "항목: 내용"
       성명: 홍길동
       소속: 정보보호부
  ③ 줄이 하나뿐인 표 : 칸을 " | " 로 이음
  - 빈 칸(병합 셀 "" 포함)은 건너뜀
  - 가로로 병합된 머리글(뒤 칸이 "")은 왼쪽 머리글을 같이 씀 → "시스템 접근권한: 서버 관리자"
  - 줄마다 머리글이 붙어 있어서, 표가 나뉘어도 조각 하나만 읽어도 뜻이 통함

나누는 규칙
  - 표 한 줄은 자르지 않고, max_chars 까지 줄을 모아서 한 조각
  - 한 줄이 혼자서 max_chars 를 넘을 때만 " | " 기준으로 (그래도 길면 글자 수로) 자름
  - 표 청크끼리는 겹치지 않음 (줄마다 머리글이 있어서 필요 없음)

PDF 페이지 넘김 표
  PDF 는 한 표가 두 페이지에 걸치면 페이지마다 따로 table 블록이 나오고,
  뒤 페이지 조각은 머리글 없이 데이터부터 시작해요. (['37', 'user037', ...])
  fill_continued_headers() 가 이런 조각을 찾아서 앞 표의 머리글을 붙여줘요.
  블록을 합치지는 않아서 page 는 원래 페이지 그대로예요 (인용할 때 페이지가 정확함).
"""

import copy
import re

try:      # 패키지로 쓸 때
    from .chunk_format import CHUNK_MAX_CHARS
except ImportError:   # 이 폴더 안에서 바로 실행할 때
    from chunk_format import CHUNK_MAX_CHARS

CELL_SEP = " | "
KV_MAX_LABEL = 20                               # 2칸 항목표의 "항목" 칸 최대 글자 수
HAS_DIGIT = re.compile(r"\d")
NUMBER_LIKE = re.compile(r"^[\d\s,.\-:/%()+~]+$")   # 숫자·날짜·시간·금액·비율


def table_chunks(block, heading=None, max_chars=CHUNK_MAX_CHARS):
    """
    표 블록 하나를 표 청크 조각 목록으로 바꿉니다.
    조각에는 chunk_index·chunk_id·evidence_id 등이 없어요 → finalize_chunks() 가 붙임.
    table 블록이 아니거나 내용이 없으면 [] 를 돌려줘요.
    """
    if not isinstance(block, dict) or block.get("block_type") != "table":
        return []
    rows = _clean_rows((block.get("table") or {}).get("rows"))
    if not rows:
        return []

    lines = table_lines(rows)
    parts = []
    for text in _pack(lines, max_chars):
        parts.append({
            "chunk_type": "table",
            "page_start": block.get("page"),
            "page_end": block.get("page"),
            "heading": heading or None,
            "text": text,
            "block_orders": [block.get("order")],
            "source": "parser",
        })
    return parts


def table_lines(rows):
    """표(2차원 배열)를 글자 줄 목록으로 바꿉니다. 한 줄 = 표의 한 행."""
    rows = _clean_rows(rows)
    if not rows:
        return []
    if len(rows) == 1:
        return [_join(rows[0])]
    if is_key_value_table(rows):
        return _key_value_lines(rows)
    return _header_lines(rows)


def is_key_value_table(rows):
    """
    2칸 표가 "항목: 내용" 표인지 판단합니다.
    머리글이 있는 2칸 표(계정/권한 → user01/admin)와 구분하려고, 아래 중 하나면 "머리글 표"로 봐요.
      - 왼쪽 칸 값(첫 줄 제외)의 절반 이상에 숫자가 있음 (user01, 2026-09-01, 1, 2, 3 …)
      - 오른쪽 칸: 첫 줄은 글자인데, 나머지는 전부 숫자 (건수/12, 8)
    애매하면 항목표로 봐요. 항목표로 잘못 봐도 "항목: 건수 / 계정 발급: 12" 처럼 읽을 수는 있지만,
    머리글 표로 잘못 보면 "성명: 소속 | 홍길동: 정보보호부" 처럼 뜻이 망가지기 때문이에요.
    """
    if not rows or len(rows[0]) != 2 or len(rows) < 2:
        return False
    body = rows[1:]
    left = [r[0] for r in body if r[0]]
    right = [r[1] for r in body if r[1]]
    if any(len(r[0]) > KV_MAX_LABEL for r in rows):
        return False
    if left and sum(1 for v in left if HAS_DIGIT.search(v)) * 2 >= len(left):
        return False
    if rows[0][1] and not NUMBER_LIKE.match(rows[0][1]) and right and all(NUMBER_LIKE.match(v) for v in right):
        return False
    return True


def fill_continued_headers(blocks):
    """
    PDF 등에서 페이지가 넘어가며 나뉜 표 조각에 앞 표의 머리글 줄을 붙인 "사본"을 돌려줍니다.
    원본 blocks 는 바꾸지 않아요. 청킹-A 가 청킹 시작 전에 한 번 부르면 돼요.

    이어진 표로 보는 조건 (모두 만족)
      - 바로 앞 블록(order 가 1 작음)도 table 이고, 페이지가 딱 1 크다
      - 칸 수가 같다
      - 앞 표가 "머리글 표" 다 (항목표·한 줄 표는 머리글이 없음)
      - 이 조각의 첫 줄이 이미 같은 머리글이면 → 붙이지 않음 (머리글 반복 인쇄된 표)
      - 이 조각의 첫 줄이 자기 머리글처럼 보이면 → 붙이지 않음 (다른 표)
        (첫 줄엔 숫자가 없는데 둘째 줄엔 숫자가 있음)
    """
    out = copy.deepcopy(blocks)
    for prev, cur in zip(out, out[1:]):
        if prev.get("block_type") != "table" or cur.get("block_type") != "table":
            continue
        if not (isinstance(prev.get("page"), int) and isinstance(cur.get("page"), int)):
            continue
        if cur.get("page") != prev["page"] + 1 or cur.get("order") != prev.get("order", 0) + 1:
            continue
        prev_rows = _clean_rows(prev["table"]["rows"])
        cur_rows = _clean_rows(cur["table"]["rows"])
        if len(prev_rows) < 2 or not cur_rows or len(prev_rows[0]) != len(cur_rows[0]):
            continue
        if is_key_value_table(prev_rows):
            continue
        header = prev_rows[0]
        if cur_rows[0] == header:
            continue
        if len(cur_rows) >= 2 and not _row_has_digit(cur_rows[0]) and _row_has_digit(cur_rows[1]):
            continue
        cur["table"]["rows"] = [list(header)] + [list(r) for r in cur["table"]["rows"]]
    return out


# ─────────────────────────────────────────────
# 내부 도우미 함수
# ─────────────────────────────────────────────
def _clean_rows(rows):
    """칸 글자를 정리하고(None → "", 줄바꿈·여러 공백 → 공백 하나) 빈 줄은 버립니다."""
    if not isinstance(rows, list):
        return []
    cleaned = []
    for row in rows:
        if not isinstance(row, list):
            continue
        cells = [" ".join(str(c).split()) if c is not None else "" for c in row]
        if any(cells):
            cleaned.append(cells)
    if not cleaned:
        return []
    width = max(len(r) for r in cleaned)
    return [r + [""] * (width - len(r)) for r in cleaned]


def _join(cells):
    return CELL_SEP.join(c for c in cells if c)


def _row_has_digit(row):
    return any(HAS_DIGIT.search(c) for c in row)


def _header_lines(rows):
    """첫 줄을 머리글로 보고 "머리글: 값 | 머리글: 값" 줄을 만듭니다."""
    labels, last = [], ""
    for cell in rows[0]:
        last = cell or last           # 가로 병합된 머리글("")은 왼쪽 머리글을 이어받음
        labels.append(last)

    lines = []
    for row in rows[1:]:
        if row == rows[0]:            # 표 중간에 반복된 머리글 줄은 건너뜀
            continue
        groups = []                   # [(머리글, [값, 값]), ...] 같은 머리글끼리 묶음
        for label, value in zip(labels, row):
            if not value:
                continue
            if groups and groups[-1][0] == label and label:
                groups[-1][1].append(value)
            else:
                groups.append((label, [value]))
        parts = [f"{label}: {' '.join(vals)}" if label else " ".join(vals) for label, vals in groups]
        if parts:
            lines.append(CELL_SEP.join(parts))
    if not lines:                     # 머리글 줄만 있는 표
        lines.append(_join(rows[0]))
    return lines


def _key_value_lines(rows):
    """2칸 항목표 : 줄마다 "항목: 내용". 항목 칸이 비어 있으면(세로 병합) 위 항목을 이어받음."""
    lines, last_key = [], ""
    for key, value in rows:
        key = key or last_key
        last_key = key
        if key and value:
            lines.append(f"{key}: {value}")
        elif value or key:
            lines.append(value or key)
    return lines


def _pack(lines, max_chars):
    """줄을 자르지 않고 max_chars 까지 모아서 조각을 만듭니다."""
    pieces, current = [], ""
    for line in lines:
        for part in _split_long_line(line, max_chars):
            if current and len(current) + 1 + len(part) > max_chars:
                pieces.append(current)
                current = part
            else:
                current = f"{current}\n{part}" if current else part
    if current:
        pieces.append(current)
    return pieces


def _split_long_line(line, max_chars):
    """한 줄이 혼자서 max_chars 를 넘을 때만 자릅니다. " | " 기준 → 그래도 길면 글자 수 기준."""
    if len(line) <= max_chars:
        return [line]
    out, current = [], ""
    for cell in line.split(CELL_SEP):
        while len(cell) > max_chars:                  # 칸 하나가 너무 긴 경우
            if current:
                out.append(current)
                current = ""
            out.append(cell[:max_chars])
            cell = cell[max_chars:]
        if not cell:
            continue
        if current and len(current) + len(CELL_SEP) + len(cell) > max_chars:
            out.append(current)
            current = cell
        else:
            current = f"{current}{CELL_SEP}{cell}" if current else cell
    if current:
        out.append(current)
    return out
