"""
pdf_parser.py : PDF 텍스트/페이지 추출

하는 일
  PDF 파일을 위에서 아래로 읽어서 "제목(heading)", "문단(paragraph)", "표(table)"를
  순서대로 뽑고, 각각 몇 페이지에 있었는지 기록한 결과(딕셔너리)를 돌려줍니다.

팀 합의 규칙 (v2)
  1. block_type : paragraph, table, heading 세 가지만
  2. order      : 문서 내 등장 순서, 1부터 연속 (블록 식별자 역할도 겸함, block_id 없음)
  3. page       : PDF는 실제 페이지 번호
  4. level      : heading 일 때만 1, 2, 3 중 하나, 나머지는 null
  5. table      : block_type 이 table 일 때만 {"rows": [...]}, 아니면 null
  6. table.rows : 2차원 배열, 병합 셀은 빈 문자열 ""
  7. text       : paragraph/heading 일 때만 채우고, table 이면 ""
  8. 빈 텍스트 블록은 버림 (글자 없는 문단, 모든 칸이 빈 표)
  9. 손상/빈 문서 : 예외를 던지지 않고 blocks: [] + errors 에 사유 코드
 10. errors     : 항상 리스트, 없으면 []

PDF에서 제목(heading)을 찾는 방법
  PDF 파일 안에는 "이 줄이 제목이다"라는 정보가 없어요.
  그래서 "본문보다 글자가 확실히 큰 짧은 줄"을 제목으로 봅니다.
    - 본문 글자 크기 = 문서에서 가장 많이 쓰인 글자 크기
    - 본문보다 15% 이상 크고, 100자 이하, 2줄 이하면 제목
    - 제목 글자 크기가 가장 큰 것부터 level 1, 2, 3 (4단계 이상은 3)

errors 코드 (팀 합의: 두 가지만 사용)
  corrupted_file   파일을 열 수 없음
                   (깨진 파일, PDF가 아닌 파일, 잘린 파일, 0바이트, 비밀번호 걸린 파일, 없는 파일)
  empty_document   열리긴 하는데 뽑을 글자·표가 하나도 없음
                   (빈 문서, 글자 없이 그림만 있는 스캔본)
  → 둘 다 blocks 는 [] 이고, errors 에는 코드가 딱 하나 들어갑니다.
  → 일부 페이지만 비었거나 일부 페이지만 읽다 실패하면, 그 페이지는 건너뛰고
    나머지 페이지만 뽑습니다. 이때 errors 는 [] 예요.

사용법
  from pdf_parser import parse_pdf
  result = parse_pdf("C:/team/database/data/evidence/000001_v1.pdf")

  명령창에서 결과 보기:  python pdf_parser.py 파일경로.pdf
"""

import json
import re
import os
import sys
from collections import Counter
from pathlib import Path

import pdfplumber
from pdfplumber.utils import extract_text

PARAGRAPH_GAP_RATIO = 0.8   # 줄 간격이 "글자 높이 × 이 값"보다 크면 새 문단
HEADING_SIZE_RATIO = 1.15   # 본문보다 이 배수 이상 크면 제목 후보
HEADING_MAX_CHARS = 100     # 제목은 이 글자 수 이하
HEADING_MAX_LINES = 2       # 제목은 이 줄 수 이하
MAX_LEVEL = 3
CELL_MERGE_TOL = 6          # 표 경계선이 이 거리(pt) 이내면 같은 선으로 봄
SPLIT_TABLE_RATIO = 0.5     # 위아래 줄의 칸 경계가 이 비율보다 적게 겹치면 다른 표로 자름
WRAP_GAP_RATIO = 1.3        # 윗줄이 오른쪽 끝까지 찼으면 줄 간격이 "글자 높이 × 이 값"까지는 같은 문단
# 이런 글자로 시작하는 줄은 항상 새 문단 (목록 항목): ● • ○ ■ □ ▶ ※ - / ㅇ o · (뒤에 띄어쓰기) / 1. 2) / 가. 나) / (1) / 제3조
LIST_START = re.compile(r"^([●•○◦■□▪▶※\-–]|[ㅇo·]\s|\d{1,2}[.)]\s|[가-하][.)]\s|\(\d{1,2}\)|제\s*\d+\s*조)")
EDGE_ZONE_RATIO = 0.12      # 페이지 위·아래 이 비율 안에 있으면 머리글·바닥글 자리로 봄
MIN_REPEAT_PAGES = 3        # 최소 이 페이지 수 이상 반복돼야 머리글·바닥글로 판단
WRAP_SLACK_CHARS = 6        # 오른쪽 끝에서 글자 몇 개 이내로 끝나면 "끝까지 찬 줄"로 봄


