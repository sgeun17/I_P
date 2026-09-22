"""
chunk_format.py : 청크 형식 고정 (Phase 1 입력 - 청킹-B)

하는 일
  1. 청크 형식(칸 이름·순서)과 크기 설정을 한 곳에서 관리합니다.
  2. make_chunk_id()   : chunk_id 만들기  → 000001_v1_c0003
  3. finalize_chunks() : 청크 조각들에 번호·ID·증적 정보를 붙여 완성된 청크로 만들기
  4. validate_chunks() : 완성된 청크가 공통 규칙을 지키는지 검사 (문제 목록, 없으면 [])

청크 하나의 형식 (공통 규칙)
  {
    "chunk_id": "000001_v1_c0003",   증적번호 6자리 _ v버전 _ c청크순서 4자리
    "evidence_id": 1,
    "version": 1,
    "chunk_index": 3,                0부터 (INDEX_START), 문서 순서대로 빈 번호 없이
    "file_type": "pdf",              pdf, docx, xlsx, pptx, txt, csv
    "source_file": "계정대장.pdf",     원래 파일 이름 (파서 결과의 source_file)
    "chunk_type": "text",            "text" 또는 "table" (글과 표를 섞지 않음)
    "page_start": 2,                 PDF=페이지, PPTX=슬라이드, XLSX=시트 번호
    "page_end": 3,                   DOCX·TXT·CSV 는 둘 다 null
    "heading": "2. 접근통제",         가장 최근 제목, 없으면 null
    "text": "…원문 그대로…",           CHUNK_MAX_CHARS 글자 이하 (겹침 포함)
    "block_orders": [5, 6, 7],       이 청크를 만든 블록들의 order
    "source": "parser"               지금은 항상 parser (OCR 이 붙으면 "ocr")
  }

사용법
  from chunk_format import make_chunk_id, finalize_chunks, validate_chunks, CHUNK_MAX_CHARS
"""

import re

# ─────────────────────────────────────────────
# 크기 설정 (★ 한 곳에서만 관리. 청킹-A 도 여기서 import 해서 씀)
#   검색팀 임베딩 모델의 최대 입력 토큰을 확인한 뒤 조정 (WBS "청크 크기·겹침 조정")
# ─────────────────────────────────────────────
CHUNK_MAX_CHARS = 800    # 청크 text 최대 글자 수 (겹침 포함)
OVERLAP_CHARS = 100      # 글 청크끼리 겹치는 글자 수 (표 청크는 겹치지 않음)

ID_DIGITS = 4            # chunk_id 의 청크 순서 자릿수 (c0003 → 10,000개까지 정렬이 맞음)
INDEX_START = 0          # 청크 번호 시작 값 (★ 청킹-A 와 맞춤: 0부터 → 첫 청크 c0000)

# 완성된 청크의 칸 (이 순서 그대로)
CHUNK_KEYS = ["chunk_id", "evidence_id", "version", "chunk_index", "file_type", "source_file",
              "chunk_type", "page_start", "page_end", "heading", "text", "block_orders", "source"]

# 청크 조각의 칸 : table_chunks() / 청킹-A 가 만드는 것. 번호·ID·증적 정보는 finalize_chunks() 가 붙임
PART_KEYS = ["chunk_type", "page_start", "page_end", "heading", "text", "block_orders", "source"]

FILE_TYPES = {"pdf", "docx", "xlsx", "pptx", "txt", "csv"}
PAGED_TYPES = {"pdf", "xlsx", "pptx"}          # page 가 숫자인 파일
CHUNK_TYPES = {"text", "table"}
SOURCES = {"parser", "ocr"}

CHUNK_ID = re.compile(r"^(\d{6})_v(\d+)_c(\d{%d})$" % ID_DIGITS)


def make_chunk_id(evidence_id, version, index):
    """
    chunk_id 를 만듭니다.  make_chunk_id(1, 1, 3) → "000001_v1_c0003"
    앞부분(000001_v1)은 B파트 저장 파일 이름(000001_v1.pdf)과 같은 모양이에요.
    """
    for name, value in [("evidence_id", evidence_id), ("version", version)]:
        if not _is_pos_int(value):
            raise ValueError(f"{name} 는 1 이상의 정수여야 해요: {value!r}")
    if not _is_int(index) or index < INDEX_START:
        raise ValueError(f"index 는 {INDEX_START} 이상의 정수여야 해요: {index!r}")
    if index >= 10 ** ID_DIGITS:
        raise ValueError(f"청크 번호가 {10 ** ID_DIGITS - 1} 을 넘었어요 (ID_DIGITS 를 늘려야 함)")
    return f"{evidence_id:06d}_v{version}_c{index:0{ID_DIGITS}d}"


