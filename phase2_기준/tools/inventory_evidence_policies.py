"""Collect literal criteria for policy review; never invent runtime policy values."""
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parents[1]
TEMPORAL = re.compile(r'주기|정기|매년|연\s*\d|월\s*\d|\d+\s*(?:일|개월|년)|기한|보유기간|보존기간|유효기간|지체\s*없이|즉시')


def inventory(document):
    rows = []
    for control in document['controls']:
        for item in control['items']:
            hints = []
            for field in ('source_clause', 'question', 'applicability_condition'):
                text = item.get(field, '')
                if TEMPORAL.search(text):
                    hints.append({'field': field, 'text': text})
            rows.append({
                'control_id': control['control_id'], 'item_id': item['item_id'],
                'question': item['question'], 'check_kind': item['check_kind'],
                'source_refs': item['source_refs'],
                'candidate_evidence': item['evidence_rule']['candidate_evidence'],
                'temporal_wording': hints,
                'policy_status': 'NEEDS_POLICY_DECISION',
            })
    return {
        'checked_at': '2026-10-05', 'checklist_version': document['draft_version'],
        'checklist_approved': document['approved'], 'question_count': len(rows),
        'temporal_wording_question_count': sum(bool(r['temporal_wording']) for r in rows),
        'runtime_policies_exported': False,
        'limitations': [
            '문구 검색 결과이며 최신성 기준이 필요한 문항 전체를 확정한 분류가 아니다.',
            '점검 주기·보존 기간·사건 기한을 문서 허용 경과 개월 수로 자동 변환하지 않는다.',
            'candidate_evidence는 증적 내용 종류이며 allowed_types 파일 확장자가 아니다.',
            'freshness.max_age_months/date_label, 실행 as_of, allowed_types 및 예외 처리는 별도 결정이 필요하다.',
        ], 'items': rows,
    }


if __name__ == '__main__':
    source = HERE / 'full_checklist_draft.json'
    result = inventory(json.loads(source.read_text(encoding='utf-8')))
    result['checklist_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    output = HERE / 'reports/criteria_policy_readiness_2026-10-05.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({k:v for k,v in result.items() if k!='items'}, ensure_ascii=False))
