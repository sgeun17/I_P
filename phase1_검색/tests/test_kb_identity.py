"""ZIP/checkout의 줄바꿈 차이는 허용하고 실제 KB 변경은 거부한다."""
from copy import deepcopy
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from judgment_adapter import ROOT, MappingAdapterError, read, to_mapping_input
from kb_identity import kb_sha256


def test_line_endings_keep_existing_shared_identity():
    raw = (ROOT / "controls.json").read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    expected = read(ROOT.parent / "phase1_판단/data/control_index.json")["kb_sha256"]
    assert hashlib.sha256(lf).hexdigest() != hashlib.sha256(crlf).hexdigest()
    assert kb_sha256(lf) == kb_sha256(crlf) == expected


@pytest.mark.parametrize("change", [
    lambda raw: raw.replace(b'"A"', b'"B"'),
    lambda raw: b"\xef\xbb\xbf" + raw,
    lambda raw: raw.replace(b":", b": "),
    lambda raw: raw.rstrip(b"\n"),
])
def test_changes_other_than_line_endings_are_not_ignored(change):
    raw = b'[{"requirement":"A"}]\n'
    assert kb_sha256(raw) != kb_sha256(change(raw))


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_adapter_accepts_equivalent_kb_but_rejects_real_change(newline):
    kb_path = ROOT / "controls.json"
    raw = kb_path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
    original_read = Path.read_bytes
    sample = read(ROOT / "examples/docx_output.json")
    def equivalent(path):
        return raw if path == kb_path else original_read(path)
    with patch.object(Path, "read_bytes", equivalent):
        result = to_mapping_input(sample)
        assert result["kb_sha256"] == sample["index"]["kb_sha256"]
        stale = deepcopy(sample)
        stale["index"]["kb_sha256"] = "0" * 64
        with pytest.raises(MappingAdapterError, match="해시"):
            to_mapping_input(stale)
    def changed(path):
        return raw.replace(b'"control_id"', b'"control_id" ', 1) if path == kb_path else original_read(path)
    with patch.object(Path, "read_bytes", changed):
        with pytest.raises(MappingAdapterError, match="해시"):
            to_mapping_input(sample)
