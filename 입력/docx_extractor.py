"""
docx_extractor.py : DOCX 파일에서 문단과 표를 꺼내는 파일 (전처리 - DOCX 담당)

사용법 (전처리 담당이 get_file_path() 로 받은 경로를 그대로 넘깁니다)
    from docx_extractor import extract_docx

    result = extract_docx(path, source_file="정책.docx")
    if result["errors"]:
        ...  # 손상/빈 문서. update_status(..., "FAILED", error_code=result["errors"][0]["code"], ...)

extract_docx 는 절대 예외를 내지 않습니다. 문제는 result["errors"] 에 담깁니다.

돌려주는 값 (팀 JSON 규격)
    {
      "source_file": "policy.docx",
      "file_type": "docx",
      "page_count": null,
      "blocks": [
        {"order": 1, "block_type": "heading",   "level": 1,    "page": null, "text": "제1장 총칙", "table": null},
        {"order": 2, "block_type": "paragraph", "level": null, "page": null, "text": "제1조 ...",   "table": null},
        {"order": 3, "block_type": "table",     "level": null, "page": null, "text": "",
         "table": {"rows": [["구분", "내용"], ["접근통제", "..."]]}}
      ],
      "errors": []
    }

규칙
    - block_type 은 paragraph, table, heading 세 가지뿐입니다.
    - order 는 문서에 나온 순서대로 1부터 빠짐없이 붙습니다. (블록 식별자 역할, block_id 없음)
    - page, page_count 는 null 입니다. (DOCX 는 쪽 나눔이 열 때마다 달라짐)
    - level 은 heading 일 때만 1, 2, 3 중 하나, 나머지는 null 입니다.
      "Heading 1", "제목 1", "Title" 스타일이 heading 이고, 4단계 이하 제목은 3으로 묶습니다.
    - table 은 block_type 이 table 일 때만 값이 있고, 나머지는 null 입니다.
    - text 는 paragraph/heading 일 때만 채우고, table 이면 빈 문자열입니다.
    - table.rows 는 2차원 배열입니다. 병합으로 가려진 칸은 빈 문자열이라 모든 줄의 칸 수가 같습니다.
      표 칸 안에 표가 또 있으면 그 내용은 칸 글자 뒤에 이어 붙입니다.
    - 빈 텍스트 블록(공백뿐인 문단, 글자가 하나도 없는 표)은 버립니다. (팀 확인 필요)
    - errors 는 항상 리스트입니다. 없으면 [] (null 아님). 문제가 있으면 blocks 는 [] 이고
      errors 에 {"code", "message"} 가 담깁니다. (EvidenceError 와 같은 code / message 모양)
        FILE_NOT_FOUND    : 파일이 없음
        EMPTY_FILE        : 0바이트 파일
        FILE_PARSE_FAILED : 손상됨, DOCX 가 아님, 암호가 걸림 등 열 수 없음
        EMPTY_DOCUMENT    : 열리지만 글자가 하나도 없음

머리말/꼬리말, 텍스트 상자, 각주, 그림 속 글자는 아직 다루지 않습니다.
"""

import os
import re
import zipfile

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.opc.exceptions import PackageNotFoundError

MAX_HEADING_LEVEL = 3