def parse_pdf(path):
    """PDF 한 개를 읽어서 팀 합의 포맷의 딕셔너리로 돌려줍니다. 예외를 밖으로 던지지 않아요."""
    path = Path(path)
    result = {
        "source_file": path.name,
        "file_type": "pdf",
        "page_count": None,
        "blocks": [],
        "errors": [],
    }

    # ① 파일이 없거나 0바이트면 열 수 없음
    if not path.exists() or os.path.getsize(path) == 0:
        result["errors"].append("corrupted_file")
        return result

    # ② 열기 (깨진 파일, 잘린 파일, 비밀번호 걸린 파일은 여기서 실패)
    try:
        pdf = pdfplumber.open(str(path))
        page_count = len(pdf.pages)
    except Exception:
        result["errors"].append("corrupted_file")
        return result

    # ③ 1차: 페이지마다 표·글자 덩어리 뽑기 (아직 제목/문단 구분 전)
    raw = []                  # (페이지, 종류, 내용, 글자크기, 줄 수, 세로 위치, 페이지 높이)
    size_counter = Counter()  # 문서 전체 글자 크기 통계 → 본문 크기 찾기
    failed_pages = 0
    with pdf:
        result["page_count"] = page_count
        for page_no, page in enumerate(pdf.pages, start=1):
            try:
                items = _extract_page(page, size_counter)
            except Exception:
                failed_pages += 1      # 이 페이지만 건너뜀
                continue
            raw += [(page_no, *item) for item in items]   # 빈 페이지면 아무것도 안 붙음

    if not raw:
        # 한 글자도 못 뽑음 → 페이지를 전부 못 읽었으면 손상, 읽었는데 비었으면 빈 문서
        broken = page_count > 0 and failed_pages == page_count
        result["errors"].append("corrupted_file" if broken else "empty_document")
        return result

    # ③-2 매 페이지 반복되는 머리글·바닥글 버리기
    raw = _drop_headers_footers(raw, page_count)

    # ④ 2차: 제목 판단 + 최종 블록 만들기
    body_size = size_counter.most_common(1)[0][0] if size_counter else 0
    heading_sizes = sorted(
        {size for _, kind, text, size, n_lines, *_ in raw
         if kind == "text" and _looks_like_heading(text, size, n_lines, body_size)},
        reverse=True,
    )

    blocks = []
    for page_no, kind, payload, size, n_lines, top, page_h in raw:
        if kind == "table":
            block_type, level, text, table = "table", None, "", {"rows": payload}
        elif size in heading_sizes and _looks_like_heading(payload, size, n_lines, body_size):
            block_type, level, text, table = "heading", min(heading_sizes.index(size) + 1, MAX_LEVEL), payload, None
        else:
            block_type, level, text, table = "paragraph", None, payload, None
        blocks.append({
            "order": len(blocks) + 1,
            "block_type": block_type,
            "level": level,
            "page": page_no,
            "text": text,
            "table": table,
        })

    result["blocks"] = blocks
    return result


