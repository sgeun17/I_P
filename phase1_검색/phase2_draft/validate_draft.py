"""Validate draft references and provenance; optionally compare local PDF text.

Uses the standard library by default; --verify-pdf additionally needs pypdf.
Never edits the KB, questions, source documents, or operational index.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(value):
    return re.sub(r'\s+', '', value).replace('･', '·')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-pdf', action='store_true')
    args = parser.parse_args()
    draft_path = HERE / 'checklist_draft.json'
    d = load(draft_path)
    kb_path = (HERE / d['source']['path']).resolve()
    kb = {c['control_id']: c for c in load(kb_path)}
    baseline = load(HERE.parent / 'reports/source_review_2026-09-27/checklist_before_review.json')
    old = {i['item_id']: i for c in baseline['controls'] for i in c['items']}
    items = [i for c in d['controls'] for i in c['items']]
    ids = [i['item_id'] for i in items]
    active = dict(zip(ids, items))
    retired = {i['item_id']: i for i in d['retired_items']}
    docs = {x['source_id']: x for x in d['source_documents']}
    checks = []

    def check(name, passed):
        checks.append({'check': name, 'passed': bool(passed)})

    check('원본 KB 해시 일치', digest(kb_path) == d['source']['sha256'])
    check('팀 미승인 초안', d['approved'] is False and d['status'] == 'DRAFT_FOR_TEAM_REVIEW')
    check('활성 ID 중복 없음', len(ids) == len(set(ids)))
    check('질문 문장 완전 중복 없음', len({i['question'] for i in items}) == len(items))
    check('기존 ID 보존 및 활성/퇴역 분리', set(old).issubset(set(active) | set(retired)) and not set(active) & set(retired))
    check('퇴역 ID 재사용 없음', set(retired).issubset(old) and all(retired[k]['question'] == old[k]['question'] for k in retired))
    check('퇴역 질문 대체 참조 유효', all(i['replaced_by'] and set(i['replaced_by']).issubset(active) for i in retired.values()))
    check('선정 후보 ID 중복 없음 및 KB 연결', len({c['control_id'] for c in d['priority_candidates']}) == len(d['priority_candidates']) and all(c['control_id'] in kb and c['control_name'] == kb[c['control_id']]['control_name'] for c in d['priority_candidates']))
    review_text = (HERE / 'checklist_review.md').read_text(encoding='utf-8')
    check('활성 질문 문서와 JSON 동기화', all(f"| {i['item_id']} | {i['question']} |" in review_text for i in items))
    check('요약 수량 일치', d['review_summary']['active_question_count'] == len(items) and d['review_summary']['retired_question_count'] == len(retired) and d['review_summary']['new_question_count'] == len(set(active)-set(old)))
    check('KB 내용 및 통제항목 연결', all(c['control_id'] in kb and c['control_name'] == kb[c['control_id']]['control_name'] and c['requirement'] == kb[c['control_id']]['requirement'] and all(i['control_id'] == c['control_id'] and i['item_id'].startswith(c['control_id']+'-Q') for i in c['items']) for c in d['controls']))
    check('critical 미정 및 원문 대조 초안 표시', all(i['critical'] is None and i['critical_status'] == 'UNDECIDED' and i['review_status'] == 'SOURCE_REVIEWED_DRAFT' for i in items))
    fields = ['candidate_evidence','required_context','met','not_met','unknown','citation_required_for','scope_rule']
    check('판정 근거·컨텍스트·인용 필드', all(all(i['evidence_rule'].get(f) for f in fields) and i['evidence_rule']['citation_required_for'] == ['MET','NOT_MET'] for i in items))
    check('출처 파일 및 해시', all((HERE / doc['path']).is_file() and digest(HERE / doc['path']) == doc['sha256'] for doc in docs.values()))
    check('출처 ID 및 쪽수 참조', all(i['source_refs'] and all(r['source_id'] in docs and isinstance(r['pdf_page'],int) and r['pdf_page'] > 0 and r['printed_page'] > 0 for r in i['source_refs']) for i in items))
    examples = load(HERE / 'review_examples.json')
    check('기존 합성 예시 의미·참조 보존', len(examples['cases']) == 9 and all(x['item_id'] in active and active[x['item_id']]['question'] == old[x['item_id']]['question'] and x['expected_result_draft'] in d['proposed_result_values'] for x in examples['cases']))
    check('검토 보고서 존재', (HERE / d['review_summary']['report']).is_file())
    if args.verify_pdf:
        from pypdf import PdfReader
        readers = {sid: PdfReader(HERE / doc['path']) for sid,doc in docs.items()}
        text_cache = {}

        def page(sid, n):
            key = sid,n
            if key not in text_cache:
                text_cache[key] = normalized(readers[sid].pages[n-1].extract_text() or '')
            return text_cache[key]

        check('54개 활성 질문 source_clause가 참조 원문에 있음', all(any(normalized(i['source_clause']) in page(r['source_id'], r['pdf_page']) for r in i['source_refs']) for i in items))
        comparison = load(HERE.parent / 'reports/source_review_2026-09-27/kb_comparison.json')
        for c in comparison['controls']:
            cid = c['control_id']
            for sid, key in [('KISA-2023','general_pdf_pages'),('FSI-2023','financial_pdf_pages')]:
                text = ''.join(page(sid,n) for n in c[key])
                check(f'{cid} {sid} 요구사항 원문 일치', normalized(kb[cid]['requirement']) in text)
            text = ''.join(page('KISA-2023',n) for n in c['general_pdf_pages'])
            check(f'{cid} 증거 예시 전체 원문 일치', all(normalized(s) in text for s in kb[cid]['evidence_examples']))
    report = {
        'status': 'PASS' if all(c['passed'] for c in checks) else 'FAIL',
        'checked_at': datetime.now().astimezone().isoformat(),
        'draft_version': d['draft_version'], 'draft_sha256': digest(draft_path),
        'kb_sha256': digest(kb_path), 'candidate_count': len(d['priority_candidates']),
        'sample_control_count': len(d['controls']), 'original_question_count': len(old),
        'active_question_count': len(items), 'retired_question_count': len(retired),
        'new_question_count': len(set(active)-set(old)),
        'synthetic_example_count': len(examples['cases']), 'pdf_text_verified': args.verify_pdf,
        'checks': checks,
        'limits': ['보유 원문 대조 및 구조·참조 검증. 팀 승인 또는 최신 법규 확인 아님.', '세부 설명 전체·금융권 추가요건을 모두 구현한 체크리스트 아님.', '실제 증적·LLM·통합 테스트 미실행. 합성 예시는 실행 결과 아님.']
    }
    (HERE / 'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'checks':len(checks),'failed':[c['check'] for c in checks if not c['passed']],'active_questions':len(items),'pdf_text_verified':args.verify_pdf},ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
