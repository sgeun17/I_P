"""검수용 Phase2 체크리스트의 버전 저장·통제항목/문항 조회. 판정을 수행하지 않는다.

표준 라이브러리 SQLite를 사용한다. 동일 버전의 다른 내용은 거부하고,
조회 시 초안 상태와 미정 critical을 그대로 반환한다. 운영 승인 기능은 없다.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'phase1_검색'))
from kb_identity import kb_sha256
DEFAULT_DATABASE = HERE / 'data/checklists.sqlite3'


class ChecklistError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, message, code='INVALID_CHECKLIST'):
    if not condition:
        raise ChecklistError(code, message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def read_bytes(path, code='INPUT_FILE_ERROR'):
    try:
        return path.read_bytes()
    except OSError as error:
        raise ChecklistError(code, f'파일을 읽을 수 없습니다: {path}: {error}') from error


def decode_document(raw):
    try:
        return json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError) as error:
        raise ChecklistError('INVALID_JSON', f'유효한 UTF-8 JSON이 아닙니다: {error}') from error


def validate_document(document, kb, kb_sha):
    """적재 전에 원본 KB 연결·활성/퇴역 ID·근거·초안 상태를 확인한다."""
    require(isinstance(document, dict), '체크리스트는 객체여야 합니다.')
    require(nonempty(document.get('draft_version')), 'draft_version이 필요합니다.')
    require(document.get('approved') is False and document.get('status') == 'DRAFT_FOR_TEAM_REVIEW',
            '검수용 초안만 적재합니다. 이 모듈로 운영 승인을 부여할 수 없습니다.')
    require(isinstance(document.get('source'), dict), 'source가 필요합니다.')
    require(document['source'].get('sha256') == kb_sha, 'Phase1 KB 해시가 다릅니다.', 'SOURCE_MISMATCH')
    require(document.get('proposed_result_values') == ['MET', 'NOT_MET', 'UNKNOWN'], '판정값 제안이 다릅니다.')
    controls = document.get('controls')
    require(isinstance(controls, list) and controls, 'controls가 필요합니다.')
    require(isinstance(kb, list) and all(isinstance(c, dict) and nonempty(c.get('control_id'))
                                       and nonempty(c.get('control_name')) and nonempty(c.get('requirement'))
                                       for c in kb), '원본 KB 구조가 잘못됐습니다.', 'SOURCE_MISMATCH')
    master = {c['control_id']: c for c in kb}
    require(len(master) == len(kb), '원본 KB ID가 중복됐습니다.', 'SOURCE_MISMATCH')
    sources = document.get('source_documents')
    require(isinstance(sources, list) and sources, 'source_documents가 필요합니다.')
    require(all(isinstance(s, dict) and all(nonempty(s.get(k)) for k in ('source_id', 'path', 'sha256'))
                for s in sources), '출처 ID·경로·해시가 필요합니다.')
    source_ids = {s['source_id'] for s in sources}
    require(len(source_ids) == len(sources), '출처 ID가 중복됐습니다.')
    active, control_ids = {}, set()
    for control in controls:
        require(isinstance(control, dict), '통제항목은 객체여야 합니다.')
        cid = control.get('control_id')
        require(nonempty(cid) and cid in master and cid not in control_ids, f'통제항목 ID가 누락·중복·잘못됐습니다: {cid}')
        control_ids.add(cid)
        require(control.get('control_name') == master[cid]['control_name']
                and control.get('requirement') == master[cid]['requirement'],
                f'{cid}: Phase1 기준과 명칭·요구사항이 다릅니다.', 'SOURCE_MISMATCH')
        require(isinstance(control.get('items'), list) and control['items'], f'{cid}: 문항이 없습니다.')
        for item in control['items']:
            require(isinstance(item, dict), f'{cid}: 문항은 객체여야 합니다.')
            iid = item.get('item_id')
            require(isinstance(iid, str) and re.fullmatch(re.escape(cid) + r'-Q\d{2}', iid)
                    and item.get('control_id') == cid and iid not in active, f'문항 ID·소속·중복 오류: {iid}')
            require(nonempty(item.get('question')) and nonempty(item.get('source_clause')), f'{iid}: 질문·원문이 필요합니다.')
            require(item.get('check_kind') in ('procedure', 'implementation', 'record'), f'{iid}: check_kind 오류')
            require(item.get('critical') is None and item.get('critical_status') == 'UNDECIDED',
                    f'{iid}: 미합의 critical 값을 이 적재 과정에서 확정하지 않습니다.')
            require(item.get('review_status') == 'SOURCE_REVIEWED_DRAFT', f'{iid}: 검수 초안 상태가 필요합니다.')
            rule = item.get('evidence_rule')
            require(isinstance(rule, dict), f'{iid}: evidence_rule이 필요합니다.')
            require(all(nonempty(rule.get(k)) for k in ('met', 'not_met', 'unknown', 'scope_rule')), f'{iid}: 판정 근거가 필요합니다.')
            require(all(isinstance(rule.get(k), list) and rule[k] and all(nonempty(s) for s in rule[k])
                        for k in ('candidate_evidence', 'required_context')), f'{iid}: 증적·컨텍스트 목록이 필요합니다.')
            require(rule.get('citation_required_for') == ['MET', 'NOT_MET'], f'{iid}: 인용 규칙이 다릅니다.')
            refs = item.get('source_refs')
            require(isinstance(refs, list) and refs and all(
                isinstance(r, dict) and nonempty(r.get('source_id')) and r['source_id'] in source_ids
                and type(r.get('pdf_page')) is int and r['pdf_page'] > 0
                and type(r.get('printed_page')) is int and r['printed_page'] > 0 for r in refs), f'{iid}: 출처·쪽수 오류')
            active[iid] = item
    retired = document.get('retired_items', [])
    require(isinstance(retired, list), 'retired_items는 목록이어야 합니다.')
    retired_ids = set()
    for item in retired:
        require(isinstance(item, dict), '퇴역 문항은 객체여야 합니다.')
        iid = item.get('item_id')
        require(nonempty(iid) and iid not in active and iid not in retired_ids, f'활성·퇴역 ID 충돌: {iid}')
        require(nonempty(item.get('control_id')) and item['control_id'] in control_ids
                and re.fullmatch(re.escape(item['control_id']) + r'-Q\d{2}', iid), f'{iid}: 퇴역 문항 통제항목·ID 오류')
        require(nonempty(item.get('question')), f'{iid}: 퇴역 질문이 필요합니다.')
        require(isinstance(item.get('replaced_by'), list) and item['replaced_by']
                and all(nonempty(s) for s in item['replaced_by'])
                and set(item['replaced_by']) <= set(active)
                and all(active[s]['control_id'] == item['control_id'] for s in item['replaced_by']), f'{iid}: 대체 문항 참조 오류')
        retired_ids.add(iid)
    return {'control_count': len(controls), 'active_question_count': len(active),
            'retired_question_count': len(retired)}


SCHEMA = '''
CREATE TABLE IF NOT EXISTS versions (
    version TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
    kb_sha256 TEXT NOT NULL, document_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS controls (
    version TEXT NOT NULL REFERENCES versions(version), control_id TEXT NOT NULL,
    control_json TEXT NOT NULL, ordinal INTEGER NOT NULL,
    PRIMARY KEY (version, control_id)
);
CREATE TABLE IF NOT EXISTS items (
    version TEXT NOT NULL, item_id TEXT NOT NULL, control_id TEXT NOT NULL,
    item_json TEXT NOT NULL, ordinal INTEGER NOT NULL,
    PRIMARY KEY (version, item_id),
    FOREIGN KEY (version, control_id) REFERENCES controls(version, control_id)
);
CREATE TABLE IF NOT EXISTS retired_items (
    version TEXT NOT NULL REFERENCES versions(version), item_id TEXT NOT NULL,
    item_json TEXT NOT NULL, PRIMARY KEY (version, item_id)
);
'''


class ChecklistStore:
    def __init__(self, database=DEFAULT_DATABASE):
        self.database = Path(database).resolve()

    def _connect(self, create=False):
        if create:
            self.database.parent.mkdir(parents=True, exist_ok=True)
        else:
            require(self.database.is_file(), '체크리스트 DB가 없습니다.', 'STORE_MISSING')
        connection = (sqlite3.connect(str(self.database)) if create else
                      sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True))
        connection.execute('PRAGMA foreign_keys=ON')
        if create:
            connection.executescript(SCHEMA)
        return connection

    def import_draft(self, path):
        require(isinstance(path, (str, Path)), '체크리스트 파일 경로가 필요합니다.')
        path = Path(path).resolve()
        source_bytes = read_bytes(path)
        document = decode_document(source_bytes)
        require(isinstance(document, dict) and isinstance(document.get('source'), dict)
                and nonempty(document['source'].get('path')), 'source.path가 필요합니다.')
        kb_path = (path.parent / document['source']['path']).resolve()
        kb_bytes = read_bytes(kb_path, 'SOURCE_FILE_ERROR')
        counts = validate_document(document, decode_document(kb_bytes), kb_sha256(kb_bytes))
        source_files = {kb_path: kb_bytes, path: source_bytes}
        for source in document['source_documents']:
            source_path = (path.parent / source['path']).resolve()
            raw = read_bytes(source_path, 'SOURCE_FILE_ERROR')
            require(digest_bytes(raw) == source['sha256'], f"{source['source_id']}: 출처 파일 해시가 다릅니다.", 'SOURCE_MISMATCH')
            source_files[source_path] = raw
        # 동일 버전의 내용은 덮어쓰지 않고 새 버전으로 변경을 기록한다.
        version, content_sha = document['draft_version'], digest_bytes(source_bytes)
        with closing(self._connect(create=True)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT source_sha256 FROM versions WHERE version=?', (version,)).fetchone()
            if previous:
                require(previous[0] == content_sha, '같은 버전의 다른 내용입니다. 새 draft_version이 필요합니다.', 'VERSION_CONFLICT')
                inserted = False
            else:
                # 새 버전에서도 기존·퇴역 ID를 다른 질문에 재사용하지 않는다.
                declared = {i['item_id'] for c in document['controls'] for i in c['items']}
                declared.update(i['item_id'] for i in document.get('retired_items', []))
                previous_ids = {row[0] for row in db.execute('SELECT item_id FROM items UNION SELECT item_id FROM retired_items')}
                require(previous_ids <= declared, '이전 문항을 삭제하려면 퇴역 정보와 대체 문항을 보존해야 합니다.', 'ITEM_HISTORY_LOST')
                for item in document.get('retired_items', []):
                    prior = db.execute('SELECT item_json FROM items WHERE item_id=? UNION ALL SELECT item_json FROM retired_items WHERE item_id=?',
                                       (item['item_id'], item['item_id'])).fetchall()
                    require(all(json.loads(row[0])['question'] == item['question'] for row in prior),
                            f"{item['item_id']}: 퇴역한 질문의 내용이 바뀌었습니다.", 'ITEM_ID_REUSED')
                for control in document['controls']:
                    for item in control['items']:
                        prior = db.execute('SELECT item_json FROM items WHERE item_id=?', (item['item_id'],)).fetchall()
                        retired_before = db.execute('SELECT 1 FROM retired_items WHERE item_id=?', (item['item_id'],)).fetchone()
                        require(not retired_before, f"{item['item_id']}: 이전에 퇴역한 ID입니다.", 'RETIRED_ITEM')
                        require(all(json.loads(row[0])['question'] == item['question'] for row in prior),
                                f"{item['item_id']}: 같은 ID의 질문이 변경됐습니다. 새 ID가 필요합니다.", 'ITEM_ID_REUSED')
                db.execute('INSERT INTO versions VALUES (?,?,?,?,?)', (version, content_sha, document['source']['sha256'],
                           json.dumps(document, ensure_ascii=False), datetime.now().astimezone().isoformat()))
                for order, control in enumerate(document['controls']):
                    db.execute('INSERT INTO controls VALUES (?,?,?,?)',
                               (version, control['control_id'], json.dumps(control, ensure_ascii=False), order))
                    for ordinal, item in enumerate(control['items']):
                        db.execute('INSERT INTO items VALUES (?,?,?,?,?)',
                                   (version, item['item_id'], control['control_id'], json.dumps(item, ensure_ascii=False), ordinal))
                for item in document.get('retired_items', []):
                    db.execute('INSERT INTO retired_items VALUES (?,?,?)',
                               (version, item['item_id'], json.dumps(item, ensure_ascii=False)))
                inserted = True
            require(all(read_bytes(p, 'SOURCE_MISMATCH') == raw for p, raw in source_files.items()),
                    '적재 중 원본 파일이 바뀌었습니다.', 'SOURCE_MISMATCH')
        return {'version': version, 'source_sha256': content_sha, 'approved': False,
                'status': document['status'], 'inserted': inserted, **counts}

    def _document(self, db, version):
        require(nonempty(version), '버전은 비어 있지 않은 문자열이어야 합니다.')
        row = db.execute('SELECT document_json, source_sha256 FROM versions WHERE version=?', (version,)).fetchone()
        require(row is not None, f'체크리스트 버전이 없습니다: {version}', 'VERSION_NOT_FOUND')
        return json.loads(row[0]), row[1]

    @staticmethod
    def _review_allowed(allow_draft):
        require(allow_draft is True, '팀 미승인 검수용 KB입니다. 검토 호출에 allow_draft=True를 명시하세요.', 'DRAFT_NOT_APPROVED')

    def get_control(self, version, control_id, *, allow_draft=False):
        require(nonempty(control_id), '통제항목 ID는 비어 있지 않은 문자열이어야 합니다.')
        with closing(self._connect()) as db:
            document, content_sha = self._document(db, version)
            self._review_allowed(allow_draft)
            row = db.execute('SELECT control_json FROM controls WHERE version=? AND control_id=?', (version, control_id)).fetchone()
            require(row is not None, f'초안 범위에 없는 통제항목입니다: {control_id}', 'CONTROL_NOT_FOUND')
            control = json.loads(row[0])
            return {'version': version, 'source_sha256': content_sha, 'source': document['source'],
                    'source_documents': document['source_documents'], 'status': document['status'],
                    'approved': document['approved'], 'proposed_result_values': document['proposed_result_values'],
                    'control': control}

    def get_item(self, version, item_id, *, allow_draft=False):
        require(nonempty(item_id), '문항 ID는 비어 있지 않은 문자열이어야 합니다.')
        with closing(self._connect()) as db:
            document, content_sha = self._document(db, version)
            self._review_allowed(allow_draft)
            retired = db.execute('SELECT item_json FROM retired_items WHERE version=? AND item_id=?', (version, item_id)).fetchone()
            if retired:
                replacements = json.loads(retired[0])['replaced_by']
                raise ChecklistError('RETIRED_ITEM', f'퇴역 문항입니다. 대체 문항: {replacements}')
            row = db.execute('SELECT item_json FROM items WHERE version=? AND item_id=?', (version, item_id)).fetchone()
            require(row is not None, f'문항이 없습니다: {item_id}', 'ITEM_NOT_FOUND')
            return {'version': version, 'source_sha256': content_sha, 'source': document['source'],
                    'source_documents': document['source_documents'], 'status': document['status'],
                    'approved': document['approved'], 'proposed_result_values': document['proposed_result_values'],
                    'item': json.loads(row[0])}

    def list_versions(self):
        with closing(self._connect()) as db:
            return [{'version': row[0], 'source_sha256': row[1], 'kb_sha256': row[2],
                     'status': json.loads(row[3])['status'], 'approved': json.loads(row[3])['approved']}
                    for row in db.execute('SELECT version,source_sha256,kb_sha256,document_json FROM versions ORDER BY imported_at,version')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=DEFAULT_DATABASE)
    sub = parser.add_subparsers(dest='command', required=True)
    upload = sub.add_parser('import-draft')
    upload.add_argument('--draft', type=Path, default=HERE / 'checklist_draft.json')
    query = sub.add_parser('get-control')
    query.add_argument('--version', required=True)
    query.add_argument('--control-id', required=True)
    query.add_argument('--allow-draft', action='store_true')
    item = sub.add_parser('get-item')
    item.add_argument('--version', required=True)
    item.add_argument('--item-id', required=True)
    item.add_argument('--allow-draft', action='store_true')
    sub.add_parser('list-versions')
    args = parser.parse_args()
    store = ChecklistStore(args.database)
    try:
        if args.command == 'import-draft':
            result = store.import_draft(args.draft)
        elif args.command == 'get-control':
            result = store.get_control(args.version, args.control_id, allow_draft=args.allow_draft)
        elif args.command == 'get-item':
            result = store.get_item(args.version, args.item_id, allow_draft=args.allow_draft)
        else:
            result = store.list_versions()
        print(json.dumps({'success': True, 'data': result}, ensure_ascii=False, indent=2))
        return 0
    except (ChecklistError, OSError, sqlite3.Error, json.JSONDecodeError, UnicodeError) as error:
        print(json.dumps({'success': False, 'error': {'code': getattr(error, 'code', 'STORE_ERROR'),
                                                    'message': str(error)}}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
