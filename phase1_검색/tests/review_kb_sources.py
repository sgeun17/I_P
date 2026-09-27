"""Compare all KB entries with the two local guides and the general checklist.

Run with the bundled Python (pypdf/openpyxl), not the search virtualenv.
Produces source traceability and keyword review records; never changes the KB.
"""
import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT.parent.parent / 'ISMP-P 인증기준·제도'
HEADER = re.compile(r'항\s*목\s*(\d\.\d+\.\d+)')


def normalized(text):
    value = unicodedata.normalize('NFKC', text).translate(str.maketrans('･・∙⋅', '····'))
    return re.sub(r'\s+', '', value)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_guide(path):
    from pypdf import PdfReader
    pages = [page.extract_text() or '' for page in PdfReader(path).pages]
    starts, position = [], 0
    for text in pages:
        starts.append(position)
        position += len(text) + 1
    joined = '\n'.join(pages)
    headers = list(HEADER.finditer(joined))
    sections = {}
    for i, match in enumerate(headers):
        cid = match.group(1)
        if cid in sections:
            raise ValueError(f'Duplicate source section {cid}')
        end = headers[i + 1].start() if i + 1 < len(headers) else len(joined)
        text = joined[match.end():end]
        label = re.search(r'인\s*증\s*기\s*준', text)
        checks = re.search(r'주\s*요\s*확\s*인\s*사\s*항', text)
        if not label or not checks or checks.start() < label.end():
            raise ValueError(f'Cannot isolate source requirement {cid}')
        first, last = bisect_right(starts, match.start()), bisect_right(starts, end - 1)
        sections[cid] = {
            'control_name': text[:label.start()].strip(),
            'requirement': text[label.end():checks.start()].strip(),
            'pdf_pages': list(range(first, last + 1)), 'text': text,
            'page_texts': [(n, joined[max(starts[n - 1], match.end()):min(starts[n - 1] + len(pages[n - 1]), end)])
                           for n in range(first, last + 1)],
        }
    return {'path': str(path.relative_to(ROOT.parent.parent)), 'sha256': digest(path),
            'page_count': len(pages), 'sections': sections}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=ROOT / 'reports' / ('kb_source_review_' + datetime.now().strftime('%Y-%m-%d')))
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if out.exists():
        raise ValueError('Use a new output directory to preserve earlier reports')
    kb_path = ROOT / 'controls.json'
    before = digest(kb_path)
    controls = json.loads(kb_path.read_text(encoding='utf-8'))
    guides = {}
    for sid, name in [('KISA-2023', 'ISMS-P 인증기준 안내서(2023.11.23).pdf'),
                      ('FSI-2023', '금융보안원 - 금융권에 적합한 ISMS-P 인증기준 점검항목 안내서(2023.12.).pdf')]:
        print(f'Extracting {sid}...', flush=True)
        guides[sid] = read_guide(SOURCES / name)
    import openpyxl
    excel_path = SOURCES / 'ISMS-P 인증기준 점검항목.xlsx'
    workbook = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)
    excel = {row[0]: {'control_name': row[2], 'requirement': row[3], 'row': n}
             for n, row in enumerate(workbook.active.values, 1)
             if isinstance(row[0], str) and re.fullmatch(r'\d\.\d+\.\d+', row[0])}
    workbook.close()
    ids = {c['control_id'] for c in controls}
    assert len(ids) == len(controls) == 101
    assert all(set(g['sections']) == ids for g in guides.values())
    assert set(excel) == ids
    rows, keywords, differences = [], [], []
    for control in controls:
        cid = control['control_id']
        sources = {}
        for sid, guide in guides.items():
            section = guide['sections'][cid]
            name_match = normalized(control['control_name']) == normalized(section['control_name'])
            requirement_match = normalized(control['requirement']) == normalized(section['requirement'])
            sources[sid] = {'pdf_pages': section['pdf_pages'], 'name_match': name_match,
                            'requirement_match': requirement_match,
                            'extracted_name': section['control_name'],
                            'extracted_requirement': section['requirement']}
            if not name_match or not requirement_match:
                differences.append({'control_id': cid, 'source_id': sid,
                                    'name_match': name_match, 'requirement_match': requirement_match})
        source_excel = excel[cid]
        sources['GENERAL-XLSX'] = {'row': source_excel['row'],
            'name_match': normalized(control['control_name']) == normalized(source_excel['control_name']),
            'requirement_match': normalized(control['requirement']) == normalized(source_excel['requirement']),
            'extracted_name': source_excel['control_name'], 'extracted_requirement': source_excel['requirement']}
        if not sources['GENERAL-XLSX']['name_match'] or not sources['GENERAL-XLSX']['requirement_match']:
            differences.append({'control_id': cid, 'source_id': 'GENERAL-XLSX', **sources['GENERAL-XLSX']})
        evidence = []
        general = guides['KISA-2023']['sections'][cid]
        for value in control['evidence_examples']:
            found = normalized(value) in normalized(general['text'])
            pages = [n for n, t in general['page_texts'] if normalized(value) in normalized(t)]
            evidence.append({'text': value, 'matched_in_general_section': found,
                             'pdf_pages': pages, 'section_pdf_pages': general['pdf_pages']})
            if not found:
                differences.append({'control_id': cid, 'field': 'evidence_examples', 'text': value})
        for value in control['keyword']:
            matches = {sid: [n for n, t in guide['sections'][cid]['page_texts']
                             if normalized(value) in normalized(t)] for sid, guide in guides.items()}
            direct = any(normalized(value) in normalized(g['sections'][cid]['text']) for g in guides.values())
            keywords.append({'control_id': cid, 'keyword': value,
                             'classification': 'SOURCE_TEXT_MATCH' if direct else 'SEARCH_EXPANSION_REVIEW_NEEDED',
                             'source_pdf_pages': matches})
        rows.append({'control_id': cid, 'control_name': control['control_name'],
                     'source_comparisons': sources, 'evidence_examples': evidence})
    assert digest(kb_path) == before, 'KB changed during comparison'
    counts = Counter(k['classification'] for k in keywords)
    report = {'status': 'PASS' if not differences else 'REVIEW_NEEDED',
        'review_type': 'AUTOMATED_LOCAL_SOURCE_COMPARISON',
        'checked_at': datetime.now().astimezone().isoformat(), 'kb_sha256': before,
        'control_count': len(rows), 'evidence_example_count': sum(len(r['evidence_examples']) for r in rows),
        'keyword_count': len(keywords), 'keyword_classifications': dict(counts),
        'sources': {sid: {k: v for k, v in guide.items() if k != 'sections'} for sid, guide in guides.items()},
        'general_checklist': {'path': str(excel_path.relative_to(ROOT.parent.parent)), 'sha256': digest(excel_path)},
        'differences': differences, 'controls': rows,
        'human_review_complete': False, 'latest_regulation_review_complete': False,
        'limits': ['보유 원문의 문자열 일치 비교. 의미·적용 범위·최종 승인은 별도 확인.',
                   '검색 확장어의 원문 미일치는 오류 판정이 아니며 의미 검토가 필요함.',
                   '금융권 추가 점검사항 전체를 KB에 구현했다는 뜻이 아님.']}
    out.mkdir(parents=True)
    (out / 'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'keyword_review.json').write_text(json.dumps({'kb_sha256': before, 'keywords': keywords}, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Phase1 검색 KB 전체 원문 대조', '',
             f"- 결과: {report['status']}. 통제항목 {len(rows)}개, 증거자료 예시 {report['evidence_example_count']}개.",
             '- 일반·금융 안내서의 같은 ID 절에서 명칭과 인증기준을 대조하고, 일반 점검항목 XLSX도 교차 확인했다.',
             '- 비교에서는 공백과 유니코드 표기 차이만 정규화했다. 요구사항·증거 예시·키워드는 변경하지 않았다.',
             f'- KB SHA-256: {before}', f'- 검색 키워드 분류: {dict(counts)}', '',
             '| ID | 항목 | 일반 PDF | 금융 PDF | XLSX | 증거 예시 |',
             '|---|---|---|---|---|---|']
    for row in rows:
        c = row['source_comparisons']
        flags = ['일치' if c[s]['name_match'] and c[s]['requirement_match'] else '확인 필요' for s in ('KISA-2023', 'FSI-2023', 'GENERAL-XLSX')]
        evidence_count = sum(e['matched_in_general_section'] for e in row['evidence_examples'])
        lines.append(f"| {row['control_id']} | {row['control_name']} | {' | '.join(flags)} | {evidence_count}/{len(row['evidence_examples'])} |")
    lines += ['', '## 별도 검토할 차이', '', *[f'- {json.dumps(d, ensure_ascii=False)}' for d in differences],
              '', '## 남은 확인', '', *[f'- {limit}' for limit in report['limits']],
              '- 원문에 그대로 없는 키워드는 keyword_review.json에서 담당자가 적절성을 확인한다.',
              '- human_review_complete=false. 자동 대조를 사람의 독립 내용 검수로 표시하지 않는다.']
    (out / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('status', 'control_count', 'evidence_example_count', 'keyword_classifications')}, ensure_ascii=False))
    print(f'Differences: {len(differences)}; output: {out}', flush=True)


if __name__ == '__main__':
    main()
