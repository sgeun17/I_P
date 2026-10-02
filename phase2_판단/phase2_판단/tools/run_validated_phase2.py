"""Run one complete control checklist; output and audit use separate files."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / "src")]
from validated_pipeline import run_control_judgment
from validation.contracts import interface_module


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--control-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--checklist", default=str(HERE.parent / "phase2_기준/chapter2_full_checklist_draft.json"))
    parser.add_argument("--reason-codes", default=str(HERE.parent / "phase2_기준/chapter2_reason_codes_draft.json"))
    parser.add_argument("--policies", help="Optional per-item policy JSON; no built-in freshness age")
    parser.add_argument("--as-of", help="Explicit YYYY-MM-DD reference date")
    args = parser.parse_args()
    out = Path(args.out_dir)
    # Refuse overwrite before any expensive calls.
    out.mkdir(parents=True, exist_ok=False)
    raw_controls = read(HERE.parent / "phase1_검색/controls.json")
    rows = raw_controls if isinstance(raw_controls, list) else raw_controls["controls"]
    controls = {row["control_id"]: row["control_name"] for row in rows}
    result = run_control_judgment(read(args.input), args.control_id,
        catalog=read(args.checklist), reason_catalog=read(args.reason_codes), controls=controls,
        model=args.model, allow_draft=args.allow_draft,
        item_policies=read(args.policies) if args.policies else None, as_of=args.as_of,
        controls_sha256=interface_module("kb_identity").kb_sha256((HERE.parent / "phase1_검색/controls.json").read_bytes()),
        progress=lambda n, total, item_id, status: print(f"[{n}/{total}] {item_id}: {status}", flush=True))
    for name, data in (("audit.json", result["audit"]), ("output.json", result["output"]),
                       ("status.json", {"processing_status": result["processing_status"]})):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['processing_status']}: {out}")
    return 1 if result["processing_status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
