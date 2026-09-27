from copy import deepcopy
import unittest
from unittest.mock import Mock

from chunk_retriever import prepare_input, assemble_result, validate_index, RetrievalError
from retriever import load_controls, ROOT, control_text, encode_texts


def payload():
    return {"evidence_id": "000001", "version": 1, "source_file": "sample.pdf", "file_type": "pdf",
            "chunks": [{"chunk_id": "000001_v1_c0000", "evidence_id": "000001", "version": 1,
                        "chunk_index": 0, "file_type": "pdf", "source_file": "sample.pdf",
                        "chunk_type": "text", "page_start": 2, "page_end": 3, "heading": "계정",
                        "text": "  퇴직자 계정 삭제\n처리 이력  ", "block_orders": [1], "source": "parser"}]}


class ChunkTests(unittest.TestCase):
    def test_exact_text_and_metadata_are_preserved(self):
        source = payload()
        document, warnings = prepare_input(source)
        self.assertEqual(document, source)
        self.assertEqual(warnings, [])
        document["chunks"][0]["text"] = "changed"
        self.assertNotEqual(document, source)

    def test_legacy_mode_is_explicit_and_does_not_mutate_input(self):
        source = payload()
        source["evidence_id"] = source["chunks"][0]["evidence_id"] = 1
        del source["chunks"][0]["source_file"]
        source["chunks"][0]["source"] = "C:\\old\\sample.pdf"
        original = deepcopy(source)
        with self.assertRaises(RetrievalError):
            prepare_input(source)
        document, warnings = prepare_input(source, True)
        self.assertEqual(source, original)
        self.assertEqual(document["evidence_id"], "000001")
        self.assertEqual(document["chunks"][0]["source"], "parser")
        self.assertEqual(len(warnings), 4)
        self.assertEqual(document["chunks"][0]["text"], source["chunks"][0]["text"])

    def test_bad_metadata_and_text_fail_before_search(self):
        for change in ("empty", "duplicate", "mixed_id", "mixed_version", "page", "file", "preprocess_error"):
            p = payload()
            if change == "empty":
                p["chunks"][0]["text"] = " \n "
            elif change == "duplicate":
                p["chunks"].append(deepcopy(p["chunks"][0]))
                p["chunks"][1]["chunk_index"] = 1
            elif change == "mixed_id":
                p["chunks"][0]["evidence_id"] = "000002"
            elif change == "mixed_version":
                p["chunks"][0]["version"] = 2
            elif change == "page":
                p["chunks"][0]["page_end"] = 1
            elif change == "file":
                p["chunks"][0]["source_file"] = "different.pdf"
            else:
                p["errors"] = ["corrupted_file"]
            with self.subTest(change=change), self.assertRaises(RetrievalError):
                prepare_input(p, True)

    def test_per_chunk_rank_and_deduplicated_controls(self):
        controls = load_controls(ROOT / "controls.json")[:2]
        p = payload()
        p["chunks"].append(deepcopy(p["chunks"][0]))
        p["chunks"][1].update(chunk_index=1, chunk_id="000001_v1_c0001")
        ids = [c["control_id"] for c in controls]
        result = assemble_result(p, [], controls,
            {"ids": [ids, ids[::-1]], "distances": [[0.4, 0.1], [0.2, 1.3]]}, 2, "hash", "key")
        self.assertEqual(result["retrieval"]["chunk_results"][0]["candidates"][0]["control_id"], ids[1])
        self.assertEqual(result["retrieval"]["chunk_results"][1]["candidates"][1]["similarity_score"], -0.3)
        self.assertEqual([c["control_id"] for c in result["retrieval"]["candidates"]], ids[::-1])
        self.assertEqual([c["similarity_score"] for c in result["retrieval"]["candidates"]], [0.9, 0.6])
        self.assertEqual(len(result["candidate_controls"]), 2)
        for c in result["candidate_controls"]:
            self.assertEqual(c["matched_chunk_ids"], ["000001_v1_c0000", "000001_v1_c0001"])
            self.assertIn("requirement", c)
        self.assertEqual(result["evidence_chunks"], p["chunks"])

    def test_document_top_k_deduplicates_and_keeps_best_chunk(self):
        controls = load_controls(ROOT / "controls.json")[:3]
        ids = [c["control_id"] for c in controls]
        p = payload()
        p["chunks"].append(deepcopy(p["chunks"][0]))
        p["chunks"][1].update(chunk_index=1, chunk_id="000001_v1_c0001")
        result = assemble_result(p, [], controls,
            {"ids": [ids, ids], "distances": [[0.2, 0.9, 1.0], [0.2, 0.05, 0.8]]}, 1, "hash", "key")
        candidate = result["retrieval"]["candidates"][0]
        self.assertEqual(candidate["control_id"], ids[1])
        self.assertEqual(candidate["best_chunk_id"], "000001_v1_c0001")
        self.assertEqual(candidate["similarity_score"], 0.95)
        self.assertEqual(len(result["candidate_controls"]), 1)
        self.assertEqual(result["retrieval"]["unit"], "document")

    def test_document_ties_and_negative_scores(self):
        controls = load_controls(ROOT / "controls.json")[:3]
        ids = [c["control_id"] for c in controls]
        p = payload()
        result = assemble_result(p, [], controls,
            {"ids": [ids[::-1]], "distances": [[1.2, 1.2, 1.2]]}, 2, "hash", "key")
        self.assertEqual([c["control_id"] for c in result["retrieval"]["candidates"]], sorted(ids)[:2])
        self.assertEqual([c["similarity_score"] for c in result["retrieval"]["candidates"]], [-0.2, -0.2])

    def test_missing_db_controls_or_nan_cannot_be_aggregated(self):
        controls = load_controls(ROOT / "controls.json")[:2]
        ids = [c["control_id"] for c in controls]
        for results in ({"ids": [ids[:1]], "distances": [[0.1]]},
                        {"ids": [ids], "distances": [[0.1, float("nan")]]}):
            with self.assertRaises(RetrievalError):
                assemble_result(payload(), [], controls, results, 1, "hash", "key")

    def test_stale_or_wrong_index_is_rejected(self):
        controls = load_controls(ROOT / "controls.json")
        stored = {"ids": [c["control_id"] for c in controls], "documents": [control_text(c) for c in controls],
                  "metadatas": [{"control_id": c["control_id"], "control_name": c["control_name"],
                                 "kb_sha256": "hash", "embedding_cache_key": "key"} for c in controls]}
        collection = Mock()
        collection.count.return_value = 101
        collection.configuration = {"hnsw": {"space": "cosine"}}
        collection.get.return_value = stored
        validate_index(collection, controls, "hash", "key")
        for field in ("kb_sha256", "embedding_cache_key", "control_name"):
            bad = deepcopy(stored)
            bad["metadatas"][0][field] = "stale"
            collection.get.return_value = bad
            with self.subTest(field=field), self.assertRaises(RetrievalError):
                validate_index(collection, controls, "hash", "key")

    def test_over_limit_is_not_silently_truncated(self):
        model = Mock()
        model.max_seq_length = 3
        model.tokenizer.return_value = {"input_ids": [[1, 2, 3, 4]]}
        with self.assertRaises(ValueError):
            encode_texts(model, ["긴 청크"])
        model.encode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
