"""Validate policy rejection and delivery through real judgment harness (mock transport)."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from tools.run_with_criteria_policy import prepare, execute, read, JUDGMENT, policy_self_check

ASSESSMENT = {'as_of': '2026-10-07', 'period_start': '2026-01-01', 'period_end': '2026-06-30'}
CATALOG = read(HERE / 'full_checklist_draft.json')
ID = '2.5.1-Q01'


class RuntimePolicyTests(unittest.TestCase):
    def test_default_does_not_invent_policy(self):
        value = prepare(CATALOG, '2.5.1', ASSESSMENT, {})
        self.assertEqual(value['item_policies'], {})
        self.assertEqual(value['critical_policy'], {'mode': 'explicit'})
        self.assertTrue(all(v['freshness_check'] == 'CONTENT_REVIEW_NO_FIXED_AGE' for v in value['coverage']))

    def test_bad_dates_block_before_run(self):
        for patch in ({'as_of': 'today'}, {'as_of': '2026-02-30'},
                      {'period_end': '2027-01-01'}, {'period_start': '2026-07-01'},
                      {'extra': 'ignored?'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                prepare(CATALOG, '2.5.1', {**ASSESSMENT, **patch}, {})

    def test_bad_policies_block(self):
        for policies in ({'unknown': {}}, {ID: {}}, {ID: {'typo': 1}},
                         {ID: {'allowed_types': []}}, {ID: {'allowed_types': ['PDF']}},
                         {ID: {'allowed_types': ['pdf', 'pdf']}},
                         {ID: {'freshness': {'max_age_months': True, 'date_label': 'performed_date'}}},
                         {ID: {'freshness': {'max_age_months': 12, 'date_label': 'mtime'}}},
                         {ID: {'freshness': {'max_age_months': -1, 'date_label': 'performed_date'}}}):
            with self.subTest(policies=policies), self.assertRaises(ValueError):
                prepare(CATALOG, '2.5.1', ASSESSMENT, policies)

    def test_explicit_policy_is_copied_without_changing_checklist(self):
        policies = {ID: {'freshness': {'max_age_months': 6, 'date_label': 'performed_date'}, 'allowed_types': ['txt']}}
        before = deepcopy(CATALOG)
        value = prepare(CATALOG, '2.5.1', ASSESSMENT, policies)
        value['item_policies'][ID]['allowed_types'].append('pdf')
        self.assertEqual(policies[ID]['allowed_types'], ['txt'])
        self.assertEqual(before, CATALOG)

    def test_actual_pipeline_receives_context_and_enforces_date_format(self):
        sys.path[:0] = [str(JUDGMENT), str(JUDGMENT / 'src')]
        from validated_pipeline import run_control_judgment
        from validation.contracts import interface_module
        catalog = deepcopy(CATALOG)
        control = next(c for c in catalog['controls'] if c['control_id'] == '2.5.1')
        control['items'] = control['items'][:1]
        item_id = control['items'][0]['item_id']
        payload = read(HERE.parent / 'phase2_인터페이스/phase2_input_sample.json')
        payload['checklist_version'] = catalog['draft_version']
        kb_path = HERE.parent / 'phase1_검색/controls.json'
        kb_hash = interface_module('kb_identity').kb_sha256(kb_path.read_bytes())
        payload['source_versions']['kb_sha256'] = kb_hash
        controls = {c['control_id']: c['control_name'] for c in catalog['controls']}
        reasons = read(HERE / 'full_reason_codes_draft.json')
        seen = []

        def transport(system, user, model, schema, **kwargs):
            self.assertIn('2026-06-30', user)
            self.assertIn('P2_U_EVIDENCE_CONFLICT', user)
            self.assertIn('criteria-execution-v1', user)
            seen.append(user)
            return json.dumps({'item_id': item_id, 'result': 'UNKNOWN',
                'reason': '판정 근거가 부족합니다.', 'reason_codes': ['P2_U_EVIDENCE_INSUFFICIENT'],
                'citations': []}, ensure_ascii=False)

        def runner(*args, **kwargs):
            return run_control_judgment(*args, **kwargs, llm_call=transport)

        for policies in ({}, {item_id: {'freshness': {'max_age_months': 6, 'date_label': 'performed_date'}, 'allowed_types': ['pdf']}}):
            prepared = prepare(catalog, '2.5.1', ASSESSMENT, policies)
            result = execute(payload, '2.5.1', catalog, reasons, controls, kb_hash, prepared, 'mock', runner=runner)
            self.assertTrue(result['audit']['items'], result['audit'])
            audit = result['audit']['items'][0]
            self.assertEqual(audit['freshness']['status'], 'DATE_MISSING' if policies else 'POLICY_MISSING')
            if policies:
                self.assertIn('FORMAT_MISMATCH', result['audit']['review_signals'])
                self.assertTrue(result['output']['human_review']['required'])
                self.assertTrue(result['output']['provisional'])
            self.assertIn('criteria_execution_policy', result['audit'])
            self.assertFalse(any(i.get('severity') == 'error' for i in result['audit']['issues']), result['audit']['issues'])
        self.assertEqual(len(seen), 2)

    def test_boundary_dates_use_calendar_months(self):
        sys.path.insert(0, str(JUDGMENT / 'src'))
        from date_extractor import check_freshness
        for value, expected in [('2026-04-07', 'FRESH'), ('2026-04-06', 'STALE'), ('2026-10-08', 'FUTURE_DATE')]:
            with self.subTest(value=value):
                check = check_freshness([{'label': 'performed_date', 'value': value, 'source': 'parser'}],
                                        as_of='2026-10-07', max_age_months=6, date_label='performed_date')
                self.assertEqual(check['status'], expected)

    def test_pending_exception_review_is_visible_without_changing_result(self):
        original = {'processing_status': 'COMPLETED', 'audit': {}, 'output': {'items': [
            {'item_id': ID, 'result': 'UNKNOWN', 'reason_codes': ['P2_U_NO_TRIGGER_EVENT']}]}}
        result = execute({}, '2.5.1', CATALOG, {}, {}, 'hash',
                         prepare(CATALOG, '2.5.1', ASSESSMENT, {}), 'mock',
                         runner=lambda *a, **kw: deepcopy(original))
        self.assertEqual(result['output']['items'], original['output']['items'])
        self.assertEqual(result['processing_status'], 'REVIEW_REQUIRED')
        self.assertTrue(result['output']['human_review']['required'])
        self.assertTrue(result['output']['provisional'])
        self.assertIn('P2R113', result['output']['human_review']['reasons'])
        self.assertEqual(result['audit']['criteria_execution_policy']['pending_item_reviews'][0]['item_id'], ID)

    def test_selfcheck_has_same_context_without_altering_evidence(self):
        seen = []
        def transport(system, user, model, schema, **kwargs):
            self.assertIn('2026-06-30', system)
            self.assertIn('상충', system)
            self.assertEqual(user, 'original evidence and retry message')
            self.assertEqual(kwargs['timeout'], 10)
            seen.append(system)
            return 'unchanged response'
        callback = policy_self_check(prepare(CATALOG, '2.5.1', ASSESSMENT, {}), '상충 규칙', transport)
        for _ in range(2):
            self.assertEqual(callback('base system', 'original evidence and retry message', 'mock', {}, timeout=10), 'unchanged response')
        self.assertEqual(seen[0], seen[1])


if __name__ == '__main__':
    unittest.main()
