"""저장소의 기존 DOCX를 로컬 파서→검색→판단 입력·프롬프트까지 검사한다.

운영 증적 여부나 정답을 가정하지 않는다. 문서 내용은 로컬에만 보존하며 LLM은 호출하지 않는다.
검색 가상환경에서 실행하고 --parser-python으로 python-docx가 있는 Python을 지정한다.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT.parent / 'phase1_입력'
JUDGMENT = ROOT.parent / 'phase1_판단'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parser-python', required=True)
    parser.add_argument('--output-dir', default='reports/repository_documents_2026-09-27')
    args = parser.parse_args()
    assert Path.cwd().resolve() == ROOT, 'phase1_검색에서 실행하세요.'
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(JUDGMENT / 'src')]
    from jsonschema import Draft202012Validator
    from chunk_retriever import LocalDocumentRetriever, prepare_input
    from judgment_adapter import to_mapping_input
    from retrieval_adapter import mapping_input_from_retriever
    from models import MappingInput
    from prompts import build_prompt_package
    from verify_file_pipeline import consume_contract
    from verify_pre_llm_pipeline import fingerprint

    out = ROOT / args.output_dir
    if out.exists():
        raise ValueError('새 출력 디렉토리를 사용해 이전 검사 기록을 보존하세요.')
    before = fingerprint()
    paths = sorted((INPUT / 'uploads/tmp').glob('*.docx'))
    paths += [INPUT / 'docx_test/test1.docx']
    assert paths and all(p.is_file() for p in paths)
    inventory, unique, by_sha = [], [], {}
    for path in paths:
        digest = sha(path)
        relative = '../' + path.relative_to(ROOT.parent).as_posix()
        duplicate_of = by_sha.get(digest)
        entry = {'path': relative, 'sha256': digest, 'size': path.stat().st_size,
                 'provenance': 'PROVIDED_TEST_FIXTURE' if 'docx_test' in path.parts
                               else 'REPOSITORY_UPLOAD_PROVENANCE_NOT_VERIFIED',
                 'duplicate_of': duplicate_of}
        inventory.append(entry)
        if duplicate_of is None:
            entry['id'] = f'D{len(unique) + 1:02d}'
            unique.append(entry.copy())
            by_sha[digest] = relative
    out.mkdir(parents=True)
    manifest = {'created_before_search': True, 'independent_labels_available': False,
                'production_evidence_verified': False, 'inventory': inventory, 'cases': unique}
    save(out / 'manifest.json', manifest)
    manifest_sha = sha(out / 'manifest.json')
    save(out / 'source_fingerprints_before.json', before)
    proc = subprocess.run(
        [str(Path(args.parser_python).resolve()), '-B', '-X', 'utf8',
         str(ROOT / 'tests/verify_file_pipeline.py'), '--parser-worker'],
        input=json.dumps(unique, ensure_ascii=False), capture_output=True,
        text=True, encoding='utf-8', timeout=120)
    if proc.returncode:
        raise RuntimeError(proc.stderr)
    records = json.loads(proc.stdout)
    assert len(records) == len(unique)
    save(out / 'parsed_and_chunks.json', records)
    input_validator = Draft202012Validator(json.loads(
        (ROOT / 'schemas/chunk_input.schema.json').read_text(encoding='utf-8')))
    output_validator = Draft202012Validator(json.loads(
        (ROOT / 'schemas/chunk_output.schema.json').read_text(encoding='utf-8')))
    for record in records:
        assert record['parsed']['errors'] == [], record['case']['id']
        input_validator.validate(record['payload'])
        assert record['payload']['chunks']
        prepare_input(record['payload'], compatibility=False)
    print(f'Parsed {len(records)} unique DOCX files. Loading local BGE-M3.', flush=True)
    engine = LocalDocumentRetriever()
    rows = []
    for record in records:
        case, payload = record['case'], record['payload']
        cid = case['id']
        result = engine.search(payload, compatibility=False)
        output_validator.validate(result)
        assert result['success'] and result['warnings'] == []
        assert result['evidence_chunks'] == payload['chunks']
        references = consume_contract(result)
        model = MappingInput.model_validate(to_mapping_input(result))
        assert mapping_input_from_retriever(result).model_dump() == model.model_dump()
        assert model.evidence_id == payload['evidence_id'] and model.version == payload['version']
        assert [c.model_dump(mode='json') for c in model.chunks] == payload['chunks']
        package = build_prompt_package(model)
        evidence = json.loads(package.user.split('<evidence>\n', 1)[1].split('\n</evidence>', 1)[0])
        assert [c['text'] for c in evidence['chunks']] == [c['text'] for c in payload['chunks']]
        assert [c['source'] for c in evidence['chunks']] == [c['source'] for c in payload['chunks']]
        assert '"similarity_score"' not in package.user and '"rank"' not in package.user
        save(out / f'{cid}_search.json', result)
        save(out / f'{cid}_mapping_input.json', model.model_dump(mode='json'))
        save(out / f'{cid}_prompt.json', {'status': 'PRE_LLM_NOT_SENT', 'prompt_version': package.prompt_version,
                                         'messages': package.openai_compatible_messages(),
                                         'output_schema': package.output_schema})
        save(out / f'{cid}_references.json', references)
        row = {'id': cid, 'file': case['path'], 'provenance': case['provenance'],
               'sha256': case['sha256'], 'parser_block_types': dict(Counter(
                   b['block_type'] for b in record['parsed']['blocks'])),
               'chunk_count': len(model.chunks), 'null_page_count': sum(c.page_start is None for c in model.chunks),
               'sources': sorted({c.source.value for c in model.chunks}),
               'candidate_ids': [c.control_id for c in model.candidate_controls],
               'check': 'PASS', 'search_quality_assessed': False, 'llm_called': False}
        rows.append(row)
        print(json.dumps({'id': cid, 'chunks': row['chunk_count'], 'top5': row['candidate_ids'],
                          'check': row['check']}, ensure_ascii=False), flush=True)
    assert before == fingerprint(), '검사 중 소스 파일 변경 발견'
    assert sha(out / 'manifest.json') == manifest_sha
    assert all(sha(ROOT / e['path']) == e['sha256'] for e in inventory)
    summary = {'status': 'PASS', 'checked_at': datetime.now().astimezone().isoformat(),
               'scope': '기존 DOCX→입력팀 파서·청커→실제 로컬 검색→판단 입력·프롬프트 생성. LLM 전송 없음.',
               'inventory_file_count': len(inventory), 'unique_document_count': len(unique),
               'duplicate_file_count': len(inventory) - len(unique), 'manifest_sha256': manifest_sha,
               'kb_sha256': engine.kb_sha, 'embedding_cache_key': engine.cache_key,
               'sources_unchanged': True, 'source_fingerprint_count': len(before),
               'input_judgment_source_count': sum(k.replace('\\', '/').startswith(
                   ('phase1_입력/', 'phase1_판단/')) for k in before),
               'independent_labels_available': False, 'production_evidence_verified': False,
               'llm_calls': 0, 'rows': rows,
               'limits': ['저장소 업로드 문서의 실제 운영 출처는 확인되지 않았다. 알려진 테스트 DOCX도 포함한다.',
                          '정답이 없어 후보 검색 품질·매핑·충족 판정의 정확도를 계산하지 않는다.',
                          '문서 단위 Top-5는 복수 주제를 모두 망라하는 결과라고 보장하지 않는다.',
                          '업로드 API·DB·화면·실제 LLM 호출·판정은 이 검사에 포함하지 않는다.']}
    save(out / 'summary.json', summary)
    lines = ['# 저장소 DOCX 연결 검사', '', f"- 결과: **{summary['status']}**. {summary['scope']}",
             f'- 기존 파일 {len(inventory)}개 중 해시 중복 {len(inventory) - len(unique)}개를 제외한 고유 문서 {len(unique)}개를 검사했다.',
             '- 원본 파일, 운영 KB, 입력팀·판단팀·검색팀 Python 소스는 검사 전후 동일하다.',
             '- 모든 청크 원문·출처와 DOCX의 null 페이지를 보존하고 판단 입력 모델 및 프롬프트 생성을 확인했다.',
             '- 이 문서들은 운영 정확도 평가 정답 자료로 집계하지 않는다.', '',
             '| ID | 파일 | 청크 수 | 후보 Top-5 | 연결 검사 |', '|---|---|---:|---|---|']
    for r in rows:
        lines.append(f"| {r['id']} | {Path(r['file']).name} | {r['chunk_count']} | {', '.join(r['candidate_ids'])} | {r['check']} |")
    lines += ['', '## 검사 한계', '', *[f'- {limit}' for limit in summary['limits']], '',
              'manifest.json은 검색 전에 기록한 파일·해시·출처 상태다. parsed_and_chunks.json과 사례별 입력·프롬프트·원문 참조는 로컬 검토 자료다.']
    (out / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'status': summary['status'], 'unique_documents': len(unique), 'llm_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
