from copy import deepcopy
import unittest

from judgment_adapter import ROOT, read, to_mapping_input, MappingAdapterError


class JudgmentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.result = read(ROOT / "examples/docx_output.json")

    def test_join_preserves_rank_score_requirement_and_source(self):
        before = deepcopy(self.result)
        payload = to_mapping_input(self.result)
        self.assertEqual(before, self.result)
        self.assertEqual(payload["chunks"], self.result["evidence_chunks"])
        self.assertEqual(len(payload["candidate_controls"]), 5)
        for candidate, rank, detail in zip(payload["candidate_controls"], self.result["retrieval"]["candidates"], self.result["candidate_controls"]):
            self.assertEqual(candidate["similarity_score"], rank["similarity_score"])
            self.assertEqual(candidate["rank"], rank["rank"])
            self.assertEqual(candidate["source_chunk_ids"], rank["matched_chunk_ids"])
            self.assertEqual(candidate["requirement"], detail["requirement"])

    def test_search_error_is_never_sent_to_judgment(self):
        with self.assertRaises(MappingAdapterError):
            to_mapping_input({"success": False})

    def test_stale_kb_or_changed_requirement_is_rejected(self):
        for kind in ("hash", "requirement", "name"):
            p = deepcopy(self.result)
            if kind == "hash":
                p["index"]["kb_sha256"] = "0" * 64
            else:
                p["candidate_controls"][0]["requirement" if kind == "requirement" else "control_name"] = "다른 본문"
            with self.subTest(kind=kind), self.assertRaises(MappingAdapterError):
                to_mapping_input(p)

    def test_bad_rank_duplicate_and_missing_source_are_rejected(self):
        for kind in ("rank", "duplicate", "source"):
            p = deepcopy(self.result)
            candidates = p["retrieval"]["candidates"]
            if kind == "rank":
                candidates[0]["rank"] = 2
            elif kind == "duplicate":
                candidates[1] = deepcopy(candidates[0])
            else:
                candidates[0]["matched_chunk_ids"] = ["missing"]
                p["candidate_controls"][0]["matched_chunk_ids"] = ["missing"]
            with self.subTest(kind=kind), self.assertRaises(MappingAdapterError):
                to_mapping_input(p)

    def test_judgment_800_char_limit_and_id_relationship_are_enforced(self):
        for kind in ("length", "id"):
            p = deepcopy(self.result)
            p["evidence_chunks"][0]["text" if kind == "length" else "chunk_id"] = "가" * 801 if kind == "length" else "arbitrary-id"
            with self.subTest(kind=kind), self.assertRaises(MappingAdapterError):
                to_mapping_input(p)


if __name__ == "__main__":
    unittest.main()
