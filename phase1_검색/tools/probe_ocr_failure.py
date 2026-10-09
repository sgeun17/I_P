"""Real EasyOCR missing-model failure with DB calls replaced; downloads disabled."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'phase1_입력'), str(ROOT/'phase1_입력/chunking')]


def main():
    from PIL import Image
    import easyocr
    import ocr_parser
    import pipeline
    output = ROOT/'phase1_검색/reports/ocr_missing_model_2026-10-09.json'
    if output.exists():
        raise ValueError('report already exists')
    with tempfile.TemporaryDirectory(dir=ROOT.parent/'tmp', prefix='ocr-probe-') as folder:
        folder = Path(folder)
        png = folder/'blank.png'
        Image.new('RGB',(100,100),'white').save(png)
        models = folder/'models'
        models.mkdir()
        real = easyocr.Reader
        def offline(*a, **kw):
            return real(*a, **kw, model_storage_directory=str(models),
                        download_enabled=False, gpu=False, verbose=False)
        statuses = []
        with patch.object(easyocr, 'Reader', side_effect=offline), \
             patch.object(ocr_parser, '_reader', None), \
             patch.object(pipeline, 'get_evidence', return_value={'file_type':'png','version':1,'file_name':'blank.png'}), \
             patch.object(pipeline, 'get_file_path', return_value=str(png)), \
             patch.object(pipeline, 'delete_chunks') as deleted, \
             patch.object(pipeline, 'save_chunks', side_effect=AssertionError('must not save')), \
             patch.object(pipeline, 'update_status', side_effect=lambda *a,**kw: statuses.append({'status':a[1], 'error_code':kw.get('error_code')})):
            result = pipeline.process_one('E9999', verbose=False)
        report = {'network_download_enabled':False,'real_db_used':False,'ocr_inference_executed':False,
                  'actual_easyocr_initialization':True,'result':result,'status_transitions':statuses,
                  'stale_chunk_cleanup_called':deleted.called,
                  'passed':result[0]=='failed' and statuses[-1]['status']=='FAILED' and deleted.called,
                  'limits':['Valid synthetic PNG with deliberately empty model directory.',
                            'DB methods mocked; real database persistence not tested.',
                            'Does not validate OCR recognition accuracy or scan-PDF failure classification.']}
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
        return int(not report['passed'])


if __name__=='__main__':
    raise SystemExit(main())
