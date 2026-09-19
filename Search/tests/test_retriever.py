"""모델을 다운로드하지 않고 데이터 연결·점수 계산·캐시 오류를 검사합니다.

아래 가짜 모델은 검색 품질을 평가하는 용도가 아닙니다.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import retriever


class FakeModel:
    max_seq_length = 8192

    def __init__(self):
        self.calls = []

    def tokenizer(self, texts, **kwargs):
        return {"input_ids": [[1] * len(t) for t in texts]}

    def get_sentence_embedding_dimension(self):
        return 3

    def get_embedding_dimension(self):
        return 3

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        return np.array([[1, len(t) % 7 + 1, 2] for t in texts], dtype=np.float32)


class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.controls = retriever.load_controls(retriever.ROOT / "controls.json")

    def test_real_kb_and_document_fields(self):
        self.assertEqual(len(self.controls), 101)
        c = next(c for c in self.controls if c["control_id"] == "2.5.1")
        text = retriever.control_text(c)
        for part in [c["control_name"], c["requirement"], *c["keyword"], *c["evidence_examples"]]:
            self.assertIn(part, text)

    def test_invalid_kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "controls.json"
            for kind in ("duplicate", "blank", "array", "count", "object"):
                data = deepcopy(self.controls)
                if kind == "duplicate":
                    data[1]["control_id"] = data[0]["control_id"]
                elif kind == "blank":
                    data[0]["requirement"] = " "
                elif kind == "array":
                    data[0]["keyword"] = "계정"
                elif kind == "count":
                    data.pop()
                else:
                    data[0] = None
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    retriever.load_controls(path)

    def test_cosine_ranking_and_schema(self):
        # 서로 다른 길이의 벡터라도 같은 방향이면 점수는 1입니다.
        controls = self.controls[:4]
        vectors = [[0, 5], [2, 0], [100, 0], [-3, 0]]
        result = retriever.rank_controls(controls, vectors, [[4, 0]], 4, "EVID-TEST")
        self.assertEqual(set(result), {"evidence_id", "retrieval"})
        self.assertEqual(result["evidence_id"], "EVID-TEST")
        rows = result["retrieval"]["candidates"]
        self.assertEqual([r["control_id"] for r in rows], [controls[i]["control_id"] for i in [1, 2, 0, 3]])
        self.assertEqual([r["similarity_score"] for r in rows], [1, 1, 0, -1])
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3, 4])
        self.assertEqual(set(rows[0]), {"rank", "control_id", "control_name", "similarity_score"})
        self.assertEqual(len(retriever.rank_controls(controls, vectors, [[4, 0]], 2, "E")
                             ["retrieval"]["candidates"]), 2)

    def test_invalid_vectors_and_k(self):
        for vectors in ([[0, 0]], [[float("nan"), 1]], [[float("inf"), 1]], [1, 2]):
            with self.subTest(vectors=vectors), self.assertRaises(ValueError):
                retriever.normalized_vectors(vectors)
        for k in (0, -1, 3, True, 1.5):
            with self.subTest(k=k), self.assertRaises(ValueError):
                retriever.rank_controls(self.controls[:2], [[1, 0], [0, 1]], [[1, 0]], k, "E")
        with self.assertRaises(ValueError):
            retriever.rank_controls(self.controls[:2], [[1, 0]], [[1, 0]], 1, "E")

    def test_cache_reuse_invalidation_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kb = base / "controls.json"
            kb.write_text(json.dumps(self.controls), encoding="utf-8")
            model_dir = base / "model"
            model_dir.mkdir()
            config = model_dir / "config.json"
            config.write_text('{"revision":1}', encoding="utf-8")
            def build():
                fake = FakeModel()
                with patch("retriever.load_model", return_value=fake):
                    instance = retriever.Retriever(kb, model_dir, base / "cache")
                return instance, fake
            first, fake = build()
            self.assertEqual(len(fake.calls), 1)
            second, fake = build()
            self.assertEqual(fake.calls, [])
            self.assertEqual(len(second.search("계정 관리")["retrieval"]["candidates"]), 5)
            # KB 문구와 순서가 바뀌면 기존 벡터를 재사용하면 안 됩니다.
            changed = deepcopy(self.controls)
            changed[0]["keyword"].append("변경된 용어")
            changed.reverse()
            kb.write_text(json.dumps(changed), encoding="utf-8")
            third, fake = build()
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(third.controls[0]["control_id"], changed[0]["control_id"])
            config.write_text('{"revision":222}', encoding="utf-8")
            fourth, fake = build()
            self.assertEqual(len(fake.calls), 1)
            fourth.cache_path.write_bytes(b"broken cache")
            repaired, fake = build()
            self.assertEqual(len(fake.calls), 1)
            for text in ("", " ", "x" * 8193):
                with self.subTest(text_length=len(text)), self.assertRaises(ValueError):
                    repaired.search(text)
            with self.assertRaises(ValueError):
                repaired.search("계정", evidence_id=" ")


if __name__ == "__main__":
    unittest.main()
