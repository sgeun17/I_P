"""Freeze 24 new source-grounded boundary labels before searching them."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source = ROOT / 'tests/keyword_boundary_cases_2026-09-27.json'
    original = ROOT / 'tests/source_reviewed_cases_2026-09-27.json'
    target = ROOT / 'tests/source_reviewed_keyword_cases_2026-09-27.json'
    if target.exists():
        raise ValueError('Do not overwrite frozen boundary labels')
    base = json.loads(original.read_text(encoding='utf-8'))
    boundary = json.loads(source.read_text(encoding='utf-8'))
    kb = {c['control_id']: c for c in json.loads((ROOT / 'controls.json').read_text(encoding='utf-8'))}
    comparisons = json.loads((ROOT / 'reports/kb_source_review_2026-09-27_v2/comparison.json').read_text(encoding='utf-8'))
    refs = {c['control_id']: c['source_comparisons']['KISA-2023'] for c in comparisons['controls']}
    new_cases = []
    for case in boundary['cases']:
        category = 'RELATED' if case['expected'] else 'NOT_RELATED' if case['kind'] == 'unrelated' else 'INSUFFICIENT_INFORMATION'
        references = [{'control_id': cid, 'control_name': kb[cid]['control_name'],
                       'source_id': 'KISA-2023', 'pdf_pages': refs[cid]['pdf_pages'],
                       'requirement': kb[cid]['requirement']} for cid in case['expected']]
        new_cases.append({**case, 'origin_dataset': source.name, 'synthetic': True,
                          'review_category': category, 'required_control_ids': case['expected'],
                          'review_reason': case['rationale'], 'source_references': references,
                          'labels_exhaustive': not bool(case['expected']),
                          'human_review': {'status': 'PENDING', 'reviewer': None, 'approved_at': None}})
    assert len(new_cases) == 24
    merged = base['cases'] + new_cases
    assert len(merged) == len({c['id'] for c in merged}) == 54
    result = {**base, 'version': 'source-keyword-boundary-review-2026-09-27',
              'source_dataset_sha256': {**base['source_dataset_sha256'], source.name: sha(source)},
              'base_review_sha256': sha(original), 'cases': merged,
              'label_changes': [], 'new_boundary_case_count': 24,
              'limits': [x.replace('30개', '54개') for x in base['limits']]}
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert result['cases'][:30] == base['cases']
    print(f'PASS: 30 original + 24 new cases frozen; no prior label changes; {target}')


if __name__ == '__main__':
    main()
