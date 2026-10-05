from copy import deepcopy
import json

import pytest

from citation_spans import materialize, prepare
from test_validated_pipeline import REASONS, setup_case, supported
from validated_pipeline import run_validated_item


def sample_context():
    return {"chunks": [{"chunk_id": "E9999_v1_c0000", "role": "evidence",
                        "page_start": 1, "page_end": 1,
                        "text": "  원문 문장을 유지한다.\r\n\r\n대상: A | 처리일: 10/3\n"}]}


def test_spans_preserve_exact_offsets_without_mutating_context():
    original = sample_context()
    before = deepcopy(original)
    prompt, spans, schema = prepare(original, "X", [])
    assert original == before
    assert "text" not in prompt["chunks"][0]
    for span in spans.values():
        text = original["chunks"][0]["text"]
        assert text[span["start"]:span["end"]] == span["quote"]
    output, errors = materialize({"item_id": "X", "result": "MET", "reason": "직접 근거",
                                  "reason_codes": [], "citation_span_ids": ["S00001", "S00002"]},
                                 spans, schema)
    assert not errors
    assert [row["quote"] for row in output["citations"]] == [
        "원문 문장을 유지한다.", "대상: A | 처리일: 10/3",
    ]


@pytest.mark.parametrize("selection", [["FORGED"], ["S00001", "S00001"], [0], "S00001"])
def test_invalid_span_selection_is_rejected(selection):
    _, spans, schema = prepare(sample_context(), "X", [])
    output, errors = materialize({"item_id": "X", "result": "MET", "reason": "직접 근거",
                                  "reason_codes": [], "citation_span_ids": selection}, spans, schema)
    assert output is None
    assert errors


@pytest.mark.parametrize("kind", ["duplicate", "role", "budget", "empty"])
def test_invalid_context_fails_instead_of_truncating(kind):
    context = sample_context()
    if kind == "duplicate":
        context["chunks"].append(deepcopy(context["chunks"][0]))
    elif kind == "role":
        context["chunks"][0]["role"] = "instruction"
    elif kind == "budget":
        context["chunks"][0]["text"] = "x" * 24001
    else:
        context["chunks"][0]["text"] = "  "
    with pytest.raises(ValueError):
        prepare(context, "X", [])


def test_span_mode_still_runs_semantic_self_check(setup_case):
    payload, _, item, context, old = setup_case

    def llm(system, user, model, schema, **kwargs):
        assert "citation_span_ids" in schema["properties"]
        return json.dumps({"item_id": item["item_id"], "result": "MET", "reason": old["reason"],
                           "reason_codes": [], "citation_span_ids": ["S00001"]}, ensure_ascii=False)

    output, audit = run_validated_item(
        item, context, evidence_id=payload["evidence_id"], version=payload["version"],
        model="qwen3:test", reason_codes=REASONS["codes"], llm_call=llm,
        self_check_call=supported, citation_span_selection=True, sleeper=lambda _: None,
    )
    assert output["result"] == "MET"
    assert audit["run"]["source_spans"]
    assert "citation_span_ids" not in output
