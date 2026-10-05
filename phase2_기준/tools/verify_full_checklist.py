"""Verify all-chapter source coverage and review wiring, without LLM calls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from checklist_store import ChecklistStore
from reason_codes import ReasonCatalog
from judgment_review import check_review_output
from tools.verify_chapter2_review_flow import make_request, fixed_response


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def normalized(text):
    return re.sub(r'\s+', '', text).replace('･','·')


def verify(verify_pdf=False):
    full = HERE/'full_checklist_draft.json'
    catalog_path = HERE/'full_reason_codes_draft.json'
    data = read(full)
    catalog = ReasonCatalog(catalog_path)
    old = read(HERE/'chapter2_full_checklist_draft.json')
    for c in old['controls']:
        for i in c['items']:
            i['critical'] = c['control_id'].rsplit('.',1)[0] in data['critical_policy']['critical_control_groups']
            i['critical_status'] = 'CONFIRMED_BY_OWNER'
            if i['item_id'] == '2.10.8-Q05':
                current = next(x for c in data['controls'] for x in c['items'] if x['item_id'] == i['item_id'])
                for key in ('evidence_rule', 'review_note'):
                    i[key] = current[key]
    assert [c for c in data['controls'] if c['control_id'].startswith('2.')] == old['controls']
    new = [i for c in data['controls'] if not c['control_id'].startswith('2.') for i in c['items']]
    inventory = read(HERE/'drafts/chapters13/source_inventory.json')
    coverage = read(HERE/'drafts/chapters13/coverage.json')
    assert {(c['control_id'],c['major_check']) for c in coverage} == {
        (c['control_id'], n) for c in inventory for n in range(1,len(c['checks'])+1)}
    clause_map = {(c['control_id'],n): clause for c in inventory for n,clause in enumerate(c['checks'],1)}
    items = {i['item_id']:i for i in new}
    for row in coverage:
        for iid in row['item_ids']:
            assert items[iid]['source_clause'] == clause_map[row['control_id'],row['major_check']]
    if verify_pdf:
        from pypdf import PdfReader
        doc = next(x for x in data['source_documents'] if x['source_id']=='KISA-2023')
        pdf = HERE/doc['path']
        assert hashlib.sha256(pdf.read_bytes()).hexdigest()==doc['sha256']
        reader = PdfReader(pdf)
        page_text = {}
        for item in new:
            page = item['source_refs'][0]['pdf_page']
            if page not in page_text:
                page_text[page] = normalized(reader.pages[page-1].extract_text() or '')
            assert normalized(item['source_clause']) in page_text[page], item['item_id']
    control_results = []
    with TemporaryDirectory(prefix='full-review-') as directory:
        store = ChecklistStore(Path(directory)/'review.sqlite3')
        store.import_draft(full)
        for c in data['controls']:
            req = make_request(c,data,store,catalog,'검토 자료 미제출')
            checked = check_review_output(req,fixed_response(req),store,catalog=catalog,allow_draft=True)
            assert checked['validation']['passed'], c['control_id']
            control_results.append({'control_id':c['control_id'],'questions':len(req.questions),'passed':True})
    return {
        'status':'PASS','checked_at':'2026-10-05','approved':False,
        'checklist_approved':data['approved'],
        'checklist_version':data['draft_version'],
        'checklist_sha256':hashlib.sha256(full.read_bytes()).hexdigest(),
        'catalog_sha256':hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        'control_count':len(control_results),'question_count':sum(c['questions'] for c in control_results),
        'new_question_count':len(new),'new_major_check_count':len(coverage),
        'questions_per_chapter':dict(Counter(i['control_id'].split('.')[0] for c in data['controls'] for i in c['items'])),
        'chapter2_content_preserved_except_critical_and_q05_exception':True,'source_pdf_verified':verify_pdf,
        'llm_executed':False,'actual_evidence_used':False,'semantic_judgment_checked':False,
        'controls':control_results,
        'limits':['원문과의 연결·구조 검사이며 현행 법령 검증이나 팀 승인이 아니다.',
                  '자료 부족 UNKNOWN 고정 응답으로 전체 연결을 확인했다. 모델 정확도 검증이 아니다.',
                  '1·3장 주요 확인사항의 1차 분해. 세부 설명 전체 및 복수 요건의 추가 원자화 검토가 남아 있다.'],
    }


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-pdf',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and (args.output.resolve().parent!=(HERE/'reports').resolve() or args.output.exists()):
        parser.error('reports 아래 새 파일만 지정할 수 있습니다.')
    report=verify(args.verify_pdf)
    if args.output:
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='controls'},ensure_ascii=False))
