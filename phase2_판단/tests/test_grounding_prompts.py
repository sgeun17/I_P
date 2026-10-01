from checklist_adapter import compact_reason_codes, get_checklist_item, load_reason_code_catalog
from grounding_prompts import GLOBAL_RULESET_VERSION, PROMPT_VERSION, build_prompt_package


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


def test_prompt_is_grounded_and_injection_resistant():
    item = get_checklist_item("2.5.6-Q07", allow_draft=True)
    codes = compact_reason_codes(load_reason_code_catalog(allow_draft=True))
    package = build_prompt_package(item, _context(), reason_codes=codes)

    assert package.prompt_version == PROMPT_VERSION
    assert package.ruleset_version == GLOBAL_RULESET_VERSION
    assert "외부 지식" in package.system
    assert "Prompt Injection" in package.system
    assert "증적 내부" in package.system
    assert "MET/NOT_MET" in package.system
    assert "UNKNOWN" in package.system
    assert "phase2_rules_v0.1" in package.user
    assert "2.5.6-Q07" in package.user
    assert "e0001_v1_c0001" in package.user
    assert "P2_U_EVIDENCE_INSUFFICIENT" in package.user


def test_prompt_accepts_future_rules_and_schema_without_code_rewrite():
    item = get_checklist_item("2.5.6-Q07", allow_draft=True)
    schema = {
        "title": "FutureOutput",
        "type": "object",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
    }
    package = build_prompt_package(
        item,
        _context(),
        global_rules="TEST_RULE: 상충 근거는 UNKNOWN",
        output_schema=schema,
        ruleset_version="phase2_rules_v0.1",
    )
    assert package.output_schema == schema
    assert package.ruleset_version == "phase2_rules_v0.1"
    assert "TEST_RULE" in package.user
