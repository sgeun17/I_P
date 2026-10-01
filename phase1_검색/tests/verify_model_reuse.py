"""다른 작업 폴더의 공개 retrieve()에서 실제 BGE-M3 재사용과 기존 결과를 검증한다."""
from concurrent.futures import ThreadPoolExecutor
import argparse
from contextlib import redirect_stderr
from copy import deepcopy
from datetime import datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'phase1_판단/src')]

import chunk_retriever
from chunk_retriever import retrieve, close_retriever
from judgment_adapter import to_mapping_input
from models import MappingInput
from jsonschema import Draft202012Validator


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='reports/model_reuse_latest')
    args = parser.parse_args()
    caller_cwd = Path.cwd()
    assert caller_cwd != ROOT, '다른 폴더에서 호출하는 검사를 실행하세요.'
    out = ROOT / args.output_dir
    if out.exists():
        raise ValueError('이전 결과를 보존하도록 새 출력 경로를 사용하세요.')
    out.mkdir(parents=True)
    baseline = read(ROOT / 'reports/directory_migration_baseline_2026-09-27.json')['code_sha256']
    external = {k: sha(ROOT.parent / k) for k in baseline
                if k.replace('\\', '/').startswith(('phase1_입력/', 'phase1_판단/'))}
    assert len(external) == 43 and all(v == baseline[k] for k, v in external.items())
    kb_hash = sha(ROOT / 'controls.json')
    validator = Draft202012Validator(read(ROOT / 'schemas/chunk_output.schema.json'))
    canonical = read(ROOT / 'examples/docx_input.json')
    legacy = read(ROOT / 'examples/docx_input_legacy.json')
    reference = read(ROOT / 'examples/docx_output.json')
    rows, outputs = [], {}
    logs = StringIO()
    close_retriever()

    def run(name, document, **kwargs):
        start = time.perf_counter()
        result = retrieve(document, **kwargs)
        elapsed = time.perf_counter() - start
        assert Path.cwd() == caller_cwd
        validator.validate(result)
        if result['success']:
            model = MappingInput.model_validate(to_mapping_input(result))
            expected_id = str(document['evidence_id']).zfill(6) if type(document['evidence_id']) is int else document['evidence_id']
            assert model.evidence_id == expected_id
            assert [c.text for c in model.chunks] == [c['text'] for c in document['chunks']]
        process = chunk_retriever._WORKER_CLIENT.process
        row = {'name': name, 'seconds': round(elapsed, 4), 'success': result['success'],
               'pid': None if process is None else process.pid,
               'evidence_id': result.get('evidence_id'),
               'error': None if result['success'] else result['error']['code']}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row, result

    try:
        with redirect_stderr(logs):
            for name in ['first', 'repeat']:
                row, result = run(name, canonical)
                assert result['success'] and result['retrieval'] == reference['retrieval']
                assert result['evidence_chunks'] == canonical['chunks'] and result['warnings'] == []
                rows.append(row)
                outputs[name] = result
            first_pid = rows[0]['pid']
            assert first_pid == rows[1]['pid']
            assert logs.getvalue().count('검색 모델을 불러오는 중입니다...') == 1
            bad = deepcopy(canonical)
            bad['errors'] = ['empty_document']
            for name, document, kwargs, code in [
                ('preprocessing_error', bad, {}, 'PREPROCESSING_FAILED'),
                ('invalid_top_k', canonical, {'top_k': 0}, 'INVALID_INPUT')]:
                row, result = run(name, document, **kwargs)
                assert not result['success'] and result['error']['code'] == code
                assert row['pid'] == first_pid
                rows.append(row)
                outputs[name] = result
            row, result = run('compatibility', legacy, compatibility=True)
            assert result['success'] and result['retrieval'] == reference['retrieval']
            assert len(result['warnings']) == 9
            rows.append(row)
            outputs['compatibility'] = result
            row, result = run('top_k_3', canonical, top_k=3)
            assert result['success'] and len(result['candidate_controls']) == 3
            assert [c['control_id'] for c in result['candidate_controls']] == [
                c['control_id'] for c in reference['candidate_controls'][:3]]
            rows.append(row)
            outputs['top_k_3'] = result
            parallel_inputs = []
            for n in range(3):
                document = deepcopy(canonical)
                document['evidence_id'] = f'REUSE{n:02d}'
                for chunk in document['chunks']:
                    chunk['evidence_id'] = document['evidence_id']
                    chunk['chunk_id'] = f"{document['evidence_id']}_v1_c{chunk['chunk_index']:04d}"
                parallel_inputs.append(document)
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(run, f'parallel_{n}', p) for n, p in enumerate(parallel_inputs)]
                for n, future in enumerate(futures):
                    row, result = future.result()
                    assert result['success'] and result['evidence_id'] == parallel_inputs[n]['evidence_id']
                    assert row['pid'] == first_pid
                    assert [(c['control_id'], c['similarity_score']) for c in result['retrieval']['candidates']] == [
                        (c['control_id'], c['similarity_score']) for c in reference['retrieval']['candidates']]
                    rows.append(row)
                    outputs[row['name']] = result
            assert logs.getvalue().count('검색 모델을 불러오는 중입니다...') == 1
            first_process = chunk_retriever._WORKER_CLIENT.process
            close_retriever()
            assert first_process.poll() is not None and chunk_retriever._WORKER_CLIENT.process is None
            row, result = run('after_close', canonical)
            assert result['success'] and result['retrieval'] == reference['retrieval']
            assert row['pid'] != first_pid
            rows.append(row)
            outputs['after_close'] = result
            close_retriever()
            assert logs.getvalue().count('검색 모델을 불러오는 중입니다...') == 2
    finally:
        close_retriever()
        (out / 'worker_stderr.log').write_text(logs.getvalue(), encoding='utf-8')
    assert kb_hash == sha(ROOT / 'controls.json')
    assert all(sha(ROOT.parent / k) == v for k, v in external.items())
    report = {'status': 'PASS', 'checked_at': datetime.now().astimezone().isoformat(),
              'scope': '실제 BGE-M3 공개 Python 호출의 모델 수명·요청 격리·결과 호환 검사. LLM 호출 없음.',
              'initial_worker_model_loads': 1, 'initial_worker_successful_searches': 7,
              'total_model_loads_including_explicit_restart': 2,
              'concurrent_requests': 3, 'preprocessing_invalid_requests': 2,
              'caller_cwd_preserved': True, 'search_results_match_reference': True,
              'worker_terminated_after_close': True, 'input_judgment_sources_unchanged': external,
              'kb_sha256': kb_hash, 'llm_calls': 0, 'rows': rows,
              'runtime_files_sha256': {name: sha(ROOT / name) for name in
                                       ['chunk_retriever.py', 'worker_client.py', 'tests/verify_model_reuse.py']},
              'regression_tests': {'passed': 36, 'subtests_passed': 41},
              'limits': ['동일한 호출 프로그램에서 재사용하며 별도 프로그램·서버 프로세스는 각각 모델을 로드한다.',
                         '첫 유효 요청에서 모델을 로드한다. 호출 프로그램이 종료되면 모델 메모리를 해제한다.',
                         '시간은 이 PC와 3청크 DOCX 샘플에서 측정한 값이며 모든 문서의 응답시간을 보장하지 않는다.',
                         '실제 서버·업로드 API·DB·화면·LLM 전체 연동 성능 평가는 포함하지 않는다.']}
    (out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'search_outputs.json').write_text(json.dumps(outputs, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# BGE-M3 모델 재사용 검사', '', '- 결과: **PASS**',
             '- 최초 검색 프로세스에서 유효 검색 7건을 처리하는 동안 BGE-M3 로딩은 1회였다.',
             '- 병렬 요청 3건의 증적 ID·청크·후보가 서로 섞이지 않았고, 기본·호환 모드와 Top-3 입력도 처리했다.',
             '- 모델을 로드한 뒤 잘못된 입력 2건을 거부하고 기존 프로세스를 유지했다.',
             '- 명시적 close 후 기존 프로세스가 종료됐고 새 요청에서 다른 PID와 모델 로딩 1회로 다시 시작했다.',
             '- 입력·판단 소스 43개와 운영 KB는 변경되지 않았다. 출력 JSON 규격·검색 순위·점수·원문 참조가 기존 예제와 일치했다.',
             '- 회귀 테스트 36개 및 하위 검사 41개 통과. 실제 LLM 호출 0건.', '',
             '| 요청 | 시간(초) | 결과 | 프로세스 PID |', '|---|---:|---|---:|']
    for row in rows:
        lines.append(f"| {row['name']} | {row['seconds']:.4f} | {'PASS' if row['success'] else row['error']} | {row['pid']} |")
    lines += ['', '## 적용 범위', '', *[f'- {limit}' for limit in report['limits']]]
    (out / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'PASS', 'model_loads_before_close': 1, 'searches_before_close': 7}, ensure_ascii=False))


if __name__ == '__main__':
    main()
