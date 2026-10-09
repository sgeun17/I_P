"""Read-only inventory; does not install packages, load models or contact services."""
import argparse
import ast
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[2] / 'phase1_검색'


def inspect():
    packages = {}
    required = ('torch', 'sentence-transformers', 'transformers', 'chromadb',
                'jsonschema', 'pydantic', 'httpx', 'PyYAML', 'fastapi', 'uvicorn',
                'python-multipart', 'pymysql', 'cryptography', 'python-dotenv',
                'filetype', 'pdfplumber', 'openpyxl', 'python-pptx', 'python-docx', 'easyocr')
    for name in required:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    tree = ast.parse((ROOT / 'retriever.py').read_text(encoding='utf-8'))
    model_files = next(ast.literal_eval(n.value) for n in tree.body
                       if isinstance(n, ast.Assign) and any(
                           isinstance(t, ast.Name) and t.id == 'MODEL_FILES' for t in n.targets))
    files = {'models/bge-m3/' + name: (ROOT / 'models/bge-m3' / name).is_file()
             for name in model_files}
    files['data/chroma_kb/chroma.sqlite3'] = (ROOT / 'data/chroma_kb/chroma.sqlite3').is_file()
    hashes = {}
    for relative in ('phase1_검색/controls.json', 'phase2_기준/full_checklist_draft.json',
                     'phase2_기준/full_reason_codes_draft.json'):
        path = ROOT.parent / relative
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {'scope': 'FILE_AND_PACKAGE_INVENTORY_ONLY', 'python': platform.python_version(),
            'platform': platform.platform(), 'packages': packages, 'search_files': files,
            'input_db_env_exists': (ROOT.parent / 'phase1_입력/database/.env').is_file(),
            'raw_file_sha256': hashes, 'missing_packages': [k for k, v in packages.items() if v is None],
            'e2e_verified': False, 'offline_install_verified': False,
            'limits': ['Package metadata is not an import or ABI compatibility test.',
                       'File presence is not model/index integrity validation.',
                       'Database, OCR weights, LLM and web services are not contacted.',
                       'Raw file hashes are not the normalized shared KB identity.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = inspect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(args.output), 'missing_packages': report['missing_packages'],
                      'missing_search_files': [k for k, v in report['search_files'].items() if not v],
                      'input_db_env_exists': report['input_db_env_exists'], 'e2e_verified': False}, ensure_ascii=False))
