from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from checklist_store import validate_document, ChecklistError
from judgment_review import ReviewQuestion
from pydantic import ValidationError


class CriticalDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc=json.loads((HERE/'full_checklist_draft.json').read_text(encoding='utf-8'))
        cls.kb=json.loads((HERE.parent/'phase1_검색/controls.json').read_text(encoding='utf-8'))

    def test_all_groups_and_record_items_follow_owner_decision(self):
        groups={'2.5','2.6','2.10','2.11'}
        for c in self.doc['controls']:
            expected=c['control_id'].rsplit('.',1)[0] in groups
            for i in c['items']:
                with self.subTest(item=i['item_id']):
                    self.assertIs(i['critical'],expected)
                    self.assertEqual(i['critical_status'],'CONFIRMED_BY_OWNER')
                    self.assertIs(ReviewQuestion.model_validate(i).critical,expected)
        self.assertTrue(self.doc['approved'])
        self.assertEqual(self.doc['critical_policy']['runtime_policy'],{'mode':'explicit'})

    def test_value_and_status_must_agree_in_store_and_review(self):
        for value,status in [(None,'CONFIRMED_BY_OWNER'),(True,'UNDECIDED'),('false','CONFIRMED_BY_OWNER'),(1,'CONFIRMED_BY_OWNER')]:
            with self.subTest(value=value,status=status):
                d=deepcopy(self.doc);i=d['controls'][0]['items'][0]
                i.update(critical=value,critical_status=status)
                with self.assertRaises(ChecklistError):validate_document(d,self.kb,d['source']['sha256'])
                with self.assertRaises(ValidationError):ReviewQuestion.model_validate(i)

    def test_legacy_undecided_remains_readable(self):
        old=json.loads((HERE/'chapter2_full_checklist_draft.json').read_text(encoding='utf-8'))
        self.assertIsNone(ReviewQuestion.model_validate(old['controls'][0]['items'][0]).critical)
