from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from checklist_store import validate_document, ChecklistError
from reason_codes import ReasonCatalog, ReasonCodeError
from tools.inventory_evidence_policies import inventory


def read(name):
    return json.loads((HERE/name).read_text(encoding='utf-8'))


class ApprovalTests(unittest.TestCase):
    def test_inconsistent_approval_flags_are_rejected(self):
        doc = read('full_checklist_draft.json')
        kb = json.loads((HERE.parent/'phase1_검색/controls.json').read_text(encoding='utf-8'))
        for approved, status in [(True, 'DRAFT_FOR_TEAM_REVIEW'), (False, 'APPROVED_FOR_USE'), (1, 'APPROVED_FOR_USE')]:
            bad = deepcopy(doc)
            bad.update(approved=approved, status=status)
            with self.assertRaises(ChecklistError):
                validate_document(bad, kb, doc['source']['sha256'])

    def test_catalog_cannot_approve_an_unapproved_checklist(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc = read('full_checklist_draft.json')
            doc.update(approved=False, status='DRAFT_FOR_TEAM_REVIEW')
            source = root/'full_checklist_draft.json'
            source.write_text(json.dumps(doc), encoding='utf-8')
            catalog = read('full_reason_codes_draft.json')
            catalog['source_files'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            target = root/'full_reason_codes_draft.json'
            target.write_text(json.dumps(catalog), encoding='utf-8')
            with self.assertRaises(ReasonCodeError) as caught:
                ReasonCatalog(target)
            self.assertEqual(caught.exception.code, 'INVALID_SOURCE')

    def test_legacy_catalog_still_requires_opt_in(self):
        catalog = ReasonCatalog(HERE/'chapter2_reason_codes_draft.json')
        with self.assertRaises(ReasonCodeError) as caught:
            catalog.list_codes()
        self.assertEqual(caught.exception.code, 'DRAFT_NOT_APPROVED')
        self.assertFalse(catalog.list_codes(allow_draft=True)['approved'])

    def test_policy_inventory_preserves_sources_without_inventing_values(self):
        doc = read('full_checklist_draft.json')
        result = inventory(doc)
        items = {i['item_id']: i for c in doc['controls'] for i in c['items']}
        self.assertEqual(len(result['items']), 883)
        self.assertFalse(result['runtime_policies_exported'])
        for row in result['items']:
            original = items[row['item_id']]
            self.assertEqual(row['candidate_evidence'], original['evidence_rule']['candidate_evidence'])
            for hint in row['temporal_wording']:
                self.assertEqual(hint['text'], original[hint['field']])
            self.assertNotIn('freshness', row)
            self.assertNotIn('allowed_types', row)