# ─────────────────────────────────────────────
# 내부 도우미 함수
# ─────────────────────────────────────────────
def _looks_like_heading(text, size, n_lines, body_size):
    return (body_size > 0
            and size >= body_size * HEADING_SIZE_RATIO
            and len(text) <= HEADING_MAX_CHARS
            and n_lines <= HEADING_MAX_LINES)


def _extract_page(page, size_counter):
    """
    한 페이지에서 표와 글자 덩어리를 위에서 아래 순서대로 돌려줍니다.
      표   : ("table", rows, None, None, 세로 위치, 페이지 높이)
      글자 : ("text",  text, 글자크기, 줄 수, 세로 위치, 페이지 높이)
    표 안의 글자는 글자 덩어리로 한 번 더 나오지 않게 뺍니다.
    """
    items = []  # (세로 위치, 종류, 내용, 크기, 줄 수)

    # 칸 배경색 상자(테두리 없이 색만 칠한 넓은 사각형)는 표 선으로 보지 않음
    lines_only = page.filter(lambda o: not _is_background_box(o))
    tables = lines_only.find_tables()
    table_boxes = [t.bbox for t in tables]
    for t in tables:
        for table_top, rows in _extract_table(page, t):     # 모든 칸이 빈 표는 여기서 이미 빠짐 (규칙 8)
            items.append((table_top, "table", rows, None, None))

    def outside_tables(obj):
        if obj.get("object_type") != "char":
            return True
        cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
        return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in table_boxes)

    text_page = page.filter(outside_tables) if table_boxes else page
    lines = []
    for ln in text_page.extract_text_lines(return_chars=True):
        chars = [c for c in ln["chars"] if c["text"].strip()]
        if not ln["text"].strip() or not chars:     # 빈 줄 버림 (규칙 8)
            continue
        size = round(sum(c["size"] for c in chars) / len(chars) * 2) / 2   # 0.5pt 단위로 반올림
        size_counter[size] += len(chars)
        lines.append({"top": ln["top"], "bottom": ln["bottom"], "x1": max(c["x1"] for c in chars),
                      "text": ln["text"].strip(), "size": size})

    for top, text, size, n_lines in _group_paragraphs(lines):
        items.append((top, "text", text, size, n_lines))

    items.sort(key=lambda x: x[0])
    return [(*item[1:], item[0], page.height) for item in items]   # 뒤에 세로 위치·페이지 높이 추가


def _group_paragraphs(lines):
    """
    줄들을 문단으로 묶습니다.
      - 글자 크기가 바뀌면 새 문단
      - 줄 간격이 좁으면(글자 높이 × PARAGRAPH_GAP_RATIO 이하) 같은 문단
      - 줄 간격이 넓어도, 윗줄이 오른쪽 끝까지 차 있으면(= 자동 줄바꿈) 같은 문단
        (줄 간격을 넓게 쓴 문서에서 한 문장이 두 문단으로 쪼개지는 것 방지)
    """
    right_edge = max((ln["x1"] for ln in lines), default=0)   # 이 페이지 글자의 오른쪽 끝

    groups, current, prev = [], [], None
    for ln in sorted(lines, key=lambda l: l["top"]):
        if prev is not None:
            height = max(prev["bottom"] - prev["top"], 1)
            gap = ln["top"] - prev["bottom"]
            prev_full = prev["x1"] >= right_edge - prev["size"] * WRAP_SLACK_CHARS
            same_para = (abs(ln["size"] - prev["size"]) <= 0.5
                         and (gap <= height * PARAGRAPH_GAP_RATIO
                              or (prev_full and gap <= height * WRAP_GAP_RATIO
                                  and not LIST_START.match(ln["text"]))))
            if not same_para:
                groups.append(current)
                current = []
        current.append(ln)
        prev = ln
    if current:
        groups.append(current)

    out = []
    for g in groups:
        text = _join_lines(l["text"] for l in g)
        if text:
            size = Counter(l["size"] for l in g).most_common(1)[0][0]
            out.append((g[0]["top"], text, size, len(g)))
    return out


