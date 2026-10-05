import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from checklist_store import ChecklistStore
from reason_codes import ReasonCatalog
from judgment_review import check_review_output
from tools.verify_chapter2_review_flow import make_request, fixed_response


class ItemExceptionTests(unittest.TestCase):
    def test_exception_rules_reach_review_and_cases_obey_contract(self):
        doc = json.loads((HERE/'full_checklist_draft.json').read_text(encoding='utf-8'))
        cases = json.loads((HERE/'tests/fixtures/item_exception_cases.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['draft_version'], cases['checklist_version'])
        catalog = ReasonCatalog(HERE/'full_reason_codes_draft.json')
        with TemporaryDirectory() as temp:
            store = ChecklistStore(Path(temp)/'check.sqlite3')
            store.import_draft(HERE/'full_checklist_draft.json')
            for case in cases['cases']:
                with self.subTest(case=case['case_id']):
                    control = next(c for c in doc['controls'] if any(i['item_id']==case['item_id'] for i in c['items']))
                    request = make_request(control, doc, store, catalog, case['evidence_text'], item_ids=[case['item_id']])
                    met = request.questions[0].evidence_rule.met
                    self.assertIn('이 문항의 예외', met)
                    response = fixed_response(request, case['expected_result'])
                    response['item_results'][0]['reason'] = case['rationale']
                    checked = check_review_output(request, response, store, catalog=catalog, allow_draft=True)
                    self.assertTrue(checked['validation']['passed'])
                    self.assertFalse(checked['validation']['semantic_judgment_checked'])
                    if case['expected_result']=='MET':
                        response['item_results'][0]['citations'] = []
                        self.assertFalse(check_review_output(request, response, store, catalog=catalog, allow_draft=True)['validation']['passed'])
