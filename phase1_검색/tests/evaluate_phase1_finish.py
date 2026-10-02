"""Evaluate frozen source-reviewed development labels with the local index."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kb_identity import kb_sha256
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from chunk_retriever import LocalDocumentRetriever
from evaluate_review_cases import payload
from jsonschema import Draft202012Validator
from retriever import encode_texts


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, default=ROOT / 'tests/source_reviewed_cases_2026-09-27.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'reports/search_quality_2026-09-27')
    args = parser.parse_args()
    labels_path = args.labels.resolve()
    label_hash = sha(labels_path)
    dataset = json.loads(labels_path.read_text(encoding='utf-8'))
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'evaluation.json').exists():
        raise ValueError('Use a new output directory to preserve earlier evaluation')
    for name, digest in dataset['source_dataset_sha256'].items():
        assert sha(ROOT / 'tests' / name) == digest
    validator = Draft202012Validator(json.loads((ROOT / 'schemas/chunk_output.schema.json').read_text(encoding='utf-8')))
    input_validator = Draft202012Validator(json.loads((ROOT / 'schemas/chunk_input.schema.json').read_text(encoding='utf-8')))
    engine = LocalDocumentRetriever()
    assert engine.kb_sha == dataset['kb_sha256']
    rows, outputs = [], []
    for case in dataset['cases']:
        document = payload(case)
        input_validator.validate(document)
        result = engine.search(document)
        validator.validate(result)
        assert result['success']
        candidates = result['retrieval']['candidates']
        ids = [c['control_id'] for c in candidates]
        required = set(case['required_control_ids'])
        ranks = [ids.index(cid) + 1 for cid in required if cid in ids]
        rows.append({'id': case['id'], 'category': case['review_category'], 'required': sorted(required),
                     'top5': ids, 'top1_score': candidates[0]['similarity_score'],
                     'top1_gap': candidates[0]['similarity_score'] - candidates[1]['similarity_score'],
                     'missing_required': sorted(required - set(ids)),
                     'recall': {str(k): len(required & set(ids[:k])) / len(required) if required else None for k in (1, 3, 5)},
                     'reciprocal_rank': 1 / min(ranks) if ranks else 0 if required else None})
        outputs.append({'case_id': case['id'], 'output': result})
        print(f'{case["id"]}: Top-5 {ids}; missing={rows[-1]["missing_required"]}', flush=True)
    # Directly using KB requirement text checks index coverage, not retrieval
    # performance on independent evidence. Keep these diagnostics separate.
    print('Checking all 101 indexed control requirements...', flush=True)
    vectors = encode_texts(engine.model, [c['requirement'] for c in engine.controls])
    probe = engine.collection.query(query_embeddings=vectors.tolist(), n_results=5, include=['distances'])
    coverage = [{'control_id': c['control_id'], 'top5': ids,
                 'self_rank': ids.index(c['control_id']) + 1 if c['control_id'] in ids else None}
                for c, ids in zip(engine.controls, probe['ids'])]
    assert sha(labels_path) == label_hash
    assert kb_sha256((ROOT / 'controls.json').read_bytes()) == engine.kb_sha
    related = [r for r in rows if r['required']]
    metrics = {'categories': dict(Counter(r['category'] for r in rows)), 'related_documents': len(related),
               'all_required_in_top5': sum(not r['missing_required'] for r in related),
               'macro_recall': {str(k): sum(r['recall'][str(k)] for r in related) / len(related) for k in (1, 3, 5)},
               'mean_reciprocal_rank_at_5': sum(r['reciprocal_rank'] for r in related) / len(related),
               'single_required_top1_hits': sum(r['top5'][0] in r['required'] for r in related if len(r['required']) == 1),
               'single_required_documents': sum(len(r['required']) == 1 for r in related)}
    report = {'execution_status': 'PASS', 'checked_at': datetime.now().astimezone().isoformat(),
              'labels_status': dataset['status'], 'labels_sha256': label_hash,
              'kb_sha256': engine.kb_sha, 'embedding_cache_key': engine.cache_key,
              'index_count': engine.collection.count(), 'metrics': metrics, 'cases': rows,
              'index_self_query_diagnostic': {'count': len(coverage), 'found_in_top1': sum(r['self_rank'] == 1 for r in coverage),
                                            'found_in_top5': sum(r['self_rank'] is not None for r in coverage),
                                            'independent_quality_metric': False, 'controls': coverage},
              'score_ranges': {category: {'min': min(r['top1_score'] for r in rows if r['category'] == category),
                                          'max': max(r['top1_score'] for r in rows if r['category'] == category)}
                               for category in ('RELATED', 'NOT_RELATED', 'INSUFFICIENT_INFORMATION')},
              'human_approved': False, 'llm_calls': 0,
              'limits': ['사람 미승인 합성 개발 사례의 후보 검색 평가. 운영 정확도나 최종 관련성 판정 성능이 아님.',
                         '양성 정답은 필수 후보의 부분 목록이므로 Top-5 precision은 계산하지 않는다.',
                         '무관·정보 부족 문서도 후보를 반환한다. 이들의 관련성 판정은 LLM·검토 단계 검증이 필요함.',
                         '101개 자기 질의는 원문을 그대로 사용한 색인 진단이며 별도 품질 지표로 사용하지 않는다.']}
    (out / 'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'search_outputs.json').write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Phase1 검색 후보 품질 재검사', '',
             f"- 실제 BGE-M3·ChromaDB 실행. 문서 {len(rows)}건, 관련 정답 문서 {len(related)}건.",
             f"- 모든 필수 정답 Top-5 포함: {metrics['all_required_in_top5']}/{len(related)}.",
             f"- Recall@1/3/5 평균: {metrics['macro_recall']}.",
             f"- 단일 필수 정답 Top-1: {metrics['single_required_top1_hits']}/{metrics['single_required_documents']}.",
             f"- 101개 자기 질의 색인 진단: Top-5 {report['index_self_query_diagnostic']['found_in_top5']}/101. 독립 성능 평가 아님.",
             f'- 고정 정답 SHA-256: {label_hash}', '',
             '| 사례 | 구분 | 필수 정답 | 실제 Top-5 | 누락 |', '|---|---|---|---|---|']
    for row in rows:
        lines.append(f"| {row['id']} | {row['category']} | {', '.join(row['required']) or '-'} | {', '.join(row['top5'])} | {', '.join(row['missing_required']) or '-'} |")
    lines += ['', '## 관련·무관 점수 범위', '', *[f'- {k}: {v}' for k, v in report['score_ranges'].items()],
              '', '## 평가의 범위', '', *[f'- {limit}' for limit in report['limits']]]
    (out / 'evaluation.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(metrics, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
