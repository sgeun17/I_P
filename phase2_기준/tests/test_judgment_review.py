"""검수용 Phase2 입출력의 경계와 원문 인용을 검사한다.

실제 LLM이나 조직 판정 정확도를 검사하지 않는다. 원본 초안은 읽기만 하고
SQLite와 변경 검증용 카탈로그는 임시 폴더에 둔다.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from checklist_store import ChecklistStore
from judgment_review import (
    JudgmentReviewError,
    JudgmentReviewInput,
    JudgmentReviewOutput,
    check_review_output,
    prepare_judgment_review,
)
from models import Chunk
from phase1_review_adapter import Phase1MappingResult
from reason_codes import ReasonCatalog


class JudgmentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="phase2-judgment-review-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.document = json.loads((HERE / "checklist_draft.json").read_text(encoding="utf-8"))
        self.store = ChecklistStore(self.directory / "checklists.sqlite3")
        # 경로를 다시 쓰면 원본 파일 해시가 달라진다. 실제 원본을 직접 읽는다.
        self.store.import_draft(HERE / "checklist_draft.json")
        self.version = self.document["draft_version"]
        self.kb_sha = self.document["source"]["sha256"]
        self.control_ids = [control["control_id"] for control in self.document["controls"]]
        self.names = {control["control_id"]: control["control_name"]
                      for control in json.loads((HERE / self.document["source"]["path"]).read_text(encoding="utf-8"))}
        self.catalog = ReasonCatalog()
        self.catalog_version = self.catalog.list_codes(allow_draft=True)["catalog_version"]
        self.quote = "승인 기록을 확인한 후 계정을 생성한다."
        self.context = {"subject": "합성 시험 시스템", "period_or_event": "합성 시험 기간",
                        "applicable_basis": "합성 시험용 기준", "effective_basis": None}

    def phase1(self, control_ids=None, review_status="NOT_REQUIRED", match_status="MATCHED"):
        if control_ids is None:
            control_ids = self.control_ids[:1] if match_status == "MATCHED" else []
        controls = [{"control_id": control_id, "control_name": self.names[control_id],
                     "relation": "PRIMARY" if index == 0 else "RELATED",
                     "llm_confidence": 0.88, "similarity_score": 0.65,
                     "reason": "합성 시험용 매핑이다.",
                     "citations": [{"chunk_id": "E-TEST_v2_c0000", "page": None,
                                    "quote": self.quote}]}
                    for index, control_id in enumerate(control_ids)]
        review = {"required": review_status != "NOT_REQUIRED", "status": review_status,
                  "reasons": ["R205"] if review_status != "NOT_REQUIRED" else []}
        if review_status in ("APPROVED", "MODIFIED", "REJECTED"):
            review.update(reviewer_id="synthetic-reviewer",
                          reviewed_at="2026-10-01T10:00:00+09:00",
                          review_note="합성 검수 시험")
        return {"evidence_id": "E-TEST", "version": 2, "processing_status": "COMPLETED",
                "match_status": match_status, "mapped_controls": controls,
                "candidate_decisions": [],
                "validation": {"passed": True, "schema_valid": True, "control_ids_valid": True,
                               "citations_valid": True, "rules_valid": True, "issues": [], "warnings": []},
                "human_review": review,
                "versions": {"prompt_version": "synthetic-test", "model_name": "not-called",
                             "ruleset_version": "synthetic-test", "kb_sha256": self.kb_sha},
                "trace_id": "synthetic-review-test", "created_at": "2026-10-01T10:00:00+09:00"}

    def chunk(self, *, evidence_id="E-TEST", version=2, index=0, paged=False):
        return Chunk(chunk_id=f"{evidence_id}_v{version}_c{index:04d}",
                     evidence_id=evidence_id, version=version, chunk_index=index,
                     file_type="pdf" if paged else "docx", source_file="synthetic.pdf" if paged else "synthetic.docx",
                     chunk_type="text", text=self.quote, block_orders=[1],
                     page_start=3 if paged else None, page_end=4 if paged else None)

    def prepare(self, *, phase1=None, chunks=None, item_ids=None, context=None, catalog=None):
        return prepare_judgment_review(
            self.phase1() if phase1 is None else phase1, self.store, self.version,
            [self.chunk()] if chunks is None else chunks,
            context=self.context if context is None else context,
            item_ids=["2.5.1-Q01"] if item_ids is None else item_ids,
            catalog=self.catalog if catalog is None else catalog, allow_draft=True)

    def response(self, *, result="MET", item_ids=None, page=None):
        if item_ids is None:
            item_ids = ["2.5.1-Q01"]
        codes = {"MET": [], "NOT_MET": ["P2_NM_RULE_NOT_DEFINED"],
                 "UNKNOWN": ["P2_U_EVIDENCE_INSUFFICIENT"]}[result]
        return {"checklist_version": self.version, "catalog_version": self.catalog_version,
                "evidence_id": "E-TEST", "version": 2,
                "item_results": [{"item_id": item_id, "control_id": item_id.split("-Q")[0],
                                  "result": result, "reason": "합성 응답의 구조 검증용 사유다.",
                                  "reason_codes": list(codes),
                                  "citations": [] if result == "UNKNOWN" else [
                                      {"chunk_id": "E-TEST_v2_c0000", "page": page, "quote": self.quote}]}
                                 for item_id in item_ids]}

    def check(self, request, response, *, catalog=None):
        return check_review_output(request, response, self.store,
                                   catalog=self.catalog if catalog is None else catalog, allow_draft=True)

    def assert_error(self, operation, code=None):
        with self.assertRaises(JudgmentReviewError) as captured:
            operation()
        self.assertTrue(captured.exception.code)
        self.assertTrue(str(captured.exception))
        if code is not None:
            self.assertEqual(captured.exception.code, code)
        return captured.exception

    def assert_failed(self, request, response, flag):
        checked = self.check(request, response)
        self.assertIs(checked["validation"]["passed"], False)
        self.assertIs(checked["validation"][flag], False)
        self.assertTrue(checked["validation"]["issues"])
        self.assertIs(checked["review_required"], True)
        self.assertIs(checked["validation"]["semantic_judgment_checked"], False)
        return checked

    def test_valid_response_is_review_only_and_does_not_claim_accuracy(self):
        request = self.prepare()
        self.assertIsInstance(request, JudgmentReviewInput)
        checked = self.check(request, self.response())
        for flag in ("passed", "schema_valid", "question_coverage_valid", "reasons_valid", "citations_valid"):
            self.assertIs(checked["validation"][flag], True)
        self.assertEqual(checked["validation"]["issues"], [])
        self.assertIs(checked["validation"]["semantic_judgment_checked"], False)
        for flag, expected in (("review_only", True), ("approved", False), ("human_approved", False),
                               ("llm_executed", False), ("review_required", True)):
            self.assertIs(checked[flag], expected)
        self.assertEqual(checked["parsed_output"], self.response())

    def test_dict_json_and_model_outputs_are_equivalent(self):
        request = self.prepare()
        raw = self.response()
        expected = self.check(request, raw)
        for response in (json.dumps(raw, ensure_ascii=False), JudgmentReviewOutput.model_validate(raw)):
            with self.subTest(kind=type(response).__name__):
                self.assertEqual(self.check(request, response), expected)

    def test_draft_permission_is_required_for_prepare_and_check(self):
        request = self.prepare()
        for permission in (False, None, 1, "true"):
            with self.subTest(permission=permission):
                self.assert_error(lambda: prepare_judgment_review(
                    self.phase1(), self.store, self.version, [self.chunk()], allow_draft=permission),
                    "DRAFT_NOT_APPROVED")
                self.assert_error(lambda: check_review_output(
                    request, self.response(), self.store, allow_draft=permission), "DRAFT_NOT_APPROVED")

    def test_pending_phase1_is_not_allowed(self):
        self.assert_error(lambda: self.prepare(phase1=self.phase1(review_status="PENDING")),
                          "PHASE1_REVIEW_NOT_CONFIRMED")

    def test_no_match_does_not_generate_a_met_or_unknown_question(self):
        self.assert_error(lambda: self.prepare(phase1=self.phase1(match_status="NO_MATCH")),
                          "NO_PHASE2_QUESTIONS")

    def test_partial_selection_cannot_hide_out_of_scope_mapping(self):
        mapped = self.phase1(["2.5.1", "2.2.5"], review_status="APPROVED")
        self.assert_error(lambda: self.prepare(phase1=mapped), "CHECKLIST_SCOPE_MISSING")

    def test_full_mapping_preserves_all_54_questions_and_unapproved_criteria(self):
        phase1 = self.phase1(self.control_ids)
        request = prepare_judgment_review(phase1, self.store, self.version, [self.chunk()],
                                          context=self.context, catalog=self.catalog, allow_draft=True)
        self.assertEqual(request.available_question_count, 54)
        self.assertEqual(len(request.questions), 54)
        self.assertFalse(request.partial_review)
        self.assertEqual(request.mapped_control_ids, self.control_ids)
        self.assertEqual(request.source_phase1_result,
                         Phase1MappingResult.model_validate(phase1).model_dump(mode="json"))
        self.assertEqual(request.checklist_source_sha256,
                         hashlib.sha256((HERE / "checklist_draft.json").read_bytes()).hexdigest())
        self.assertEqual(request.kb_sha256, self.kb_sha)
        self.assertEqual(request.catalog_sha256, self.catalog.sha256)
        self.assertEqual(request.catalog_version, self.catalog_version)
        expected = [dict(item, applicability_condition=item.get("applicability_condition"))
                    for control in self.document["controls"] for item in control["items"]]
        self.assertEqual([item.model_dump(mode="json") for item in request.questions], expected)
        for question in request.questions:
            self.assertIsNone(question.critical)
            self.assertEqual(question.critical_status, "UNDECIDED")
        self.assertEqual(request.reason_definitions, self.catalog.list_codes(allow_draft=True)["codes"])
        self.assertEqual(request.source_documents, self.document["source_documents"])
        checked = self.check(request, self.response(item_ids=[item.item_id for item in request.questions]))
        self.assertTrue(checked["validation"]["passed"])
        self.assertFalse(checked["partial_review"])

    def test_multi_mapping_partial_selection_is_explicit_and_keeps_canonical_order(self):
        phase1 = self.phase1(self.control_ids[:2])
        request = self.prepare(phase1=phase1, item_ids=["2.5.6-Q01", "2.5.1-Q01"])
        self.assertEqual(request.available_question_count, 34)
        self.assertTrue(request.partial_review)
        self.assertEqual(request.mapped_control_ids, self.control_ids[:2])
        self.assertEqual([item.item_id for item in request.questions], ["2.5.1-Q01", "2.5.6-Q01"])
        checked = self.check(request, self.response(item_ids=["2.5.6-Q01", "2.5.1-Q01"]))
        self.assertTrue(checked["validation"]["passed"])
        self.assertTrue(checked["partial_review"])

    def test_invalid_or_retired_selection_is_rejected(self):
        for item_ids in ([], ["2.5.1-Q01", "2.5.1-Q01"], ["2.5.1-Q12"],
                         ["2.5.6-Q01"], ["2.5.1-Q99"], "2.5.1-Q01"):
            with self.subTest(item_ids=item_ids):
                self.assert_error(lambda: self.prepare(item_ids=item_ids))

    def test_evidence_and_version_of_all_chunks_must_match(self):
        for chunks in ([], [self.chunk(), self.chunk()], [self.chunk(evidence_id="OTHER")],
                       [self.chunk(version=3)], [self.chunk(), self.chunk(evidence_id="OTHER", index=1)]):
            with self.subTest(chunks=[chunk.chunk_id for chunk in chunks]):
                self.assert_error(lambda: self.prepare(chunks=chunks))

    def test_invalid_context_is_not_coerced(self):
        for context in ({"subject": 7}, {"subject": True}, {"invented": "추가 입력"}):
            with self.subTest(context=context):
                self.assert_error(lambda: self.prepare(context=context))

    def test_missing_context_stays_missing_and_requires_human_review(self):
        request = prepare_judgment_review(self.phase1(), self.store, self.version, [self.chunk()],
                                          item_ids=["2.5.1-Q01"], catalog=self.catalog, allow_draft=True)
        self.assertEqual(request.context.model_dump(), {key: None for key in self.context})
        checked = self.check(request, self.response())
        self.assertTrue(checked["validation"]["passed"])
        self.assertTrue(checked["review_required"])
        self.assertFalse(checked["validation"]["semantic_judgment_checked"])
        warning = next(row for row in checked["validation"]["warnings"]
                       if row["code"] == "REVIEW_CONTEXT_INCOMPLETE")
        self.assertEqual(set(warning["fields"]), set(self.context))

    def test_unknown_is_a_valid_explicit_row_and_empty_output_is_not_unknown(self):
        request = self.prepare()
        checked = self.check(request, self.response(result="UNKNOWN"))
        self.assertIs(checked["validation"]["passed"], True)
        self.assertEqual(checked["parsed_output"]["item_results"][0]["result"], "UNKNOWN")
        empty = self.response()
        empty["item_results"] = []
        failed = self.assert_failed(request, empty, "question_coverage_valid")
        self.assertEqual(failed["parsed_output"]["item_results"], [])

    def test_malformed_json_and_non_object_response_are_schema_errors(self):
        request = self.prepare()
        for response in ("{깨짐", "[]", [], None, 42):
            with self.subTest(response=response):
                checked = self.assert_failed(request, response, "schema_valid")
                self.assertIsNone(checked["parsed_output"])

    def test_duplicate_json_keys_are_not_silently_overwritten(self):
        raw = json.dumps(self.response(), ensure_ascii=False)
        raw = raw.replace('"version": 2', '"version": 3, "version": 2', 1)
        checked = self.assert_failed(self.prepare(), raw, "schema_valid")
        self.assertIsNone(checked["parsed_output"])

    def test_unsafe_output_model_copy_is_revalidated(self):
        model = JudgmentReviewOutput.model_validate(self.response())
        invalid = model.model_copy(update={"version": True})
        self.assertIsNone(self.assert_failed(self.prepare(), invalid, "schema_valid")["parsed_output"])

    def test_missing_required_and_extra_fields_are_schema_errors(self):
        request = self.prepare()
        variants = []
        for key in self.response():
            raw = self.response()
            del raw[key]
            variants.append(raw)
        raw = self.response()
        raw["overall_result"] = "MET"
        variants.append(raw)
        raw = self.response()
        raw["item_results"][0]["critical"] = True
        variants.append(raw)
        raw = self.response()
        raw["item_results"][0]["citations"][0]["invented"] = "추가"
        variants.append(raw)
        for index, response in enumerate(variants):
            with self.subTest(index=index):
                self.assertIsNone(self.assert_failed(request, response, "schema_valid")["parsed_output"])

    def test_response_types_blank_reason_and_invalid_enum_are_rejected(self):
        request = self.prepare()
        variants = []
        for version in ("2", True, 2.0):
            raw = self.response()
            raw["version"] = version
            variants.append(raw)
        for key, value in (("result", "PASS"), ("reason", "  \n "), ("reason_codes", "P2_U_EVIDENCE_INSUFFICIENT")):
            raw = self.response()
            raw["item_results"][0][key] = value
            variants.append(raw)
        for page in ("3", True):
            raw = self.response()
            raw["item_results"][0]["citations"][0]["page"] = page
            variants.append(raw)
        for index, response in enumerate(variants):
            with self.subTest(index=index):
                self.assertIsNone(self.assert_failed(request, response, "schema_valid")["parsed_output"])

    def test_missing_duplicate_extra_retired_and_wrong_control_rows_are_not_repaired(self):
        request = self.prepare(item_ids=["2.5.1-Q01", "2.5.1-Q02"])
        original = self.response(item_ids=["2.5.1-Q01", "2.5.1-Q02"])
        variants = []
        raw = deepcopy(original)
        raw["item_results"].pop()
        variants.append(raw)
        raw = deepcopy(original)
        raw["item_results"].append(deepcopy(raw["item_results"][0]))
        variants.append(raw)
        for item_id in ("2.5.1-Q03", "2.5.1-Q12", "2.5.6-Q01"):
            raw = deepcopy(original)
            raw["item_results"][1]["item_id"] = item_id
            raw["item_results"][1]["control_id"] = item_id.split("-Q")[0]
            variants.append(raw)
        raw = deepcopy(original)
        raw["item_results"][0]["control_id"] = "2.5.6"
        variants.append(raw)
        for index, response in enumerate(variants):
            with self.subTest(index=index):
                checked = self.assert_failed(request, response, "question_coverage_valid")
                self.assertEqual(checked["parsed_output"], response)

    def test_response_identity_and_versions_must_match_the_request(self):
        request = self.prepare()
        for field, value in (("checklist_version", "different-checklist"), ("catalog_version", "different-catalog"),
                             ("evidence_id", "OTHER"), ("version", 3)):
            response = self.response()
            response[field] = value
            with self.subTest(field=field):
                checked = self.check(request, response)
                self.assertFalse(checked["validation"]["passed"])
                self.assertTrue(checked["validation"]["issues"])
                self.assertEqual(checked["parsed_output"], response)

    def test_reason_codes_are_checked_against_the_catalog(self):
        request = self.prepare()
        cases = (("MET", ["P2_U_EVIDENCE_INSUFFICIENT"]), ("UNKNOWN", []),
                 ("NOT_MET", []), ("UNKNOWN", ["P2_NM_RULE_NOT_DEFINED"]),
                 ("NOT_MET", ["P2_U_EVIDENCE_INSUFFICIENT"]),
                 ("UNKNOWN", ["P2_U_UNREGISTERED"]),
                 ("UNKNOWN", ["P2_U_EVIDENCE_INSUFFICIENT", "P2_U_EVIDENCE_INSUFFICIENT"]))
        for result, codes in cases:
            response = self.response(result=result)
            response["item_results"][0]["reason_codes"] = codes
            with self.subTest(result=result, codes=codes):
                checked = self.assert_failed(request, response, "reasons_valid")
                self.assertEqual(checked["parsed_output"], response)

    def test_met_and_not_met_require_citations(self):
        request = self.prepare()
        for result in ("MET", "NOT_MET"):
            response = self.response(result=result)
            response["item_results"][0]["citations"] = []
            with self.subTest(result=result):
                self.assert_failed(request, response, "citations_valid")

    def test_not_met_with_valid_reference_is_only_structurally_validated(self):
        checked = self.check(self.prepare(), self.response(result="NOT_MET"))
        self.assertTrue(checked["validation"]["passed"])
        # 이 인용이 미수립을 뜻하는지는 여기서 검사하지 않는다.
        self.assertFalse(checked["validation"]["semantic_judgment_checked"])
        self.assertTrue(checked["review_required"])

    def test_fake_quote_unknown_chunk_and_other_evidence_citations_fail(self):
        request = self.prepare()
        for field, value in (("quote", "원문에 없는 허위 인용"), ("chunk_id", "E-TEST_v2_c0099"),
                             ("chunk_id", "OTHER_v2_c0000"), ("page", 3)):
            response = self.response()
            response["item_results"][0]["citations"][0][field] = value
            with self.subTest(field=field, value=value):
                checked = self.assert_failed(request, response, "citations_valid")
                self.assertEqual(checked["parsed_output"], response)

    def test_optional_unknown_citations_are_still_validated(self):
        response = self.response(result="UNKNOWN")
        response["item_results"][0]["citations"] = [
            {"chunk_id": "E-TEST_v2_c0000", "page": None, "quote": "원문에 없는 문장"}]
        self.assert_failed(self.prepare(), response, "citations_valid")

    def test_page_range_error_is_blocking_but_missing_page_is_a_warning(self):
        request = self.prepare(chunks=[self.chunk(paged=True)])
        valid = self.check(request, self.response(page=3))
        self.assertTrue(valid["validation"]["passed"])
        self.assertNotIn("E407", json.dumps(valid["validation"]["warnings"]))
        missing = self.check(request, self.response(page=None))
        self.assertTrue(missing["validation"]["passed"])
        self.assertTrue(missing["validation"]["citations_valid"])
        self.assertTrue(missing["validation"]["warnings"])
        self.assertIn("E407", json.dumps(missing["validation"]["warnings"]))
        failed = self.assert_failed(request, self.response(page=5), "citations_valid")
        self.assertIn("E404", json.dumps(failed["validation"]["issues"]))

    def test_whitespace_normalization_reuses_the_existing_citation_policy(self):
        response = self.response()
        response["item_results"][0]["citations"][0]["quote"] = "승인   기록을\n확인한 후 계정을 생성한다."
        self.assertTrue(self.check(self.prepare(), response)["validation"]["passed"])

    def test_source_data_phase1_chunks_and_responses_are_unchanged(self):
        protected = [HERE / name for name in ("checklist_draft.json", "review_examples.json", "reason_codes_draft.json")]
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        phase1, chunks, context, response = self.phase1(), [self.chunk()], deepcopy(self.context), self.response()
        before = deepcopy((phase1, [chunk.model_dump(mode="json") for chunk in chunks], context, response))
        request = self.prepare(phase1=phase1, chunks=chunks, context=context)
        self.check(request, response)
        self.assertEqual((phase1, [chunk.model_dump(mode="json") for chunk in chunks], context, response), before)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}, hashes)

    def test_changed_request_criteria_source_and_reason_definitions_are_rejected(self):
        request = self.prepare()
        before = request.model_dump(mode="json")
        variants = []
        for field in ("question", "source_clause"):
            payload = deepcopy(before)
            payload["questions"][0][field] = "원본과 다른 기준"
            variants.append(payload)
        payload = deepcopy(before)
        payload["questions"][0]["evidence_rule"]["met"] = "자료 없이 충족으로 바꾼 기준"
        variants.append(payload)
        payload = deepcopy(before)
        payload["questions"][0]["source_refs"][0]["pdf_page"] += 1
        variants.append(payload)
        payload = deepcopy(before)
        payload["reason_definitions"][0]["use_when"] = "임의로 변경한 사유 정의"
        variants.append(payload)
        payload = deepcopy(before)
        payload["source_documents"][0]["sha256"] = "0" * 64
        variants.append(payload)
        payload = deepcopy(before)
        payload["available_question_count"] = 99
        variants.append(payload)
        payload = deepcopy(before)
        payload["mapped_control_ids"] = ["2.5.6"]
        variants.append(payload)
        payload = deepcopy(before)
        payload["catalog_sha256"] = "0" * 64
        variants.append(payload)
        for index, changed in enumerate(variants):
            with self.subTest(index=index):
                self.assert_error(lambda: self.check(changed, self.response()), "REVIEW_INPUT_CHANGED")
        self.assertEqual(request.model_dump(mode="json"), before)

    def test_unsafe_request_model_copy_and_forged_approval_are_revalidated(self):
        request = self.prepare()
        variants = (request.model_copy(update={"version": True}),
                    request.model_copy(update={"approved": True}),
                    request.model_copy(update={"review_only": False}),
                    request.model_copy(update={"questions": []}))
        for index, changed in enumerate(variants):
            with self.subTest(index=index):
                self.assert_error(lambda: self.check(changed, self.response()), "INVALID_REVIEW_INPUT")

    def test_matching_version_with_different_checklist_bytes_is_not_accepted(self):
        document = deepcopy(self.document)
        document["source"]["path"] = str((HERE / document["source"]["path"]).resolve())
        for source in document["source_documents"]:
            source["path"] = str((HERE / source["path"]).resolve())
        path = self.directory / "changed-path-draft.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        store = ChecklistStore(self.directory / "different-bytes.sqlite3")
        store.import_draft(path)
        self.assert_error(lambda: prepare_judgment_review(
            self.phase1(), store, self.version, [self.chunk()], catalog=self.catalog, allow_draft=True),
            "CATALOG_CHECKLIST_MISMATCH")

    def test_prepare_and_check_do_not_send_an_llm_request(self):
        with patch("httpx.Client.send", side_effect=AssertionError("외부 요청 금지")):
            checked = self.check(self.prepare(), self.response())
        self.assertTrue(checked["validation"]["passed"])
        self.assertFalse(checked["llm_executed"])

    def test_changed_catalog_is_not_accepted_after_a_request_was_prepared(self):
        copied = self.directory / "catalog"
        copied.mkdir()
        for name in ("reason_codes_draft.json", "checklist_draft.json", "review_examples.json"):
            shutil.copyfile(HERE / name, copied / name)
        catalog = ReasonCatalog(copied / "reason_codes_draft.json")
        request = self.prepare(catalog=catalog)
        path = copied / "reason_codes_draft.json"
        path.write_bytes(path.read_bytes() + b" ")
        self.assert_error(lambda: self.check(request, self.response(), catalog=catalog))


if __name__ == "__main__":
    unittest.main()
