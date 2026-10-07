import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from checklist_store import ChecklistStore
from reason_codes import ReasonCatalog
from judgment_review import check_review_output
from tools.verify_chapter2_review_flow import make_request, fixed_response


class EvidenceScopeTests(unittest.TestCase):
    def test_scoped_rules_and_positive_negative_cases_reach_review(self):
        doc=json.loads((HERE/'full_checklist_draft.json').read_text(encoding='utf-8'))
        cases=json.loads((HERE/'tests/fixtures/evidence_scope_cases.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['draft_version'],cases['checklist_version'])
        catalog=ReasonCatalog(HERE/'full_reason_codes_draft.json')
        with TemporaryDirectory() as temp:
            store=ChecklistStore(Path(temp)/'criteria.sqlite3')
            store.import_draft(HERE/'full_checklist_draft.json')
            for case in cases['cases']:
                with self.subTest(case=case['case_id']):
                    control=next(c for c in doc['controls'] if any(i['item_id']==case['item_id'] for i in c['items']))
                    req=make_request(control,doc,store,catalog,case['evidence_text'],item_ids=[case['item_id']])
                    self.assertIn('인정 범위:',req.questions[0].evidence_rule.met)
                    self.assertIn('오판 방지:',req.questions[0].evidence_rule.unknown)
                    code='P2_NM_REQUIRED_ACTION_NOT_DONE' if case['expected_result']=='NOT_MET' else 'P2_U_EVIDENCE_INSUFFICIENT'
                    response=fixed_response(req,case['expected_result'],code)
                    response['item_results'][0]['reason']=case['rationale']
                    check=check_review_output(req,response,store,catalog=catalog,allow_draft=True)
                    self.assertTrue(check['validation']['passed'])
                    self.assertFalse(check['validation']['semantic_judgment_checked'])
                    if case['expected_result']!='UNKNOWN':
                        response['item_results'][0]['citations'][0]['quote']='입력에 없는 근거'
                        self.assertFalse(check_review_output(req,response,store,catalog=catalog,allow_draft=True)['validation']['passed'])
