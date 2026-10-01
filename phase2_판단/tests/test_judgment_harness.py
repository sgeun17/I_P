import json

from checklist_adapter import compact_reason_codes, get_checklist_item, load_reason_code_catalog
from judgment_harness import run_item_judgment
from phase1_runtime import DEFAULT_RETRY_POLICY


def _context():
    return {
        "context_version": "phase2_context_v0.1",
        "chunks": [{
            "chunk_id": "e0001_v1_c0001",
            "role": "evidence",
            "page_start": 1,
            "page_end": 1,
            "heading": "권한 검토",
            "source_file": "synthetic.pdf",
            "file_type": "pdf",
            "source": "parser",
            "text": "2026년 9월 계정 및 접근권한 정기 검토를 수행하였다.",
            "truncated": False,
            "original_text_sha256": "0" * 64,
        }],
        "evidence_chunk_ids": ["e0001_v1_c0001"],
        "omitted_chunk_ids": [],
        "token_usage": {"limit": None, "used": None},
    }


def test_dev_harness_runs_structured_item_judgment_without_real_server():
    item = get_checklist_item("2.5.6-Q07", allow_draft=True)
    codes = compact_reason_codes(load_reason_code_catalog(allow_draft=True))
    response = json.dumps({
        "item_id": "2.5.6-Q07",
        "result": "MET",
        "reason": "정기 검토 수행 사실이 제공된 원문에 직접 나타난다.",
        "reason_codes": [],
        "citations": [{
            "chunk_id": "e0001_v1_c0001",
            "page": 1,
            "quote": "2026년 9월 계정 및 접근권한 정기 검토를 수행하였다.",
        }],
    }, ensure_ascii=False)

    calls = []
    def fake_call(system, user, model, schema, **kwargs):
        calls.append({"system": system, "user": user, "model": model, "schema": schema, "kwargs": kwargs})
        return response

    result = run_item_judgment(
        item,
        _context(),
        model="qwen3:test",
        reason_codes=codes,
        llm_call=fake_call,
    )
    assert result.succeeded is True
    assert result.parsed_output["result"] == "MET"
    assert result.attempts == 1
    assert calls[0]["kwargs"]["client_config"].timeout_seconds == DEFAULT_RETRY_POLICY.timeout_seconds


def test_dev_harness_retries_invalid_json_using_phase1_retry_policy():
    item = get_checklist_item("2.5.6-Q07", allow_draft=True)
    codes = compact_reason_codes(load_reason_code_catalog(allow_draft=True))
    good = json.dumps({
        "item_id": "2.5.6-Q07",
        "result": "UNKNOWN",
        "reason": "검토 주기와 대상 기간을 연결할 직접 근거가 부족하다.",
        "reason_codes": ["P2_U_EVIDENCE_INSUFFICIENT"],
        "citations": [],
    }, ensure_ascii=False)

    calls = []
    def fake_call(system, user, model, schema, **kwargs):
        calls.append(user)
        return "not-json" if len(calls) == 1 else good

    result = run_item_judgment(
        item,
        _context(),
        model="qwen3:test",
        reason_codes=codes,
        llm_call=fake_call,
        sleeper=lambda _: None,
    )
    assert result.succeeded is True
    assert result.attempts == 2
    assert result.retry_count == 1
    assert "E201" in calls[1]
