"""저장된 검색 결과→최신 판단 호출부→Phase2 검수 조회를 가상 응답으로 검사한다.

실제 BGE/LLM 호출, 사람의 실제 승인, 문항 충족 판정을 수행하지 않는다.
모든 SQLite 쓰기는 임시 폴더에 한정한다.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
PHASE2 = ROOT.parent / "phase2_기준"
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT), str(PHASE2)]

import httpx

from judgment_adapter import read, to_mapping_input
from judgment_pipeline import JudgmentRequestError, run_judgment
from checklist_store import ChecklistStore
from phase1_review_adapter import ReviewPlanError, prepare_review_plan
from review import ReviewDecision, apply_review
from verify_judgment_integration import response_fixture


def main():
    fixture_dir = ROOT / "reports/file_pipeline_2026-09-24"
    protected = [ROOT / "controls.json", PHASE2 / "checklist_draft.json",
                 PHASE2 / "review_examples.json", PHASE2 / "reason_codes_draft.json"]
    protected.extend(sorted(fixture_dir.glob("*_output.json")))
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    records = []
    with TemporaryDirectory(prefix="team-pipeline-review-") as temporary:
        store = ChecklistStore(Path(temporary) / "review.sqlite3")
        draft = read(PHASE2 / "checklist_draft.json")
        store.import_draft(PHASE2 / "checklist_draft.json")
        scenarios = [
            ("single", "UTF8", ["2.5.1"], 20),
            ("multiple", "UTF8", ["2.5.1", "2.5.6"], 34),
            ("scope_missing", "UTF8", ["2.5.1", "2.2.5"], None),
            ("no_match", "UNRELATED", [], 0),
            ("candidate_quote_invalid", "UTF8", ["2.5.1"], None),
        ]
        for name, case, selected, expected_questions in scenarios:
            search = read(fixture_dir / f"{case}_output.json")
            original = deepcopy(search)
            # 두 목록에 들어간 인용은 실제 HTTP JSON처럼 별도 객체로 분리한다.
            response = json.loads(json.dumps(response_fixture(to_mapping_input(search), selected),
                                             ensure_ascii=False))
            if name == "candidate_quote_invalid":
                decision = next(item for item in response["candidate_decisions"]
                                if item["control_id"] == selected[0])
                decision["citations"][0] = {**decision["citations"][0],
                                            "quote": "원문에 없는 가상 오류 인용 XYZ"}
            raw = json.dumps(response, ensure_ascii=False)
            calls = []

            def handle(request):
                calls.append(str(request.url))
                return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

            with httpx.Client(transport=httpx.MockTransport(handle)) as client:
                run = run_judgment(search, model="MOCK_ONLY_NO_LLM", http_client=client,
                                   sleeper=lambda _: None, trace_id=f"synthetic-{name}")
            assert search == original
            assert len(calls) == 1
            result = run.mapping_result
            record = {"scenario": name, "http_transport": "httpx.MockTransport",
                      "processing_status": result.processing_status.value,
                      "validation_passed": result.validation.passed,
                      "issue_codes": [item.code.value for item in result.validation.issues],
                      "evidence_status": run.evidence_status.value,
                      "review_reasons": [item.value for item in result.human_review.reasons],
                      "attempts": run.llm_run.attempts,
                      "retry_count": run.llm_run.retry_count,
                      "synthetic_review_transition": False}
            if name == "candidate_quote_invalid":
                assert not result.validation.citations_valid
                assert "E403" in record["issue_codes"] and "R104" in record["review_reasons"]
                assert record["evidence_status"] == "REVIEW_REQUIRED"
                try:
                    prepare_review_plan(result, store, draft["draft_version"], allow_draft=True)
                except ReviewPlanError as error:
                    record["review_plan_blocked"] = error.code
                else:
                    raise AssertionError("미확정된 잘못된 인용 결과가 질문 조회로 연결됐습니다.")
            else:
                assert result.validation.passed
                if result.human_review.required:
                    try:
                        prepare_review_plan(result, store, draft["draft_version"], allow_draft=True)
                    except ReviewPlanError as error:
                        assert error.code == "PHASE1_REVIEW_NOT_CONFIRMED"
                        record["pending_review_blocked"] = True
                    else:
                        raise AssertionError("대기 중 검토 결과가 연결됐습니다.")
                    # 연결 상태 전이 검사만을 위한 합성 승인이다. 실제 증적 승인으로 기록하지 않는다.
                    result, _ = apply_review(
                        result, ReviewDecision(status="APPROVED", reviewer_id="SYNTHETIC_CONNECTION_CHECK",
                                               note="가상 응답의 연결 상태 전이 시험이며 실제 승인이 아님"),
                        record_id=f"synthetic-{name}",
                    )
                    record["synthetic_review_transition"] = True
                try:
                    plan = prepare_review_plan(result, store, draft["draft_version"], allow_draft=True)
                except ReviewPlanError as error:
                    assert name == "scope_missing" and error.code == "CHECKLIST_SCOPE_MISSING"
                    assert error.details["missing_control_ids"] == ["2.2.5"]
                    record["review_plan_blocked"] = error.code
                    record["missing_control_ids"] = error.details["missing_control_ids"]
                else:
                    assert name != "scope_missing"
                    assert plan["question_count"] == expected_questions
                    assert plan["review_only"] is True and plan["approved"] is False
                    assert [item["phase1_mapping"]["control_id"] for item in plan["controls"]] == selected
                    record["question_count"] = plan["question_count"]
                    record["review_only"] = plan["review_only"]
            records.append(record)

        with httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(404, json={"error": {"message": "synthetic missing model"}}))) as client:
            try:
                run_judgment(read(fixture_dir / "UTF8_output.json"), model="MOCK_ONLY_NO_LLM",
                             http_client=client, sleeper=lambda _: None)
            except JudgmentRequestError as error:
                assert error.status_code == 404
                assert error.run_result.attempts == 1 and error.run_result.retry_count == 0
                records.append({"scenario": "http_4xx", "request_error_status": error.status_code,
                                "mapping_result_created": False, "retry_count": 0})
            else:
                raise AssertionError("HTTP 4xx가 명시적 요청 오류로 중단되지 않았습니다.")

    assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    report = {"status": "PASS", "checked_at": datetime.now().astimezone().isoformat(),
              "live_llm_calls": 0, "bge_model_loaded": False, "test_only": True,
              "scope": "저장된 실제 검색 출력과 가상 LLM HTTP 응답으로 연결 동작만 검사",
              "accuracy_evaluation": False, "protected_files_unchanged_during_run": len(protected),
              "cases": records}
    output = ROOT / "reports/team_integration_2026-09-30/summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