class DocxExtractError(Exception):
    """안에서만 쓰는 예외. extract_docx 가 잡아서 errors 로 바꿉니다."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# "Heading 1", "heading 2", "제목 1" 처럼 끝에 번호가 붙은 제목 스타일
_HEADING_RE = re.compile(r"^(?:heading|제목)\s*(\d+)$", re.IGNORECASE)


def _heading_level(style_name):
    """제목 스타일이면 1~3, 아니면 None. 문서 제목(Title, 제목)은 1, 4단계 이하는 3으로 봅니다."""
    name = (style_name or "").strip()
    if name.lower() == "title" or name == "제목":
        return 1
    m = _HEADING_RE.match(name)
    if not m:
        return None
    return min(max(int(m.group(1)), 1), MAX_HEADING_LEVEL)


def _open_document(path):
    """파일을 열어 Document 를 돌려줍니다. 열 수 없으면 이유에 맞는 DocxExtractError"""
    if not os.path.isfile(path):
        raise DocxExtractError("FILE_NOT_FOUND", f"파일을 찾을 수 없습니다: {os.path.basename(str(path))}")
    if os.path.getsize(path) == 0:
        raise DocxExtractError("EMPTY_FILE", "빈 파일입니다.")

    try:
        return Document(str(path))
    except PackageNotFoundError as e:
        # zip 이 아닌 파일 (암호 걸린 DOCX, 이름만 .docx 인 다른 파일 등)
        raise DocxExtractError(
            "FILE_PARSE_FAILED", "DOCX 형식이 아니거나 암호가 걸려 있어 열 수 없습니다."
        ) from e
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as e:
        # zip 은 열리는데 안이 깨졌거나 (word/document.xml 없음, XML 깨짐 등) 중간에 잘린 파일
        raise DocxExtractError("FILE_PARSE_FAILED", "손상된 DOCX 파일입니다.") from e
    except Exception as e:  # lxml 의 XML 문법 오류 등 예상 못 한 손상
        raise DocxExtractError("FILE_PARSE_FAILED", f"DOCX 파일을 읽지 못했습니다: {type(e).__name__}") from e


def _table_rows(table):
    """
    표를 칸 글자의 2차원 목록으로 바꿉니다.
    가로로 병합된 칸은 글자를 첫 칸에 두고 가려진 칸은 "" 로 채웁니다. (세로 병합의 아래 칸도 "")
    """
    rows = []
    for tr in table._tbl.tr_lst:
        row = []
        for tc in tr.tc_lst:
            row.append(_cell_text(_Cell(tc, table)))
            row.extend([""] * (tc.grid_span - 1))
        rows.append(row)
    return rows


def _cell_text(cell):
    """칸 안의 글자. 문단은 줄바꿈으로 잇고, 칸 안의 표는 한 줄씩 이어 붙입니다."""
    parts = []
    for child in cell._tc.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, cell).text.strip()
            if text:
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            for nested_row in _table_rows(Table(child, cell)):
                line = " | ".join(c for c in nested_row if c)
                if line:
                    parts.append(line)
    return "\n".join(parts)


def _extract_blocks(path):
    """문서에서 blocks 를 꺼냅니다. 문제가 있으면 DocxExtractError"""
    doc = _open_document(path)

    blocks = []
    try:
        for child in doc.element.body.iterchildren():
            if child.tag == qn("w:p"):
                p = Paragraph(child, doc)
                text = p.text.strip()
                if not text:
                    continue
                level = _heading_level(p.style.name if p.style is not None else None)
                blocks.append(
                    {
                        "order": len(blocks) + 1,
                        "block_type": "heading" if level else "paragraph",
                        "level": level,
                        "page": None,
                        "text": text,
                        "table": None,
                    }
                )
            elif child.tag == qn("w:tbl"):
                rows = _table_rows(Table(child, doc))
                if not any(cell for row in rows for cell in row):
                    continue  # 글자가 하나도 없는 표는 버림
                blocks.append(
                    {
                        "order": len(blocks) + 1,
                        "block_type": "table",
                        "level": None,
                        "page": None,
                        "text": "",
                        "table": {"rows": rows},
                    }
                )
    except Exception as e:  # 열리긴 했지만 본문 구조가 깨진 경우
        raise DocxExtractError("FILE_PARSE_FAILED", f"DOCX 본문을 읽지 못했습니다: {type(e).__name__}") from e

    if not blocks:
        raise DocxExtractError("EMPTY_DOCUMENT", "문서에 추출할 글자가 없습니다.")
    return blocks


def extract_docx(path, source_file=None):
    """
    DOCX 하나를 팀 JSON 규격(위 설명)으로 바꿉니다. 절대 예외를 내지 않습니다.
    source_file : 사용자가 올린 원래 파일명. 안 주면 path 의 파일명을 씁니다.
    """
    try:
        blocks, errors = _extract_blocks(path), []
    except DocxExtractError as e:
        blocks, errors = [], [{"code": e.code, "message": e.message}]
    except Exception as e:  # 예상 못 한 문제도 반환값으로 알린다
        blocks, errors = [], [{"code": "FILE_PARSE_FAILED", "message": f"DOCX 처리 중 오류: {type(e).__name__}"}]

    return {
        "source_file": source_file or os.path.basename(str(path)),
        "file_type": "docx",
        "page_count": None,
        "blocks": blocks,
        "errors": errors,
    }
