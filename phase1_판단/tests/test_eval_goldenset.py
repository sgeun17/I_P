"""평가 결과의 재현성 메타데이터를 검증한다."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDENSET_PATH = ROOT / "tests" / "fixtures" / "goldenset.json"
sys.path.insert(0, str(ROOT / "tools"))

import eval_goldenset  # noqa: E402
from review_policy import DEFAULT_THRESHOLDS  # noqa: E402


def test_결과에_재현성_메타데이터를_기록한다(tmp_path, monkeypatch):
    output_path = tmp_path / "evaluation.json"
    response_path = ROOT / "runs" / "qwen3-4b-v05.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eval_goldenset.py",
            "--responses",
            str(response_path),
            "--model",
            "qwen3:4b",
            "--prompt",
            "phase1_mapping_v0.5",
            "--out",
            str(output_path),
        ],
    )

    assert eval_goldenset.main() == 0

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    expected_hash = hashlib.sha256(GOLDENSET_PATH.read_bytes()).hexdigest()
    assert payload["threshold_profile"] == DEFAULT_THRESHOLDS.profile_name
    assert payload["goldenset_sha256"] == expected_hash
    assert payload["response_file"] == "runs/qwen3-4b-v05.jsonl"
