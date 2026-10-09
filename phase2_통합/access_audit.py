"""Opt-in access guard. The caller MUST supply a server-authenticated identity.

No default grants, login mechanism, retention duration or production activation.
Audit records exclude document contents, credentials and exception messages.
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

ACTIONS = frozenset({'VIEW_ORIGINAL', 'DOWNLOAD_ORIGINAL', 'REVIEW'})


class AccessDenied(PermissionError):
    pass


class AccessAudit:
    def __init__(self, database, grants):
        # grants: authenticated role -> explicitly allowed action names
        self.grants = {role: frozenset(actions) for role, actions in grants.items()}
        if any(not actions <= ACTIONS for actions in self.grants.values()):
            raise ValueError('Unknown action in access policy')
        self.database = Path(database)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS access_event (
                id INTEGER PRIMARY KEY, request_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, actor TEXT, role TEXT,
                action TEXT NOT NULL, evidence_id TEXT NOT NULL,
                evidence_version INTEGER NOT NULL, outcome TEXT NOT NULL)''')

    def _record(self, request_id, actor, role, action, evidence_id, version, outcome):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO access_event VALUES(NULL,?,?,?,?,?,?,?,?)',
                       (request_id, datetime.now(timezone.utc).isoformat(), actor,
                        role, action, evidence_id, version, outcome))

    def execute(self, *, actor, role, action, evidence_id, version, operation):
        """Wrap synchronous work; SUCCESS means callback returned, not download received.

        actor/role must come from verified server auth, NEVER request fields.
        operation must complete inside this call (not a lazy generator/response).
        Audit unavailable before execution => operation is not called.
        A post-operation log failure propagates; it cannot undo a completed write.
        """
        if action not in ACTIONS or not evidence_id or type(version) is not int or version < 1:
            raise ValueError('Invalid audit target/action')
        request_id = uuid.uuid4().hex
        allowed = bool(actor and actor.strip()) and action in self.grants.get(role, ())
        record = lambda outcome: self._record(request_id, actor, role, action,
                                              evidence_id, version, outcome)
        if not allowed:
            record('DENIED')
            raise AccessDenied('Access denied')
        record('STARTED')
        try:
            result = operation()
        except Exception:
            record('FAILED')
            raise
        record('SUCCEEDED')
        return result
