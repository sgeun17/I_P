"""기존 가상 사례를 Phase2 검수용 입력·고정 응답 검사에 연결한다.

기대값을 응답으로 제공하므로 모델 정확도를 측정하지 않는다. 실제 검색·LLM·
파서·조직 증적·운영 Phase1 확정은 사용하지 않는다. 원래 사례는 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from checklist_store import ChecklistStore
from judgment_review import prepare_judgment_review, check_review_output
from reason_codes import ReasonCatalog

SOURCE_NAMES = ("checklist_draft.json", "review_examples.json", "reason_codes_draft.json",
                "reason_code_examples.json")


def _source_hashes():
    return {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in SOURCE_NAMES}


def _synthetic_mapping(case, control, checklist, evidence_id, chunk_id, quote):
    """검수용 게이트를 시험하는 가상 결과. 실제 매핑·승인 결과가 아니다."""
    return {
        "evidence_id": evidence_id, "version": 1, "processing_status": "COMPLETED",
        "match_status": "MATCHED", "candidate_decisions": [],
        "mapped_controls": [{"control_id": control["control_id"], "control_name": control["control_name"],
                             "relation": "PRIMARY", "llm_confidence": 0.9, "similarity_score": 0.0,
                             "reason": "해당 합성 사례 문항의 ID로 만든 시험용 매핑이다.",
                             "citations": [{"chunk_id": chunk_id, "page": None, "quote": quote}]}],
        "validation": {"passed": True, "schema_valid": True, "control_ids_valid": True,
                       "citations_valid": True, "rules_valid": True, "issues": [], "warnings": []},
        "human_review": {"required": False, "status": "NOT_REQUIRED", "reasons": []},
        "versions": {"prompt_version": "SYNTHETIC_FIXTURE_NOT_EXECUTED",
                     "model_name": "NOT_CALLED", "ruleset_version": "SYNTHETIC_FIXTURE",
                     "kb_sha256": checklist["source"]["sha256"]},
        "trace_id": f"synthetic-{case['case_id']}",
        "created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
    }


def prepare_case(case, assignment, control, checklist, store, catalog):
    """합성 입력과 고정 응답을 분리한다. 기대 판정·사례 해설은 입력에 넣지 않는다."""
    evidence_id = f"SYN-{case['case_id']}"
    chunk_id = f"{evidence_id}_v1_c0000"
    quote = assignment["evidence_excerpt"]
    mapping = _synthetic_mapping(case, control, checklist, evidence_id, chunk_id, quote)
    chunks = [{"chunk_id": chunk_id, "evidence_id": evidence_id, "version": 1,
               "chunk_index": 0, "file_type": "txt", "source_file": f"synthetic_{case['case_id']}.txt",
               "chunk_type": "text", "text": case["evidence_text"], "block_orders": [0],
               "page_start": None, "page_end": None}]
    request = prepare_judgment_review(
        mapping, store, checklist["draft_version"], chunks, item_ids=[case["item_id"]],
        context={"subject": case["assumed_scope"]}, catalog=catalog, allow_draft=True)
    response = {"checklist_version": request.checklist_version, "catalog_version": request.catalog_version,
                "evidence_id": request.evidence_id, "version": request.version,
                "item_results": [{"item_id": case["item_id"], "control_id": control["control_id"],
                                  "result": case["expected_result_draft"], "reason": assignment["rationale"],
                                  "reason_codes": list(assignment["reason_codes_draft"]),
                                  "citations": [{"chunk_id": chunk_id, "page": None, "quote": quote}]}]}
    return request, response


def verify_synthetic_cases():
    """모든 기존 합성 사례의 고정 응답을 검증하고 재현 가능한 범위를 기록한다."""
    before = _source_hashes()
    catalog = ReasonCatalog()
    catalog.validate_examples(allow_draft=True)
    checklist = json.loads((HERE / "checklist_draft.json").read_text(encoding="utf-8"))
    cases = json.loads((HERE / "review_examples.json").read_text(encoding="utf-8"))["cases"]
    assignments = {case["case_id"]: case for case in json.loads(
        (HERE / "reason_code_examples.json").read_text(encoding="utf-8"))["cases"]}
    controls = {item["item_id"]: control for control in checklist["controls"] for item in control["items"]}
    summaries, q01_examples = [], []
    with TemporaryDirectory(prefix="synthetic-phase2-review-") as directory:
        store = ChecklistStore(Path(directory) / "checklists.sqlite3")
        store.import_draft(HERE / "checklist_draft.json")
        for case in cases:
            request, response = prepare_case(case, assignments[case["case_id"]], controls[case["item_id"]],
                                             checklist, store, catalog)
            checked = check_review_output(request, response, store, catalog=catalog, allow_draft=True)
            summaries.append({"case_id": case["case_id"], "item_id": case["item_id"],
                              "provided_result_draft": case["expected_result_draft"],
                              "partial_review": checked["partial_review"],
                              "provided_response_validation_passed": checked["validation"]["passed"],
                              "issue_codes": [row["code"] for row in checked["validation"]["issues"]],
                              "warning_codes": [row["code"] for row in checked["validation"]["warnings"]]})
            if case["item_id"] == "2.5.1-Q01":
                q01_examples.append({"case_id": case["case_id"], "assumed_scope": case["assumed_scope"],
                                     "synthetic_text": case["evidence_text"],
                                     "provided_result_draft": case["expected_result_draft"],
                                     "rationale_draft": assignments[case["case_id"]]["rationale"]})
    if _source_hashes() != before:
        raise ValueError("검증 중 원본 사례 또는 기준이 변경됐습니다.")
    failed = sum(not row["provided_response_validation_passed"] for row in summaries)
    return {
        "status": "SYNTHETIC_FIXED_RESPONSE_FLOW_CHECK", "review_only": True, "synthetic": True,
        "approved": False, "human_approved": False, "actual_evidence_used": False,
        "llm_executed": False, "search_executed": False, "parser_executed": False,
        "semantic_judgment_checked": False, "model_accuracy_measured": False,
        "phase1_mapping_origin": "SYNTHETIC_FIXTURE_FROM_CASE_ITEM_ID",
        "phase1_confirmation_is_operational": False,
        "response_origin": "SYNTHETIC_EXPECTED_DRAFT_FIXTURE",
        "expected_labels_used_as_fixed_response": True,
        "context_construction": "VERBATIM_CASE_ASSUMED_SCOPE_ONLY_OTHER_FIELDS_UNSET",
        "checked_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "source_sha256": before, "sources_unchanged": True,
        "case_count": len(summaries), "question_count": len({row["item_id"] for row in summaries}),
        "counts_by_provided_result": dict(Counter(row["provided_result_draft"] for row in summaries)),
        "provided_response_validation_passed_count": len(summaries) - failed,
        "provided_response_validation_failed_count": failed,
        "limitations": ["기대 판정값을 고정 응답으로 사용하므로 판정 정확도나 모델 성능을 평가하지 않는다.",
                        "Phase1 매핑·검증 통과·NOT_REQUIRED는 합성 fixture 상태이며 실제 승인·매핑이 아니다.",
                        "증적 원문·파일명은 합성이다. 실제 파일 또는 파서를 실행하지 않는다.",
                        "합성 가정 범위를 subject에 보존하며 대상·기간·효력을 자동 추출하지 않는다.",
                        "한 문항씩 부분 검토하며 통제항목 전체 또는 조직의 판정을 계산하지 않는다."],
        "q01_examples": q01_examples, "cases": summaries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="선택적으로 검증 JSON 저장")
    arguments = parser.parse_args()
    protected = {(HERE / name).resolve() for name in SOURCE_NAMES}
    if arguments.output is not None and arguments.output.resolve() in protected:
        parser.error("검증 결과로 원본 기준·사례·사유 코드 파일을 덮어쓸 수 없습니다.")
    report = verify_synthetic_cases()
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "case_count", "question_count",
                     "provided_response_validation_passed_count", "provided_response_validation_failed_count",
                     "llm_executed", "model_accuracy_measured", "sources_unchanged")}, ensure_ascii=False, indent=2))
    return int(report["provided_response_validation_failed_count"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
