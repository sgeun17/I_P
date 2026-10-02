"""
parser_dispatcher.py : 파일 형식을 보고 알맞은 파서를 골라 실행합니다.

    from parser_dispatcher import parse_file
    parsed = parse_file("data/evidence/E0001_v1.pdf", "pdf")

파서마다 파일·함수 이름이 달라서 여기 한 곳에만 적어둡니다.
새 형식이 생기면 아래 PARSERS 에 한 줄만 추가하면 됩니다.

돌려주는 값은 형식과 상관없이 항상 같은 모양이에요.
    {"source_file": ..., "file_type": ..., "page_count": ..., "blocks": [...], "errors": [...]}

[수정 2026-10-02]
  - png·jpg 를 ocr_parser 로 연결 (GitHub 최신본과 동일. 이 PC 는 easyocr_test 로 돼 있었음)
  - 글자가 하나도 안 나온 PDF(스캔본)는 페이지를 이미지로 바꿔 OCR 합니다. (1회차 시험 2건 실패 원인)
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
    "png":  ("",              "ocr_parser",   ["parse_image"]),   # [수정] easyocr_test → ocr_parser
    "jpg":  ("",              "ocr_parser",   ["parse_image"]),   # [수정] easyocr_test → ocr_parser
}

OCR_PDF_DPI = 300        # [추가] 스캔 PDF 를 이미지로 바꿀 해상도 (A4 가로 약 2480px)
OCR_PDF_MAX_PAGES = 30   # [추가] 페이지당 10~30초라 상한을 둠


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
    result = get_parser(file_type)(str(path))                               # [수정] 바로 return 하지 않음
    if file_type == "pdf" and result.get("errors") == ["empty_document"]:   # [추가] 글자 0개인 PDF만
        result = _ocr_scanned_pdf(path, result)                             # [추가] OCR 로 다시 시도
    return result


def _ocr_scanned_pdf(path, result):
    """
    [추가] 스캔 PDF → 페이지를 이미지로 바꿔 OCR.
    pdfplumber 에 들어 있는 렌더러를 쓰므로 새로 설치할 것은 없습니다.
    OCR 블록에는 source="ocr" 이 붙습니다 (판단팀 R207 대상).
    실패하면 원래 결과(empty_document)를 그대로 돌려줍니다.
    """
    import pdfplumber
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from ocr_parser import ocr_pil
    blocks = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page_no, page in enumerate(pdf.pages[:OCR_PDF_MAX_PAGES], start=1):
                img = page.to_image(resolution=OCR_PDF_DPI).original
                blocks += ocr_pil(img, page=page_no)
    except Exception:
        return result
    for order, b in enumerate(blocks, 1):      # 페이지를 이어 붙였으니 순서를 다시 매김
        b["order"] = order
    if blocks:
        result = {**result, "blocks": blocks, "errors": []}
    return result


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