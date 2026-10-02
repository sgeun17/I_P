from versions import build_version_info


def test_version_info_records_current_draft_sources_and_phase1_retry_policy():
    info = build_version_info(model_name="qwen3:test")
    assert info["prompt_version"].startswith("phase2_grounding_")
    assert info["context_builder_version"] == "phase2_context_v0.1"
    assert info["checklist_version"] == "phase2-checklist-chapter2-full-draft-2026-10-01-r3"
    assert info["checklist_approved"] is False
    assert len(info["checklist_sha256"]) == 64
    assert info["reason_codes_version"] == "phase2-reason-codes-chapter2-full-draft-2026-10-01-r3"
    assert info["reason_codes_approved"] is False
    assert info["output_schema_sha256"]
    assert info["phase1_retry_policy"]["max_retries"] == 1
    assert info["phase1_retry_policy"]["timeout_seconds"] == 60
