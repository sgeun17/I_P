"""Public Phase 2 execution entry: official schema -> harness -> validators -> review.

The old harness remains useful for prompt experiments. Production callers use this entry.
The return value separates schema-compliant output from processing/audit metadata.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from context_builder import build_evidence_context, ContextBuildError
from date_extractor import extract_dates, check_freshness, check_evidence_format
from judgment_harness import run_item_judgment
from phase1_runtime import call_llm, DEFAULT_RETRY_POLICY, retry_policy_snapshot
from self_check import run_self_check, SELF_CHECK_VERSION
from grounding_prompts import PROMPT_VERSION
from validation.contracts import ROOT, INTERFACE, interface_module, item_schema, issue, validate_schema
from validation.logic import validate_input, validate_item, validate_output, rule_version
from validation.review import decide_review, injection_suspected, REVIEW_PROFILE, DEFAULT_CONFIDENCE_THRESHOLD

RULES_PATH = Path(__file__).resolve().parents[1] / "docs" / "phase2_rules_v0.1.md"


def unknown(item, message, reason_code="P2_U_EVIDENCE_INSUFFICIENT"):
    return {"item_id": item["item_id"], "result": "UNKNOWN", "reason": message,
            "reason_codes": [reason_code], "citations": [], "check_kind": item["check_kind"]}


def run_validated_item(item, context, *, evidence_id, version, model, reason_codes,
                       self_check_call=call_llm, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
                       freshness_policy=None, format_policy=None, as_of=None, **harness_kwargs):
    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be in [0,1]")
    last_issues = []
    def validator(output):
        last_issues[:] = validate_item(output, item, context, reason_codes,
                                      evidence_id=evidence_id, version=version)
        return [f"{i['code']}: {i['message']}" for i in last_issues if i["severity"] == "error"]
    run = run_item_judgment(item, context, model=model, reason_codes=reason_codes,
                           global_rules=RULES_PATH.read_text(encoding="utf-8"),
                           output_schema=item_schema(), output_validator=validator, **harness_kwargs)
    issues = list(last_issues)
    signals = []
    audit = {"run": asdict(run), "issues": issues, "review_signals": signals}
    if not run.succeeded:
        if run.final_error_code:
            issues.append(issue(run.final_error_code, "문항 호출/검증 실패"))
        if run.request_error_status is not None:
            signals.append("LLM_REQUEST_REJECTED")
        return unknown(item, "문항 호출 또는 검증 실패로 근거를 확정할 수 없습니다."), audit
    output = deepcopy(run.parsed_output)
    output["check_kind"] = item["check_kind"]
    output.pop("critical", None)  # computed from the trusted checklist later
    date_candidates = extract_dates(context["chunks"])
    audit["date_candidates"] = date_candidates
    audit["freshness"] = {"status": "POLICY_MISSING"}
    audit["evidence_format"] = check_evidence_format(context["chunks"], format_policy)
    if freshness_policy is not None:
        if as_of is None:
            raise ValueError("as_of must be explicitly supplied with freshness_policy")
        audit["freshness"] = check_freshness(date_candidates, as_of=as_of, **freshness_policy)
        if audit["freshness"]["status"] != "FRESH":
            signals.append("FRESHNESS_" + audit["freshness"]["status"])
            output = unknown(item, "최신성 확인에 필요한 날짜·기준·시점 관계를 확정할 수 없습니다.",
                             "P2_U_TIME_OR_VERSION_UNCLEAR")
    if format_policy is not None and audit["evidence_format"]["status"] != "ALLOWED":
        signals.append(audit["evidence_format"]["status"])
        output = unknown(item, "항목별 인정 증적 형식을 확인할 수 없습니다.")
    if output["result"] in {"MET", "NOT_MET"}:
        if self_check_call is None:
            audit["self_check"] = {"verdict": "NOT_RUN"}
            signals.append("SELF_CHECK_NOT_RUN")
            output = unknown(item, "인용 근거의 의미상 충분성을 재검토하지 못했습니다.")
        else:
            options = {k: v for k, v in harness_kwargs.items()
                       if k in {"client_config", "generation", "http_client", "retry_policy", "sleeper"}}
            check = run_self_check(item, output, model=model, llm_call=self_check_call, **options)
            audit["self_check"] = check
            if check.get("error_code"):
                issues.append(issue(check["error_code"], "Self-check 호출/형식 검증 실패"))
            unsupported = check["verdict"] != "SUPPORTED" or bool(check["unsupported_conditions"])
            low_confidence = check["confidence"] < confidence_threshold
            if unsupported or low_confidence:
                signals.append("SELF_CHECK_" + check["verdict"] if unsupported else "SELF_CHECK_LOW_CONFIDENCE")
                reason = "P2_U_EVIDENCE_CONFLICT" if check["verdict"] == "CONFLICT" else "P2_U_EVIDENCE_INSUFFICIENT"
                output = unknown(item, "인용문만으로 판정을 확정할 수 없어 검토가 필요합니다: " + check["reason"], reason)
    else:
        audit["self_check"] = {"verdict": "NOT_APPLICABLE_UNKNOWN"}
    audit["confidence_threshold"] = confidence_threshold
    return output, audit


def run_control_judgment(payload, control_id, *, catalog, reason_catalog, controls, model,
                         allow_draft=False, critical_policy=None, item_policies=None, as_of=None, progress=None,
                         controls_sha256=None,
                         **item_kwargs):
    issues = validate_input(payload, catalog, controls)
    audit = {"validator_version": "phase2_validator_v0.1", "review_profile": REVIEW_PROFILE,
             "issues": issues, "items": [], "review_signals": [], "model": model,
             "as_of": str(as_of) if as_of else None,
             "item_policies": deepcopy(item_policies or {}),
             "source_versions": deepcopy(payload.get("source_versions", {})) if isinstance(payload, dict) else {}}
    audit["prompt_version"] = PROMPT_VERSION
    audit["self_check_version"] = SELF_CHECK_VERSION
    audit["retry_policy"] = retry_policy_snapshot(item_kwargs.get("retry_policy", DEFAULT_RETRY_POLICY))
    audit["hashes"] = {"rules_sha256": sha256(RULES_PATH.read_bytes()).hexdigest(),
                       "input_schema_sha256": sha256((INTERFACE / "phase2_input.schema.json").read_bytes()).hexdigest(),
                       "output_schema_sha256": sha256((INTERFACE / "phase2_output.schema.json").read_bytes()).hexdigest()}
    for key, value in (("input", payload), ("checklist", catalog), ("reason_catalog", reason_catalog), ("control_names", controls)):
        audit["hashes"][key + "_canonical_sha256"] = sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if controls_sha256 is not None:
        audit["hashes"]["controls_identity_sha256"] = controls_sha256
        audit["kb_hash_rule"] = interface_module("kb_identity").KB_HASH_RULE
        if not issues and payload["source_versions"].get("kb_sha256") != controls_sha256:
            issues.append(issue("P2E005", "실제 로드한 controls 파일 해시와 Phase 1 입력 해시 불일치"))
    def stopped(status):
        return {"processing_status": status, "output": None, "audit": audit}
    if issues:
        return stopped("FAILED")
    if catalog.get("approved") is not True or reason_catalog.get("approved") is not True:
        issues.append(issue("P2E006", "미승인 체크리스트/사유 코드 사용", severity="warning"))
        if not allow_draft:
            return stopped("FAILED")
    target = next((t for t in payload["targets"] if t["control_id"] == control_id and t["judge"]), None)
    if target is None:
        issues.append(issue("P2E001", "선택한 control_id는 판정 대상이 아니다", severity="warning"))
        return stopped("COMPLETED")
    control = next((c for c in catalog["controls"] if c["control_id"] == control_id), None)
    if control is None or not control.get("items"):
        issues.append(issue("P2E002", "현재 체크리스트에 질문지가 없다", severity="warning"))
        return stopped("COMPLETED")
    if control.get("control_name") != target["control_name"]:
        issues.append(issue("P2E005", "체크리스트 통제항목 명칭 불일치"))
        return stopped("FAILED")
    checklist_items = control["items"]
    ids = [i["item_id"] for i in checklist_items]
    if len(set(ids)) != len(ids) or any(i.get("check_kind") not in {"procedure", "record", "implementation"}
        or i.get("control_id") != control_id for i in checklist_items):
        issues.append(issue("P2E005", "체크리스트 문항 중복 또는 check_kind 오류"))
        return stopped("FAILED")
    if item_policies:
        unknown_ids = set(item_policies) - {i["item_id"] for c in catalog["controls"] for i in c["items"]}
        if unknown_ids:
            raise ValueError(f"policy refers to unknown item IDs: {sorted(unknown_ids)}")
        for item_id, entry in item_policies.items():
            if set(entry) - {"freshness", "allowed_types"}:
                raise ValueError(f"unknown policy field: {item_id}")
            if "allowed_types" in entry and (not isinstance(entry["allowed_types"], list) or not entry["allowed_types"]
                    or not set(entry["allowed_types"]) <= {"pdf", "docx", "xlsx", "pptx", "txt", "csv", "png", "jpg"}):
                raise ValueError(f"invalid allowed_types: {item_id}")
            if "freshness" in entry:
                if as_of is None:
                    raise ValueError("as_of is required for freshness policies")
                check_freshness([], as_of=as_of, **entry["freshness"])
    policy = critical_policy or interface_module("overall").default_policy()
    if policy.get("mode") not in {"check_kind", "explicit", "all"}:
        raise ValueError("unknown critical policy")
    if policy["mode"] == "check_kind" and (not policy.get("critical_kinds") or not set(policy["critical_kinds"]) <= {"procedure", "record", "implementation"}):
        raise ValueError("critical_kinds must be explicit and nonempty")
    if policy["mode"] == "explicit" and any(type(i.get("critical")) is not bool for i in checklist_items):
        issues.append(issue("P2E503", "명시적 critical 값이 미정이다"))
        audit["review_signals"].append("CRITICAL_UNDECIDED")
        return stopped("REVIEW_REQUIRED")
    try:
        anchors = target["mapping_citations"]
        context = build_evidence_context(payload["chunks"], [c["chunk_id"] for c in anchors])
    except ContextBuildError as exc:
        mapping = {"NO_CHUNKS": "P2E003", "INVALID_CHUNK": "P2E004", "DUPLICATE_CHUNK_ID": "P2E007",
                   "NO_EVIDENCE_ANCHOR": "P2E008", "ANCHOR_CHUNK_NOT_FOUND": "P2E009"}
        issues.append(issue(mapping.get(exc.code, "P2E005"), str(exc)))
        return stopped("FAILED")
    audit["context"] = context
    injected = injection_suspected(context)
    outputs = []
    for index, item in enumerate(checklist_items, start=1):
        if progress:
            progress(index, len(checklist_items), item["item_id"], "START")
        policy_for_item = (item_policies or {}).get(item["item_id"], {})
        output, report = run_validated_item(item, context, evidence_id=payload["evidence_id"],
                    version=payload["version"], model=model, reason_codes=reason_catalog["codes"],
                    freshness_policy=policy_for_item.get("freshness"), format_policy=policy_for_item.get("allowed_types"),
                    as_of=as_of, **item_kwargs)
        output["critical"] = interface_module("overall").is_critical(item, policy)
        outputs.append(output)
        report["item_id"] = item["item_id"]
        audit["items"].append(report)
        issues.extend(report["issues"])
        audit["review_signals"].extend(report["review_signals"])
        if progress:
            progress(index, len(checklist_items), item["item_id"], output["result"])
    summary = interface_module("overall").compute(outputs, policy)
    summary["rule_version"] = rule_version(policy)
    result = {"schema_version": "phase2-output-0.2", "evidence_id": payload["evidence_id"],
              "version": payload["version"], "control_id": control_id, "control_name": control["control_name"],
              "checklist_version": catalog.get("draft_version") or catalog.get("version"),
              "items": outputs, **summary}
    result["human_review"] = decide_review(outputs, context, issues, signals=audit["review_signals"], injection=injected)
    assembly_errors = validate_output(result, payload, checklist_items, policy)
    if assembly_errors:
        issues.extend(assembly_errors)
        return stopped("FAILED")
    return {"processing_status": "REVIEW_REQUIRED" if result["human_review"]["required"] else "COMPLETED",
            "output": result, "audit": audit}
