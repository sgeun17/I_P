"""Save reviewed differences and a reproducible, unapproved Phase1 snapshot."""
from collections import Counter
from datetime import datetime
import difflib
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kb_identity import kb_sha256
sys.path.insert(0, str(ROOT / 'tests'))
from review_kb_sources import normalized


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    source_dir = ROOT / 'reports/kb_source_review_2026-09-27_v2'
    quality_dir = ROOT / 'reports/search_quality_2026-09-27'
    comparison = load(source_dir / 'comparison.json')
    evaluation = load(quality_dir / 'evaluation.json')
    kb_path = ROOT / 'controls.json'
    kb = {c['control_id']: c for c in load(kb_path)}
    rows = {c['control_id']: c for c in comparison['controls']}
    assert comparison['kb_sha256'] == evaluation['kb_sha256'] == kb_sha256(kb_path.read_bytes())
    assert all(c['source_comparisons']['KISA-2023']['name_match']
               and c['source_comparisons']['KISA-2023']['requirement_match']
               and all(e['matched_in_general_section'] for e in c['evidence_examples'])
               for c in comparison['controls'])
    reviewed = []
    for difference in comparison['differences']:
        cid, sid = difference['control_id'], difference['source_id']
        original = rows[cid]['source_comparisons'][sid]
        changes = []
        for field, match in [('control_name', 'name_match'), ('requirement', 'requirement_match')]:
            if original[match]:
                continue
            a = normalized(kb[cid][field])
            b = normalized(original['extracted_' + ('name' if field == 'control_name' else field)])
            edits = [{'kb_text': a[i:j], 'comparison_text': b[k:l]}
                     for tag,i,j,k,l in difflib.SequenceMatcher(None, a, b).get_opcodes() if tag != 'equal']
            changes.append({'field': field, 'edits': edits})
        if sid == 'FSI-2023':
            reason = ('금융 안내서에는 명칭 표기가 다르다. 일반 안내서와 일치하는 KB 명칭을 유지한다.'
                      if cid != '3.2.3' else
                      '금융 안내서 인증기준 칸이 주요 확인사항과 같은 질문형 문장으로 인쇄되어 있다. 일반 안내서의 인증기준 문장을 유지한다.')
        elif cid == '2.8.4':
            reason = '일반 점검항목 XLSX의 인증기준 셀이 비어 있다. 일반 안내서 PDF의 인증기준 문장과 일치하는 KB를 유지한다.'
        else:
            reason = '점검항목 XLSX에 명칭·조사·단어·구두점 차이가 있다. 일반 안내서 PDF와 일치하는 KB를 유지한다.'
        reviewed.append({'control_id': cid, 'source_id': sid, 'changes': changes,
                         'review_type': 'AI_SOURCE_DIFFERENCE_REVIEW',
                         'visual_pdf_page_verified': sid == 'FSI-2023',
                         'decision': 'KEEP_GENERAL_GUIDE_MATCHING_KB', 'reason': reason,
                         'scope_approval': 'PENDING'})
    (source_dir / 'source_differences_review.json').write_text(json.dumps({
        'kb_sha256': kb_sha256(kb_path.read_bytes()), 'primary_source': 'KISA-2023', 'primary_source_exact_matches': 101,
        'general_evidence_examples_exact_matches': 399,
        'differences_by_source': dict(Counter(d['source_id'] for d in reviewed)),
        'kb_modified': False, 'reviews': reviewed}, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 원자료 간 차이 검토', '',
             '- 일반 안내서 PDF 기준으로 명칭·요구사항 101개 및 증거 예시 399개가 일치한다.',
             '- 금융 안내서 3건과 일반 점검항목 XLSX 16건의 차이는 KB 자동 수정 대상으로 삼지 않았다.',
             '- 금융권 추가 요건을 검색 KB에 포함할 범위는 담당자 합의가 필요하다.', '',
             '| ID | 비교 자료 | 확인 내용·처리 |', '|---|---|---|']
    lines += [f"| {d['control_id']} | {d['source_id']} | {d['reason']} |" for d in reviewed]
    (source_dir / 'source_differences_review.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    # Freeze bytes and metadata without implying final approval or copying the
    # multi-gigabyte model/database. Rebuilding uses the existing offline assets.
    release = ROOT / 'releases/phase1-review-2026-09-27'
    if release.exists():
        raise ValueError('Snapshot already exists; use a separate version for changed results')
    release.mkdir(parents=True)
    labels = ROOT / 'tests/source_reviewed_cases_2026-09-27.json'
    assert sha(labels) == evaluation['labels_sha256']
    (release / 'controls.json').write_bytes(kb_path.read_bytes())
    (release / 'source_reviewed_cases.json').write_bytes(labels.read_bytes())
    tracked = [kb_path, labels,
        source_dir / 'comparison.json', source_dir / 'keyword_review.json',
        source_dir / 'source_differences_review.json', quality_dir / 'evaluation.json',
        quality_dir / 'search_outputs.json', ROOT / 'data/chroma_kb_manifest.json',
        ROOT / 'chunk_retriever.py', ROOT / 'retriever.py', ROOT / 'judgment_adapter.py',
        ROOT / 'schemas/chunk_input.schema.json', ROOT / 'schemas/chunk_output.schema.json',
        ROOT / 'requirements.txt', ROOT / 'requirements-chroma.txt',
        ROOT / 'tests/review_kb_sources.py', ROOT / 'tests/review_retriever_labels.py',
        ROOT / 'tests/evaluate_phase1_finish.py', ROOT / 'tests/record_phase1_finish_review.py']
    old_index = load(ROOT / 'reports/chroma_index_result.json')
    manifest = {
        'snapshot_id': release.name, 'status': 'REVIEW_SNAPSHOT_NOT_FINAL_RELEASE',
        'created_at': datetime.now().astimezone().isoformat(),
        'kb_sha256': kb_sha256(kb_path.read_bytes()), 'control_count': 101, 'labels_sha256': sha(labels),
        'model': old_index['model'], 'embedding_cache_key': evaluation['embedding_cache_key'],
        'index_count_verified': evaluation['index_count'],
        'index_manifest_sha256': sha(ROOT / 'data/chroma_kb_manifest.json'),
        'top_k': 5, 'aggregation': 'max_chunk_similarity', 'schema_version': 'retriever-0.2',
        'files': {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in tracked},
        'kb_source_text_comparison_complete': True,
        'human_kb_review_complete': False, 'independent_labels_approved': False,
        'keyword_semantic_review_complete': False,
        'actual_llm_integration_verified': False, 'full_upload_db_ui_integration_verified': False,
        'production_relevance_cutoff': None,
        'rebuild_command_from_search_directory': '.venv/Scripts/python.exe -X utf8 chroma_index.py --stage all',
        'recheck_command_from_search_directory': '.venv/Scripts/python.exe -X utf8 tests/evaluate_phase1_finish.py --output-dir reports/new_quality_run',
        'limits': ['모델·DB는 기존 로컬 자산을 사용한다. 스냅샷에 모델 가중치나 DB 원본을 복사하지 않았다.',
                   '검수용 재현 묶음이며 최종 KB 승인·배포 버전·Phase1 완료 선언이 아니다.']}
    (release / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    assert sha(release / 'controls.json') == sha(kb_path)
    assert sha(release / 'source_reviewed_cases.json') == sha(labels)
    cases = {r['id']: r for r in evaluation['cases']}
    failure_notes = ['# 검색 결과의 경계와 남은 평가', '',
        '- 관련 사례 19건의 필수 후보가 모두 Top-5에 포함되었다. 후보 전달 역할에서는 현재 세트의 정답 누락이 없다.',
        '- S10 재해복구 훈련의 필수 후보 2.12.2는 3위다. 재해대응·복구전략 후보가 1·2위라 단일 1위 선택은 오류가 된다.',
        '- R01 퇴사 계정 삭제의 필수 후보 2.5.1은 2위다. 퇴직 및 직무변경 관리 2.2.5가 1위로 경합한다.',
        f"- N01 단어 퍼즐의 1위 점수는 {cases['N01']['top1_score']:.6f}이며 관련 사례의 최저 1위 점수 {evaluation['score_ranges']['RELATED']['min']:.6f}보다 높다. 키워드만으로 관련성을 확정할 수 없다.",
        f"- 정보 부족 사례 최고 1위 점수는 {evaluation['score_ranges']['INSUFFICIENT_INFORMATION']['max']:.6f}다. 정보 부족을 무관이나 충족으로 자동 변환하지 않는다.",
        '- 작은 합성 세트의 점수로 임계값·키워드를 조정하면 평가 세트에 과적합될 수 있어 운영 설정을 변경하지 않았다.',
        '- 다음 품질 평가에는 별도로 확보한 실제 증적과 사람이 승인한 정답이 필요하다. 승인 후 기존 세트를 회귀 검사로 사용한다.',
        '- 실제 LLM으로 NOT_RELATED·UNCERTAIN 및 복수 후보의 인용 검증을 확인해야 한다. 현재 LLM 호출은 0회다.']
    (quality_dir / 'failure_analysis.md').write_text('\n'.join(failure_notes) + '\n', encoding='utf-8')
    print(f'PASS: source differences reviewed and frozen snapshot created: {release}')


if __name__ == '__main__':
    main()
