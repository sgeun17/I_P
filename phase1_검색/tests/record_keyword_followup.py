"""확장어·경계 사례·저장소 문서 검사의 검토 자료와 추가 스냅샷을 기록한다.

운영 KB나 기존 승인 상태를 바꾸지 않는다. 이전 검수용 스냅샷도 보존한다.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    meaning_dir = ROOT / 'reports/keyword_meaning_review_2026-09-27'
    quality_dir = ROOT / 'reports/keyword_boundary_quality_2026-09-27'
    docs_dir = ROOT / 'reports/repository_documents_2026-09-27'
    snapshot_dir = ROOT / 'releases/phase1-review-2026-09-27-keywords'
    if snapshot_dir.exists():
        raise ValueError('이전 검토 기록을 덮어쓰지 않도록 새 버전을 사용하세요.')
    meaning = read(meaning_dir / 'review.json')
    attention = read(meaning_dir / 'attention_items.json')
    quality = read(quality_dir / 'evaluation.json')
    docs = read(docs_dir / 'summary.json')
    labels_path = ROOT / 'tests/source_reviewed_keyword_cases_2026-09-27.json'
    labels = read(labels_path)
    base_labels = read(ROOT / 'tests/source_reviewed_cases_2026-09-27.json')
    fingerprints = read(docs_dir / 'source_fingerprints_before.json')
    baseline = read(ROOT / 'reports/pre_llm_directory_migration_20260927_191451/summary.json')['code_sha256']
    external = {k: v for k, v in fingerprints.items()
                if k.replace('\\', '/').startswith(('phase1_입력/', 'phase1_판단/'))}
    assert len(external) == 43
    assert all(baseline[k] == v == sha(ROOT.parent / k) for k, v in external.items())
    # Correct the report-only counter for Windows separators; executed checks remain unchanged.
    if docs['input_judgment_source_count'] != len(external):
        assert docs['input_judgment_source_count'] == 0
        docs['input_judgment_source_count'] = len(external)
        docs['report_corrections'] = ['Windows 경로 구분자를 정규화해 입력·판단 소스 개수 집계를 0→43으로 수정. 실행 검사 결과에는 영향 없음.']
        save(docs_dir / 'summary.json', docs)
    kb_hash = sha(ROOT / 'controls.json')
    assert kb_hash == meaning['kb_sha256'] == quality['kb_sha256'] == docs['kb_sha256']
    assert quality['labels_sha256'] == sha(labels_path)
    assert labels['cases'][:30] == base_labels['cases'] and len(labels['cases']) == 54
    assert labels['label_changes'] == [] and labels['human_approved'] is False
    pairs = [(r['control_id'], r['keyword']) for r in meaning['keywords']]
    kb = read(ROOT / 'controls.json')
    assert set(pairs) == {(c['control_id'], w) for c in kb for w in c['keyword']}
    assert len(pairs) == len(set(pairs)) == 1438
    expansions = [r for r in meaning['keywords'] if r['classification'] == 'SEARCH_EXPANSION_REVIEW_NEEDED']
    assert len(expansions) == 878
    assert dict(Counter(r['semantic_classification'] for r in expansions)) == meaning['expansion_classifications']
    assert attention['count'] == len(attention['keywords']) == 106
    assert attention['keywords'] == [r for r in expansions if r['semantic_classification'] != 'MEANING_ALIGNED']
    assert all(not r['human_approved'] and r['source_reference']['exact_requirement_match'] for r in expansions)
    assert docs['status'] == 'PASS' and docs['unique_document_count'] == 6
    assert docs['sources_unchanged'] and sum(r['chunk_count'] for r in docs['rows']) == 108
    assert all(r['chunk_count'] == r['null_page_count'] for r in docs['rows'])
    assert quality['execution_status'] == 'PASS'
    additions = [r for r in quality['cases'] if r['id'].startswith('K')]
    positives = [r for r in additions if r['required']]
    assert len(additions) == 24 and len(positives) == 12
    assert all(not r['missing_required'] for r in positives)
    groups = {'CONTEXT_REQUIRED': '대상·행위의 문맥 확인', 'APPLICABILITY_REQUIRED': '적용 조건·근거 확인',
              'IMPLEMENTATION_EXAMPLE': '특정 도구·구현 예시로 취급'}
    lines = ['# 확장 키워드 중 검토 주의점 106개', '',
             '878개 확장어의 AI 보조 검토 결과 중 추가 문맥·범위 확인이 필요한 표현이다. 사람의 최종 승인은 대기 상태다.',
             '삭제 대상이나 검색 제외 규칙이 아니다. 관련 후보를 찾는 표현과 필수 충족 조건을 구분하기 위한 메모다.', '',
             '| 구분 | 개수 | 검토할 것 |', '|---|---:|---|',
             '| 문맥 확인 | 59 | 계정 잠금·별도 보관·권한 등은 대상과 행위를 보고 관련 항목을 구분 |',
             '| 적용 조건 확인 | 27 | 숫자·기한·예외는 대상과 근거 확인. 보유 2023년 자료가 현재 법규를 보증하지 않음 |',
             '| 구현 예시 | 20 | Git·CI/CD·SIEM 등은 가능한 운영 방식이며 특정 기술 사용을 필수 조건으로 추가하지 않음 |']
    for category, title in groups.items():
        lines += ['', f'## {title}', '', '| ID | 항목 | 키워드 | 확인할 내용 |', '|---|---|---|---|']
        for row in attention['keywords']:
            if row['semantic_classification'] == category:
                lines.append(f"| {row['control_id']} | {row['control_name']} | {row['keyword']} | {row['review_reason']} |")
    (meaning_dir / 'attention_items.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    summary_lines = ['# 확장 키워드 후속 검토 결과', '',
                     '- 878개 확장어 전체를 요구사항의 의미·범위에 대해 AI 보조 검토했다. 의미 부합 772개, 문맥 확인 59개, 적용 조건 확인 27개, 구현 예시 20개다.',
                     '- 106개 주의 표현은 삭제하거나 검색에서 제외하지 않았다. 분류는 검토 기록이며 사람의 최종 승인 전이다.',
                     '- 기존 30건의 정답을 그대로 두고 경계 사례 24건을 추가했다. 총 54건 중 관련 31건은 모두 필수 정답이 Top-5에 포함됐고, 단일 필수 정답 Top-1은 25/28이다.',
                     '- 추가 관련 사례 12건은 모두 필수 정답 Top-5 포함, 11/12은 Top-1이다. 퇴사 계정 잠금 K01의 필수 정답 2.5.1은 2위다.',
                     '- 저장소 DOCX 7개 중 중복 1개를 제외한 6개·108청크는 파서→검색→판단 입력·프롬프트 생성을 통과했다. 실제 운영 출처와 정답은 미확인이다.',
                     '- 입력·판단 소스 43개의 해시는 폴더명 변경 후 연결 검사 기준과 같다. 운영 KB·검색 알고리즘·임계값·Phase2·루트 문서 변경은 없다.',
                     '- 검색팀 회귀 검사: 테스트 24개와 하위 검사 37개 통과. 실제 LLM 호출 0건.', '',
                     '## 검토할 자료', '',
                     '- [106개 주의 표현](attention_items.md)', '- [878개 전체 검토 목록](review.md)',
                     '- [54개 검색 평가 결과](../keyword_boundary_quality_2026-09-27/evaluation.md)',
                     '- [경계 사례의 해석](../keyword_boundary_quality_2026-09-27/failure_analysis.md)',
                     '- [기존 DOCX 연결 검사](../repository_documents_2026-09-27/summary.md)', '',
                     '## 아직 남은 완료 조건', '',
                     '- 사람의 KB·키워드·정답 승인과 금융권 추가 요건 포함 범위 확정.',
                     '- 실제 출처가 확인된 증적과 독립 정답으로 검색 품질 평가.',
                     '- 실제 LLM 및 업로드 API·DB·화면 연동, 최종 버전 승인.',
                     '- 보유 2023년 자료 기준의 의미 검토이며 현재 법규·기한 검증 또는 Phase1 완료 선언이 아니다.']
    (meaning_dir / 'summary.md').write_text('\n'.join(summary_lines) + '\n', encoding='utf-8')
    analysis = ['# 추가 경계 사례에서 확인한 점', '',
                '- 기존 30개 사례 정답을 변경하지 않고 추가 24개 정답을 검색 전에 고정했다. 사람 미승인 합성 개발 세트다.',
                '- 같은 단어라도 대상·행위에 따라 항목이 다르다. 계정 잠금은 퇴사 계정 회수(2.5.1)와 로그인 실패 차단(2.5.3)을 구분해야 한다.',
                '- 로그 수집·보관(2.9.4)과 검토·후속 조치(2.9.5), 일반 파기(3.4.1)와 법정 보존 분리(3.4.2), 앱 단말 접근(3.2.3)과 업무 앱 접근통제(2.6.3)를 각각 확인했다.',
                '- 신규 양성 12개는 필수 후보가 모두 Top-5 안에 있다. K01은 2.2.5가 1위, 필수 2.5.1이 2위이므로 첫 후보만 자동 정답으로 쓰면 누락된다.',
                '- 무관한 소설의 Git 닉네임(KN02), 게임의 RTO(KN03), 개인정보가 없는 종이 소품 파기(KN06)에도 보안 통제 후보가 나온다.',
                '- 정보 부족 사례도 후보를 반환한다. 이번에는 후보의 생성·보존만 검사했고 LLM의 NO_MATCH·수동 검토 전환을 실행하지 않았다.', '',
                '| 구분 | Top-1 점수 최저 | Top-1 점수 최고 |', '|---|---:|---:|']
    for category, score in quality['score_ranges'].items():
        analysis.append(f"| {category} | {score['min']:.6f} | {score['max']:.6f} |")
    analysis += ['', '점수 구간이 겹친다. 작은 합성 세트에 맞춰 운영용 임계값·가중치·제외 규칙을 추가하지 않았다.',
                 '검색 표현의 의미 부합과 문서의 최종 관련성·충족 여부는 별도 확인 대상이다. 출처가 검증된 문서, 독립 정답 승인, 실제 LLM 판정 결과가 필요하다.']
    (quality_dir / 'failure_analysis.md').write_text('\n'.join(analysis) + '\n', encoding='utf-8')
    snapshot_dir.mkdir(parents=True)
    old_manifest = ROOT / 'releases/phase1-review-2026-09-27/manifest.json'
    old = read(old_manifest)
    files = set(old['files'])
    files.update({'tests/review_keyword_meanings.py', 'tests/build_keyword_boundary_review.py',
                  'tests/keyword_boundary_cases_2026-09-27.json', 'tests/source_reviewed_keyword_cases_2026-09-27.json',
                  'tests/verify_repository_documents.py', 'tests/record_keyword_followup.py'})
    for folder in (meaning_dir, quality_dir, docs_dir):
        files.update(p.relative_to(ROOT).as_posix() for p in folder.rglob('*') if p.is_file())
    files.update({'handoff/9월27일_Phase1_검색_마무리.md', 'handoff/검색팀_진행현황.md'})
    copies = {'controls.json': ROOT / 'controls.json', 'source_reviewed_keyword_cases.json': labels_path}
    for name, source in copies.items():
        (snapshot_dir / name).write_bytes(source.read_bytes())
        assert sha(snapshot_dir / name) == sha(source)
    manifest = {'snapshot_id': snapshot_dir.name, 'status': 'REVIEW_SNAPSHOT_NOT_FINAL_RELEASE',
                'created_at': datetime.now().astimezone().isoformat(),
                'previous_snapshot': old_manifest.relative_to(ROOT).as_posix(), 'previous_snapshot_sha256': sha(old_manifest),
                'kb_sha256': kb_hash, 'model': old['model'], 'embedding_cache_key': quality['embedding_cache_key'],
                'index_count_verified': quality['index_count'], 'labels_sha256': sha(labels_path),
                'keyword_ai_review_count': 878, 'keyword_attention_count': 106,
                'independent_human_approval': False, 'actual_llm_integration_verified': False,
                'full_upload_db_ui_integration_verified': False, 'production_evidence_verified': False,
                'all_original_labels_preserved': True, 'input_judgment_source_hashes_unchanged': external,
                'top_k': old['top_k'], 'aggregation': old['aggregation'],
                'files': {name: sha(ROOT / name) for name in sorted(files)},
                'regression_tests': {'passed': 24, 'subtests_passed': 37},
                'llm_calls': 0, 'production_relevance_cutoff': None,
                'limits': ['이전 스냅샷은 보존하고 이번 검토·평가 도구 및 보고서 해시를 추가 기록했다.',
                           'AI 의미 검토·합성 평가·기존 파일 연결 확인 자료. 최종 승인·운영 정확도·배포 버전이 아니다.']}
    save(snapshot_dir / 'manifest.json', manifest)
    assert sha(old_manifest) == manifest['previous_snapshot_sha256']
    print(json.dumps({'status': 'PASS', 'expansions': 878, 'attention': 106,
                      'labels': 54, 'unchanged_input_judgment_sources': len(external),
                      'snapshot': snapshot_dir.name}, ensure_ascii=False))


if __name__ == '__main__':
    main()
