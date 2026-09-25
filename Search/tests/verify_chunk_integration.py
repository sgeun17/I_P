"""선택 실행: 실제 BGE-M3/101개 ChromaDB로 청크 인계 계약을 검증합니다."""
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator, FormatChecker
from chunk_retriever import retrieve, prepare_input


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main():
    input_schema = read("schemas/chunk_input.schema.json")
    output_schema = read("schemas/chunk_output.schema.json")
    for schema in (input_schema, output_schema):
        Draft202012Validator.check_schema(schema)
    output_validator = Draft202012Validator(output_schema, format_checker=FormatChecker())
    canonical = read("examples/docx_input.json")
    legacy = read("examples/docx_input_legacy.json")
    Draft202012Validator(input_schema).validate(canonical)
    old_result = read("reports/chunk_search_result.json")
    output_validator.validate(old_result)
    assert old_result["success"] and len(old_result["warnings"]) == 9
    cwd = Path.cwd()
    result = retrieve(canonical)
    assert Path.cwd() == cwd, "Python 호출자의 작업 폴더가 바뀌었습니다."
    output_validator.validate(result)
    assert result["success"], result
    assert result["warnings"] == []
    assert result["evidence_chunks"] == canonical["chunks"]
    assert result["retrieval"] == old_result["retrieval"]
    assert len(result["retrieval"]["chunk_results"]) == 3
    assert len(result["retrieval"]["candidates"]) == 5
    assert result["retrieval"]["unit"] == "document"
    for index, group in enumerate(result["retrieval"]["chunk_results"]):
        assert group["chunk_id"] == legacy["chunks"][index]["chunk_id"]
        assert result["evidence_chunks"][index]["text"] == legacy["chunks"][index]["text"]
        assert [r["rank"] for r in group["candidates"]] == [1, 2, 3, 4, 5]
        scores = [r["similarity_score"] for r in group["candidates"]]
        assert scores == sorted(scores, reverse=True)
    controls = {c["control_id"]: c for c in read("controls.json")}
    expected_ids = [c["control_id"] for c in result["retrieval"]["candidates"]]
    assert len(set(expected_ids)) == 5
    assert [c["control_id"] for c in result["candidate_controls"]] == expected_ids
    maximums = {}
    for group in result["retrieval"]["chunk_results"]:
        for c in group["candidates"]:
            maximums[c["control_id"]] = max(maximums.get(c["control_id"], -1), c["similarity_score"])
    for c in result["retrieval"]["candidates"]:
        assert abs(c["similarity_score"] - maximums[c["control_id"]]) < 1e-6
        assert c["best_chunk_id"] in c["matched_chunk_ids"]
    for candidate in result["candidate_controls"]:
        assert candidate["requirement"] == controls[candidate["control_id"]]["requirement"]
    strict_error = retrieve(legacy)
    output_validator.validate(strict_error)
    assert strict_error["success"] is False
    assert strict_error["error"]["code"] == "INVALID_INPUT"
    (ROOT / "examples/docx_output.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "examples/error_output.json").write_text(json.dumps(strict_error, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"status": "PASS", "checked_at": datetime.now().astimezone().isoformat(),
              "checks": ["정식 입력/출력 JSON Schema 검사", "실제 DOCX에서 나온 3개 청크의 본문·ID 보존",
                         "문서 전체 Top-5 중복 제거·순위·최대 유사도 집계", "명령행 호환 모드와 Python 정식 입력 검색 결과 일치",
                         "후보 중복 제거 및 원본 KB 요구사항 연결", "기본 모드에서 잘못된 입력 거부",
                         "다른 작업 폴더에서 호출해도 호출자 폴더 유지"],
              "compatibility_warnings": len(old_result["warnings"]), "chunk_count": 3,
              "candidate_controls_count": len(expected_ids), "kb_sha256": result["index"]["kb_sha256"],
              "scope": "현재 입력팀 DOCX 샘플의 파싱/청킹 반환값을 사용한 검색 모듈 연결 검증. 업로드 서버 연결 및 정답률 평가는 아님."}
    (ROOT / "reports/chunk_integration_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 청크 검색 연결 검증 결과", "", f"- 결과: **PASS** ({report['checked_at']})",
             "- 자료: 입력팀 test1.docx를 실제 파싱·청킹해 얻은 3개 청크",
             "- 증적 ID 1 / 버전 1은 시험용 인자이며 DB에서 조회한 값이 아님",
             "- 검색 대상: ChromaDB isms_p_controls, 인증기준 101개",
             "- 기본 모드 / 임시 호환 모드의 동작 및 JSON 규격 검사 통과",
             "- 원문·청크 ID 보존, 3개 청크 각각 Top-5 반환",
             f"- 문서 전체 Top-5: {len(expected_ids)}개. 같은 통제항목의 청크별 최대 유사도로 집계",
             "- 호환 모드의 필드 보정 경고: 9개. 기본 모드는 같은 구형 입력을 거부함", "",
             "| 청크 | 종류 | 1위 후보 | 유사도 |", "|---|---|---|---|"]
    for chunk, group in zip(result["evidence_chunks"], result["retrieval"]["chunk_results"]):
        top = group["candidates"][0]
        lines.append(f"| {chunk['chunk_id']} | {chunk['chunk_type']} | {top['control_id']} {top['control_name']} | {top['similarity_score']:.6f} |")
    lines += ["", "위 후보는 실제 검색 결과이며 정답으로 확정하거나 정확도를 평가한 것은 아닙니다.",
              "입력팀 원본 코드는 수정하지 않았습니다. 업로드 서버→청킹 자동 연결, 입력팀 규격 정정, 판단팀 규격 합의 및 실제 연동은 남아 있습니다."]
    (ROOT / "reports/chunk_integration_result.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
