try:      # 패키지로 쓸 때 (from chunking.chunker import make_chunks)
    from .chunk_links import (CHUNK_MAX_CHARS, INDEX_START, OVERLAP_CHARS,
                              fill_continued_headers, make_chunk_id, table_chunks)
except ImportError:   # 이 폴더 안에서 바로 실행할 때
    from chunk_links import (CHUNK_MAX_CHARS, INDEX_START, OVERLAP_CHARS,
                             fill_continued_headers, make_chunk_id, table_chunks)

MAX_CHARS = CHUNK_MAX_CHARS      # 크기 설정은 chunk_format.py 한 곳에서 관리
OVERLAP = OVERLAP_CHARS


def split_long_text(text, max_chars, overlap):
    if len(text) <= max_chars:
        return [text]

    parts = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            cut = max(
                text.rfind(". ", start, end),
                text.rfind("\n", start, end),
                text.rfind(" ", start, end),
            )
            if cut > start + max_chars // 2:
                end = cut + 1
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def add_overlap(pieces, max_chars, overlap):
    """
    이어지는 글 청크끼리 앞 청크의 끝부분을 overlap 글자만큼 겹쳐 붙입니다.
    청크 경계에서 문장이 끊겨도 앞 내용이 조금 남아 검색이 잘 되게 하려는 것.
      - 표 청크는 줄마다 머리글이 붙어 있어서 겹치지 않음
      - 페이지(시트·슬라이드)가 바뀌는 곳은 겹치지 않음 (page 가 어긋나면 인용이 틀림)
      - 겹쳐도 max_chars 를 넘지 않게 자름
    """
    if overlap <= 0:
        return pieces
    for prev, cur in zip(pieces, pieces[1:]):
        if prev["chunk_type"] != "text" or cur["chunk_type"] != "text":
            continue
        if prev["block_orders"] == cur["block_orders"]:
            continue                               # 긴 문단 하나를 나눈 것 → split_long_text 가 이미 겹쳐 놓음
        if prev["page_end"] != cur["page_start"]:
            continue
        room = max_chars - len(cur["text"]) - 1
        if room <= 0:
            continue
        tail = prev["text"][-min(overlap, room):]
        cut = tail.find(" ")                       # 단어 중간에서 시작하지 않게
        if 0 <= cut < len(tail) - 1:
            tail = tail[cut + 1:]
        tail = tail.strip()
        if not tail:
            continue
        cur["text"] = tail + "\n" + cur["text"]
        cur["block_orders"] = sorted(set(cur["block_orders"] + prev["block_orders"][-1:]))
    return pieces


def make_chunks(parsed, evidence_id, version, max_chars=MAX_CHARS, overlap=OVERLAP):
    if parsed.get("errors"):
        return []

    pieces = []
    heading = None
    basket = []
    basket_size = 0
    last_page = None

    def text_piece(text, blocks):
        pages = [b["page"] for b in blocks if b.get("page") is not None]
        return {
            "chunk_type": "text",
            "page_start": min(pages) if pages else None,
            "page_end": max(pages) if pages else None,
            "heading": heading,
            "text": text,
            "block_orders": [b["order"] for b in blocks],
        }

    def close_basket():
        nonlocal basket, basket_size
        if not basket:
            return
        if not all(b["block_type"] == "heading" for b in basket):
            pieces.append(text_piece("\n".join(b["text"] for b in basket), basket))
        basket = []
        basket_size = 0

    blocks = parsed["blocks"]
    if parsed["file_type"] == "pdf":                # 페이지 넘김 표는 PDF 에서만 생김
        blocks = fill_continued_headers(blocks)     # 뒤 페이지 조각에 앞 페이지 머리글 붙이기
    for block in blocks:
        kind = block["block_type"]

        if parsed["file_type"] in ("pptx", "xlsx"):
            page = block.get("page")
            if last_page is not None and page != last_page:
                close_basket()
                heading = None
            last_page = page

        if kind == "table":
            close_basket()
            pieces.extend(table_chunks(block, heading, max_chars))
            continue

        if kind == "heading":
            close_basket()
            heading = block["text"]

        if len(block["text"]) > max_chars:
            close_basket()
            for part in split_long_text(block["text"], max_chars, overlap):
                pieces.append(text_piece(part, [block]))
            continue

        add = len(block["text"]) + (1 if basket else 0)
        if basket and basket_size + add > max_chars:
            close_basket()
            add = len(block["text"])
        basket.append(block)
        basket_size += add

    close_basket()
    add_overlap(pieces, max_chars, overlap)

    chunks = []
    for i, p in enumerate(pieces, start=INDEX_START):
        chunks.append({
            "chunk_id": make_chunk_id(evidence_id, version, i),
            "evidence_id": evidence_id,
            "version": version,
            "chunk_index": i,
            "file_type": parsed["file_type"],
            "source_file": parsed["source_file"],
            "source": "parser",
            **p,
        })
    return chunks
