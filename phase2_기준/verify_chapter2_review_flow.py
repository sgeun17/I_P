"""전체 초안의 검수 연결 검사. 고정 응답을 사용하며 LLM 정확도를 측정하지 않는다."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from checklist_store import ChecklistStore
from judgment_review import prepare_judgment_review, check_review_output
from reason_codes import ReasonCatalog
from verify_synthetic_review_flow import _synthetic_mapping

HERE = Path(__file__).resolve().parent
FULL = HERE / "chapter2_full_checklist_draft.json"
CATALOG = HERE / "chapter2_reason_codes_draft.json"


def make_request(control, checklist, store, catalog, text, *, item_ids=None):
    """합성 Phase1 확정 fixture이다. 실제 검색·승인으로 표시하지 않는다."""
    evidence_id = "SYN-CHAPTER2"
    chunk_id = evidence_id + "_v1_c0000"
    mapping = _synthetic_mapping({"case_id": control["control_id"]}, control, checklist,
                                 evidence_id, chunk_id, text)
    chunks = [{"chunk_id": chunk_id, "evidence_id": evidence_id, "version": 1,
               "chunk_index": 0, "file_type": "txt", "source_file": "synthetic.txt",
               "chunk_type": "text", "text": text, "block_orders": [0],
               "page_start": None, "page_end": None}]
    return prepare_judgment_review(mapping, store, checklist["draft_version"], chunks,
                                   item_ids=item_ids, catalog=catalog, allow_draft=True,
                                   context={"subject": "가상 A시스템", "period_or_event": "2026년 3분기"})


def fixed_response(request, result="UNKNOWN", code="P2_U_EVIDENCE_INSUFFICIENT"):
    return {"checklist_version": request.checklist_version,
            "catalog_version": request.catalog_version, "evidence_id": request.evidence_id,
            "version": request.version,
            "item_results": [{"item_id": q.item_id, "control_id": q.control_id,
                              "result": result, "reason": "합성 고정 응답의 계약 검증이다.",
                              "reason_codes": [] if result == "MET" else [code],
                              "citations": [] if result == "UNKNOWN" else [{
                                  "chunk_id": request.chunks[0].chunk_id, "page": None,
                                  "quote": request.chunks[0].text}]}
                             for q in request.questions]}


def verify():
    protected = [FULL, CATALOG, HERE / "checklist_draft.json", HERE / "reason_codes_draft.json",
                 HERE / "review_examples.json", HERE / "reason_code_examples.json",
                 HERE / "chapter2_review_cases.json", HERE / "verify_chapter2_review_flow.py"]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    document = json.loads(FULL.read_text(encoding="utf-8"))
    catalog = ReasonCatalog(CATALOG)
    coverage, boundaries = [], []
    with TemporaryDirectory(prefix="chapter2-review-") as directory:
        store = ChecklistStore(Path(directory) / "checklists.sqlite3")
        store.import_draft(HERE / "checklist_draft.json")
        store.import_draft(FULL)
        for control in document["controls"]:
            request = make_request(control, document, store, catalog,
                                   "검토에 필요한 자료가 제출되지 않았다.")
            checked = check_review_output(request, fixed_response(request), store,
                                          catalog=catalog, allow_draft=True)
            assert checked["validation"]["passed"], checked
            assert not checked["validation"]["semantic_judgment_checked"]
            coverage.append({"control_id": control["control_id"],
                             "item_count": len(request.questions), "passed": True})
        cases = json.loads((HERE / "chapter2_review_cases.json").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            control = next(c for c in document["controls"] if c["control_id"] == case["control_id"])
            request = make_request(control, document, store, catalog, case["evidence_text"],
                                   item_ids=[case["item_id"]])
            response = fixed_response(request, case["expected_result_draft"], case.get("reason_code"))
            response["item_results"][0]["reason"] = case["rationale"]
            checked = check_review_output(request, response, store, catalog=catalog, allow_draft=True)
            assert checked["validation"]["passed"], checked
            # A real citation guard must reject a fabricated quote in each MET/NOT_MET case.
            rejected = None
            if case["expected_result_draft"] != "UNKNOWN":
                bad = deepcopy(response)
                bad["item_results"][0]["citations"][0]["quote"] = "원문에 없는 허위 인용"
                rejected = not check_review_output(request, bad, store, catalog=catalog,
                                                   allow_draft=True)["validation"]["passed"]
                assert rejected
            boundaries.append({"case_id": case["case_id"], "item_id": case["item_id"],
                               "fixed_result": case["expected_result_draft"], "passed": True,
                               "fabricated_quote_rejected": rejected})
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    assert before == after
    return {"status": "PASS", "review_only": True, "approved": False, "human_approved": False,
            "llm_executed": False, "actual_evidence_used": False, "semantic_judgment_checked": False,
            "phase1_mapping_origin": "SYNTHETIC_FIXTURE_NOT_OPERATIONAL_APPROVAL",
            "response_origin": "FIXED_EXPECTED_LABELS_NOT_MODEL_OUTPUT",
            "checklist_version": document["draft_version"], "catalog_version": catalog.list_codes(allow_draft=True)["catalog_version"],
            "source_sha256": before, "sources_unchanged": True, "temporary_database_only": True,
            "control_count": len(coverage), "item_count": sum(c["item_count"] for c in coverage),
            "representative_case_count": len(boundaries), "controls": coverage, "cases": boundaries,
            "limits": ["700문항은 자료 부족 UNKNOWN 고정 응답의 구조 연결만 검사했다.",
                       "대표 사례의 정답은 팀 미승인 초안이다. 문항 전체의 의미 정확도나 모델 성능을 검증하지 않았다.",
                       "critical·종합 등급·운영 승인·Phase2 LLM 호출은 포함하지 않는다."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None:
        target = args.output.resolve()
        if target.parent != (HERE / "reports").resolve() or target.exists():
            parser.error("reports 아래의 새 파일에만 저장할 수 있습니다.")
    report = verify()
    if args.output is not None:
        args.output.parent.mkdir(exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["status", "control_count", "item_count", "representative_case_count", "llm_executed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
