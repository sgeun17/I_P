"""Phase1 확정 게이트와 체크리스트 연결의 범위·출처 보존을 검사한다.

검색팀 기존 Pydantic 환경으로 실행한다:
  phase1_검색/.venv/Scripts/python.exe -m unittest discover
      -s phase2_기준/tests -p test_phase1_review_adapter.py -v
저장소 원본은 읽기만 하며 모든 SQLite 쓰기는 임시 폴더에서 수행한다.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from checklist_store import ChecklistStore
from phase1_review_adapter import Phase1MappingResult, ReviewPlanError, prepare_review_plan
from models import CandidateControl, Chunk, MappedControl, MappingInput, VersionInfo
from review import ReviewDecision, apply_review, is_confirmed_for_phase2
from service import build_result


class Phase1ReviewAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="phase1-review-adapter-")
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        self.document = json.loads((HERE / "checklist_draft.json").read_text(encoding="utf-8"))
        self.document["source"]["path"] = str((HERE / self.document["source"]["path"]).resolve())
        for source in self.document["source_documents"]:
            source["path"] = str((HERE / source["path"]).resolve())
        draft = directory / "draft.json"
        draft.write_text(json.dumps(self.document, ensure_ascii=False), encoding="utf-8")
        self.store = ChecklistStore(directory / "checklists.sqlite3")
        self.store.import_draft(draft)
        self.version = self.document["draft_version"]
        self.kb_sha = self.document["source"]["sha256"]
        self.kb = {item["control_id"]: item for item in json.loads(
            Path(self.document["source"]["path"]).read_text(encoding="utf-8"))}
        self.control_ids = [item["control_id"] for item in self.document["controls"]]

    def result(self, control_ids=None, review_status="NOT_REQUIRED", match_status="MATCHED"):
        if control_ids is None:
            control_ids = self.control_ids[:1] if match_status == "MATCHED" else []
        mapped = [{"control_id": control_id,
                   "control_name": self.kb[control_id]["control_name"],
                   "relation": "PRIMARY" if index == 0 else "RELATED",
                   "llm_confidence": 0.88 - index * 0.01,
                   "similarity_score": 0.65 - index * 0.01,
                   "reason": "합성 시험 증적의 계정 관리 내용과 이 통제항목이 관련되어 있다.",
                   "citations": [{"chunk_id": "E-TEST_v2_c0000", "page": None,
                                  "quote": "승인 기록을 확인한 후 계정을 생성한다."}]}
                  for index, control_id in enumerate(control_ids)]
        review = {"required": review_status != "NOT_REQUIRED", "status": review_status,
                  "reasons": ["R205"] if review_status != "NOT_REQUIRED" else []}
        if review_status in ("APPROVED", "MODIFIED", "REJECTED"):
            review.update(reviewer_id="synthetic-reviewer", reviewed_at="2026-09-30T10:00:00+09:00",
                          review_note="내부 연결 시험용 합성 검토 상태")
        return {"evidence_id": "E-TEST", "version": 2, "processing_status": "COMPLETED",
                "match_status": match_status, "mapped_controls": mapped,
                "candidate_decisions": [],
                "validation": {"passed": True, "schema_valid": True, "control_ids_valid": True,
                               "citations_valid": True, "rules_valid": True, "issues": [], "warnings": []},
                "human_review": review,
                "versions": {"prompt_version": "synthetic-test", "model_name": "not-called",
                             "ruleset_version": "synthetic-test", "kb_sha256": self.kb_sha},
                "trace_id": "internal-synthetic-test", "created_at": "2026-09-30T10:00:00+09:00"}

    def prepare(self, result, **kwargs):
        return prepare_review_plan(result, self.store, self.version, allow_draft=True, **kwargs)

    def assert_code(self, code, operation):
        with self.assertRaises(ReviewPlanError) as captured:
            operation()
        self.assertEqual(captured.exception.code, code)
        self.assertIsInstance(captured.exception.details, dict)
        self.assertTrue(str(captured.exception))
        return captured.exception

    def test_all_54_questions_and_source_refs_are_preserved_in_mapping_order(self):
        result = self.result(self.control_ids, review_status="APPROVED")
        plan = self.prepare(result)
        self.assertEqual(plan["question_count"], 54)
        self.assertEqual([item["phase1_mapping"]["control_id"] for item in plan["controls"]], self.control_ids)
        self.assertEqual(plan["checklist_version"], self.version)
        self.assertEqual(plan["checklist_source_sha256"], self.store.list_versions()[0]["source_sha256"])
        self.assertTrue(plan["review_only"])
        self.assertIs(plan["approved"], False)
        self.assertEqual(plan["status"], "DRAFT_FOR_TEAM_REVIEW")
        self.assertIsNone(plan["empty_plan_reason"])
        for index, control in enumerate(self.document["controls"]):
            with self.subTest(control=control["control_id"]):
                item = plan["controls"][index]
                self.assertEqual(item["phase1_mapping"], result["mapped_controls"][index])
                self.assertEqual(item["checklist"], control)
                self.assertEqual(item["source"], self.document["source"])
                self.assertEqual(item["source_documents"], self.document["source_documents"])
                for question in item["checklist"]["items"]:
                    self.assertIsNone(question["critical"])
                    self.assertEqual(question["critical_status"], "UNDECIDED")
                    self.assertTrue(question["source_refs"])
                    self.assertNotIn("result", question)

    def test_draft_permission_is_required_and_truthy_strings_do_not_grant_it(self):
        for permission in (False, None, 1, "true"):
            with self.subTest(permission=permission):
                self.assert_code("DRAFT_NOT_APPROVED", lambda: prepare_review_plan(
                    self.result(), self.store, self.version, allow_draft=permission))

    def test_not_required_approved_and_modified_are_allowed_by_existing_review_gate(self):
        for status in ("NOT_REQUIRED", "APPROVED", "MODIFIED"):
            with self.subTest(status=status):
                result = self.result(review_status=status)
                plan = self.prepare(result)
                self.assertEqual(plan["source_phase1_result"]["human_review"]["status"], status)
                self.assertEqual(plan["source_phase1_result"]["mapped_controls"], result["mapped_controls"])
                self.assertIs(plan["phase1_validation_overridden_by_review"], False)
                self.assertEqual(plan["phase1_validation_scope"], "STORED_RESULT_ONLY")

    def test_pending_and_rejected_review_are_blocked(self):
        for status in ("PENDING", "REJECTED"):
            for validation_passed in (True, False):
                with self.subTest(status=status, validation_passed=validation_passed):
                    result = self.result(review_status=status)
                    result["validation"]["passed"] = validation_passed
                    error = self.assert_code("PHASE1_REVIEW_NOT_CONFIRMED",
                                             lambda: self.prepare(result))
                    self.assertEqual(error.details["review_status"], status)

    def test_failed_output_corrected_by_existing_apply_review_keeps_original_validation(self):
        """판단팀 기존 FAILED→MODIFIED 동작을 실제 함수를 통해 재현한다."""
        control_id = self.control_ids[0]
        mapping_input = MappingInput(
            evidence_id="E-TEST", version=2,
            chunks=[Chunk(chunk_id="E-TEST_v2_c0000", evidence_id="E-TEST", version=2,
                          chunk_index=0, file_type="docx", source_file="synthetic.docx",
                          chunk_type="text", text="승인 기록을 확인한 후 계정을 생성한다.",
                          block_orders=[1])],
            candidate_controls=[CandidateControl(rank=1, control_id=control_id,
                                                 control_name=self.kb[control_id]["control_name"],
                                                 similarity_score=0.65)],
            kb_sha256=self.kb_sha)
        versions = VersionInfo.model_validate(self.result()["versions"])
        failed = build_result("{깨짐", mapping_input, versions)
        original = failed.model_dump(mode="json")
        self.assertEqual(failed.processing_status.value, "FAILED")
        self.assertFalse(failed.validation.passed)
        self.assertTrue(failed.validation.issues)
        decision = ReviewDecision(
            status="MODIFIED", reviewer_id="synthetic-reviewer", note="직접 매핑한 합성 시험",
            modified_match_status="MATCHED",
            modified_controls=[MappedControl.model_validate(self.result()["mapped_controls"][0])])
        confirmed, _ = apply_review(failed, decision, record_id="SYNTHETIC-REVIEW-1")
        confirmed_snapshot = confirmed.model_dump(mode="json")
        self.assertEqual(confirmed.processing_status.value, "COMPLETED")
        self.assertTrue(is_confirmed_for_phase2(confirmed))
        plan = self.prepare(confirmed)
        self.assertTrue(plan["phase1_validation_overridden_by_review"])
        self.assertEqual(plan["phase1_validation_scope"], "STORED_RESULT_ONLY")
        self.assertEqual(plan["source_phase1_result"]["validation"], original["validation"])
        self.assertEqual(plan["source_phase1_result"]["mapped_controls"],
                         confirmed_snapshot["mapped_controls"])
        self.assertEqual(failed.model_dump(mode="json"), original)
        self.assertEqual(confirmed.model_dump(mode="json"), confirmed_snapshot)

    def test_approved_result_overrides_stored_failure_without_erasing_issues(self):
        result = self.result(review_status="PENDING")
        result["validation"]["passed"] = False
        result["validation"]["citations_valid"] = False
        result["validation"]["issues"] = [{"code": "E403", "message": "원문에 없는 인용"}]
        pending = Phase1MappingResult.model_validate(result)
        original = pending.model_dump(mode="json")
        confirmed, _ = apply_review(
            pending, ReviewDecision(status="APPROVED", reviewer_id="synthetic-reviewer"),
            record_id="SYNTHETIC-REVIEW-2")
        plan = self.prepare(confirmed)
        self.assertTrue(plan["phase1_validation_overridden_by_review"])
        self.assertEqual(plan["phase1_validation_scope"], "STORED_RESULT_ONLY")
        self.assertEqual(plan["source_phase1_result"]["validation"], original["validation"])
        self.assertEqual(pending.model_dump(mode="json"), original)
        self.assertEqual(plan["question_count"], len(self.document["controls"][0]["items"]))

    def test_modified_result_with_stored_pass_does_not_claim_to_revalidate_new_citations(self):
        pending = Phase1MappingResult.model_validate(self.result(review_status="PENDING"))
        original = pending.model_dump(mode="json")
        modified = deepcopy(self.result()["mapped_controls"][0])
        modified["citations"][0]["quote"] = "새 인용은 원문과 대조하지 않은 합성 문장이다."
        confirmed, _ = apply_review(
            pending,
            ReviewDecision(status="MODIFIED", reviewer_id="synthetic-reviewer",
                           modified_match_status="MATCHED",
                           modified_controls=[MappedControl.model_validate(modified)]),
            record_id="SYNTHETIC-REVIEW-3")
        snapshot = confirmed.model_dump(mode="json")
        plan = self.prepare(confirmed)
        self.assertIs(plan["phase1_validation_overridden_by_review"], False)
        self.assertEqual(plan["phase1_validation_scope"], "STORED_RESULT_ONLY")
        self.assertEqual(plan["controls"][0]["phase1_mapping"]["citations"][0]["quote"],
                         modified["citations"][0]["quote"])
        self.assertEqual(plan["source_phase1_result"]["validation"], original["validation"])
        self.assertEqual(pending.model_dump(mode="json"), original)
        self.assertEqual(confirmed.model_dump(mode="json"), snapshot)

    def test_failed_pending_and_processing_results_are_blocked(self):
        for status in ("FAILED", "PENDING", "PROCESSING"):
            with self.subTest(status=status):
                result = self.result()
                result["processing_status"] = status
                self.assert_code("PHASE1_NOT_COMPLETED", lambda: self.prepare(result))

    def test_each_validation_flag_must_be_true(self):
        for flag in ("passed", "schema_valid", "control_ids_valid", "citations_valid", "rules_valid"):
            with self.subTest(flag=flag):
                result = self.result()
                result["validation"][flag] = False
                error = self.assert_code("PHASE1_VALIDATION_FAILED", lambda: self.prepare(result))
                self.assertEqual(error.details["failed_flags"], [flag])

    def test_validation_issues_block_even_when_all_flags_claim_success(self):
        result = self.result()
        result["validation"]["issues"] = [{"code": "E403", "message": "인용 원문 없음"}]
        error = self.assert_code("PHASE1_VALIDATION_FAILED", lambda: self.prepare(result))
        self.assertEqual(error.details["issue_codes"], ["E403"])

    def test_non_blocking_warning_is_preserved_and_does_not_block(self):
        result = self.result()
        result["validation"]["warnings"] = [{"code": "E506", "message": "판단 사유가 짧음"}]
        plan = self.prepare(result)
        self.assertEqual(plan["source_phase1_result"]["validation"]["warnings"][0]["code"], "E506")

    def test_missing_invalid_and_mismatched_kb_hashes_are_explicit_errors(self):
        for digest, code in ((None, "KB_HASH_MISSING"), ("", "KB_HASH_MISSING"),
                             ("not-a-hash", "KB_HASH_INVALID"), ("0" * 64, "KB_SOURCE_MISMATCH")):
            with self.subTest(digest=digest):
                result = self.result()
                result["versions"]["kb_sha256"] = digest
                self.assert_code(code, lambda: self.prepare(result))

    def test_missing_checklist_version_does_not_fall_back_to_latest(self):
        self.assert_code("VERSION_NOT_FOUND", lambda: prepare_review_plan(
            self.result(), self.store, "missing-version", allow_draft=True))
        self.assert_code("CHECKLIST_VERSION_REQUIRED", lambda: prepare_review_plan(
            self.result(), self.store, "", allow_draft=True))

    def test_multi_mapping_missing_scope_blocks_whole_plan_with_all_missing_ids(self):
        ids = [self.control_ids[0], "2.2.5", "3.4.1"]
        error = self.assert_code("CHECKLIST_SCOPE_MISSING", lambda: self.prepare(
            self.result(ids, review_status="APPROVED")))
        self.assertEqual(error.details["missing_control_ids"], ["2.2.5", "3.4.1"])
        self.assertEqual(error.details["mapped_control_ids"], ids)
        self.assertNotIn("controls", error.details)

    def test_single_out_of_scope_control_is_not_interpreted_as_no_match(self):
        result = self.result(["2.2.5"])
        self.assert_code("CHECKLIST_SCOPE_MISSING", lambda: self.prepare(result))
        self.assertEqual(result["match_status"], "MATCHED")

    def test_no_match_is_an_explicit_empty_plan_without_document_judgment(self):
        result = self.result(match_status="NO_MATCH", review_status="APPROVED")
        plan = self.prepare(result)
        self.assertEqual(plan["match_status"], "NO_MATCH")
        self.assertEqual(plan["controls"], [])
        self.assertEqual(plan["question_count"], 0)
        self.assertEqual(plan["empty_plan_reason"], "PHASE1_NO_MATCH")
        self.assertEqual(plan["source_versions"]["kb_sha256"], self.kb_sha)
        self.assertNotIn("result", plan)
        self.assertNotIn("overall_result", plan)

    def test_no_match_still_requires_kb_hash_and_confirmed_review(self):
        result = self.result(match_status="NO_MATCH", review_status="PENDING")
        self.assert_code("PHASE1_REVIEW_NOT_CONFIRMED", lambda: self.prepare(result))
        result = self.result(match_status="NO_MATCH", review_status="APPROVED")
        result["versions"]["kb_sha256"] = None
        self.assert_code("KB_HASH_MISSING", lambda: self.prepare(result))

    def test_control_name_must_match_the_selected_kb(self):
        result = self.result()
        result["mapped_controls"][0]["control_name"] = "다른 통제항목 명칭"
        self.assert_code("CONTROL_NAME_MISMATCH", lambda: self.prepare(result))

    def test_dict_and_pydantic_objects_remain_unchanged_and_output_is_detached(self):
        result = self.result()
        original = deepcopy(result)
        plan = self.prepare(result)
        self.assertEqual(result, original)
        plan["source_phase1_result"]["mapped_controls"][0]["reason"] = "검수 계획 편집"
        plan["controls"][0]["phase1_mapping"]["citations"][0]["quote"] = "검수 계획 편집"
        self.assertEqual(result, original)
        model = Phase1MappingResult.model_validate(result)
        snapshot = model.model_dump(mode="json")
        second = self.prepare(model)
        self.assertEqual(model.model_dump(mode="json"), snapshot)
        self.assertEqual(second["source_phase1_result"], snapshot)
        self.assertEqual(second["evidence_id"], "E-TEST")
        self.assertEqual(second["version"], 2)
        self.assertEqual(self.store.get_control(self.version, self.control_ids[0], allow_draft=True)["control"],
                         self.document["controls"][0])

    def test_invalid_input_and_unsafe_model_copy_are_revalidated(self):
        for invalid in (None, [], {"evidence_id": "E-TEST"}):
            with self.subTest(invalid=invalid):
                self.assert_code("INVALID_PHASE1_RESULT", lambda: self.prepare(invalid))
        model = Phase1MappingResult.model_validate(self.result())
        unsafe = model.model_copy(update={"mapped_controls": []})
        self.assert_code("INVALID_PHASE1_RESULT", lambda: self.prepare(unsafe))

    def test_inconsistent_no_match_cannot_hide_mapped_controls(self):
        result = self.result()
        result["match_status"] = "NO_MATCH"
        self.assert_code("INVALID_PHASE1_RESULT", lambda: self.prepare(result))

    def test_version_source_hash_is_checked_on_each_control_response(self):
        read = self.store.get_control
        def changed(*args, **kwargs):
            response = read(*args, **kwargs)
            response["source_sha256"] = "0" * 64
            return response
        with patch.object(self.store, "get_control", side_effect=changed):
            self.assert_code("CHECKLIST_CHANGED_DURING_READ", lambda: self.prepare(self.result()))

    def test_changed_version_metadata_is_checked_after_empty_plan_read(self):
        before = self.store.list_versions()
        after = deepcopy(before)
        after[0]["source_sha256"] = "0" * 64
        with patch.object(self.store, "list_versions", side_effect=[before, after]):
            self.assert_code("CHECKLIST_CHANGED_DURING_READ", lambda: self.prepare(
                self.result(match_status="NO_MATCH", review_status="APPROVED")))

    def test_missing_database_has_a_structured_error_and_is_not_created(self):
        missing = self.store.database.parent / "missing.sqlite3"
        self.assert_code("STORE_MISSING", lambda: prepare_review_plan(
            self.result(), ChecklistStore(missing), self.version, allow_draft=True))
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
