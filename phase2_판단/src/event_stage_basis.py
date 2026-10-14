"""Narrow report-to-deliberation citation diagnostic, never a verdict.

Passing this check does not certify subject, period or sufficiency. Unrecognised
phrasing is left to normal semantic review. Full context is not new evidence.
"""
import re

_ACTUAL = re.compile(r'결과\s*[:：][^\n|]{0,30}(?:의결|가결|부결)|'
    r'(?:심의|의결)(?:를|을)?\s*(?:완료|하였|했다|하였음)|'
    r'안건[^\n]{0,70}(?:원안\s*의결|가결|부결)')


def actual_event_locations(item, context):
    if (item.get('check_kind') not in {'implementation', 'record'}
            or not re.search(r'심의|의결', str(item.get('question', '')))):
        return []
    return [{'chunk_id': c['chunk_id'], 'source_line': line}
        for c in (context or {}).get('chunks', [])
        for line in str(c.get('text', '')).splitlines() if _ACTUAL.search(line)]


def event_stage_basis_gap(item, output, context):
    if (output.get('result') != 'MET' or output.get('error_code')
            or item.get('check_kind') not in {'implementation', 'record'}
            or not re.search(r'심의|의결', str(item.get('question', '')))):
        return None
    quotes = '\n'.join(str(c.get('quote', '')) for c in output.get('citations', []))
    if not quotes.strip() or not re.search(r'보고|보고자', quotes) or _ACTUAL.search(quotes):
        return None
    # Do not propose citations from model-created strings. These locations copy
    # actual input lines, and remain hints until subject/event binding is reviewed.
    locations = actual_event_locations(item, context)
    return {'version': 'phase2_event_stage_basis_v1',
        'code': 'REPORT_CITATION_AS_DELIBERATION',
        'scope': 'selected_citations_not_whole_document_absence',
        'candidate_source_locations': locations,
        'requires_source_review': True, 'safe_as_sole_verdict_rule': False,
        'explanation': '선택 인용은 보고 안건이며 실제 심의·의결의 연결을 직접 보여 주지 않습니다. 전체 원문에서 동일 위원회·사건의 실제 심의 근거를 대조해야 합니다.'}
