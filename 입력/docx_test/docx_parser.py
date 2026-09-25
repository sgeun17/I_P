import json
import re
from pathlib import Path
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph


def get_heading_level(style_name):
    m = re.match(r"^(Heading|제목)\s*(\d+)$", style_name)
    if m:
        return int(m.group(2))
    return None


def extract_table_rows(table):
    """
    표를 2차원 배열로 만듭니다.
      - 병합 셀은 왼쪽 위 칸에만 값을 넣고 나머지는 "" (팀 규칙 6)
        python-docx 는 병합된 칸을 자리마다 똑같이 돌려줘서, 이미 나온 칸인지 확인해야 합니다.
      - 완전히 빈 줄은 버리고, 표 전체가 비어 있으면 [] (팀 규칙 8)
    """
    rows, seen = [], []                   # seen : 이미 값을 넣은 칸(XML 요소) 목록
    for row in table.rows:
        values = []
        for cell in row.cells:
            tc = cell._tc                     # 병합된 칸은 자리마다 같은 XML 요소로 나옴
            merged = any(tc is done for done in seen)
            values.append("" if merged else " ".join(cell.text.split()))
            seen.append(tc)
        if any(values):
            rows.append(values)
    return rows


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
            level = get_heading_level(p.style.name if p.style is not None else "")
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
            rows = extract_table_rows(t)
            if not rows:                      # 모든 칸이 빈 표는 버림 (팀 규칙 8)
                continue
            order += 1
            blocks.append({
                "order": order,
                "block_type": "table",
                "level": None,
                "page": None,
                "text": "",
                "table": {"rows": rows},
            })

    return blocks


def parse_docx(path):
    result = {
        "source_file": Path(path).name,
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
        result["errors"].append("corrupted_file")
        return result

    if not blocks:
        result["errors"].append("empty_document")
        return result

    result["blocks"] = blocks
    return result


# 다른 파서와 이름을 맞췄습니다 (parse_pdf · parse_xlsx · parse_pptx · parse_text · parse_docx).
# 옛 이름으로 부르던 코드가 남아 있어도 당분간 돌아가도록 같은 함수를 가리켜 둡니다.
# 팀 전체가 parse_docx 로 바꾼 뒤에 이 줄은 지우면 됩니다.
extract_docx = parse_docx


if __name__ == "__main__":
    result = parse_docx("test1.docx")
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("result.json 저장 완료")