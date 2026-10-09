"""Run the existing upload/progress UI against a separately configured local test DB.

No production source edits or database initialization. Bind loopback only.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True, help='Private local test JSON outside Git')
    p.add_argument('--port', type=int, default=18080)
    args = p.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    if config['DB_HOST'] != '127.0.0.1' or config['DB_NAME'] != 'evidence_db':
        raise ValueError('Only the isolated loopback test DB is supported')
    if str(config['DB_PORT']) != '13306':
        raise ValueError('Dedicated local test DB port 13306 required')
    for key in ('DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_PASSWORD','STORAGE_DIR','TEMP_DIR'):
        os.environ[key] = str(config[key])
    for key in ('STORAGE_DIR','TEMP_DIR'):
        directory=Path(config[key]).resolve()
        if not directory.is_relative_to((ROOT.parent/'tmp').resolve()):
            raise ValueError('Test storage must stay in workspace tmp')
        directory.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(ROOT/'phase1_입력'))
    import main as upload
    # Existing module has a fixed temp path; override only this process, not its source.
    upload.TMP_DIR = Path(config['TEMP_DIR'])
    from local_search_app import attach
    attach(upload, args.config.resolve().parent)
    import uvicorn
    uvicorn.run(upload.app, host='127.0.0.1', port=args.port)


if __name__=='__main__':
    main()
