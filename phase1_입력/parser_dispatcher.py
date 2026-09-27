"""
parser_dispatcher.py : 파일 형식을 보고 알맞은 파서를 골라 실행합니다.

    from parser_dispatcher import parse_file
    parsed = parse_file("data/evidence/E0001_v1.pdf", "pdf")

파서마다 파일·함수 이름이 달라서 여기 한 곳에만 적어둡니다.
새 형식이 생기면 아래 PARSERS 에 한 줄만 추가하면 됩니다.

돌려주는 값은 형식과 상관없이 항상 같은 모양이에요.
    {"source_file": ..., "file_type": ..., "page_count": ..., "blocks": [...], "errors": [...]}
"""

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 형식 → (파서가 들어 있는 폴더, 파이썬 파일 이름, 함수 이름들)
#   폴더가 "" 이면 이 파일(parser_dispatcher.py)과 같은 폴더를 뜻합니다.
PARSERS = {
    "pdf":  ("pdf_test",      "pdf_parser",   ["parse_pdf"]),
    "docx": ("docx_test",     "docx_parser",  ["parse_docx"]),
    "xlsx": ("xlsx_test",     "xlsx_parser",  ["parse_xlsx"]),
    "pptx": ("pptx_test",     "pptx_parser",  ["parse_pptx"]),
    "txt":  ("txt_csv_test",  "text_parser",  ["parse_text"]),
    "csv":  ("txt_csv_test",  "text_parser",  ["parse_text"]),
    "png":  ("",              "ocr_parser",   ["parse_image"]),
    "jpg":  ("",              "ocr_parser",   ["parse_image"]),
}


class UnsupportedFileType(Exception):
    """우리가 다루지 않는 파일 형식"""


class ParserNotInstalled(Exception):
    """파서 파일이나 파서가 쓰는 도구가 없음 (파일 문제가 아니라 설치 문제)"""


def supported_types():
    return sorted(PARSERS)


def get_parser(file_type):
    """형식에 맞는 파서 함수를 돌려줍니다."""
    file_type = (file_type or "").lower().lstrip(".")
    if file_type == "jpeg":
        file_type = "jpg"
    if file_type not in PARSERS:
        raise UnsupportedFileType(
            f"다루지 않는 형식이에요: {file_type!r} (가능: {', '.join(supported_types())})")

    folder, module_name, func_names = PARSERS[file_type]
    parser_dir = HERE / folder if folder else HERE
    where = f"{folder}/{module_name}.py" if folder else f"{module_name}.py"
    if str(parser_dir) not in sys.path:
        sys.path.insert(0, str(parser_dir))
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name == module_name:
            raise ParserNotInstalled(f"{where} 를 찾지 못했어요.") from e
        raise ParserNotInstalled(
            f"{module_name}.py 가 쓰는 도구가 설치돼 있지 않아요: {e.name}\n"
            f"  pip install {e.name}") from e

    for name in func_names:
        func = getattr(module, name, None)
        if callable(func):
            return func
    raise ParserNotInstalled(
        f"{where} 안에 {' 또는 '.join(func_names)} 함수가 없어요.")


def parse_file(file_path, file_type=None):
    """
    파일 하나를 읽어서 팀 합의 포맷(blocks)으로 돌려줍니다.

      file_path : 저장된 파일 경로
      file_type : pdf·docx·xlsx·pptx·txt·csv·png·jpg. 생략하면 확장자로 판단합니다.
    """
    path = Path(file_path)
    if file_type is None:
        file_type = path.suffix.lower().lstrip(".")
    return get_parser(file_type)(str(path))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    result = parse_file(sys.argv[1])
    kinds = {}
    for b in result["blocks"]:
        kinds[b["block_type"]] = kinds.get(b["block_type"], 0) + 1
    print(f"{result['source_file']}  ({result['file_type']})")
    print(f"  블록 {len(result['blocks'])}개 {kinds}  page_count={result['page_count']}"
          f"  errors={result['errors']}")
