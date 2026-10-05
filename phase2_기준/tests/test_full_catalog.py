"""Whole-KB coverage, chapter 2 preservation, draft guards and review contracts."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from checklist_store import ChecklistStore, ChecklistError
from reason_codes import ReasonCatalog
from judgment_review import check_review_output, prepare_judgment_review, JudgmentReviewError
from tools.verify_chapter2_review_flow import make_request, fixed_response

FULL = HERE / 'full_checklist_draft.json'
CATALOG = HERE / 'full_reason_codes_draft.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class FullCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix='phase2-full-')
        cls.store = ChecklistStore(Path(cls.temp.name) / 'review.sqlite3')
        cls.store.import_draft(FULL)
        cls.data = read(FULL)
        cls.catalog = ReasonCatalog(CATALOG)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_kb_control_and_chapter2_preserved(self):
        kb = read(HERE.parent / 'phase1_검색/controls.json')
        self.assertEqual([c['control_id'] for c in self.data['controls']], [c['control_id'] for c in kb])
        old = read(HERE/'chapter2_full_checklist_draft.json')
        for c in old['controls']:
            for i in c['items']:
                i['critical'] = c['control_id'].rsplit('.',1)[0] in {'2.5','2.6','2.10','2.11'}
                i['critical_status'] = 'CONFIRMED_BY_OWNER'
                if i['item_id'] == '2.10.8-Q05':
                    current = next(x for c in self.data['controls'] for x in c['items'] if x['item_id'] == i['item_id'])
                    for key in ('evidence_rule', 'review_note'):
                        i[key] = deepcopy(current[key])  # r5 문항별 예외, 나머지 필드는 보존 검사
        self.assertEqual([c for c in self.data['controls'] if c['control_id'].startswith('2.')], old['controls'])
        self.assertEqual(self.data['retired_items'], old['retired_items'])
        self.assertEqual(sum(len(c['items']) for c in self.data['controls']), 883)

    def test_new_major_checks_fully_mapped_and_draft_conditions(self):
        inv = read(HERE/'drafts/chapters13/source_inventory.json')
        links = read(HERE/'drafts/chapters13/coverage.json')
        expected = {(c['control_id'], n) for c in inv for n in range(1,len(c['checks'])+1)}
        self.assertEqual({(c['control_id'],c['major_check']) for c in links}, expected)
        items = {i['item_id']:i for c in self.data['controls'] for i in c['items']}
        linked = [iid for c in links for iid in c['item_ids']]
        self.assertEqual(len(linked), len(set(linked)))
        self.assertEqual(set(linked), {iid for iid in items if not iid.startswith('2.')})
        self.assertEqual(len({i['question'] for i in items.values()}), len(items))
        for iid in linked:
            with self.subTest(item=iid):
                i = items[iid]
                self.assertIs(i['critical'], False)
                if iid.startswith('3.'):
                    self.assertTrue(i['applicability_condition'])
                    self.assertIn('법령', i['evidence_rule']['unknown'])

    def test_all_controls_lookup_and_unknown_response_contract(self):
        for c in self.data['controls']:
            with self.subTest(control=c['control_id']):
                stored = self.store.get_control(self.data['draft_version'], c['control_id'], allow_draft=True)
                self.assertEqual(stored['control'], c)
                req = make_request(c, self.data, self.store, self.catalog, '필요한 증적 미제출')
                checked = check_review_output(req, fixed_response(req), self.store,
                                              catalog=self.catalog, allow_draft=True)
                self.assertTrue(checked['validation']['passed'], checked)
                self.assertFalse(checked['approved'])
                self.assertFalse(checked['validation']['semantic_judgment_checked'])

    def test_new_chapters_reject_bad_quote_wrong_version_and_missing_item(self):
        for cid in ['1.1.1', '3.1.1']:
            c = next(c for c in self.data['controls'] if c['control_id']==cid)
            req = make_request(c, self.data, self.store, self.catalog, '가상 대상의 확인 기록')
            good = fixed_response(req, 'MET')
            self.assertTrue(check_review_output(req,good,self.store,catalog=self.catalog,allow_draft=True)['validation']['passed'])
            for mutation in ['quote','version','missing']:
                with self.subTest(control=cid,mutation=mutation):
                    bad = deepcopy(good)
                    if mutation=='quote': bad['item_results'][0]['citations'][0]['quote']='없는 인용'
                    if mutation=='version': bad['checklist_version']='wrong'
                    if mutation=='missing': bad['item_results'].pop()
                    self.assertFalse(check_review_output(req,bad,self.store,catalog=self.catalog,allow_draft=True)['validation']['passed'])

    def test_approval_and_catalog_mixing_guards(self):
        self.assertTrue(self.store.get_control(self.data['draft_version'],'1.1.1')['approved'])
        self.assertTrue(self.catalog.list_codes()['approved'])
        c = self.data['controls'][0]
        req = make_request(c,self.data,self.store,self.catalog,'자료 없음')
        with self.assertRaises(JudgmentReviewError) as caught:
            prepare_judgment_review(req.source_phase1_result,self.store,req.checklist_version,
                                   req.chunks,catalog=ReasonCatalog(HERE/'chapter2_reason_codes_draft.json'),allow_draft=True)
        self.assertEqual(caught.exception.code,'CATALOG_CHECKLIST_MISMATCH')


if __name__ == '__main__':
    unittest.main()
