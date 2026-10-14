"""Narrow public-claim guard: a policy revision is not revision-history management.

This module never decides relevance, selects a primary, or removes a mapping.
It checks affirmative history-management claims against citation-bound source
records. A finding requests the existing explanation repair/review path; no
finding is not a general proof of factual correctness or compliance.
"""
from __future__ import annotations

import re


CODE = 'POLICY_REVISION_HISTORY_CLAIM_UNSUPPORTED'
_HISTORY = re.compile(r'(?:개정|변경|버전|문서)?이력(?:관리)?|(?:개정|변경)내역', re.I)
_POLICY = re.compile(r'정책|처리방침|지침|규정|시행문서|절차서|매뉴얼|policy', re.I)
_ASSERTION = re.compile(
    r'수행|완료|실시|기록|보관|보존|운영|갱신|작성|유지|수립|구축|'
    r'관리(?:하|되|한|됨|중)|확인(?:되|됨)|나타|포함|명시', re.I)
_QUALIFIED = re.compile(
    r'미확인|미수행|미실시|미작성|미관리|미보관|미보존|미구축|미흡|누락|불분명|'
    r'확인(?:되지|되지않|할수없|불가|이어렵)|'
    r'(?:하지|되지|되어있지|돼있지|하지는|되지는)않|안(?:했|하|되|됨)|못(?:했|하|하였)|'
    r'(?:근거|기록|내역|증거|내용|절차|이력)(?:가|이|는|은)?(?:없|부족)|'
    r'(?:원문|증적|문서)(?:에|에서)?없|(?:보기|확인하기|판단하기)어렵|단정할수없|입증되지|'
    r'필요(?:하다|하다고|함|합니다|가)|확인필요|검토필요|예정|'
    r'계획(?:이다|이|하|된|중|임)|(?:수행|관리|실시|작성)계획|'
    r'권고|요구(?:한다|된다|됨|하|된)|해야|하여야|여부', re.I)
_CLAUSE_BREAK = re.compile(
    r'[\n.!?;,]+|(?:하지만|그러나|다만|반면)\s*|(?<=지만)\s*|(?<=으나)\s*')


def _compact(text):
    return re.sub(r'\s+', '', str(text or ''))


def _clauses(text):
    return [_compact(part) for part in _CLAUSE_BREAK.split(str(text or '')) if part.strip()]


def _affirmative_history(clause):
    """Require an asserted activity/record, not a topic, question, or qualification."""
    for match in _HISTORY.finditer(clause):
        tail = clause[match.end():]
        # Negation in the same activity clause qualifies the assertion. A
        # separate positive clause is still inspected independently.
        if not _QUALIFIED.search(clause) and _ASSERTION.search(tail):
            return True
    return False


def _ledger_record(text, heading):
    """Accept a real revision ledger row; its heading alone is never evidence."""
    combined = _compact(heading) + text
    if not (_POLICY.search(combined) and _HISTORY.search(combined)):
        return False
    if _QUALIFIED.search(text):
        return False
    # Values are required for each field; empty labels or a described future
    # ledger do not establish an existing version/change record.
    fields = {}
    for piece in text.split('|'):
        if ':' in piece:
            key, value = piece.split(':', 1)
            if value and value not in {'-', '없음', '미작성', '미확인'}:
                fields[key] = value
    return (any(re.search(r'버전|version|차수', key, re.I) for key in fields)
            and any(re.search(r'개정일|변경일|일자|날짜', key) for key in fields)
            and any(re.search(r'개정내용|변경내용|변경사항|개정사항', key) for key in fields))


def _source_supports_history(evidence_rows):
    for row in evidence_rows or []:
        if isinstance(row, str):
            row = {'text': row}
        if not isinstance(row, dict) or row.get('field', 'text') != 'text':
            continue
        heading = row.get('heading', '')
        for clause in _clauses(row.get('text', '')):
            if (_POLICY.search(_compact(heading) + clause) and _affirmative_history(clause)):
                return True
        for line in str(row.get('text', '')).splitlines():
            if _ledger_record(_compact(line), heading):
                return True
    return False


def policy_revision_claim_codes(control_id, reason, evidence_rows):
    """Return only the claim-scope blocker, using the caller's bound evidence.

    Negated, uncertain, recommended, or future history management is not an
    assertion of performance. A revision-only claim remains unaffected. Source
    plans/requirements do not prove that history management was performed.
    """
    if control_id != '2.1.1' or not any(_affirmative_history(c) for c in _clauses(reason)):
        return []
    return [] if _source_supports_history(evidence_rows) else [CODE]


def _value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def policy_revision_findings(mapping_input, output):
    """Inspect retained public explanations without modifying either argument.

    Only exact citations in supplied body text are evidence. Chunk headings can
    identify a policy-history table but cannot substitute for its body record.
    Finding metadata intentionally contains no replacement decision or mapping.
    """
    chunks = {_value(c, 'chunk_id'): c for c in _value(mapping_input, 'chunks', [])}
    candidates = {_value(c, 'control_id'): c for c in _value(output, 'candidate_decisions', [])}
    for mapping in _value(output, 'mapped_controls', []):
        if _value(mapping, 'control_id') != '2.1.1':
            continue
        for claim in (mapping, candidates.get('2.1.1')):
            if claim is None:
                continue
            evidence = []
            for citation in _value(claim, 'citations', []):
                chunk = chunks.get(_value(citation, 'chunk_id'))
                quote = _value(citation, 'quote', '')
                if chunk is not None and quote and quote in _value(chunk, 'text', ''):
                    evidence.append({'field': 'text', 'text': quote, 'heading': _value(chunk, 'heading', '')})
            if policy_revision_claim_codes('2.1.1', _value(claim, 'reason', ''), evidence):
                return [{'control_id': '2.1.1', 'code': CODE}]
    return []
