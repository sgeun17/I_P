"""Deterministic rules. Natural-language entailment is handled by self_check.py."""
from collections import Counter

from .contracts import interface_module, issue, validate_schema
from .citations import validate_citations
from dataclasses import asdict


def validate_input(payload, catalog, controls):
    issues = validate_schema(payload, "input")
    if issues:
        return issues
    chunks = payload["chunks"]
    ids = [c["chunk_id"] for c in chunks]
    if len(ids) != len(set(ids)):
        issues.append(issue("P2E007", "중복 chunk_id"))
    prefix = f"{payload['evidence_id']}_v{payload['version']}_c"
    for chunk in chunks:
        if not chunk["text"].strip():
            issues.append(issue("P2E004", "공백뿐인 청크", chunk_id=chunk["chunk_id"]))
        if not chunk["chunk_id"].startswith(prefix):
            issues.append(issue("P2E009", "입력 증적 ID/버전과 청크가 다르다", chunk_id=chunk["chunk_id"]))
        start, end = chunk.get("page_start"), chunk.get("page_end")
        if (start is None) != (end is None) or (start is not None and (start < 1 or end < start)):
            issues.append(issue("P2E004", "잘못된 페이지 범위", chunk_id=chunk["chunk_id"]))
    targets = payload["targets"]
    kb_hash = catalog.get("source", {}).get("sha256")
    if kb_hash and payload["source_versions"]["kb_sha256"] != kb_hash:
        issues.append(issue("P2E005", "Phase 1 KB 해시와 체크리스트 기준 KB 해시 불일치"))
    target_ids = [t["control_id"] for t in targets]
    if len(set(target_ids)) != len(target_ids):
        issues.append(issue("P2E005", "중복 control_id"))
    if sum(t["relation"] == "PRIMARY" for t in targets) > 1:
        issues.append(issue("P2E005", "PRIMARY가 여러 개다"))
    for target in targets:
        cid = target["control_id"]
        if cid not in controls or target["control_name"] != controls[cid]:
            issues.append(issue("P2E005", "Phase 1 KB control_id/명칭 불일치", control_id=cid))
        expected_judge = payload["judge_policy"] == "all_mapped" or target["relation"] == "PRIMARY"
        if target["judge"] != expected_judge:
            issues.append(issue("P2E005", "judge_policy와 judge 불일치", control_id=cid))
        if target["judge"]:
            anchors = target.get("mapping_citations") or []
            if not anchors:
                issues.append(issue("P2E008", "Phase 1 근거 청크가 없다", control_id=cid))
            elif any(c["chunk_id"] not in ids for c in anchors):
                issues.append(issue("P2E009", "Phase 1 근거가 현재 청크에 없다", control_id=cid))
            else:
                checked = validate_citations({"result": "UNKNOWN", "citations": anchors},
                    {"chunks": [{**c, "role": "evidence"} for c in chunks]},
                    expected_evidence_id=payload["evidence_id"], expected_version=payload["version"])
                if checked.issues:
                    issues.append(issue("P2E005", "Phase 1 mapping_citations 원문/위치 불일치", control_id=cid))
    requested_version = payload.get("checklist_version")
    actual_version = catalog.get("draft_version") or catalog.get("version")
    if requested_version and requested_version != actual_version:
        issues.append(issue("P2E005", "요청한 체크리스트 버전과 로드한 버전이 다르다"))
    return issues


def validate_item(output, item, context, reason_codes, *, evidence_id=None, version=None):
    issues = validate_schema(output, "item")
    if not isinstance(output, dict):
        return issues
    if output.get("item_id") != item["item_id"]:
        issues.append(issue("P2E301", "요청 문항과 item_id가 다르다"))
    if issues:
        # Also preserve the actionable citation-missing code beside schema errors.
        if output.get("result") in {"MET", "NOT_MET"} and output.get("citations") == []:
            issues.append(issue("P2E501", "MET/NOT_MET 인용 누락"))
        return issues
    result = output["result"]
    codes = output.get("reason_codes", [])
    catalog = {row["code"]: row["result"] for row in reason_codes}
    if (result == "MET" and codes) or any(c not in catalog or catalog[c] != result for c in codes):
        issues.append(issue("P2E502", "판정 결과와 사유 코드 카탈로그 불일치"))
    if "check_kind" in output and output["check_kind"] != item.get("check_kind"):
        issues.append(issue("E202", "check_kind는 체크리스트 값이어야 한다"))
    if len(output["reason"].strip()) < interface_module("errors").THRESHOLDS["min_reason_length"]:
        issues.append(issue("P2E506", "판정 이유가 짧다", severity="warning"))
    citations = validate_citations(output, context, expected_evidence_id=evidence_id, expected_version=version)
    issues.extend(asdict(row) for row in (*citations.issues, *citations.warnings))
    return issues


def validate_output(output, payload, checklist_items, policy):
    issues = validate_schema(output, "output")
    if issues:
        return issues
    for key in ("evidence_id", "version"):
        if output[key] != payload[key]:
            issues.append(issue("E202", f"입력/출력 {key} 불일치"))
    if output["control_id"] not in {t["control_id"] for t in payload["targets"] if t["judge"]}:
        issues.append(issue("E202", "판정 대상에 없는 control_id"))
    expected = {i["item_id"]: i for i in checklist_items}
    ids = [i["item_id"] for i in output["items"]]
    if len(ids) != len(expected) or set(ids) != set(expected):
        issues.append(issue("P2E302", "전체 질문지와 문항 집합/개수 불일치"))
    if any(n > 1 for n in Counter(ids).values()):
        issues.append(issue("P2E303", "중복 item_id"))
    if set(ids) - set(expected):
        issues.append(issue("P2E301", "질문지에 없는 문항"))
    if output["critical_policy"] != policy:
        issues.append(issue("P2E504", "critical 정책이 실행 설정과 다르다"))
    overall = interface_module("overall")
    trusted = []
    for row in output["items"]:
        reference = expected.get(row["item_id"])
        if not reference:
            continue
        critical = overall.is_critical(reference, policy)
        if row.get("critical") != critical or row.get("check_kind") != reference.get("check_kind"):
            issues.append(issue("P2E504", "critical/check_kind는 서버가 질문지에서 계산한다"))
        trusted.append({**row, "check_kind": reference.get("check_kind"), "critical": critical})
    recomputed = overall.compute(trusted, policy)
    # The interface helper hard-codes the check_kind version; separate policy variants explicitly.
    recomputed["rule_version"] = rule_version(policy)
    for key in ("overall_result", "counts", "decisive_items", "rule_version"):
        if key in output and output[key] != recomputed[key]:
            issues.append(issue("P2E504", f"계산한 {key}와 출력 불일치"))
    return issues


def rule_version(policy):
    if policy == interface_module("overall").default_policy():
        return "overall-v0.1-checkkind"
    from hashlib import sha256
    import json
    digest = sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:12]
    return f"overall-v0.1-{policy['mode']}-{digest}"
