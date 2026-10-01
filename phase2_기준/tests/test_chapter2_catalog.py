"""전체 초안의 명시적 선택, 버전 혼합 거부 및 원본 변조 감지."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from checklist_store import ChecklistStore
from judgment_review import JudgmentReviewError, check_review_output, prepare_judgment_review
from reason_codes import ReasonCatalog, ReasonCodeError
from tools.verify_chapter2_review_flow import CATALOG, FULL, fixed_response, make_request


class Chapter2CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix="chapter2-catalog-tests-")
        cls.store = ChecklistStore(Path(cls.temp.name) / "checklists.sqlite3")
        cls.store.import_draft(HERE / "checklist_draft.json")
        cls.store.import_draft(FULL)
        cls.document = json.loads(FULL.read_text(encoding="utf-8"))
        cls.catalog = ReasonCatalog(CATALOG)
        cls.request = make_request(cls.document["controls"][0], cls.document, cls.store,
                                   cls.catalog, "자료 미제출")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_default_and_full_catalogs_are_distinct(self):
        original = ReasonCatalog()
        self.assertEqual(original.checklist_source, "checklist_draft.json")
        self.assertEqual(self.catalog.checklist_source, FULL.name)
        self.assertNotEqual(original.sha256, self.catalog.sha256)
        self.assertEqual(original.list_codes(allow_draft=True)["codes"],
                         self.catalog.list_codes(allow_draft=True)["codes"])

    def test_full_version_requires_explicit_catalog(self):
        with self.assertRaises(JudgmentReviewError) as caught:
            prepare_judgment_review(self.request.source_phase1_result, self.store,
                                   self.request.checklist_version, self.request.chunks, allow_draft=True)
        self.assertEqual(caught.exception.code, "CATALOG_CHECKLIST_MISMATCH")

    def test_draft_guard_is_retained(self):
        with self.assertRaises(ReasonCodeError) as caught:
            self.catalog.list_codes()
        self.assertEqual(caught.exception.code, "DRAFT_NOT_APPROVED")

    def test_original_examples_are_not_claimed_as_full_coverage(self):
        with self.assertRaises(ReasonCodeError) as caught:
            self.catalog.validate_examples(allow_draft=True)
        self.assertEqual(caught.exception.code, "EXAMPLE_SET_UNAVAILABLE")

    def test_response_wrong_catalog_and_missing_items_rejected(self):
        for mutation in ["version", "missing"]:
            with self.subTest(mutation=mutation):
                response = fixed_response(self.request)
                if mutation == "version":
                    response["catalog_version"] = "wrong-version"
                else:
                    response["item_results"].pop()
                checked = check_review_output(self.request, response, self.store,
                                              catalog=self.catalog, allow_draft=True)
                self.assertFalse(checked["validation"]["passed"])

    def test_source_changes_after_loading_rejected(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            shutil.copy2(CATALOG, folder / CATALOG.name)
            shutil.copy2(FULL, folder / FULL.name)
            catalog = ReasonCatalog(folder / CATALOG.name)
            with (folder / FULL.name).open("a", encoding="utf-8") as output:
                output.write("\n")
            with self.assertRaises(ReasonCodeError) as caught:
                catalog.list_codes(allow_draft=True)
            self.assertEqual(caught.exception.code, "SOURCE_CHANGED")

    def test_invalid_source_selector_rejected(self):
        document = json.loads(CATALOG.read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            for value in ["../chapter2_full_checklist_draft.json", [], "missing.json"]:
                with self.subTest(value=value):
                    changed = deepcopy(document)
                    changed["checklist_source"] = value
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ReasonCodeError) as caught:
                        ReasonCatalog(path)
                    self.assertEqual(caught.exception.code, "INVALID_CATALOG")


if __name__ == "__main__":
    unittest.main()
