import importlib.util
from pathlib import Path


def load_runner():
    path = Path(__file__).resolve().parents[1] / "tools" / "run_r5_focused_regression.py"
    spec = importlib.util.spec_from_file_location("phase2_r5_focused_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_synthetic_chunk_id_matches_evidence_version_contract():
    runner = load_runner()
    assert runner.synthetic_chunk_id("NOTICE-NO-EVENT-POLICY") == (
        "NOTICE-NO-EVENT-POLICY_v1_c0001"
    )


def test_semantic_cases_cover_positive_and_negative_relation_boundaries():
    runner = load_runner()
    assert len(runner.SEMANTIC_CASES) == 19
    by_id = {case["case_id"]: case["expected_result"] for case in runner.SEMANTIC_CASES}
    assert by_id == {
        "REGISTER-DEFINES-BACKUP-CADENCE": "MET",
        "SINGLE-BACKUP-IS-NOT-POLICY": "UNKNOWN",
        "PROCEDURE-TITLE-WITHOUT-CONTENT": "UNKNOWN",
        "BACKUP-CADENCE-NOT-RESTORE-CADENCE": "UNKNOWN",
        "RECURRENCE-DIRECT-COMPARISON": "MET",
        "RECURRENCE-CURRENT-ONLY": "UNKNOWN",
        "ACCESS-ROLE-ALIGNED": "MET",
        "ACCESS-APPROVAL-ONLY": "UNKNOWN",
        "ACCESS-MIXED-CHOOSE-COMPLETE-CASE": "MET",
        "STORAGE-BOTH-CONTROLLED": "MET",
        "STORAGE-ELECTRONIC-REGISTRATION-ONLY": "UNKNOWN",
        "ROLE-SAME-PERSON-QUALIFIED": "MET",
        "ROLE-OTHER-PERSON-QUALIFICATION": "UNKNOWN",
        "ROLE-FLATTENED-OCR-AMBIGUOUS": "UNKNOWN",
        "LOG-RETENTION-POLICY-ONLY": "UNKNOWN",
        "LOG-RETENTION-OBSERVED": "MET",
        "MARKETING-NOTICE-POLICY-ONLY": "UNKNOWN",
        "MARKETING-NO-EVENT-WITH-POLICY": "MET",
        "MARKETING-ACTUAL-NOTICE": "MET",
    }