def _is_background_box(obj):
    """테두리 없이 색만 칠한 사각형(가로·세로 모두 2pt 초과) = 칸 배경색 → 표 선이 아님"""
    return (obj.get("object_type") == "rect" and obj.get("fill") and not obj.get("stroke")
            and obj["width"] > 2 and obj["height"] > 2)


def _cluster(values, tol):
    """가까운 좌표(tol 이하 차이)를 하나로 묶어서 대표값 목록을 돌려줍니다."""
    groups = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def _index(bounds, v):
    """v 에 가장 가까운 경계선 번호"""
    return min(range(len(bounds)), key=lambda i: abs(bounds[i] - v))


def _extract_table(page, t):
    """
    표를 2차원 배열로 뽑습니다. 돌려주는 값: [(표 윗변 위치, rows), ...]  (보통 1개)

    ① 워드/한글에서 만든 PDF는 칸 배경색·안쪽 여백을 작은 상자로 따로 그려서
       한 칸이 여러 개의 가는 칸으로 쪼개져 보일 때가 많아요.
       그래서 CELL_MERGE_TOL 이내로 붙어 있는 경계선은 하나로 합쳐서 "진짜 칸"을 다시 만들고,
       글자는 글자 중심이 들어간 원래 칸 → 그 칸의 왼쪽 위 진짜 칸에 넣습니다.
       병합 칸의 나머지 자리는 "" 이 됩니다 (규칙 6).
    ② 표 두 개가 딱 붙어 있으면(예: 흐름도 줄 바로 밑에 절차 표) 하나로 잡혀요.
       위아래 줄의 세로 칸 경계가 절반 이상 다르면 "다른 표"로 보고 거기서 자릅니다.
       (병합 칸 때문에 경계가 한두 개 다른 건 같은 표로 봐요)
    """
    cells = t.cells
    xs = _cluster([c[0] for c in cells] + [c[2] for c in cells], CELL_MERGE_TOL)
    ys = _cluster([c[1] for c in cells] + [c[3] for c in cells], CELL_MERGE_TOL)
    n_rows, n_cols = max(len(ys) - 1, 1), max(len(xs) - 1, 1)
    grid = [[[] for _ in range(n_cols)] for _ in range(n_rows)]
    borders = [set() for _ in range(n_rows)]    # 줄마다 쓰인 세로 경계선 번호

    x0, top, x1, bottom = t.bbox
    chars = [ch for ch in page.chars
             if x0 <= (ch["x0"] + ch["x1"]) / 2 <= x1 and top <= (ch["top"] + ch["bottom"]) / 2 <= bottom]

    for c in cells:
        r0, r1 = _index(ys, c[1]), _index(ys, c[3])
        k0, k1 = _index(xs, c[0]), _index(xs, c[2])
        if k0 != k1:
            for r in range(r0, min(r1, n_rows)):
                borders[r].update((k0, k1))

    # 작은 칸부터 → 글자가 가장 꼭 맞는 원래 칸에 들어가도록
    used = set()
    for c in sorted(cells, key=lambda c: (c[2] - c[0]) * (c[3] - c[1])):
        inside = [ch for ch in chars if id(ch) not in used
                  and c[0] <= (ch["x0"] + ch["x1"]) / 2 <= c[2]
                  and c[1] <= (ch["top"] + ch["bottom"]) / 2 <= c[3]]
        if not inside:
            continue
        used.update(id(ch) for ch in inside)
        r = min(_index(ys, c[1]), n_rows - 1)
        k = min(_index(xs, c[0]), n_cols - 1)
        grid[r][k].append((c[1], c[0], extract_text(inside)))

    # 줄 정리 + 표 자르기
    parts, current, prev_borders = [], [], None
    for r in range(n_rows):
        row = [_clean_cell(" ".join(txt for _, _, txt in sorted(p))) for p in grid[r]]
        if not any(row):                                            # 완전히 빈 줄 버림
            continue
        b = borders[r]
        if current and prev_borders and b and len(b & prev_borders) / len(b | prev_borders) < SPLIT_TABLE_RATIO:
            parts.append(current)
            current = []
        current.append((ys[r], row))
        prev_borders = b or prev_borders
    if current:
        parts.append(current)

    out = []
    for part in parts:
        rows = [row for _, row in part]
        keep = [k for k in range(n_cols) if any(r[k] for r in rows)]   # 완전히 빈 칸(열) 버림
        out.append((part[0][0], [[r[k] for k in keep] for r in rows]))
    return out


