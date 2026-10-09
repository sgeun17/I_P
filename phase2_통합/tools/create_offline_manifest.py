"""Hash an explicitly prepared wheel bundle and search assets; no evidence/config."""
import argparse
import hashlib
import json
from pathlib import Path
import platform

ROOT=Path(__file__).resolve().parents[2]


def describe(path, base):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024),b''):
            digest.update(block)
    return {'path':path.relative_to(base).as_posix(),'bytes':path.stat().st_size,'sha256':digest.hexdigest()}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    a=p.parse_args(); bundle=a.bundle.resolve()
    wheels=sorted((bundle/'wheels').glob('*.whl'))
    if not wheels: raise ValueError('No prepared wheels')
    sources=[]
    for folder in ('phase1_검색/models/bge-m3','phase1_검색/data/chroma_kb'):
        sources.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '.cache' not in p.parts)
    for name in ('phase1_검색/controls.json','phase2_기준/full_checklist_draft.json','phase2_기준/full_reason_codes_draft.json'):
        sources.append(ROOT/name)
    report={'platform':platform.platform(),'python':platform.python_version(),
            'wheels':[describe(p,bundle) for p in wheels],
            'search_assets_in_repository':[describe(p,ROOT) for p in sorted(sources)],
            'limits':['Asset inventory only: assets have not been copied into this wheel bundle.',
                      'Ollama/Qwen, OCR weights, Python and MySQL installers are not included.',
                      'Windows wheels are not a Linux deployment package.']}
    (bundle/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'wheels':len(wheels),'search_assets':len(sources),'manifest':str(bundle/'manifest.json')}))
