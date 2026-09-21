from chunk_links import make_chunk_id, table_chunks

MAX_CHARS = 800
OVERLAP = 100
INDEX_START = 0


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
        pieces.append(text_piece("\n".join(b["text"] for b in basket), basket))
        basket = []
        basket_size = 0

    for block in parsed["blocks"]:
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

    chunks = []
    for i, p in enumerate(pieces, start=INDEX_START):
        chunks.append({
            "chunk_id": make_chunk_id(evidence_id, version, i),
            "evidence_id": evidence_id,
            "version": version,
            "chunk_index": i,
            "file_type": parsed["file_type"],
            "source": parsed["source_file"],
            **p,
        })
    return chunks