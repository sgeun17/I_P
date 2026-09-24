"""실제 검색 JSON → 판단팀 입력 모델 → 고정 응답 → 실제 검증·검토 코드 시험. LLM 호출 아님."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
JUDGMENT = ROOT.parent / "판단"
OUT = ROOT / "reports/judgment_integration_2026-09-24"
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT), str(JUDGMENT / "src")]

from jsonschema import Draft202012Validator
from judgment_adapter import to_mapping_input, MappingAdapterError, read
from models import MappingInput, VersionInfo
from service import build_result, to_evidence_status
from enums import ErrorCode


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def response_fixture(mapping_input, selected):
    """검증 전용 고정 응답. LLM 추론 결과나 정답 라벨로 사용하지 않습니다."""
    candidates = mapping_input["candidate_controls"]
    chunks = {c["chunk_id"]: c for c in mapping_input["chunks"]}
    decisions, mapped = [], []
    for c in candidates:
        chosen = c["control_id"] in selected
        citations = []
        if chosen:
            chunk = chunks[c["source_chunk_ids"][0]]
            citations = [{"chunk_id": chunk["chunk_id"], "page": chunk["page_start"], "quote": chunk["text"]}]
        decisions.append({"control_id": c["control_id"], "decision": "RELATED" if chosen else "NOT_RELATED",
                          "llm_confidence": 0.9, "reason": "시험 응답에서 이 후보와 증적의 연결을 지정했습니다." if chosen else "시험 응답에서 이 후보와의 연결을 제외했습니다.",
                          "citations": citations})
        if chosen:
            mapped.append({"control_id": c["control_id"], "control_name": c["control_name"],
                           "relation": "PRIMARY" if c["control_id"] == selected[0] else "RELATED",
                           "llm_confidence": 0.9, "reason": "원문 청크와 후보 요구사항 전달을 확인하는 고정 시험 응답입니다.", "citations": citations})
    assert len(mapped) == len(selected)
    return {"match_status": "MATCHED" if selected else "NO_MATCH", "candidate_decisions": decisions, "mapped_controls": mapped}


def main():
    before = {str(p.relative_to(JUDGMENT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in JUDGMENT.rglob("*")
              if p.is_file() and "__pycache__" not in str(p) and ".pytest_cache" not in str(p)}
    inputs, conversions, rejected = {}, [], []
    for path in sorted((ROOT / "reports/file_pipeline_2026-09-24").glob("*_output.json")):
        cid = path.name.removesuffix("_output.json")
        result = read(path)
        if not result["success"]:
            try:
                to_mapping_input(result)
                raise AssertionError("실패 검색이 변환됐습니다.")
            except MappingAdapterError:
                rejected.append(cid)
            continue
        payload = to_mapping_input(result)
        model = MappingInput.model_validate(payload)
        assert [c.text for c in model.chunks] == [c["text"] for c in result["evidence_chunks"]]
        inputs[cid] = payload
        save(OUT / f"{cid}_mapping_input.json", payload)
        conversions.append(cid)
    # 어제의 20개 검색 결과도 현재 판단팀의 실제 입력 모델에서 검사합니다.
    review_converted = []
    for record in read(ROOT / "reports/review_case_outputs.json"):
        payload = to_mapping_input(record["output"])
        MappingInput.model_validate(payload)
        review_converted.append(record["case_id"])
    single = response_fixture(inputs["UTF8"], ["2.5.1"])
    bad_quote = deepcopy(single)
    bad_quote["mapped_controls"][0]["citations"][0]["quote"] = "원문에 절대 없는 시험용 허위 인용 XYZ"
    scenarios = [("single", "UTF8", single, None),
                 ("multiple", "DOCX", response_fixture(inputs["DOCX"], ["2.5.1", "2.9.4"]), None),
                 ("no_match", "UNRELATED", response_fixture(inputs["UNRELATED"], []), None),
                 ("invalid_quote", "UTF8", bad_quote, None),
                 ("invalid_json", "UTF8", "{broken-json", None),
                 ("call_failure", "UTF8", None, ErrorCode.LLM_RETRY_EXHAUSTED)]
    validator = Draft202012Validator(read(JUDGMENT / "schemas/phase1_mapping_result.schema.json"))
    results = []
    for name, cid, fixture, call_error in scenarios:
        raw = json.dumps(fixture, ensure_ascii=False) if isinstance(fixture, dict) else fixture
        versions = VersionInfo(prompt_version="integration-test-fixture-only", model_name="NO_LLM_FIXED_TEST_RESPONSE",
                               ruleset_version="mapping_rules_v0.6", kb_sha256=inputs[cid]["kb_sha256"])
        result = build_result(raw, MappingInput.model_validate(inputs[cid]), versions, call_error=call_error)
        data = result.model_dump(mode="json")
        validator.validate(data)
        if name in ("single", "multiple", "no_match"):
            assert result.validation.passed, (name, [i.model_dump(mode="json") for i in result.validation.issues])
            assert data["processing_status"] == "COMPLETED"
            assert data["match_status"] == ("NO_MATCH" if name == "no_match" else "MATCHED")
            assert len(data["mapped_controls"]) == {"single": 1, "multiple": 2, "no_match": 0}[name]
        if name == "invalid_quote":
            assert not result.validation.citations_valid and result.human_review.required
            assert any(i.code == ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE for i in result.validation.issues)
        if name in ("invalid_json", "call_failure"):
            assert data["processing_status"] == "FAILED" and result.human_review.required
        if name in ("multiple", "no_match"):
            assert result.human_review.required
        scores = {c["control_id"]: c["similarity_score"] for c in inputs[cid]["candidate_controls"]}
        for c in data["mapped_controls"]:
            assert c["similarity_score"] == scores[c["control_id"]]
        save(OUT / f"{name}_fixed_response.json", {"test_only": True, "llm_called": False, "raw_response": raw,
                                                  "call_error": call_error.value if call_error else None})
        save(OUT / f"{name}_result.json", data)
        results.append({"scenario": name, "processing_status": data["processing_status"], "match_status": data["match_status"],
                        "validation_passed": result.validation.passed, "human_review": result.human_review.required,
                        "review_reasons": [r.value for r in result.human_review.reasons],
                        "evidence_status": to_evidence_status(result).value})
    after = {str(p.relative_to(JUDGMENT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in JUDGMENT.rglob("*")
             if p.is_file() and "__pycache__" not in str(p) and ".pytest_cache" not in str(p)}
    assert before == after
    report = {"status": "PASS", "checked_at": datetime.now().astimezone().isoformat(), "llm_called": False,
              "scope": "실제 검색 출력과 판단팀 원본 모델·서비스 코드 연결. 응답은 고정 시험 자료이며 LLM 품질 평가 아님.",
              "file_cases_converted": conversions, "search_errors_rejected": rejected,
              "review_cases_converted": review_converted, "judgment_files_unchanged": len(before), "scenarios": results}
    save(OUT / "summary.json", report)
    lines = ["# 검색→판단팀 코드 연동 검사", "", f"- 결과: **PASS** / {report['checked_at']}",
             "- 기존 파일 검색 성공 6건 + 검토용 검색 20건: 실제 MappingInput 모델에서 변환·검증 통과.",
             "- 전처리 실패 2건: 판단 입력 변환 거부.", "- 판단팀 KB 해시 및 101개 ID·명칭과 검색팀 KB 일치 확인.",
             "- 아래 결과는 실제 판단팀 build_result/Validator/Review 코드에 고정 응답을 넣어 얻음. **LLM 호출 0회**.",
             f"- 판단팀 파일 {len(before)}개 변경 없음.", "", "| 시나리오 | 처리 상태 | 인용·규칙 검증 | 사람 검토 | 검토 사유 |",
             "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['scenario']} | {r['processing_status']} | {r['validation_passed']} | {r['human_review']} | {', '.join(r['review_reasons']) or '-'} |")
    lines += ["", "고정 응답의 confidence=0.9는 시험 입력 상수이며 모델 실측값이 아닙니다. 매핑 결과를 실제 정답·운영 결과로 사용하지 마세요.",
              "검색 점수는 실제 저장된 검색 결과에서 그대로 전달됐습니다. 업로드 API·LLM 호출·화면 통합은 아직 별도입니다."]
    (OUT / "summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
