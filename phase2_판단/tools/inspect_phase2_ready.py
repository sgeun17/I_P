"""실제 LLM 호출 없이 Phase 2 판단 준비물을 확인하는 smoke script."""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from checklist_adapter import compact_reason_codes, get_checklist_item, load_reason_code_catalog
from context_builder import build_context_from_phase1
from grounding_prompts import build_prompt_package
from versions import build_version_info


def main() -> int:
    fixture = json.loads((ROOT / "fixtures" / "development_case.json").read_text(encoding="utf-8"))
    item = get_checklist_item(fixture["item_id"], allow_draft=True)
    codes = compact_reason_codes(load_reason_code_catalog(allow_draft=True))
    context = build_context_from_phase1(
        fixture["chunks"],
        fixture["phase1_result"],
        fixture["control_id"],
        neighbor_count=1,
    )
    package = build_prompt_package(item, context, reason_codes=codes)
    versions = build_version_info(model_name="NOT_CALLED")
    print(json.dumps({
        "status": "READY_FOR_DEV_LLM_CALL",
        "item_id": fixture["item_id"],
        "selected_chunk_ids": [row["chunk_id"] for row in context["chunks"]],
        "prompt_version": package.prompt_version,
        "ruleset_version": package.ruleset_version,
        "output_schema_title": package.output_schema.get("title"),
        "versions": versions,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
