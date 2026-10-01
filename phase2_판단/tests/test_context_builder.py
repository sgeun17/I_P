from context_builder import (
    ContextBuildError,
    build_context_from_phase1,
    build_evidence_context,
    extract_phase1_citation_anchors,
)


def _chunks():
    return [
        {
            "chunk_id": f"e0001_v1_c{i:04d}",
            "text": (f"chunk {i} " + ("내용 " * (100 if i == 2 else 10))).strip(),
            "page_start": i,
            "page_end": i,
            "heading": f"H{i}",
            "source_file": "synthetic.pdf",
            "file_type": "pdf",
            "source": "parser",
        }
        for i in range(1, 6)
    ]


def test_phase1_citation_selects_anchor_and_neighbors_and_preserves_ids_pages():
    phase1 = {
        "mapped_controls": [
            {
                "control_id": "2.5.6",
                "citations": [
                    {"chunk_id": "e0001_v1_c0003", "page": 3, "quote": "chunk 3"}
                ],
            }
        ]
    }
    context = build_context_from_phase1(_chunks(), phase1, "2.5.6", neighbor_count=1)

    assert [c["chunk_id"] for c in context["chunks"]] == [
        "e0001_v1_c0002",
        "e0001_v1_c0003",
        "e0001_v1_c0004",
    ]
    assert [c["role"] for c in context["chunks"]] == ["context", "evidence", "context"]
    assert context["chunks"][1]["page_start"] == 3
    assert context["chunks"][1]["page_end"] == 3


def test_extracts_multiple_quotes_without_duplicates():
    phase1 = {
        "mapped_controls": [{
            "control_id": "2.5.6",
            "citations": [
                {"chunk_id": "c1", "quote": "A"},
                {"chunk_id": "c1", "quote": "A"},
                {"chunk_id": "c1", "quote": "B"},
            ],
        }]
    }
    ids, quotes = extract_phase1_citation_anchors(phase1, "2.5.6")
    assert ids == ["c1"]
    assert quotes == {"c1": ["A", "B"]}


def test_token_overflow_drops_context_before_truncating_evidence():
    # 테스트에서는 문자 수를 '토큰 단위'처럼 사용하는 deterministic counter다.
    # 운영에서는 실제 모델 tokenizer callback을 주입한다.
    chunks = _chunks()
    context = build_evidence_context(
        chunks,
        ["e0001_v1_c0002"],
        anchor_quotes={"e0001_v1_c0002": ["chunk 2"]},
        neighbor_count=1,
        max_context_tokens=700,
        token_counter=len,
    )
    ids = [row["chunk_id"] for row in context["chunks"]]
    assert "e0001_v1_c0002" in ids
    assert context["token_usage"]["used"] <= 700
    assert context["omitted_chunk_ids"] or any(row["truncated"] for row in context["chunks"])


def test_budget_requires_real_counter_callback():
    try:
        build_evidence_context(
            _chunks(), ["e0001_v1_c0002"], max_context_tokens=1000
        )
    except ContextBuildError as exc:
        assert exc.code == "TOKEN_COUNTER_REQUIRED"
    else:
        raise AssertionError("TOKEN_COUNTER_REQUIRED가 발생해야 합니다.")
