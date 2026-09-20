import json
import re
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph


def get_heading_level(style_name):
    m = re.match(r"^(Heading|제목)\s*(\d+)$", style_name)
    if m:
        return int(m.group(2))
    return None


def extract_blocks(path):
    doc = Document(path)
    blocks = []
    order = 0

    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            p = Paragraph(child, doc)
            text = p.text.strip()
            if text == "":
                continue
            order += 1
            level = get_heading_level(p.style.name)
            blocks.append({
                "order": order,
                "block_type": "heading" if level else "paragraph",
                "level": level,
                "page": None,
                "text": text,
                "table": None,
            })

        elif child.tag.endswith("}tbl"):
            t = Table(child, doc)
            order += 1
            rows = [[cell.text.strip() for cell in row.cells] for row in t.rows]
            blocks.append({
                "order": order,
                "block_type": "table",
                "level": None,
                "page": None,
                "text": "",
                "table": {"rows": rows},
            })

    return blocks


def extract_docx(path):
    result = {
        "source_file": path,
        "file_type": "docx",
        "page_count": None,
        "blocks": [],
        "errors": [],
    }

    try:
        blocks = extract_blocks(path)
    except PackageNotFoundError:
        result["errors"].append("corrupted_file")
        return result
    except Exception:
        result["errors"].append("extract_failed")
        return result

    if not blocks:
        result["errors"].append("empty_document")
        return result

    result["blocks"] = blocks
    return result


if __name__ == "__main__":
    result = extract_docx("test1.docx")
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("result.json 저장 완료")