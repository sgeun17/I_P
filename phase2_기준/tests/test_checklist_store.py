"""검수용 체크리스트의 보존·버전 이력·실패 원자성을 확인한다.

모든 쓰기는 임시 폴더에서 수행한다. 저장소 초안과 원문 KB/PDF는 읽기만 한다.
"""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from checklist_store import ChecklistError, ChecklistStore


class ChecklistStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='phase2-checklist-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / 'checklists.sqlite3'
        self.document = json.loads((HERE / 'checklist_draft.json').read_text(encoding='utf-8'))
        self.document['source']['path'] = str((HERE / self.document['source']['path']).resolve())
        for source in self.document['source_documents']:
            source['path'] = str((HERE / source['path']).resolve())
        self.version = self.document['draft_version']
        self.source = self.write_document(self.document, 'base.json')
        self.store = ChecklistStore(self.database)
        self.initial_import = self.store.import_draft(self.source)
        self.initial_versions = self.store.list_versions()

    def write_document(self, document, filename='candidate.json'):
        path = self.directory / filename
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return path

    def test_kb_line_endings_preserve_identity_but_content_changes_fail(self):
        original = Path(self.document['source']['path']).read_bytes().replace(b'\r\n', b'\n')
        kb_path = self.directory / 'controls.json'
        candidate = deepcopy(self.document)
        candidate['source']['path'] = str(kb_path)
        path = self.write_document(candidate, 'line-endings.json')
        line_store = ChecklistStore(self.directory / 'line-endings.sqlite3')
        for newline in (b'\n', b'\r\n'):
            kb_path.write_bytes(original.replace(b'\n', newline))
            result = line_store.import_draft(path)
            self.assertEqual(result['active_question_count'], 54)
        kb_path.write_bytes(original.replace(b'"control_id"', b'"control_id" ', 1))
        self.assert_code('SOURCE_MISMATCH', lambda: line_store.import_draft(path))

    def next_document(self):
        document = deepcopy(self.document)
        document['draft_version'] = self.version + '-test-next'
        return document

    def assert_code(self, code, operation):
        with self.assertRaises(ChecklistError) as captured:
            operation()
        self.assertEqual(captured.exception.code, code)
        self.assertTrue(str(captured.exception))

    def assert_original_unchanged(self):
        self.assertEqual(self.store.list_versions(), self.initial_versions)
        for control in self.document['controls']:
            result = self.store.get_control(self.version, control['control_id'], allow_draft=True)
            self.assertEqual(result['control'], control)

    def assert_import_rejected(self, document, code):
        path = self.write_document(document)
        self.assert_code(code, lambda: self.store.import_draft(path))
        self.assert_original_unchanged()

    def test_all_controls_and_54_questions_round_trip_without_approval(self):
        self.assertEqual(self.initial_import['control_count'], 3)
        self.assertEqual(self.initial_import['active_question_count'], 54)
        self.assertEqual(self.initial_import['retired_question_count'], 3)
        content_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(self.initial_import['source_sha256'], content_sha)
        question_count = 0
        for control in self.document['controls']:
            with self.subTest(control=control['control_id']):
                result = self.store.get_control(self.version, control['control_id'], allow_draft=True)
                self.assertEqual(result['control'], control)
                self.assertEqual(result['source'], self.document['source'])
                self.assertEqual(result['source_documents'], self.document['source_documents'])
                self.assertEqual(result['source_sha256'], content_sha)
                self.assertEqual(result['status'], 'DRAFT_FOR_TEAM_REVIEW')
                self.assertIs(result['approved'], False)
                self.assertEqual(result['proposed_result_values'], ['MET', 'NOT_MET', 'UNKNOWN'])
            for item in control['items']:
                with self.subTest(item=item['item_id']):
                    result = self.store.get_item(self.version, item['item_id'], allow_draft=True)
                    self.assertEqual(result['item'], item)
                    self.assertEqual(result['source'], self.document['source'])
                    self.assertEqual(result['source_documents'], self.document['source_documents'])
                    self.assertEqual(result['proposed_result_values'], ['MET', 'NOT_MET', 'UNKNOWN'])
                    self.assertIsNone(result['item']['critical'])
                    self.assertIs(result['approved'], False)
                    self.assertEqual(result['source_sha256'], content_sha)
                    question_count += 1
        self.assertEqual(question_count, 54)
        # 조회에 노출되지 않는 범위·퇴역 이력까지 원본 JSON이 보존되는지 확인한다.
        with closing(sqlite3.connect(self.database)) as connection:
            saved = connection.execute('SELECT document_json FROM versions WHERE version=?', (self.version,)).fetchone()
        self.assertEqual(json.loads(saved[0]), self.document)

    def test_new_store_object_reads_persisted_database(self):
        reopened = ChecklistStore(self.database)
        self.assertEqual(reopened.list_versions(), self.initial_versions)
        control = self.document['controls'][-1]
        item = control['items'][-1]
        self.assertEqual(reopened.get_control(self.version, control['control_id'], allow_draft=True)['control'], control)
        self.assertEqual(reopened.get_item(self.version, item['item_id'], allow_draft=True)['item'], item)

    def test_identical_version_and_bytes_are_idempotent(self):
        second = self.store.import_draft(self.source)
        self.assertIs(self.initial_import['inserted'], True)
        self.assertIs(second['inserted'], False)
        self.assertEqual(second['source_sha256'], self.initial_import['source_sha256'])
        self.assert_original_unchanged()

    def test_different_content_cannot_overwrite_same_version(self):
        document = deepcopy(self.document)
        document['scope']['selection_basis'] += ' 추가 검토 메모'
        self.assert_import_rejected(document, 'VERSION_CONFLICT')

    def test_new_version_can_update_evidence_without_changing_question(self):
        document = self.next_document()
        item = document['controls'][0]['items'][0]
        item['evidence_rule']['met'] += ' 원문의 적용 범위를 함께 확인한다.'
        item['evidence_rule']['required_context'].append('추가 검토 기록의 출처')
        result = self.store.import_draft(self.write_document(document))
        self.assertIs(result['inserted'], True)
        self.assertEqual(len(self.store.list_versions()), 2)
        revised = self.store.get_item(document['draft_version'], item['item_id'], allow_draft=True)
        self.assertEqual(revised['item'], item)
        self.assertIs(revised['approved'], False)
        self.assertIsNone(revised['item']['critical'])
        self.assertEqual(self.store.get_control(self.version, self.document['controls'][0]['control_id'],
                                               allow_draft=True)['control'], self.document['controls'][0])

    def test_existing_id_cannot_be_reused_for_a_different_question(self):
        document = self.next_document()
        document['controls'][0]['items'][0]['question'] = '다른 보안 조건의 이행을 확인했는가?'
        self.assert_import_rejected(document, 'ITEM_ID_REUSED')

    def test_retired_id_cannot_become_active_in_later_version(self):
        document = self.next_document()
        revived = document['retired_items'].pop(0)
        revived['review_status'] = 'SOURCE_REVIEWED_DRAFT'
        revived.pop('retirement_reason', None)
        revived.pop('replaced_by', None)
        for control in document['controls']:
            if control['control_id'] == revived['control_id']:
                control['items'].append(revived)
                break
        self.assert_import_rejected(document, 'RETIRED_ITEM')

    def test_retired_question_history_cannot_be_rewritten(self):
        document = self.next_document()
        document['retired_items'][0]['question'] = '이전과 다른 확인 조건으로 기록을 바꾸었는가?'
        self.assert_import_rejected(document, 'ITEM_ID_REUSED')

    def test_later_version_cannot_silently_drop_active_or_retired_history(self):
        for kind in ('active', 'retired'):
            with self.subTest(history=kind):
                document = self.next_document()
                if kind == 'active':
                    document['controls'][0]['items'].pop(0)
                else:
                    document['retired_items'].pop(0)
                self.assert_import_rejected(document, 'ITEM_HISTORY_LOST')

    def test_source_hash_mismatch_is_rejected_without_partial_import(self):
        for source_kind in ('kb', 'pdf'):
            with self.subTest(source=source_kind):
                document = self.next_document()
                target = document['source'] if source_kind == 'kb' else document['source_documents'][0]
                target['sha256'] = '0' * 64
                self.assert_import_rejected(document, 'SOURCE_MISMATCH')

    def test_invalid_active_or_replacement_id_is_rejected(self):
        for kind in ('active', 'replacement'):
            with self.subTest(reference=kind):
                document = self.next_document()
                if kind == 'active':
                    document['controls'][0]['items'][0]['item_id'] = '2.9.3-Q99'
                else:
                    document['retired_items'][0]['replaced_by'] = ['2.5.1-Q99']
                self.assert_import_rejected(document, 'INVALID_CHECKLIST')

    def test_loading_cannot_grant_approval_or_set_critical(self):
        for mutation in ('approval', 'critical'):
            with self.subTest(mutation=mutation):
                document = self.next_document()
                if mutation == 'approval':
                    document['approved'] = True
                else:
                    document['controls'][0]['items'][0]['critical'] = False
                self.assert_import_rejected(document, 'INVALID_CHECKLIST')

    def test_unapproved_draft_requires_explicit_review_permission(self):
        control = self.document['controls'][0]
        item = control['items'][0]
        for method, identifier in ((self.store.get_control, control['control_id']),
                                   (self.store.get_item, item['item_id'])):
            with self.subTest(query=method.__name__):
                self.assert_code('DRAFT_NOT_APPROVED', lambda: method(self.version, identifier))
                self.assert_code('DRAFT_NOT_APPROVED', lambda: method(self.version, identifier, allow_draft=1))

    def test_unknown_version_control_item_and_retired_item_are_distinct_errors(self):
        queries = [
            ('VERSION_NOT_FOUND', lambda: self.store.get_control('unseen-version', '2.5.1', allow_draft=True)),
            ('CONTROL_NOT_FOUND', lambda: self.store.get_control(self.version, '3.4.1', allow_draft=True)),
            ('ITEM_NOT_FOUND', lambda: self.store.get_item(self.version, '2.5.1-Q99', allow_draft=True)),
            ('RETIRED_ITEM', lambda: self.store.get_item(self.version, self.document['retired_items'][0]['item_id'],
                                                       allow_draft=True)),
        ]
        for code, query in queries:
            with self.subTest(code=code):
                self.assert_code(code, query)

    def test_invalid_import_inputs_raise_structured_checklist_error(self):
        invalid_json = self.directory / 'invalid.json'
        invalid_json.write_text('{"controls":', encoding='utf-8')
        nonobject = self.write_document([], 'nonobject.json')
        operations = [
            ('nonobject', lambda: self.store.import_draft(nonobject)),
            ('invalid_json', lambda: self.store.import_draft(invalid_json)),
            ('missing_file', lambda: self.store.import_draft(self.directory / 'missing.json')),
            ('invalid_path_type', lambda: self.store.import_draft(None)),
        ]
        for name, operation in operations:
            with self.subTest(input=name):
                with self.assertRaises(ChecklistError) as captured:
                    operation()
                self.assertIsInstance(captured.exception.code, str)
                self.assertTrue(captured.exception.code)
                self.assertTrue(str(captured.exception))
                self.assert_original_unchanged()

    def test_invalid_query_types_raise_structured_checklist_error(self):
        operations = [
            lambda: self.store.get_control([], '2.5.1', allow_draft=True),
            lambda: self.store.get_control(self.version, ['2.5.1'], allow_draft=True),
            lambda: self.store.get_item(self.version, ['2.5.1-Q01'], allow_draft=True),
        ]
        for number, operation in enumerate(operations):
            with self.subTest(query=number):
                with self.assertRaises(ChecklistError) as captured:
                    operation()
                self.assertTrue(captured.exception.code)

    def test_source_change_during_import_rolls_back_all_new_rows(self):
        document = self.next_document()
        source = self.write_document(document)
        original_read = Path.read_bytes
        read_count = 0

        def changing_read(path):
            nonlocal read_count
            raw = original_read(path)
            if path == source:
                read_count += 1
                if read_count > 1:
                    return raw + b' '
            return raw

        with patch.object(Path, 'read_bytes', changing_read):
            self.assert_code('SOURCE_MISMATCH', lambda: self.store.import_draft(source))
        self.assertGreaterEqual(read_count, 2)
        self.assert_original_unchanged()
        self.assert_code('VERSION_NOT_FOUND', lambda: self.store.get_control(
            document['draft_version'], document['controls'][0]['control_id'], allow_draft=True))
        with closing(sqlite3.connect(self.database)) as connection:
            for table in ('versions', 'controls', 'items', 'retired_items'):
                rows = connection.execute(f'SELECT COUNT(*) FROM {table} WHERE version=?',
                                          (document['draft_version'],)).fetchone()[0]
                self.assertEqual(rows, 0, table)
        # 실패한 버전을 다시 적재할 수 있어야 반쯤 남은 버전·ID가 없음을 알 수 있다.
        self.assertIs(self.store.import_draft(source)['inserted'], True)

    def test_missing_store_read_fails_without_creating_database(self):
        absent = self.directory / 'unused' / 'missing.sqlite3'
        self.assert_code('STORE_MISSING', lambda: ChecklistStore(absent).list_versions())
        self.assertFalse(absent.exists())


if __name__ == '__main__':
    unittest.main()
