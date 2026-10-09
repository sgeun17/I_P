import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from access_audit import AccessAudit, AccessDenied
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'phase1_검색'))
from chunk_retriever import worker_python


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'audit.sqlite3'
        self.guard = AccessAudit(self.path, {'REVIEWER': ['VIEW_ORIGINAL', 'REVIEW']})

    def run_action(self, callback, **kwargs):
        params = dict(actor='tester', role='REVIEWER', action='VIEW_ORIGINAL',
                      evidence_id='E-TEST', version=1, operation=callback)
        params.update(kwargs)
        return self.guard.execute(**params)

    def rows(self):
        db = sqlite3.connect(self.path)
        try:
            return db.execute('SELECT request_id,outcome FROM access_event ORDER BY id').fetchall()
        finally:
            db.close()

    def test_read_original_persists_without_document_content(self):
        source = Path(self.temp.name) / 'original.txt'
        source.write_text('private synthetic evidence', encoding='utf-8')
        self.assertEqual(self.run_action(source.read_bytes), source.read_bytes())
        rows = self.rows()
        self.assertEqual([r[1] for r in rows], ['STARTED', 'SUCCEEDED'])
        self.assertEqual(rows[0][0], rows[1][0])
        AccessAudit(self.path, {})  # Restart preserves events.
        self.assertEqual(self.rows(), rows)
        self.assertNotIn(b'private synthetic evidence', self.path.read_bytes())

    def test_denies_anonymous_unknown_role_and_download(self):
        for override in ({'actor': None}, {'role': 'UNKNOWN'}, {'action': 'DOWNLOAD_ORIGINAL'}):
            callback = Mock()
            with self.assertRaises(AccessDenied):
                self.run_action(callback, **override)
            callback.assert_not_called()
        self.assertEqual([r[1] for r in self.rows()], ['DENIED'] * 3)

    def test_failure_recorded_without_exception_secrets(self):
        with self.assertRaises(RuntimeError):
            self.run_action(Mock(side_effect=RuntimeError('secret-token')))
        self.assertEqual([r[1] for r in self.rows()], ['STARTED', 'FAILED'])
        self.assertNotIn(b'secret-token', self.path.read_bytes())

    def test_audit_failure_blocks_operation(self):
        callback = Mock()
        with patch.object(self.guard, '_record', side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_action(callback)
        callback.assert_not_called()

    def test_invalid_action_and_version(self):
        for override in ({'action': 'DELETE'}, {'version': True}, {'version': 0}):
            callback = Mock()
            with self.assertRaises(ValueError):
                self.run_action(callback, **override)
            callback.assert_not_called()

    def test_worker_paths(self):
        root = Path('search')
        self.assertEqual(worker_python(root, 'nt'), root / '.venv/Scripts/python.exe')
        self.assertEqual(worker_python(root, 'posix'), root / '.venv/bin/python')


if __name__ == '__main__':
    unittest.main()