def finalize_chunks(parts, evidence_id, version, file_type, source_file):
    """
    청크 조각 목록(문서 순서대로)에 chunk_index·chunk_id·증적 정보를 붙여서 완성된 청크로 만듭니다.
    칸 순서는 CHUNK_KEYS 그대로예요.
    """
    chunks = []
    for index, part in enumerate(parts, INDEX_START):
        chunk = {
            "chunk_id": make_chunk_id(evidence_id, version, index),
            "evidence_id": evidence_id,
            "version": version,
            "chunk_index": index,
            "file_type": file_type,
            "source_file": source_file,
        }
        for key in PART_KEYS:
            chunk[key] = part.get(key)
        chunks.append(chunk)
    return chunks


def validate_chunks(chunks, max_chars=CHUNK_MAX_CHARS):
    """
    완성된 청크 목록이 공통 규칙을 지키는지 검사합니다.
    돌려주는 값: 문제 설명 목록 (문제 없으면 [])
    """
    problems = []
    if not isinstance(chunks, list):
        return ["청크 목록이 리스트가 아님"]

    seen_ids = set()
    prev_first_order = 0
    for i, c in enumerate(chunks, INDEX_START):
        where = f"청크{i}"
        if not isinstance(c, dict):
            problems.append(f"{where} 가 딕셔너리가 아님")
            continue
        if set(c) != set(CHUNK_KEYS):          # 칸 이름만 확인 (JSON 은 칸 순서가 달라도 같은 뜻)
            missing = [k for k in CHUNK_KEYS if k not in c]
            extra = [k for k in c if k not in CHUNK_KEYS]
            problems.append(f"{where} 칸이 규칙과 다름 (빠진 칸 {missing}, 없어야 할 칸 {extra})")
            continue

        # 번호·ID
        if c["chunk_index"] != i:
            problems.append(f"{where} chunk_index={c['chunk_index']} ({INDEX_START}부터 빈 번호 없이)")
        if not (_is_pos_int(c["evidence_id"]) and _is_pos_int(c["version"])):
            problems.append(f"{where} evidence_id·version 은 1 이상의 정수")
        elif _is_int(c["chunk_index"]) and c["chunk_index"] >= INDEX_START:
            expected = make_chunk_id(c["evidence_id"], c["version"], c["chunk_index"]) \
                if c["chunk_index"] < 10 ** ID_DIGITS else None
            if c["chunk_id"] != expected:
                problems.append(f"{where} chunk_id={c['chunk_id']!r} (기대값 {expected!r})")
        if c["chunk_id"] in seen_ids:
            problems.append(f"{where} chunk_id 중복 {c['chunk_id']}")
        seen_ids.add(c["chunk_id"])
        same = ("evidence_id", "version", "file_type", "source_file")
        if i > INDEX_START and any(c[k] != chunks[0].get(k) for k in same):
            problems.append(f"{where} 한 문서 안에서 evidence_id·version·file_type·source_file 이 달라짐")

        # 종류
        if c["file_type"] not in FILE_TYPES:
            problems.append(f"{where} file_type={c['file_type']!r}")
        if not (isinstance(c["source_file"], str) and c["source_file"].strip()):
            problems.append(f"{where} source_file 이 비어 있음 (원래 파일 이름)")
        if c["chunk_type"] not in CHUNK_TYPES:
            problems.append(f"{where} chunk_type={c['chunk_type']!r} (text 또는 table)")
        if c["source"] not in SOURCES:
            problems.append(f"{where} source={c['source']!r} (parser 또는 ocr. 파일 이름은 source_file 칸)")

        # page
        ps, pe = c["page_start"], c["page_end"]
        if c["file_type"] in PAGED_TYPES:
            if not (_is_pos_int(ps) and _is_pos_int(pe) and ps <= pe):
                problems.append(f"{where} {c['file_type']} 인데 page_start={ps}, page_end={pe}")
        elif c["file_type"] in FILE_TYPES and (ps is not None or pe is not None):
            problems.append(f"{where} {c['file_type']} 는 page 가 null 이어야 함 ({ps}, {pe})")

        # 제목·본문
        if c["heading"] is not None and not (isinstance(c["heading"], str) and c["heading"].strip()):
            problems.append(f"{where} heading 은 글자 또는 null")
        text = c["text"]
        if not (isinstance(text, str) and text.strip()):
            problems.append(f"{where} text 가 비어 있음")
        elif len(text) > max_chars:
            problems.append(f"{where} text {len(text)}자 (최대 {max_chars}자)")

        # 원본 블록
        orders = c["block_orders"]
        if not (isinstance(orders, list) and orders and all(_is_pos_int(o) for o in orders)):
            problems.append(f"{where} block_orders 는 1 이상 정수 목록 (비어 있으면 안 됨)")
            continue
        if orders != sorted(set(orders)):
            problems.append(f"{where} block_orders 가 오름차순이 아니거나 중복 {orders}")
        if c["chunk_type"] == "table" and len(orders) != 1:
            problems.append(f"{where} 표 청크는 표 블록 하나에서만 나와야 함 {orders}")
        if orders[0] < prev_first_order:
            problems.append(f"{where} 문서 순서가 뒤집힘 (block_orders {orders})")
        prev_first_order = orders[0]

    return problems


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_pos_int(value):
    return _is_int(value) and value >= 1
