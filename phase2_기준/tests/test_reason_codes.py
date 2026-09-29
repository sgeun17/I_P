"""검수용 사유 코드가 승인·근거·결과 경계를 보존하는지 확인한다.

변이 검사는 임시 폴더의 사본만 바꾼다. 실제 초안·사례·카탈로그는 읽기만 한다.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from reason_codes import ReasonCatalog, ReasonCodeError


class ReasonCatalogTests(unittest.TestCase):
    FILES = (
        'reason_codes_draft.json',
        'checklist_draft.json',
        'review_examples.json',
        'reason_code_examples.json',
    )

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='phase2-reason-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repository_hashes = {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in self.FILES
        }
        self.addCleanup(self.assert_repository_files_unchanged)
        for name in self.FILES:
            shutil.copyfile(HERE / name, self.directory / name)
        self.catalog_path = self.directory / 'reason_codes_draft.json'
        self.examples_path = self.directory / 'reason_code_examples.json'
        self.catalog = ReasonCatalog(self.catalog_path)

    def assert_repository_files_unchanged(self):
        for name, digest in self.repository_hashes.items():
            self.assertEqual(hashlib.sha256((HERE / name).read_bytes()).hexdigest(), digest, name)

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text(encoding='utf-8'))

    @staticmethod
    def write_json(path, document):
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def assert_rejected(self, operation, code=None):
        with self.assertRaises(ReasonCodeError) as captured:
            operation()
        if code is not None:
            self.assertEqual(captured.exception.code, code)
        self.assertIsInstance(captured.exception, ValueError)
        self.assertIsInstance(captured.exception.code, str)
        self.assertTrue(str(captured.exception))

    def validate_copied_examples(self):
        return self.catalog.validate_examples(self.examples_path, allow_draft=True)

    def test_all_public_operations_require_explicit_draft_permission(self):
        operations = (
            lambda: self.catalog.list_codes(),
            lambda: self.catalog.validate_assignment('MET', []),
            lambda: self.catalog.validate_examples(self.examples_path),
        )
        for index, operation in enumerate(operations):
            with self.subTest(operation=index):
                self.assert_rejected(operation, 'DRAFT_NOT_APPROVED')
        for permission in (None, 1, 'True'):
            with self.subTest(permission=permission):
                self.assert_rejected(
                    lambda: self.catalog.list_codes(allow_draft=permission),
                    'DRAFT_NOT_APPROVED',
                )

    def test_catalog_lists_13_codes_and_filters_by_compatible_result(self):
        result = self.catalog.list_codes(allow_draft=True)
        self.assertEqual(result['status'], 'DRAFT_FOR_TEAM_REVIEW')
        self.assertIs(result['approved'], False)
        self.assertEqual(len(result['codes']), 13)
        self.assertTrue(result['pending_decisions'])
        for state, count in (('MET', 0), ('NOT_MET', 3), ('UNKNOWN', 10)):
            with self.subTest(result=state):
                filtered = self.catalog.list_codes(state, allow_draft=True)
                self.assertEqual(len(filtered['codes']), count)
                self.assertTrue(all(entry['result'] == state for entry in filtered['codes']))
                self.assertEqual(filtered['catalog_version'], result['catalog_version'])
                self.assertIs(filtered['approved'], False)

    def test_valid_assignments_preserve_result_codes_and_unapproved_state(self):
        assignments = (
            ('MET', []),
            ('NOT_MET', ['P2_NM_REQUIRED_ACTION_NOT_DONE']),
            ('UNKNOWN', ['P2_U_EVIDENCE_INSUFFICIENT', 'P2_U_TIME_OR_VERSION_UNCLEAR']),
        )
        for state, codes in assignments:
            with self.subTest(result=state):
                output = self.catalog.validate_assignment(state, codes, allow_draft=True)
                self.assertEqual(output['result'], state)
                self.assertEqual(output['reason_codes_draft'], codes)
                self.assertIs(output['approved'], False)

    def test_met_must_be_empty_and_non_met_must_have_a_reason(self):
        assignments = (
            ('MET', ['P2_U_EVIDENCE_INSUFFICIENT']),
            ('MET', ['P2_NM_REQUIRED_ACTION_NOT_DONE']),
            ('NOT_MET', []),
            ('UNKNOWN', []),
        )
        for state, codes in assignments:
            with self.subTest(result=state, codes=codes):
                self.assert_rejected(lambda: self.catalog.validate_assignment(state, codes, allow_draft=True))

    def test_missing_evidence_cannot_be_given_a_failure_code(self):
        # 결과와 코드의 호환성 검사이며 자료를 자동 판정한 것으로 취급하지 않는다.
        assignments = (
            ('NOT_MET', ['P2_U_EVIDENCE_INSUFFICIENT']),
            ('UNKNOWN', ['P2_NM_RULE_NOT_DEFINED']),
            ('UNKNOWN', ['P2_NM_REQUIRED_ACTION_NOT_DONE']),
            ('UNKNOWN', ['P2_U_EVIDENCE_INSUFFICIENT', 'P2_NM_RULE_VIOLATED']),
        )
        for state, codes in assignments:
            with self.subTest(result=state, codes=codes):
                self.assert_rejected(lambda: self.catalog.validate_assignment(state, codes, allow_draft=True))

    def test_invalid_result_values_are_not_silently_normalized(self):
        for value in ('N/A', 'NOT_APPLICABLE', 'unknown', '', None, 1):
            with self.subTest(result=value):
                self.assert_rejected(lambda: self.catalog.validate_assignment(value, [], allow_draft=True))

    def test_reason_codes_must_be_a_list_of_strings(self):
        bad_values = (
            None,
            'P2_U_EVIDENCE_INSUFFICIENT',
            {'code': 'P2_U_EVIDENCE_INSUFFICIENT'},
            ('P2_U_EVIDENCE_INSUFFICIENT',),
            [None],
            [1],
            [{}],
            ['P2_U_EVIDENCE_INSUFFICIENT', 1],
        )
        for codes in bad_values:
            with self.subTest(codes=codes):
                self.assert_rejected(lambda: self.catalog.validate_assignment('UNKNOWN', codes, allow_draft=True))

    def test_duplicate_and_unregistered_codes_are_rejected(self):
        bad_codes = (
            ['P2_U_EVIDENCE_INSUFFICIENT', 'P2_U_EVIDENCE_INSUFFICIENT'],
            ['P2_U_NOT_REGISTERED'],
            [''],
            [' P2_U_EVIDENCE_INSUFFICIENT'],
        )
        for codes in bad_codes:
            with self.subTest(codes=codes):
                self.assert_rejected(lambda: self.catalog.validate_assignment('UNKNOWN', codes, allow_draft=True))

    def test_source_file_changes_invalidate_the_catalog(self):
        document = self.read_json(self.catalog_path)
        for source in document['source_files']:
            path = self.directory / source['path']
            original = path.read_bytes()
            try:
                path.write_bytes(original + b' ')
                operations = (
                    lambda: ReasonCatalog(self.catalog_path).list_codes(allow_draft=True),
                    lambda: self.catalog.list_codes(allow_draft=True),
                    lambda: self.catalog.validate_assignment('MET', [], allow_draft=True),
                )
                for index, operation in enumerate(operations):
                    with self.subTest(source=source['path'], operation=index):
                        self.assert_rejected(operation, 'SOURCE_CHANGED')
            finally:
                path.write_bytes(original)
        # 이미 열린 객체도 검토 도중 카탈로그가 바뀌면 이전 정의를 그대로 쓰지 않는다.
        original_catalog = self.catalog_path.read_bytes()
        try:
            self.catalog_path.write_bytes(original_catalog + b' ')
            operations = (
                lambda: self.catalog.list_codes(allow_draft=True),
                lambda: self.catalog.validate_assignment('MET', [], allow_draft=True),
                self.validate_copied_examples,
            )
            for index, operation in enumerate(operations):
                with self.subTest(source='reason_codes_draft.json', operation=index):
                    self.assert_rejected(operation, 'SOURCE_CHANGED')
        finally:
            self.catalog_path.write_bytes(original_catalog)

    def test_complete_examples_cover_162_original_and_two_boundary_cases(self):
        result = self.validate_copied_examples()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['reason_code_count'], 13)
        self.assertEqual(result['mapped_case_count'], 162)
        self.assertEqual(result['boundary_case_count'], 2)
        self.assertEqual(result['active_item_count'], 54)
        self.assertEqual(result['counts_by_result'], {'MET': 54, 'NOT_MET': 54, 'UNKNOWN': 54})
        self.assertIs(result['approved'], False)
        self.assertIs(result['llm_executed'], False)
        self.assertIs(result['actual_evidence_used'], False)
        self.assertIs(result['source_files_verified'], True)

    def test_mapping_cannot_change_the_original_expected_label(self):
        document = self.read_json(self.examples_path)
        row = next(row for row in document['cases'] if row['expected_result_draft'] == 'MET')
        # 결과와 코드는 서로 호환되게 바꿔도 원래 정답과 다르면 차단되어야 한다.
        row['expected_result_draft'] = 'NOT_MET'
        row['reason_codes_draft'] = ['P2_NM_RULE_VIOLATED']
        row['pending_decision_ids'] = []
        self.write_json(self.examples_path, document)
        self.assert_rejected(self.validate_copied_examples)

    def test_original_case_coverage_cannot_be_omitted_or_duplicated(self):
        original = self.read_json(self.examples_path)
        for mutation in ('omit', 'append_duplicate', 'replace_with_duplicate'):
            document = deepcopy(original)
            if mutation == 'omit':
                document['cases'].pop()
            elif mutation == 'append_duplicate':
                document['cases'].append(deepcopy(document['cases'][0]))
            else:
                document['cases'][-1] = deepcopy(document['cases'][0])
            self.write_json(self.examples_path, document)
            with self.subTest(mutation=mutation):
                self.assert_rejected(self.validate_copied_examples)

    def test_mapping_excerpt_must_exist_in_the_original_evidence(self):
        document = self.read_json(self.examples_path)
        document['cases'][0]['evidence_excerpt'] = '원래 근거에 존재하지 않는 검토용 발췌 8cb33fb'
        self.write_json(self.examples_path, document)
        self.assert_rejected(self.validate_copied_examples)

    def test_pending_decisions_must_match_the_code_and_existing_definition(self):
        original = self.read_json(self.examples_path)
        row_index = next(index for index, row in enumerate(original['cases']) if row['pending_decision_ids'])
        for pending in (None, [], ['PD_NOT_REGISTERED'], ['PD_OUTPUT_CONTRACT']):
            document = deepcopy(original)
            if pending is None:
                document['cases'][row_index].pop('pending_decision_ids')
            else:
                document['cases'][row_index]['pending_decision_ids'] = pending
            self.write_json(self.examples_path, document)
            with self.subTest(pending=pending):
                self.assert_rejected(self.validate_copied_examples)
        # 카탈로그의 사유가 참조하는 협의 정의 자체가 사라진 경우도 차단한다.
        catalog_document = self.read_json(self.catalog_path)
        pending_id = next(entry['pending_decision_id'] for entry in catalog_document['codes'] if entry['pending_decision_id'])
        catalog_document['pending_decisions'] = [
            entry for entry in catalog_document['pending_decisions']
            if entry['decision_id'] != pending_id
        ]
        self.write_json(self.catalog_path, catalog_document)
        self.assert_rejected(lambda: ReasonCatalog(self.catalog_path).list_codes(allow_draft=True))

    def test_boundary_cases_preserve_active_references_result_and_coverage(self):
        original = self.read_json(self.examples_path)
        mutations = (
            'original_case_id', 'retired_item_id', 'wrong_result',
            'duplicate_boundary_id', 'missing_boundary',
        )
        for mutation in mutations:
            document = deepcopy(original)
            boundary = document['boundary_cases'][0]
            if mutation == 'original_case_id':
                boundary['case_id'] = document['cases'][0]['case_id']
            elif mutation == 'retired_item_id':
                boundary['item_id'] = '2.5.1-Q12'
            elif mutation == 'wrong_result':
                boundary['expected_result_draft'] = 'NOT_MET'
            elif mutation == 'duplicate_boundary_id':
                document['boundary_cases'][1]['case_id'] = boundary['case_id']
            else:
                document['boundary_cases'].pop(0)
            self.write_json(self.examples_path, document)
            with self.subTest(mutation=mutation):
                self.assert_rejected(self.validate_copied_examples)


if __name__ == '__main__':
    unittest.main()
