"""Create source-grounded label review records without loading predictions."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = {
    'S01': '승인 후 계정 발급, 인사이동 시 권한 변경, 퇴사 계정 삭제가 핵심이다. 절차 이행과 관련성을 확인하며 조직 전체의 충족 판정은 하지 않는다.',
    'S02': 'root·Administrator의 최소 발급, 사용자 제한과 별도 사용 승인으로 특수 계정 통제를 설명한다.',
    'S03': '업무와 현재 권한을 정기 대조하고 과다 권한을 회수하는 접근권한 검토 사례다.',
    'S04': '외부 원격접근의 승인·단말·기간·인증·암호화 조건이 구체적이다. 일반 사용자 계정 관리와 구분한다.',
    'S05': '백업 주기·보관·성공 점검·복구 절차를 설명한다. 대체센터 훈련이 없는 이 본문만으로 재해복구 시험을 필수 정답으로 추가하지 않는다.',
    'S06': '접속기록 수집·보존기간·위변조 방지가 핵심이다. 이상 징후를 분석하는 로그 검토와 구분한다.',
    'S07': '접속기록을 분석하고 이상 징후의 사유와 조치를 확인한다. 단순 로그 보존과 구분한다.',
    'S08': '교육계획·실시·미이수자 보충·교육 효과 확인을 포함한 인식제고 및 교육훈련 사례다.',
    'S09': '취약점 식별, 위험도별 조치와 재점검이 핵심이다. 설정 변경·패치는 조치 수단이며 단일 필수 정답이 전체 관련 항목을 배제하지 않는다.',
    'S10': '센터 중단을 가정한 대체센터 전환·목표 시간/시점 측정·계획 보완으로 재해복구 시험을 설명한다.',
    'S11': '보유기간/처리목적 종료 후 복구 불가능한 삭제·분쇄와 파기 기록을 설명한다.',
    'S12': '클라우드 서비스의 책임 범위·관리자 접근·공개 설정·네트워크 설정 통제를 설명한다.',
    'M01': '서로 다른 두 청크가 계정 관리와 접속기록 보존을 각각 설명하므로 두 통제항목 모두 필수 후보다.',
    'M02': '일상 백업 운영과 재해복구 훈련을 서로 다른 청크에서 설명하므로 두 통제항목 모두 필수 후보다.',
    'M03': '외부 원격접근 통제와 특수 계정 통제를 각각 설명하므로 두 통제항목 모두 필수 후보다.',
    'U01': '식단 안내다. 정보보호 통제의 대상·절차·수행 사실을 설명하지 않는다.',
    'U02': '날씨와 산책 묘사다. 정보보호 통제의 근거가 없다.',
    'U03': '축구 경기 후기로 정보보호 통제의 근거가 없다.',
    'A01': '점검 대상·기준·수행 내용이 없어 특정 통제항목을 확정할 정보가 부족하다. 무관 확정과 구분한다.',
    'A02': '일반적 보안 선언뿐이다. 특정 통제항목의 행위를 식별할 정보가 부족하다.',
    'R01': '퇴사 시 시스템 계정 삭제라는 구체적 권한 말소 행위가 있어 사용자 계정 관리 후보가 필요하다.',
    'R02': '분기별로 업무와 접근권한을 대조하고 회수하므로 접근권한 검토 후보가 필요하다.',
    'R03': '백업 성공 점검과 실패 작업 재수행은 백업 절차의 운영 이행과 관련된다.',
    'R04': '보유기간 종료 개인정보의 안전한 삭제와 파기 기록을 설명한다.',
    'N01': '보안 단어를 사용하는 낱말 퍼즐이다. 키워드 출현만으로 실제 통제 행위와 관련 있다고 볼 수 없다.',
    'N02': '소설 속 암호·성문·모험 기록이다. 정보시스템 통제의 근거가 아니다.',
    'N03': '수학의 로그 함수다. 시스템 접속기록/로그의 수집·검토와 관련이 없다.',
    'N04': '게임의 계정 카드·접근권한 토큰이며 실제 시스템과 무관하다고 명시했다.',
    'B01': '통제 관련 단어와 회의 예정만 있다. 실제 대상·수행 내용·결과 없이 관련성을 확정할 정보가 부족하다.',
    'B02': '보안 점검의 세부 대상·결과가 다른 자료에 있어 현재 본문만으로 특정 통제항목을 확정할 수 없다.',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = ROOT / 'tests/source_reviewed_cases_2026-09-27.json'
    if target.exists():
        raise ValueError('Review set already exists; preserve its frozen version')
    kb_path = ROOT / 'controls.json'
    kb = {c['control_id']: c for c in json.loads(kb_path.read_text(encoding='utf-8'))}
    comparison = ROOT / 'reports/kb_source_review_2026-09-27_v2/comparison.json'
    rows = {c['control_id']: c for c in json.loads(comparison.read_text(encoding='utf-8'))['controls']}
    sources, cases = {}, []
    for name in ('review_cases.json', 'relevance_cases.json'):
        path = ROOT / 'tests' / name
        sources[name] = sha(path)
        dataset = json.loads(path.read_text(encoding='utf-8'))
        for case in dataset['cases']:
            refs = []
            for cid in case['expected']:
                assert rows[cid]['source_comparisons']['KISA-2023']['requirement_match']
                refs.append({'control_id': cid, 'control_name': kb[cid]['control_name'],
                             'source_id': 'KISA-2023',
                             'pdf_pages': rows[cid]['source_comparisons']['KISA-2023']['pdf_pages'],
                             'requirement': kb[cid]['requirement']})
            category = 'RELATED' if case['expected'] else ('NOT_RELATED' if case['kind'] == 'unrelated' else 'INSUFFICIENT_INFORMATION')
            cases.append({**case, 'origin_dataset': name, 'synthetic': True,
                          'review_category': category, 'required_control_ids': case['expected'],
                          'review_reason': NOTES[case['id']], 'source_references': refs,
                          'labels_exhaustive': not bool(case['expected']),
                          'human_review': {'status': 'PENDING', 'reviewer': None, 'approved_at': None}})
        assert sha(path) == sources[name]
    assert {c['id'] for c in cases} == set(NOTES) and len(cases) == 30
    output = {'version': 'source-label-review-2026-09-27',
              'status': 'AI_SOURCE_REVIEWED_HUMAN_APPROVAL_PENDING', 'human_approved': False,
              'predictions_used_for_label_changes': False, 'kb_sha256': sha(kb_path),
              'source_dataset_sha256': sources, 'source_comparison_sha256': sha(comparison),
              'label_changes': [], 'cases': cases,
              'limits': ['합성 개발 사례 30개. 실제 운영 증적 또는 사람의 독립 정답 세트가 아님.',
                         '기존 필수 정답은 유지하며 원문 근거와 경계 설명을 보강했다.',
                         '양성 문서의 필수 후보 ID 목록은 가능한 관련 항목 전체를 열거한 것이 아니므로 precision을 산출하지 않는다.',
                         'INSUFFICIENT_INFORMATION은 NOT_RELATED와 다르다. 후보 반환 여부만으로 관련성을 확정하지 않는다.']}
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    out = ROOT / 'reports/search_quality_2026-09-27'
    out.mkdir(parents=True, exist_ok=True)
    lines = ['# 검색 사례 정답 검토 초안', '', '- 본문·요구사항 기준으로 정답 근거를 검토했다. 검색 결과를 읽거나 기존 정답을 검색 순위에 맞춰 바꾸지 않았다.',
             '- 관련 19건, 무관 7건, 정보 부족 4건. 전부 합성 개발 자료이며 사람 승인 대기 상태다.',
             '| 사례 | 분류 | 필수 후보 | 원문에 따른 검토 이유 |', '|---|---|---|---|']
    for c in cases:
        lines.append(f"| {c['id']} | {c['review_category']} | {', '.join(c['required_control_ids']) or '-'} | {c['review_reason']} |")
    lines += ['', *[f'- {limit}' for limit in output['limits']]]
    (out / 'label_review.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'PASS: 30 source review records; human approval pending; {target}')


if __name__ == '__main__':
    main()