def _drop_headers_footers(raw, page_count):
    """
    매 페이지 똑같이 반복되는 머리글·바닥글(회사명, 문서명, 페이지 번호)을 정리합니다.
    **맨 처음 나온 것 하나는 남기고, 그 뒤 반복되는 것만 버립니다.**
    (머리글의 "| 대외비" 같은 보안등급 표시가 증적이 될 수 있어서, 문서에 한 번은 남겨 둬요)
      조건 ① 페이지 맨 위 또는 맨 아래 EDGE_ZONE_RATIO(12%) 안에 있는 글자 덩어리
           ② 같은 글이 MIN_REPEAT_PAGES(3쪽) 이상 + 전체 페이지의 절반 이상에서 반복
    "한국공직연금관리공단 - 3 -" 처럼 쪽 번호만 달라지는 것도 잡으려고, 숫자는 # 로 바꿔서 비교해요.
    표는 절대 버리지 않고, 조건에 안 맞으면 아무것도 바꾸지 않아요.
    """
    edge_pages = {}       # 숫자를 지운 글 → 그 글이 가장자리에 나온 페이지 번호들
    for page_no, kind, payload, size, n_lines, top, page_h in raw:
        if kind != "text" or not page_h:
            continue
        if top <= page_h * EDGE_ZONE_RATIO or top >= page_h * (1 - EDGE_ZONE_RATIO):
            edge_pages.setdefault(re.sub(r"\d+", "#", payload), set()).add(page_no)

    need = max(MIN_REPEAT_PAGES, page_count / 2)
    repeated = {key for key, pages in edge_pages.items() if len(pages) >= need}
    if not repeated:
        return raw

    out, kept = [], set()
    for item in raw:
        page_no, kind, payload, size, n_lines, top, page_h = item
        if (kind == "text" and page_h
                and (top <= page_h * EDGE_ZONE_RATIO or top >= page_h * (1 - EDGE_ZONE_RATIO))):
            key = re.sub(r"\d+", "#", payload)
            if key in repeated:
                if key in kept:
                    continue              # 두 번째부터는 버림
                kept.add(key)             # 처음 나온 것은 남김
        out.append(item)
    return out


def _clean_cell(cell):
    """표 칸 정리 : None(병합·빈 칸) → "", 칸 안 줄바꿈 → 공백 (주소(URL) 중간 줄바꿈은 붙임)"""
    if cell is None:
        return ""
    return " ".join(_join_lines(str(cell).split("\n")).split())


URL_CONTINUE = re.compile(r"^[!-~]+")   # 한글 없이 영문·숫자·기호로 시작


def _join_lines(lines):
    """
    여러 줄을 한 줄로 합칩니다. 보통은 사이에 띄어쓰기를 넣지만,
    윗줄 끝이 주소(https://...)의 중간에서 끊겼으면 띄어쓰기 없이 그대로 이어 붙입니다.
    (긴 주소가 칸 너비 때문에 줄바꿈되면 주소 중간에 공백이 생겨 링크가 망가지는 것 방지)
    """
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
            continue
        last = out.split()[-1]
        first = ln.split()[0]
        if "://" in last and URL_CONTINUE.fullmatch(first) and "://" not in first:
            out += ln
        else:
            out += " " + ln
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python pdf_parser.py 파일경로.pdf")
        raise SystemExit(1)
    print(json.dumps(parse_pdf(sys.argv[1]), ensure_ascii=False, indent=2))
