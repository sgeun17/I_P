"""Build the 1/2/3 chapter draft from reviewed questions; preserve chapter 2 exactly.

No model calls, approval, database writes or changes to other teams.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
WORK = HERE / 'drafts/chapters13'
VERSION = 'phase2-checklist-full-draft-2026-10-03-r1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build():
    parent = HERE / 'chapter2_full_checklist_draft.json'
    old = read(parent)
    kb = read(HERE.parent / 'phase1_검색/controls.json')
    master = {c['control_id']: c for c in kb}
    inventory = {c['control_id']: c for c in read(WORK / 'source_inventory.json')}
    additions = {}
    coverage = {}
    common = old['controls'][0]['items'][0]['evidence_rule']
    kinds = {'p': 'procedure', 'i': 'implementation', 'r': 'record'}
    proof = {
        'p': '대상·범위·책임·수행 방법이 확인되는 효력 있는 정책 또는 절차',
        'i': '해당 사건의 실제 설정·처리 내역·점검 결과 등 이행을 확인할 자료',
        'r': '해당 대상·사건·기간에 연결되는 승인·검토·통지·처리 기록',
    }
    for line in (WORK / 'questions.txt').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        cid, index, kind, question, condition = line.split('|')
        index = int(index)
        source = inventory[cid]
        clause = source['checks'][index - 1]
        control = additions.setdefault(cid, {
            **{k: master[cid][k] for k in ('control_id', 'control_name', 'requirement')},
            'coverage_status': 'SOURCE_REVIEWED_DRAFT_NOT_APPROVED', 'items': [],
        })
        iid = f'{cid}-Q{len(control["items"])+1:02d}'
        rule = {
            'candidate_evidence': [proof[kind], *master[cid]['evidence_examples']],
            'required_context': [
                '검토 대상 조직·서비스·처리 활동 또는 자산의 식별 정보',
                '검토 대상 기간·사건과 이행 또는 검토 시기',
                '증거의 출처·위치 및 동일 대상·기간·사건 연결 정보',
                '해당 시점에 유효한 기준·정책·법적 근거 및 예외 적용 여부',
            ],
            'met': f'「{question}」에 대해 동일 대상·기간의 직접 근거로 질문의 요건 이행을 확인한다. '
                   + {'p': '절차의 유효한 본문에서 요구 내용이 명시되어야 한다.',
                      'i': '계획·절차의 존재만으로 이행을 인정하지 않고 실제 실행 근거를 확인한다.',
                      'r': '기록의 본문·대상·시점이 해당 요구사항과 연결되어야 한다.'}[kind],
            'not_met': f'「{question}」의 적용 의무와 이행 시기가 확인된 상태에서, '
                       + {'p': '요구 절차를 수립하지 않았다는 공식 확인 또는 요건에 어긋나는 유효한 절차 본문이 직접 확인됨.',
                          'i': '요구 조치를 미수행했거나 확인된 허용 범위를 위반한 실제 처리·설정·조사 결과가 직접 확인됨.',
                          'r': '요구 기록·검토·승인 등을 생성 또는 수행하지 않았거나 필수 내용을 누락한 사실이 직접 확인됨.'}[kind]
                       + ' 단순 미제출·검색 실패·다른 대상의 사례만으로 미충족을 확정하지 않는다.',
            'unknown': common['unknown'],
            'citation_required_for': ['MET', 'NOT_MET'],
            'scope_rule': common['scope_rule'] + ' 법령의 근거·고지사항·기한·예외는 검토 시점에 적용되는 확인된 기준을 사용하며 내부 정책으로 법적 요건을 대체하지 않는다.',
        }
        if cid.startswith('3.'):
            rule['required_context'] += [
                '개인정보 종류·수집 경로·처리 목적·법적 근거·수신자 또는 수탁자 등 해당 처리 범위',
                '공공기관 여부·정보주체 연령·처리 규모 등 해당 의무의 적용 요건',
            ]
            rule['unknown'] += ' 적용 법령의 유효한 버전·의무·예외가 확인되지 않으면 법적 적합성을 추정하지 않고 UNKNOWN으로 남긴다.'
        item = {
            'item_id': iid, 'control_id': cid, 'source_clause': clause,
            'question': question, 'check_kind': kinds[kind], 'critical': None,
            'critical_status': 'UNDECIDED', 'evidence_rule': rule,
            'review_status': 'SOURCE_REVIEWED_DRAFT',
            'source_refs': [{'source_id': 'KISA-2023', 'pdf_page': source['pdf_page'],
                             'printed_page': source['pdf_page']-4, 'section': '주요 확인사항'}],
            'review_note': f'보유 2023년 안내서 주요 확인사항 {index}번을 질문 단위로 정리한 초안. '
                           '세부 설명 전체·현행 법령·실증적·사람 승인은 미검증. 개인정보 원문 대신 필요한 범위의 가명·마스킹 자료로 확인한다.',
        }
        if condition != '-':
            item['applicability_condition'] = condition
            rule['required_context'].append('적용 조건의 발생 여부와 의무·기한·예외를 확인하는 자료')
        control['items'].append(item)
        coverage.setdefault((cid,index), []).append(iid)
    expected = {(cid,i) for cid,s in inventory.items() for i in range(1,len(s['checks'])+1)}
    assert set(coverage) == expected, expected-set(coverage)
    assert set(additions) == {cid for cid in master if not cid.startswith('2.')}
    full = deepcopy(old)
    full['draft_version'] = VERSION
    full['controls'] = sorted([*additions.values(), *deepcopy(old['controls'])],
                              key=lambda c: tuple(map(int,c['control_id'].split('.'))))
    total = sum(len(c['items']) for c in full['controls'])
    added = total - 700
    full['source']['basis'] = '보유 2023년 KISA 안내서 기반 1·2·3장 전체 초안. 2장 r3의 700문항을 보존하고 1·3장 주요 확인사항 133개를 분해했다. 최신 법규 확인·팀 승인 결과가 아니다.'
    full['scope'] = {
        'wbs_date': '2026-10-03', 'candidate_count': 101, 'sample_control_count': 101,
        'draft_control_ids': [c['control_id'] for c in full['controls']],
        'remaining_control_count': 0,
        'selection_basis': '사용자 요청으로 1장 관리체계 수립 및 운영과 3장 개인정보 처리 단계별 요구사항까지 확장.',
        'coverage': '통제항목 101개 포함. 1·3장은 주요 확인사항을 중심으로 한 1차 초안이며 세부 설명·법령상 조건 전체의 원자화 또는 운영 승인을 의미하지 않는다.',
    }
    full['parent_draft'] = {'path': parent.name, 'version': old['draft_version'], 'sha256': sha(parent)}
    full['review_summary'] = {
        'reviewed_at': '2026-10-03', 'reviewer_type': 'AI_ASSISTED_SOURCE_REVIEW',
        'control_count': 101, 'active_question_count': total,
        'new_control_count': 37, 'new_question_count': added,
        'preserved_control_count': 64, 'preserved_question_count': 700,
        'retired_question_count': len(old['retired_items']),
        'new_major_check_count': 133, 'team_approved': False,
        'source_pdf_pages_reviewed': sorted({s['pdf_page'] for s in inventory.values()}),
        'report': 'docs/full_checklist_review.md',
    }
    full['integration_state'] = {
        'review_only': True, 'human_approved': False, 'llm_executed': False,
        'actual_evidence_used': False, 'default_checklist_replaced': False,
        'next_steps': ['신규 문항 의미·적용 조건 검수',
                       '판단팀 실행 시 전체 체크리스트·사유 코드 쌍 명시',
                       'critical·법적 적용 기준·예외 처리 및 팀 승인'],
    }
    # Drop historical metadata tied exclusively to chapter 2, if present.
    for key in list(full):
        if key not in {'draft_version','status','approved','source','scope','proposed_result_values',
                       'source_documents','parent_draft','review_summary','integration_state','controls','retired_items'}:
            del full[key]
    target = HERE / 'full_checklist_draft.json'
    write(target, full)
    catalog = read(HERE / 'chapter2_reason_codes_draft.json')
    catalog['catalog_version'] = 'phase2-reason-codes-full-draft-2026-10-03-r1'
    catalog['checklist_version'] = VERSION
    catalog['checklist_source'] = target.name
    catalog['source_files'] = [{'path': target.name, 'sha256': sha(target)}]
    catalog['metadata'].update(created_at='2026-10-03',
        coverage=f'101개 통제항목·{total}문항의 구조 연결용 공통 사유 13종. 신규 문항의 의미별 코드 충분성은 팀 검수 전.',
        wbs_item='사용자 요청에 따른 1·2·3장 범위 확장',
        parent_catalog_version=read(HERE/'chapter2_reason_codes_draft.json')['catalog_version'],
        parent_catalog_sha256=sha(HERE/'chapter2_reason_codes_draft.json'))
    write(HERE / 'full_reason_codes_draft.json', catalog)
    write(WORK/'coverage.json', [{'control_id': cid, 'major_check': i, 'item_ids': ids}
                               for (cid,i),ids in coverage.items()])
    doc = ['# 1·2·3장 전체 체크리스트 검수본', '',
           f'101개 통제항목·{total}문항. 기존 2장 700문항 보존, 1·3장 {added}문항 추가.',
           '팀 미승인 초안. critical 미정. 보유 2023년 안내서 기준이며 현행 법령·실제 증적 판정의 정확성을 보증하지 않는다.', '',
           '1·3장 주요 확인사항 133개를 모두 연결했다. 복수 요소를 포함하는 절차·고지 요건 등은 추가 원자화 검토가 필요하다.',
           '미발생·기한 미도래·적용 불명은 UNKNOWN 보류. 자료 미제출은 NOT_MET 근거가 아니다.', '']
    for c in full['controls']:
        doc += [f'## {c["control_id"]} {c["control_name"]}', '',
                '| ID | 질문 | 검사 유형 | 적용 조건 |', '|---|---|---|---|']
        for i in c['items']:
            doc.append(f'| {i["item_id"]} | {i["question"]} | {i["check_kind"]} | {i.get("applicability_condition", "별도 사건 조건 없음; 적용 범위 확인 필요")} |')
        doc += ['']
    (HERE/'docs/full_checklist_review.md').write_text('\n'.join(doc)+'\n',encoding='utf-8')
    print(json.dumps({'controls':101,'questions':total,'added':added},ensure_ascii=False))


if __name__ == '__main__':
    build()
