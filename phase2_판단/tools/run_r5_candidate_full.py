"""Replay all eligible saved documents through the r5 candidate pipeline.

The saved source run is read-only. Phase 2 inputs are rebuilt against the current
r5 checklist so that the focused-test context and semantic guards are exercised.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import time


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def phase1_like(saved_input):
    mapped = []
    for target in saved_input["targets"]:
        row = {key: target[key] for key in (
            "control_id", "control_name", "relation", "llm_confidence", "similarity_score"
        ) if key in target}
        row["citations"] = target.get("mapping_citations", [])
        mapped.append(row)
    return {
        "evidence_id": saved_input["evidence_id"],
        "version": saved_input["version"],
        "mapped_controls": mapped,
        "versions": saved_input["source_versions"],
        **({"trace_id": saved_input["trace_id"]} if saved_input.get("trace_id") else {}),
    }


def file_hashes(root: Path):
    names = [
        "phase2_판단/src/context_builder.py",
        "phase2_판단/src/grounding_prompts.py",
        "phase2_판단/src/self_check.py",
        "phase2_판단/src/validated_pipeline.py",
        "phase2_판단/tools/run_r5_candidate_full.py",
    ]
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / "phase2_판단"), str(root / "phase2_판단/src"), str(root / "phase2_인터페이스")]
    from build_phase2_input import build, load_checklist_scope
    from citation_spans import SPAN_VERSION
    from runtime_config import runtime_profile_from_env
    from validated_pipeline import run_control_judgment
    from validation.contracts import interface_module
    from validation.logic import validate_input

    source, out = args.source_dir.resolve(), args.out_dir.resolve()
    if out.exists() or out == source or source in out.parents or out in source.parents:
        parser.error("out-dir must be new and separate from source-dir")
    summary0, rows0 = read(source / "summary.json"), read(source / "rows.json")
    catalog_path = root / "phase2_기준/full_checklist_draft.json"
    reasons_path = root / "phase2_기준/full_reason_codes_draft.json"
    controls_path = root / "phase1_검색/controls.json"
    catalog, reasons = read(catalog_path), read(reasons_path)
    scope, checklist_version = load_checklist_scope(catalog_path)
    controls = {row["control_id"]: row["control_name"] for row in read(controls_path)}
    kb_hash = interface_module("kb_identity").kb_sha256(controls_path.read_bytes())

    jobs, skipped = [], []
    for source_row in rows0:
        eid = source_row["evidence_id"]
        if source_row["status"].startswith(("SKIP_", "HOLD_")):
            skipped.append(dict(source_row))
            continue
        saved = read(source / eid / "phase2_input.json")
        rebuilt = build(phase1_like(saved), saved["chunks"], scope, checklist_version)
        issues = validate_input(rebuilt, catalog, controls)
        if issues:
            parser.error(eid + ": " + json.dumps(issues, ensure_ascii=False))
        targets = [row for row in rebuilt["targets"] if row["judge"]]
        if len(targets) != 1 or rebuilt["judge_policy"] != "primary_only":
            parser.error("unexpected selection policy: " + eid)
        control_id = targets[0]["control_id"]
        items = next(row["items"] for row in catalog["controls"] if row["control_id"] == control_id)
        jobs.append((eid, control_id, rebuilt, len(items), source_row))
    if len(rows0) != 33 or len(jobs) != 25 or sum(job[3] for job in jobs) != 255:
        parser.error("expected 33 source documents / 25 eligible documents / 255 items")

    out.mkdir(parents=True)
    meta = {
        "version": "phase2_r5_candidate_full_v1",
        "development_only": True,
        "automatic_approval": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime_profile_from_env(model_override=args.model).audit_dict(),
        "source_dir": str(source),
        "source_summary_sha256": sha256((source / "summary.json").read_bytes()).hexdigest(),
        "checklist_version": checklist_version,
        "checklist_sha256": sha256(catalog_path.read_bytes()).hexdigest(),
        "reason_codes_sha256": sha256(reasons_path.read_bytes()).hexdigest(),
        "code_sha256": file_hashes(root),
        "checklist_approved": catalog.get("approved"),
        "planned_documents": len(jobs),
        "planned_items": sum(job[3] for job in jobs),
        "total_source_documents": len(rows0),
        "source_document_statuses": dict(Counter(row["status"] for row in rows0)),
        "citation_generation_version": SPAN_VERSION,
        "context_policy": "audited selection; max 12 chunks; conservative 12000-token upper bound",
        "policy_note": "No policies invented; POLICY_MISSING remains. Phase 1 reviews are not approved.",
    }
    rows = list(skipped)
    item_counts, policies, checks, issue_counts = Counter(), Counter(), Counter(), Counter()
    technical_errors = finished_items = runtime_errors = 0
    started = time.monotonic()

    def checkpoint(state):
        summary = {
            **meta,
            "state": state,
            "finished_documents": len(rows) - len(skipped),
            "finished_items": finished_items,
            "item_results": dict(item_counts),
            "technical_error_items": technical_errors,
            "runtime_error_documents": runtime_errors,
            "self_check_verdicts": dict(checks),
            "policy_statuses": dict(policies),
            "validation_issue_occurrences": dict(issue_counts),
            "document_statuses": dict(Counter(row["status"] for row in rows)),
            "provisional_documents": [row["evidence_id"] for row in rows if row.get("provisional")],
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        save(out / "summary.json", summary)
        save(out / "rows.json", rows)
        return summary

    for eid, _, payload, _, source_row in jobs:
        dest = out / eid
        dest.mkdir()
        save(dest / "phase2_input.json", payload)
        save(dest / "source_row.json", source_row)
    if not args.execute:
        print(json.dumps(checkpoint("PREFLIGHT_ONLY"), ensure_ascii=False, indent=2))
        print("Results:", out)
        return 0

    checkpoint("RUNNING")
    try:
        for index, (eid, control_id, payload, item_total, source_row) in enumerate(jobs, 1):
            print(f"DOCUMENT [{index}/{len(jobs)}] {eid} {control_id} items={item_total}", flush=True)
            row = {
                "evidence_id": eid,
                "control_id": control_id,
                "phase1_review_required": source_row.get("phase1_review_required"),
                "phase1_review_reasons": source_row.get("phase1_review_reasons", []),
            }
            try:
                result = run_control_judgment(
                    payload, control_id, catalog=catalog, reason_catalog=reasons,
                    controls=controls, controls_sha256=kb_hash, model=args.model,
                    allow_draft=True, citation_span_selection=True,
                    progress=lambda a, b, c, d: print(f"  [{a}/{b}] {c}: {d}", flush=True),
                )
                audit, output = result["audit"], result["output"]
                save(out / eid / "audit.json", audit)
                save(out / eid / "output.json", output)
                results = dict(Counter(item["result"] for item in (output or {}).get("items", [])))
                row.update(
                    status=result["processing_status"], provisional=(output or {}).get("provisional", False),
                    human_review=(output or {}).get("human_review"), item_results=results,
                )
                item_counts.update(results)
                finished_items += len(audit["items"])
                issue_counts.update(issue["code"] for issue in audit["issues"])
                for item in audit["items"]:
                    technical_errors += int(any(issue.get("severity") == "error" for issue in item["issues"]))
                    checks.update([item.get("self_check", {}).get("verdict", "NOT_REACHED")])
                    for name in ("freshness", "evidence_format"):
                        policies.update([name + ":" + item.get(name, {}).get("status", "NOT_REACHED")])
            except Exception as exc:
                runtime_errors += 1
                row.update(status="RUNTIME_ERROR", error=f"{type(exc).__name__}: {exc}")
            rows.append(row)
            save(out / eid / "row.json", row)
            checkpoint("RUNNING")
    except KeyboardInterrupt:
        checkpoint("INTERRUPTED")
        return 130

    state = "FINISHED" if finished_items == 255 and runtime_errors == 0 else "FINISHED_WITH_FAILURES"
    summary = checkpoint(state)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Results:", out)
    return int(runtime_errors > 0 or any(row["status"] == "FAILED" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
