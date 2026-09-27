"""Image format and OCR provenance at the real chunk/search/judgment boundary."""
from copy import deepcopy
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'phase1_입력'), str(ROOT.parent / 'phase1_판단/src')]

from chunking.chunker import make_chunks
from chunking.chunk_format import validate_chunks
from chunk_retriever import prepare_input, RetrievalError
from judgment_adapter import read, to_mapping_input
from models import MappingInput


def parsed(kind='png'):
    return {'source_file': f'account.{kind}', 'file_type': kind, 'errors': [],
            'blocks': [{'order': 1, 'block_type': 'paragraph', 'page': None,
                        'level': None, 'table': None, 'source': 'ocr',
                        'confidence': .8, 'text': '사용자 계정 승인 기록과 권한 검토 내용.'}]}


class ImageContractTests(unittest.TestCase):
    def test_ocr_marker_survives_chunking_and_strict_search_input(self):
        for kind in ('png', 'jpg'):
            with self.subTest(kind=kind):
                p = parsed(kind)
                chunks = make_chunks(p, 'E0901', 2)
                self.assertEqual(validate_chunks(chunks), [])
                self.assertEqual(chunks[0]['source'], 'ocr')
                envelope = {k: p[k] for k in ('source_file', 'file_type', 'errors')}
                envelope.update(evidence_id='E0901', version=2, chunks=chunks)
                normalized, warnings = prepare_input(envelope)
                self.assertEqual(normalized['chunks'], chunks)
                self.assertEqual(warnings, [])

    def test_ocr_overlap_and_table_sources_survive(self):
        p = parsed('pdf')
        p['blocks'][0].update(page=1, text='OCR 계정 기록 ' * 5)
        p['blocks'].append(dict(p['blocks'][0], order=2, source='parser', text='파서 내용 ' * 10))
        chunks = make_chunks(p, 'E0902', 1, max_chars=85, overlap=20)
        self.assertTrue(any(len(c['block_orders']) > 1 for c in chunks))
        for c in chunks:
            if 1 in c['block_orders']:
                self.assertEqual(c['source'], 'ocr')
        p['blocks'] = [{'order':1,'block_type':'table','page':1,'level':None,
                        'text':'','table':{'rows':[['계정','승인자'],['가온','나래']]},'source':'ocr'}]
        chunks = make_chunks(p, 'E0903', 1)
        self.assertTrue(chunks)
        self.assertTrue(all(c['source'] == 'ocr' for c in chunks))

    def test_image_without_optional_block_source_is_still_ocr(self):
        p = parsed()
        del p['blocks'][0]['source']
        self.assertEqual(make_chunks(p, 'E0904', 1)[0]['source'], 'ocr')

    def test_image_search_result_adapts_and_keeps_page_null(self):
        for kind in ('png', 'jpg'):
            result = read(ROOT / 'examples/docx_output.json')
            result['file_type'] = kind
            result['source_file'] = 'account.' + kind
            for c in result['evidence_chunks']:
                c.update(file_type=kind, source_file=result['source_file'], source='ocr')
            model = MappingInput.model_validate(to_mapping_input(result))
            self.assertTrue(all(c.source.value == 'ocr' and c.page_start is None for c in model.chunks))
            bad = deepcopy(result)
            bad['evidence_chunks'][0]['page_start'] = 1
            with self.assertRaises(ValueError):
                to_mapping_input(bad)

    def test_unknown_format_still_rejected(self):
        p = parsed('exe')
        p.update(evidence_id='E0905', version=1, chunks=make_chunks(p, 'E0905', 1))
        with self.assertRaises(RetrievalError):
            prepare_input(p)


if __name__ == '__main__':
    unittest.main()
