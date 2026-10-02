"""Record the agent's scope review of all 878 non-literal search expansions.

The classifications are review annotations, not executable keyword filters or
compliance rules. Uses the bundled Python for the original PDF references.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kb_identity import kb_sha256
sys.path.insert(0, str(ROOT / 'tests'))
from review_kb_sources import normalized, read_guide, SOURCES

# These overrides describe the concrete ambiguity found while reviewing each
# control's full expansion group against its requirement. Remaining expansions
# retain their meaning as paraphrases, evidence names or related activity names.
CONTEXT = {
    '1.1.4': {'예외사항 승인': '관리체계 범위의 제외·예외 승인인지 확인. 일반 업무 예외 승인만으로 범위 설정과 연결하지 않음.'},
    '1.3.2': {'담당자 지정': '보호대책 운영 담당자 지정·공유 문맥이 필요함.',
              '서버 보안설정': '서버 설정 자체보다 운영 담당자에게 설정 내용을 공유·교육하는 문맥이 필요함.',
              '계정·권한관리': '계정 처리 자체보다 보호대책을 운영할 부서·담당자에게 공유하는 문맥이 필요함.'},
    '1.4.1': {'계약상 요구사항': '정보보호·개인정보보호 준수 검토와 연결되는 계약상 요구인지 확인. 일반 상거래 계약과 구분.'},
    '1.4.3': {'KPI': '관리체계 문제의 개선 결과·효과성을 확인하는 지표인지 확인. 일반 영업 KPI와 구분.'},
    '2.2.2': {'감사 로그': '직무 분리가 어려울 때 상위 관리자 검토 등의 보완대책으로 사용하는 로그인지 확인.',
              '상위관리자 모니터링': '직무 미분리의 보완대책인지 확인. 일반 보안 모니터링과 구분.',
              '업무 대행 승인': '권한 오남용 방지·상호견제와 연결되는 대행 승인인지 확인.'},
    '2.3.3': {'재위탁 동의': '수탁자 감독 및 해당 재위탁 조건의 이행을 확인하는 문맥이 필요함.',
              '재위탁 승인': '재위탁 조건 준수의 감독인지 확인. 개인정보 위탁 공개·계약 작성과 구분.'},
    '2.4.6': {'악성코드 검사': '반출입 기기·매체를 검사하는 문맥이 필요함. 일반 악성코드 통제와 구분.',
              '데이터 삭제': '기기 반출입 시 유출 방지를 위한 삭제인지 확인. 개인정보 파기·자산 폐기와 구분.'},
    '2.5.1': {'계정 잠금': '퇴직·휴면 등 계정 생명주기에서 비활성화하는지 확인. 로그인 실패에 따른 잠금과 구분.',
              '계정 만료일': '업무용 사용자 계정의 부여 기간과 관련된 만료인지 확인.'},
    '2.5.3': {'VPN 인증': 'VPN 접속 시 사용자를 인증하는 절차인지 확인. 원격접근의 승인·기간 관리와 구분.'},
    '2.5.4': {'개인정보취급자 인증수단': '비밀번호의 생성·변경·보관·재설정 관리 문맥이 필요함.',
              '이용자 인증수단': '비밀번호 관리 대상의 표현으로 사용. 모든 인증수단을 비밀번호 관리로 연결하지 않음.'},
    '2.5.5': {'서비스 계정': '특수 목적 계정의 최소 부여·별도 식별·통제 문맥이 필요함.',
              '배치계정': '자동 작업용 특수 계정의 별도 통제 문맥이 필요함.',
              '비상계정': '비상시에 쓰는 특수 계정의 최소 권한·승인·사용 통제 문맥이 필요함.'},
    '2.6.3': {'대량조회': '응용프로그램의 권한별 조회 제한인지 확인. 사후 로그 분석과 구분.',
              '대량다운로드': '응용프로그램에서 다운로드 범위를 제한하는 문맥이 필요함.',
              '조회 사유': '응용프로그램 조회 권한·사유 입력 통제인지 확인. 사후 이상 로그 소명과 구분.'},
    '2.6.4': {'쿼리 실행 이력': '데이터베이스 접근통제·사용자 책임추적과 연결되는지 확인. 일반 로그 보존과 구분.'},
    '2.7.1': {'비밀번호 일방향 암호화': '저장하는 비밀번호의 보호 방식인지 확인. 비밀번호 변경·복잡도 관리와 구분.'},
    '2.7.2': {'하드코딩 금지': '암호키가 소스에 노출되는 문제인지 확인. 일반 소스 품질 규칙과 구분.'},
    '2.8.2': {'기능시험': '사전 정의된 보안 요구사항의 시험인지 확인. 일반 기능 시험만으로 연결하지 않음.',
              '웹 취약점 진단': '도입·개발·변경 시 보안 요구사항의 검증인지 확인. 운영 중 정기 취약점 진단과 구분.'},
    '2.8.3': {'운영데이터 반출': '개발·시험 환경과 운영 환경의 분리 통제 문맥이 필요함. 시험 데이터 보호와 함께 볼 수 있음.'},
    '2.8.4': {'개인정보 마스킹': '시험 데이터 생성·이용 시 운영정보 유출 방지 문맥이 필요함.',
              '비식별 조치': '시험 데이터 보호의 구현 표현으로 사용. 단어만으로 법적 익명성·충족을 확정하지 않음.',
              '사용기간': '시험용 데이터의 이용 범위·기간인지 확인. 일반 문서 보유기간과 구분.'},
    '2.9.3': {'복구 목표': '백업본의 복원 목표인지 확인. 재해복구 전략 수립·시험과 구분.'},
    '2.9.7': {'복구 불가능한 삭제': '자산·매체의 재사용 또는 폐기 문맥이 필요함. 보유기간 종료 개인정보 파기와 구분.'},
    '2.10.1': {'IP·MAC 제한': '보안시스템의 관리자 접속·운영 통제인지 확인. 네트워크 접속통제와 구분.'},
    '2.10.3': {'불필요 서비스': '외부 공개 서버의 노출 최소화 문맥이 필요함. 일반 정보시스템 접근과 구분.'},
    '2.10.8': {'패치 인터넷 접속 제한': '패치 배포 서버 등의 접속 경로를 통제하는 문맥이 필요함. 일반 인터넷 이용 제한과 구분.'},
    '2.10.9': {'정기 검사': '악성코드 검사를 뜻하는지 확인. 대상이 없는 정기 점검 문장과 구분.',
               '실시간 감시': '악성코드 예방·탐지 목적의 감시인지 확인. 일반 이벤트 모니터링과 구분.'},
    '2.11.2': {'라우터·스위치': '취약점 진단의 대상 장비인지 확인. 장비 목록만으로 진단과 연결하지 않음.',
               '서버·DBMS': '서버·DBMS의 취약점 점검·조치 문맥이 필요함.',
               '웹·모바일 앱': '웹·모바일 앱의 취약점 점검·조치 문맥이 필요함.',
               '보안설정 점검': '취약점 발견·영향 분석·조치 문맥이 필요함. 단순 운영 설정 확인과 구분.'},
    '2.11.5': {'포렌식': '침해·유출 사고의 분석·복구·재발방지 활동인지 확인.'},
    '2.12.2': {'RTO·RPO 달성': '재해복구 시험에서 목표 달성 여부를 측정하는 문맥이 필요함. 목표를 정의한 문서와 구분.',
               '대체센터 전환': '재해복구 전략을 시험하는 전환인지 확인. 실제 사고 복구만을 기록한 문서와 구분.'},
    '3.1.3': {'회원가입 본인확인': '주민등록번호 대신 쓰는 본인확인 수단 제공 문맥이 필요함.',
              '휴대전화 본인확인': '주민등록번호 대체수단인지 확인. 일반 사용자 인증과 구분.',
              '아이핀·휴대폰 인증': '주민등록번호 대체 본인확인 수단인지 확인.'},
    '3.1.4': {'바이오정보': '구체적 정보의 성격·처리 목적과 적용 요건을 확인. 모든 생체 관련 자료를 동일하게 취급하지 않음.'},
    '3.2.1': {'개인정보 보유현황 공개': '개인정보 현황 관리·파일 등록 관련 공개 문맥인지 확인. 처리방침 공개와 구분.'},
    '3.2.3': {'필수 접근권한': '이용자 이동통신단말의 정보·기능 접근권한인지 확인. 업무 시스템 권한과 구분.',
              '선택 접근권한': '앱이 단말기 정보·기능에 접근하는 동의 문맥이 필요함.'},
    '3.2.4': {'별도 보관': '목적 외 이용·제공에 따른 보호조치 문맥인지 확인. 법정 보존 개인정보 분리와 구분.'},
    '3.3.2': {'수탁업무 종료': '개인정보 위탁 현황·공개·조건 관리의 문맥인지 확인. 외부자 종료 보안과 함께 검토할 수 있음.'},
    '3.4.2': {'최소 보존기간': '적용되는 보존 근거·기간을 확인. 모든 개인정보에 공통 최소 보존기간이 있다고 해석하지 않음.',
              '보존기간 만료': '별도 보존하던 개인정보의 기간 종료인지 확인. 일반 파기와 연결될 수 있음.'},
    '3.5.2': {'열람 수수료': '권리행사 처리 절차의 적용 조건을 확인. 단어만으로 모든 열람에 수수료가 필요하다고 해석하지 않음.'},
}
TECH_EXAMPLES = {
    '2.6.1': ['VLAN'], '2.6.5': ['WPA2·WPA3'],
    '2.8.5': ['Git', '브랜치 권한', '커밋 이력'], '2.8.6': ['CI/CD·DevOps'],
    '2.9.3': ['백업 암호화'], '2.9.7': ['디가우징'],
    '2.10.2': ['공동책임모델', '보안그룹', '관리자 MFA'],
    '2.10.3': ['TLS 인증서', '웹방화벽'], '2.10.5': ['SFTP 전송'],
    '2.11.1': ['CERT', '관제센터'], '2.11.3': ['SIEM', 'IDS·IPS', 'DLP'],
    '2.12.1': ['BIA'],
}
APPLICABILITY = {
    '2.2.4': ['연 1회 교육', '법정 의무교육'],
    '2.7.1': ['법정 암호화 대상'], '2.9.4': ['처리 정보주체'],
    '2.11.5': ['통지 시한'],
    '3.1.1': ['동의 없는 추가 이용', '추가 이용 판단기준'],
    '3.1.3': ['법률·대통령령 근거'],
    '3.1.5': ['3일 이내 통지', '출처 통지 예외'],
    '3.1.7': ['야간 광고 전송', '오후 9시 이후 광고', '080 수신거부'],
    '3.2.1': ['공공기관 등록', '60일 이내 등록'],
    '3.2.4': ['법적 예외사유', '관보·홈페이지 게재'],
    '3.3.1': ['추가 제공 판단기준', '추가 제공 기준'],
    '3.3.3': ['30일 이상 고지'],
    '3.3.4': ['계약 이행 국외이전', '인증에 따른 이전'],
    '3.4.1': ['5일 이내 파기'],
    '3.5.2': ['10일 이내 열람'],
    '3.5.3': ['5만명 민감·고유식별정보', '100만명 개인정보', '연 1회 통지'],
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = ROOT / 'reports/keyword_meaning_review_2026-09-27'
    if out.exists():
        raise ValueError('Use a new version; do not overwrite review records')
    kb_path = ROOT / 'controls.json'
    before = sha(kb_path)
    controls = json.loads(kb_path.read_text(encoding='utf-8'))
    control_map = {c['control_id']: c for c in controls}
    comparison_path = ROOT / 'reports/kb_source_review_2026-09-27_v2/comparison.json'
    comparison = json.loads(comparison_path.read_text(encoding='utf-8'))
    existing = json.loads((comparison_path.parent / 'keyword_review.json').read_text(encoding='utf-8'))
    source_path = ROOT.parent.parent / comparison['sources']['KISA-2023']['path']
    assert sha(source_path) == comparison['sources']['KISA-2023']['sha256']
    guide = read_guide(source_path)
    expansion_pairs = {(r['control_id'], r['keyword']) for r in existing['keywords']
                       if r['classification'] == 'SEARCH_EXPANSION_REVIEW_NEEDED'}
    declared = {(cid, keyword) for mapping in (CONTEXT, TECH_EXAMPLES, APPLICABILITY)
                for cid, words in mapping.items() for keyword in words}
    assert declared <= expansion_pairs, sorted(declared - expansion_pairs)
    rows = []
    for row in existing['keywords']:
        cid, keyword = row['control_id'], row['keyword']
        control = control_map[cid]
        section = guide['sections'][cid]
        assert normalized(control['requirement']) == normalized(section['requirement'])
        if row['classification'] == 'SOURCE_TEXT_MATCH':
            decision = 'DIRECT_SOURCE_MATCH'
            reason = '같은 통제항목 원문에 직접 있는 표현. 이번 비문자열 확장어 의미 검토에서는 별도 변경하지 않음.'
        elif keyword in APPLICABILITY.get(cid, []):
            decision = 'APPLICABILITY_REQUIRED'
            reason = '해당 대상·행위·시점·적용 근거의 문맥을 확인해야 하는 검색 표현. 숫자·기한·예외를 보편적인 현행 판정 규칙으로 사용하지 않음.'
        elif keyword in CONTEXT.get(cid, {}):
            decision, reason = 'CONTEXT_REQUIRED', CONTEXT[cid][keyword]
        elif keyword in TECH_EXAMPLES.get(cid, []):
            decision = 'IMPLEMENTATION_EXAMPLE'
            reason = '해당 통제의 도구·구현 방식·운영 형태를 찾는 검색 예시. 특정 제품·기술의 사용을 필수 충족 조건으로 추가하지 않음.'
        else:
            decision = 'MEANING_ALIGNED'
            reason = f"'{keyword}'는 '{control['control_name']}'의 요구사항과 관련되는 행위·대상·증빙·표현 확장으로 유지 가능. 독립적인 충족 조건이 아니라 후보 검색 표현으로 사용."
        rows.append({**row, 'semantic_classification': decision, 'review_reason': reason,
                     'control_name': control['control_name'],
                     'scope_requirement': control['requirement'],
                     'source_reference': {'source_id': 'KISA-2023', 'pdf_pages': section['pdf_pages'],
                                          'exact_requirement_match': True},
                     'reviewer_type': 'AI_ASSISTED_SCOPE_REVIEW', 'human_approved': False})
    assert sha(kb_path) == before and len(rows) == 1438
    expansions = [r for r in rows if r['classification'] == 'SEARCH_EXPANSION_REVIEW_NEEDED']
    assert len(expansions) == len(expansion_pairs) == 878
    counts = dict(Counter(r['semantic_classification'] for r in expansions))
    result = {'checked_at': datetime.now().astimezone().isoformat(),
              'status': 'AI_MEANING_REVIEWED_HUMAN_APPROVAL_PENDING',
              'kb_sha256': kb_sha256(kb_path.read_bytes()), 'source_comparison_sha256': sha(comparison_path),
              'source_pdf_sha256': sha(source_path), 'control_count': 101,
              'total_keyword_count': len(rows), 'reviewed_expansion_count': len(expansions),
              'expansion_classifications': counts, 'kb_changed': False, 'human_approved': False,
              'method': '101개 항목의 878개 확장어 그룹을 요구사항과 대조해 AI가 의미·범위를 검토하고, 문맥·적용 조건·구현 예시의 주의점을 기록함. 문자열 일치만으로 의미 검수 완료를 판정하지 않음.',
              'keywords': rows,
              'limits': ['동의어·상세 행위·증빙 이름은 검색 확장으로 사용할 수 있으나 독립적인 판정 의무를 추가하지 않는다.',
                         '특정 통제에 의미가 맞더라도 다른 통제와 동시에 관련될 수 있다.',
                         '보유 2023년 안내서 기준의 의미 검토. 법령·기한의 현행성 검증이나 사람의 최종 승인은 아니다.',
                         '이 분류는 검토 기록이며 검색 단계의 제외 규칙·가중치·하드 필터로 적용하지 않았다.']}
    out.mkdir(parents=True)
    (out / 'review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 검색 확장 키워드 의미 검토', '',
             f'- 확장어 878개 전체 검토: {counts}',
             '- AI 보조 의미 검토이며 사람의 최종 승인 대기 상태다. 운영 KB·색인·검색 설정은 변경하지 않았다.',
             '- 아래 이유는 검색 문맥 설명이다. 법적 의무·수치·충족 규칙을 새로 정의한 것이 아니다.', '',
             '| ID | 키워드 | 분류 | 검토 이유 |', '|---|---|---|---|']
    for row in expansions:
        lines.append(f"| {row['control_id']} | {row['keyword']} | {row['semantic_classification']} | {row['review_reason']} |")
    lines += ['', *[f'- {limit}' for limit in result['limits']]]
    (out / 'review.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    attention = [r for r in expansions if r['semantic_classification'] != 'MEANING_ALIGNED']
    (out / 'attention_items.json').write_text(json.dumps({'kb_sha256': kb_sha256(kb_path.read_bytes()), 'count': len(attention), 'keywords': attention}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'counts': counts, 'attention_count': len(attention)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
