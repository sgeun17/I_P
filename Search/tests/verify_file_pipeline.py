"""입력팀 원본을 수정하지 않고 파일→파서→청커→검색→인계 데이터 연결을 검사합니다.

Search 폴더에서 검색 가상환경으로 실행하고 --parser-python에 python-docx가 있는 Python을 지정합니다.
원본 DOCX 외의 자료는 이 검사에서 만드는 합성 파일이며 실제 운영 증적이 아닙니다.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT.parent / "입력"
OUT = ROOT / "reports/file_pipeline_2026-09-24"
FIXTURES = ROOT / "tests/fixtures/file_pipeline"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot():
    return {str(p.relative_to(INPUT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in INPUT.rglob("*") if p.is_file() and p.suffix.lower() in (".py", ".md", ".sql", ".docx")}


def parser_worker(manifest):
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(INPUT), str(INPUT / "chunk"), str(INPUT / "docx-test"), str(INPUT / "txt_csv_test")]
    from extract_docx import extract_docx
    from text_parser import parse_text
    from chunker import make_chunks
    rows = []
    for n, case in enumerate(manifest, 101):
        path = ROOT / case["path"]
        parsed = extract_docx(str(path)) if path.suffix == ".docx" else parse_text(path)
        chunks = make_chunks(parsed, evidence_id=n, version=1)
        envelope = {"evidence_id": n, "version": 1, "source_file": path.name,
                    "file_type": parsed["file_type"], "chunks": chunks, "errors": parsed["errors"]}
        rows.append({"case": case, "parsed": parsed, "payload": envelope})
    return rows


def prepare_cases():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    account = "사용자 계정 신청 및 처리 기록. 신규 입사자의 업무용 계정은 부서장의 승인 후 발급했다. 부서 이동자의 권한은 변경된 업무 범위에 맞춰 조정했고 퇴사자 계정은 퇴사 당일 삭제했다. 승인자와 처리 일시를 대장에 기록했다."
    fixtures = {
        "계정_UTF8.txt": account.encode("utf-8"),
        "계정_CP949.txt": account.encode("cp949"),
        "계정_긴문단.txt": (account + " ").encode("utf-8") * 12,
        "계정과로그.csv": ("항목,운영내역\n계정관리,신규 사용자 계정은 부서장 승인 후 발급하고 퇴사자 계정은 삭제한다. 등록 변경 삭제 처리 이력을 기록한다.\n로그관리,시스템 접속기록은 사용자 식별자와 접속 일시 및 작업 내역을 포함해 정해진 기간 동안 보관하고 위변조와 임의 삭제를 방지한다.\n").encode("utf-8-sig"),
        "무관_식단.txt": "오늘 점심 메뉴는 김치볶음밥과 달걀국이다. 후식으로 귤을 먹었다.".encode("utf-8"),
        "빈파일.txt": b"",
        "손상입력.txt": b"\x00\x01\xff\x00not-a-text-file",
    }
    for name, content in fixtures.items():
        (FIXTURES / name).write_bytes(content)
    specs = [("DOCX", "../입력/docx-test/test1.docx", ["2.5.1", "2.9.4"], None),
             ("UTF8", "계정_UTF8.txt", ["2.5.1"], None),
             ("CP949", "계정_CP949.txt", ["2.5.1"], None),
             ("LONG", "계정_긴문단.txt", ["2.5.1"], None),
             ("CSV", "계정과로그.csv", ["2.5.1", "2.9.4"], None),
             ("UNRELATED", "무관_식단.txt", [], None),
             ("EMPTY", "빈파일.txt", [], "empty_document"),
             ("CORRUPTED", "손상입력.txt", [], "corrupted_file")]
    cases = []
    for cid, name, expected, error in specs:
        relative = name if cid == "DOCX" else "tests/fixtures/file_pipeline/" + name
        cases.append({"id": cid, "path": relative, "expected_ids_draft": expected, "expected_parser_error": error,
                      "origin": "입력팀 제공 샘플" if cid == "DOCX" else "검색팀 합성 시험 파일",
                      "sha256": hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()})
    write_json(FIXTURES / "manifest.json", {"label_status": "검색 실행 전 작성한 예상 ID 초안. 사람 검수 전.", "cases": cases})
    return cases


def consume_contract(result):
    """판단 모델 실행이 아닌, 소비자 입장에서 참조와 필수 데이터를 확인하는 시험입니다."""
    candidates = result["retrieval"]["candidates"]
    chunks = {c["chunk_id"]: c for c in result["evidence_chunks"]}
    controls = {c["control_id"]: c for c in result["candidate_controls"]}
    ids = [c["control_id"] for c in candidates]
    assert len(ids) == len(set(ids)) == result["retrieval"]["top_k"]
    assert [c["rank"] for c in candidates] == list(range(1, len(ids)+1))
    assert ids == list(controls)
    scores = [c["similarity_score"] for c in candidates]
    assert scores == sorted(scores, reverse=True)
    for c in candidates:
        assert c["best_chunk_id"] in chunks and c["best_chunk_id"] in c["matched_chunk_ids"]
        assert c["matched_chunk_ids"] == controls[c["control_id"]]["matched_chunk_ids"]
        assert all(cid in chunks for cid in c["matched_chunk_ids"])
        assert controls[c["control_id"]]["requirement"].strip()
    return [{"control_id": c["control_id"], "requirement": controls[c["control_id"]]["requirement"],
             "best_chunk_id": c["best_chunk_id"], "best_chunk_text": chunks[c["best_chunk_id"]]["text"]} for c in candidates]


def main(parser_python):
    assert Path.cwd().resolve() == ROOT, "Search 폴더에서 실행하세요."
    sys.path.insert(0, str(ROOT))
    from jsonschema import Draft202012Validator
    from chunk_retriever import LocalDocumentRetriever, RetrievalError, prepare_input, error_result
    before = snapshot()
    cases = prepare_cases()  # 예상 ID와 파일 해시를 검색 전에 저장합니다.
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_sha = hashlib.sha256((FIXTURES / "manifest.json").read_bytes()).hexdigest()
    worker = subprocess.run([str(Path(parser_python).resolve()), "-B", "-X", "utf8", str(Path(__file__).resolve()), "--parser-worker"],
                            input=json.dumps(cases, ensure_ascii=False), text=True, encoding="utf-8", capture_output=True, check=True, timeout=60)
    parsed_rows = json.loads(worker.stdout)
    write_json(OUT / "parsed_and_chunks.json", parsed_rows)
    schema = json.loads((ROOT / "schemas/chunk_output.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    engine = LocalDocumentRetriever()
    rows, outputs, consumer_examples = [], {}, []
    for record in parsed_rows:
        case, payload = record["case"], record["payload"]
        cid = case["id"]
        try:
            result = engine.search(payload, compatibility=True)
        except RetrievalError as e:
            result = error_result(e.code, str(e))
        validator.validate(result)
        outputs[cid] = result
        row = {"id": cid, "origin": case["origin"], "file": case["path"], "parser_errors": record["parsed"]["errors"],
               "chunks": len(payload["chunks"]), "success": result["success"], "expected_ids_draft": case["expected_ids_draft"]}
        if case["expected_parser_error"]:
            assert record["parsed"]["errors"] == [case["expected_parser_error"]]
            assert not result["success"] and result["error"]["code"] == "PREPROCESSING_FAILED"
            row.update(check="PASS", search_error=result["error"]["code"])
        else:
            assert result["success"], result
            canonical, _ = prepare_input(payload, compatibility=True)
            assert result["evidence_chunks"] == canonical["chunks"]
            assert [c["text"] for c in result["evidence_chunks"]] == [c["text"] for c in payload["chunks"]]
            consumer_examples.append({"case_id": cid, "note": "판단 모델 없이 ID·요구사항·원문 참조만 확인",
                                      "candidates": consume_contract(result)})
            ids = [c["control_id"] for c in result["retrieval"]["candidates"]]
            row.update(check="PASS", top5=ids, missing_expected=sorted(set(case["expected_ids_draft"])-set(ids)),
                       warnings=len(result["warnings"]), top1_score=result["retrieval"]["candidates"][0]["similarity_score"])
        rows.append(row)
        write_json(OUT / f"{cid}_input.json", payload)
        write_json(OUT / f"{cid}_output.json", result)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    utf8 = outputs["UTF8"]["retrieval"]["candidates"]
    cp949 = outputs["CP949"]["retrieval"]["candidates"]
    assert [(c["control_id"], c["similarity_score"]) for c in utf8] == [(c["control_id"], c["similarity_score"]) for c in cp949]
    assert next(r for r in rows if r["id"] == "LONG")["chunks"] > 1
    after = snapshot()
    assert before == after, "입력팀 파일 변경 발견"
    assert hashlib.sha256((FIXTURES / "manifest.json").read_bytes()).hexdigest() == manifest_sha
    assert all(hashlib.sha256((ROOT/c["path"]).read_bytes()).hexdigest() == c["sha256"] for c in cases)
    report = {"status": "PASS", "checked_at": datetime.now().astimezone().isoformat(),
              "scope": "로컬 파일→입력팀 실제 파서·청커→검색→JSON 소비 참조 검사. 업로드 서버·LLM 판단 실행은 포함하지 않음.",
              "kb_sha256": engine.kb_sha, "manifest_sha256": manifest_sha, "input_files_unchanged": len(before),
              "utf8_cp949_same_result": True, "rows": rows}
    write_json(OUT / "summary.json", report)
    write_json(OUT / "consumer_reference_examples.json", consumer_examples)
    lines = ["# 9/24 파일 기반 검색 연결 검사", "", f"- 실행: {report['checked_at']}", f"- 연결 검사: **{report['status']}**",
             f"- 범위: {report['scope']}", "- 입력팀 제공 DOCX 1건 + 검색팀 합성 TXT/CSV 파일 7건. 실제 운영 증적 8건으로 집계하면 안 됨.",
             "- UTF-8/CP949 동일 본문 검색 순위·점수 일치. 긴 문단의 복수 청크 처리 확인.",
             f"- 입력팀 파일 {len(before)}개 해시 변경 없음. 입력팀 코드 수정 없이 검사 전용 프로세스에서 모듈 경로 설정.",
             "- 예상 ID는 검토 초안이며, 검색 전 manifest에 기록. 누락 발생 시 그대로 기록.", "",
             "| 사례 | 청크 수 | 결과 | Top-5 / 검색 오류 | 예상 ID 누락 |", "|---|---:|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['chunks']} | {r['check']} | {', '.join(r['top5']) if r['success'] else r['search_error']} | {', '.join(r.get('missing_expected', [])) or '-'} |")
    lines += ["", "## 판단팀에 확인할 것", "", "- 출력 JSON의 retrieval.candidates, candidate_controls, evidence_chunks를 실제 판단 코드에서 읽고 실행 가능한지 확인.",
              "- consumer_reference_examples.json은 참조 연결 확인 자료이며 LLM의 판단 결과가 아님.",
              "- UNRELATED도 후보 5개를 반환함. 후보 존재만으로 매핑하지 않고 관련 없음 처리 정책을 판단팀에서 확인.",
              "- EMPTY/CORRUPTED 출력은 success=false. 후보 필드를 읽거나 판단 모델을 호출하지 않아야 함.",
              "- 현재 입력팀 출력은 호환 모드 사용. ID·source_file·source 수정 요청은 기존 요청서 참조."]
    (OUT / "summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parser-python", help="python-docx가 설치된 Python 실행 파일")
    parser.add_argument("--parser-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.parser_worker:
        print(json.dumps(parser_worker(json.load(sys.stdin)), ensure_ascii=False))
    else:
        if not args.parser_python:
            parser.error("--parser-python이 필요합니다.")
        main(args.parser_python)
