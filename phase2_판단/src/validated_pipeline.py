"""Public Phase 2 execution entry: official schema -> harness -> validators -> review.

The old harness remains useful for prompt experiments. Production callers use this entry.
The return value separates schema-compliant output from processing/audit metadata.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re

from context_builder import build_control_evidence_context, ContextBuildError
from date_extractor import extract_dates, check_freshness, check_evidence_format
from judgment_harness import run_item_judgment
from phase1_runtime import call_llm, DEFAULT_RETRY_POLICY, retry_policy_snapshot
from self_check import run_self_check, SELF_CHECK_VERSION
from grounding_prompts import PROMPT_VERSION
from validation.contracts import (ROOT, INTERFACE, interface_module, item_schema, issue,
                                  output_schema_version, validate_schema)
from validation.logic import validate_input, validate_item, validate_output, rule_version
from validation.review import decide_review, injection_suspected, REVIEW_PROFILE, DEFAULT_CONFIDENCE_THRESHOLD

RULES_PATH = Path(__file__).resolve().parents[1] / "docs" / "phase2_rules_v0.1.md"

_EVIDENCE_ABSENCE_RE = re.compile(
    r"기록(?:되어)?\s*있지\s*않|기록이\s*없|자료가\s*(?:제공|제출)되지\s*않|"
    r"확인되지\s*않|누락|미제출|판독\s*불가|알\s*수\s*없"
)
_DIRECT_NONCOMPLIANCE_RE = re.compile(
    r"수행하지\s*않|미수행|생략|위반|미준수|하지\s*않았|방치|미적용|미수립|"
    r"수립하지\s*않|적용하지\s*않"
)

_QUALIFICATION_RE = re.compile(r"자격|경력")
_PRIVACY_OFFICER_RE = re.compile(r"개인정보\s*보호책임자|CPO", re.IGNORECASE)
_SECURITY_OFFICER_RE = re.compile(r"정보보호\s*최고책임자|CISO", re.IGNORECASE)
_ROLE_CODE_RE = re.compile(r"직무코드\s*:\s*([A-Z]+-\d+)")
_TARGET_SYSTEM_RE = re.compile(r"대상\s*시스템\s*:\s*([A-Z]+-\d+)")
_REQUESTED_PERMISSION_RE = re.compile(r"요청\s*권한\s*:\s*([^|\n]+)")
_YEAR_RE = re.compile(r"20\d{2}")
_RETENTION_OBSERVATION_RE = re.compile(r"보관\s*시작|현재\s*보관기간|최초\s*(?:로그|기록)|관찰\s*기간|생성일")
_MARKETING_NO_EVENT_RE = re.compile(
    r"(?:홍보|판매\s*권유).{0,30}위탁.{0,20}(?:없|미발생|하지\s*않)", re.IGNORECASE
)
_MARKETING_POLICY_RE = re.compile(
    r"(?:홍보.?판매\s*권유\s*업무\s*위탁\s*시|해당\s*업무를\s*위탁하면)"
    r".{0,60}(?:문자|이메일|전자우편|서면).{0,30}(?:안내|통지)",
    re.IGNORECASE,
)
_NOTICE_EVENT_RE = re.compile(r"통지일|발송일|안내일|통지\s*완료|발송\s*완료")
_RESTORE_TEST_CADENCE_RE = re.compile(
    r"(?:복구\s*테스트|복구시험).{0,50}(?:주기|매월|분기|반기|연\s*\d+\s*회|월\s*\d+\s*회)|"
    r"(?:주기|매월|분기|반기|연\s*\d+\s*회|월\s*\d+\s*회).{0,50}(?:복구\s*테스트|복구시험)"
)


def _absence_only_not_met(item, output):
    """Reject NOT_MET that proves only missing evidence, not non-compliance."""
    if output.get("result") != "NOT_MET":
        return False
    rule = item.get("evidence_rule") or {}
    boundary = " ".join(str(rule.get(key, "")) for key in ("not_met", "unknown"))
    if not any(marker in boundary for marker in ("미제출", "누락", "확인되지", "판독 불가")):
        return False
    cited = " ".join(
        str(citation.get("quote", "")) for citation in output.get("citations", [])
    )
    return bool(_EVIDENCE_ABSENCE_RE.search(cited)) and not bool(
        _DIRECT_NONCOMPLIANCE_RE.search(cited)
    )


def _ocr_qualification_binding_unproven(item, output, context):
    """Fail closed when OCR lost the row binding between an officer and qualifications."""
    if output.get("result") != "MET" or "자격" not in str(item.get("question", "")):
        return False
    question = str(item.get("question", ""))
    role_pattern = (
        _PRIVACY_OFFICER_RE if _PRIVACY_OFFICER_RE.search(question)
        else _SECURITY_OFFICER_RE if _SECURITY_OFFICER_RE.search(question)
        else None
    )
    if role_pattern is None:
        return False
    cited_ids = {row.get("chunk_id") for row in output.get("citations", [])}
    chunks = [row for row in context.get("chunks", []) if row.get("chunk_id") in cited_ids]
    if not chunks or not any(str(row.get("source", "")).lower() == "ocr" for row in chunks):
        return False
    lines = [line.strip() for row in chunks for line in str(row.get("text", "")).splitlines() if line.strip()]
    return not any(role_pattern.search(line) and _QUALIFICATION_RE.search(line) for line in lines)


def _access_appropriateness_binding_unproven(item, output):
    """Require the selected access event to be joined to its role-permission baseline."""
    question = str(item.get("question", ""))
    if output.get("result") != "MET" or "적절성" not in question or "권한" not in question:
        return False
    quotes = [str(row.get("quote", "")) for row in output.get("citations", [])]
    events = [quote for quote in quotes if "신청자" in quote and "요청 권한" in quote]
    if not events:
        return False
    for event in events:
        role = _ROLE_CODE_RE.search(event)
        system = _TARGET_SYSTEM_RE.search(event)
        permission = _REQUESTED_PERMISSION_RE.search(event)
        if not (role and system and permission):
            continue
        if re.search(r"적절(?:성)?\s*(?:검토|판정)|기준과\s*일치", event):
            return False
        requested = permission.group(1).strip()
        for quote in quotes:
            if quote == event or "신청자" in quote:
                continue
            if role.group(1) in quote and system.group(1) in quote and requested in quote:
                return False
    return True


def _direct_recurrence_evidence(item, output, context):
    """Recognize an explicit recurrence statement tied to previous/current report years."""
    if output.get("result") != "MET" or "재발" not in str(item.get("question", "")):
        return False
    cited = " ".join(str(row.get("quote", "")) for row in output.get("citations", []))
    full = " ".join(str(row.get("text", "")) for row in context.get("chunks", []))
    years = {int(year) for year in _YEAR_RE.findall(full)}
    return "재발" in cited and len(years) >= 2 and bool(
        re.search(r"대책|의무화|감시|조치|개선|방지", cited)
    )


def _partial_storage_reason(item, output):
    """Return an accurate split-scope reason for paper-lock/electronic-registration evidence."""
    if output.get("result") not in {"MET", "UNKNOWN"} or "접근" not in str(item.get("question", "")):
        return None
    cited = " ".join(str(row.get("quote", "")) for row in output.get("citations", []))
    if "잠금" not in cited or "전자" not in cited:
        return None
    if re.search(r"전자.{0,120}(?:접근권한|조회.{0,20}제한|접근.{0,20}제한)", cited):
        return None
    return (
        "종이 원본의 잠금 보관은 확인되지만, 전자 사본은 등록 사실만 확인되고 "
        "전자 접근권한·조회 제한은 확인할 수 없어 전체 보관 범위의 접근 제한을 확정할 수 없습니다."
    )


def _normalize_recurrence_reason(item, output, context):
    """Describe only the recurrence and countermeasure facts actually cited."""
    if not _direct_recurrence_evidence(item, output, context):
        return None
    return (
        "원문은 이전 점검에서 도출된 취약점이 동일 대상에서 재발했다고 직접 기록하고, "
        "보안 설정 기준 체크리스트 의무화와 설정 변경 상시 감시를 후속 대책으로 기록하고 있습니다."
    )


def _log_retention_observation_unproven(item, output):
    """A configured retention period is not proof that records survived that period."""
    question = str(item.get("question", ""))
    if output.get("result") != "MET" or "로그" not in question or "보존기간 동안" not in question:
        return False
    cited = " ".join(str(row.get("quote", "")) for row in output.get("citations", []))
    return not bool(_RETENTION_OBSERVATION_RE.search(cited))


def _trusted_line_citations(context, predicate):
    """Restore exact source lines as citations after a deterministic semantic guard."""
    citations = []
    for chunk in context.get("chunks", []):
        for line in str(chunk.get("text", "")).splitlines():
            quote = line.strip()
            if not quote or not predicate(quote):
                continue
            citation = {
                "chunk_id": chunk.get("chunk_id"),
                "page": chunk.get("page_start"),
                "quote": quote,
            }
            if citation not in citations:
                citations.append(citation)
    return citations


def _restore_test_scope(item, context):
    """Return observed restore tests and whether an applicable cadence is stated."""
    question = str(item.get("question", ""))
    if "복구 테스트" not in question or "주기" not in question:
        return [], False
    records = _trusted_line_citations(
        context,
        lambda line: (
            "일자:" in line and "대상:" in line and "방법:" in line and "결과:" in line
            and bool(re.search(r"복구|복원", line))
        ),
    )
    cadence = any(
        _RESTORE_TEST_CADENCE_RE.search(line)
        for chunk in context.get("chunks", [])
        for line in str(chunk.get("text", "")).splitlines()
    )
    return records, cadence


def _log_retention_scope(item, context):
    """Collect retention observations without inferring the assessed scope.

    A document's policy table is not an explicit list of evaluation targets.
    No company-specific system identifiers or category mappings are inferred.
    """
    question = str(item.get("question", ""))
    if "로그" not in question or "보존기간 동안" not in question:
        return [], []
    observed = _trusted_line_citations(
        context,
        lambda line: "보관 시작:" in line and "현재 보관기간:" in line,
    )
    return observed, []


def _acknowledge_observations(item, output, context):
    """Preserve an UNKNOWN's diagnostic, adding observations without new absence claims."""
    if output.get("result") != "UNKNOWN":
        return None
    records, _ = _restore_test_scope(item, context)
    activity = "복구·복원"
    if not records:
        records, _ = _log_retention_scope(item, context)
        activity = "보관 시작·현재 보관기간"
    if not records:
        return None
    original_reason = output.get("reason", "")
    output["reason"] = (
        f"제공된 문맥에 {activity} 기록이 있습니다. 아래 인용은 해당 기록의 존재를 보여주며, "
        "그 자체로 문항 충족을 확정하지 않습니다. 기존 UNKNOWN 사유와 이 기록의 "
        "대상·기준·기간 연결을 재검토해야 합니다."
    )
    for citation in records:
        if citation not in output.setdefault("citations", []):
            output["citations"].append(citation)
    return {"version": "phase2_observation_acknowledgement_v1",
            "decision": "UNKNOWN_REVIEW_REQUIRED", "original_reason": original_reason,
            "reason": "기록 존재만 보존하며 적용 주기·기준일 부재나 전체 평가 범위를 추정하지 않음"}


def _marketing_notice_basis(item, output):
    """Return actual_notice, no_event_exception, or None for a marketing-notice MET."""
    question = str(item.get("question", ""))
    if output.get("result") != "MET" or "홍보" not in question or "판매" not in question or "알렸" not in question:
        return "not_applicable"
    cited = " ".join(str(row.get("quote", "")) for row in output.get("citations", []))
    if _NOTICE_EVENT_RE.search(cited):
        return "actual_notice"
    if _MARKETING_NO_EVENT_RE.search(cited) and _MARKETING_POLICY_RE.search(cited):
        return "no_event_exception"
    return None


def _conservative_token_upper_bound(text):
    """UTF-8 byte count is a conservative upper bound for byte-level BPE tokens."""
    return len(text.encode("utf-8"))


def _anchor_quotes(citations):
    grouped = {}
    for citation in citations:
        grouped.setdefault(citation["chunk_id"], [])
        if citation["quote"] not in grouped[citation["chunk_id"]]:
            grouped[citation["chunk_id"]].append(citation["quote"])
    return grouped


def unknown(item, message, reason_code="P2_U_EVIDENCE_INSUFFICIENT", *, error_code=None):
    result = {"item_id": item["item_id"], "result": "UNKNOWN", "reason": message,
              "reason_codes": [reason_code], "citations": [], "check_kind": item["check_kind"]}
    if error_code:
        result["error_code"] = error_code
    return result


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
        return unknown(item, "문항 호출 또는 검증 실패로 근거를 확정할 수 없습니다.",
                       error_code=run.final_error_code or "E202"), audit
    output = deepcopy(run.parsed_output)
    output["check_kind"] = item["check_kind"]
    output.pop("critical", None)  # computed from the trusted checklist later
    if _absence_only_not_met(item, output):
        audit["semantic_guards"] = [{
            "version": "phase2_absence_not_failure_v1",
            "decision": "NOT_MET_TO_UNKNOWN",
            "reason": "인용은 근거의 미기록·미제출·확인 불가만 입증하며 직접 미수행·위반을 입증하지 않음",
        }]
        output = unknown(
            item,
            "근거가 기록·제출되지 않았다는 사실만으로 실제 미이행을 확정할 수 없습니다.",
        )
    if _ocr_qualification_binding_unproven(item, output, context):
        audit.setdefault("semantic_guards", []).append({
            "version": "phase2_ocr_role_qualification_binding_v1",
            "decision": "MET_TO_UNKNOWN",
            "reason": "OCR 원문에서 검토 대상 책임자와 자격·경력이 같은 행에 직접 연결되지 않음",
        })
        output = unknown(
            item,
            "OCR 원문에서 검토 대상 책임자와 자격·경력의 행 관계를 확정할 수 없습니다.",
            "P2_U_SUBJECT_OR_EVENT_UNLINKED",
        )
    if _access_appropriateness_binding_unproven(item, output):
        audit.setdefault("semantic_guards", []).append({
            "version": "phase2_access_case_binding_v1",
            "decision": "MET_TO_UNKNOWN",
            "reason": "선택한 신청 사건의 직무코드·시스템·요청 권한이 역할 기준표와 연결되지 않음",
        })
        output = unknown(
            item,
            "선택한 신청 사건의 직무코드·시스템·요청 권한과 역할 기준표의 연결을 확인할 수 없습니다.",
            "P2_U_SUBJECT_OR_EVENT_UNLINKED",
        )
    normalized_storage_reason = _partial_storage_reason(item, output)
    if normalized_storage_reason:
        prior_result = output.get("result")
        partial_citations = deepcopy(output.get("citations", []))
        audit.setdefault("semantic_guards", []).append({
            "version": "phase2_partial_storage_scope_v1",
            "decision": "MET_TO_UNKNOWN" if prior_result == "MET" else "UNKNOWN_REASON_NORMALIZED",
            "reason": "확인된 종이 잠금과 미확인 전자 접근통제를 분리해 설명",
        })
        if prior_result == "MET":
            output = unknown(item, normalized_storage_reason)
            output["citations"] = partial_citations
        else:
            output["reason"] = normalized_storage_reason
    restore_records, restore_cadence = _restore_test_scope(item, context)
    if restore_records and not restore_cadence and output.get("result") in {"MET", "UNKNOWN"}:
        audit.setdefault("semantic_guards", []).append({
            "version": "phase2_restore_test_cadence_v1",
            "decision": "MET_TO_UNKNOWN" if output.get("result") == "MET" else "UNKNOWN_REASON_NORMALIZED",
            "reason": "실제 복구 시험 기록과 적용 시험 주기·대상 기간의 확인 여부를 분리",
        })
        output = unknown(
            item,
            "복구 테스트 실시 기록은 확인되지만, 적용 시험 주기와 검토 대상 기간·기준일이 확인되지 않아 "
            "정해진 주기에 따른 실시 여부를 확정할 수 없습니다.",
            "P2_U_TIME_OR_VERSION_UNCLEAR",
        )
        output["citations"] = restore_records
    normalized_recurrence_reason = _normalize_recurrence_reason(item, output, context)
    if normalized_recurrence_reason:
        audit.setdefault("semantic_guards", []).append({
            "version": "phase2_explicit_recurrence_v2",
            "decision": "MET_REASON_NORMALIZED",
            "reason": "원문에 직접 기록된 재발과 후속대책만 설명하고 보고연도 등 추가 사실을 만들지 않음",
        })
        output["reason"] = normalized_recurrence_reason
    if _log_retention_observation_unproven(item, output):
        audit.setdefault("semantic_guards", []).append({
            "version": "phase2_log_retention_observation_v1",
            "decision": "MET_TO_UNKNOWN",
            "reason": "보존기간 설정·백업 정책 외 실제 보관 시작·유지 기간 근거가 인용되지 않음",
        })
        output = unknown(
            item,
            "설정된 보존기간과 백업 정책은 확인되지만, 실제 보관 시작·현재 유지 기간을 확인할 근거가 인용되지 않았습니다.",
            "P2_U_TIME_OR_VERSION_UNCLEAR",
        )
    marketing_basis = _marketing_notice_basis(item, output)
    if marketing_basis is None:
        audit.setdefault("semantic_guards", []).append({
            "version": "phase2_marketing_notice_basis_v1",
            "decision": "MET_TO_UNKNOWN",
            "reason": "실제 통지 기록 또는 위탁 미발생 직접 근거와 사전 통지 방침의 결합이 없음",
        })
        output = unknown(
            item,
            "실제 통지 기록이 없고, 홍보·판매 권유 위탁 미발생 사실도 직접 확인되지 않아 통지 이행을 확정할 수 없습니다.",
        )
    elif marketing_basis == "no_event_exception":
        output["reason"] = (
            "검토 기간에 홍보·판매 권유 업무 위탁이 없다는 직접 기록과, 위탁 발생 시 업무 내용과 "
            "수탁자를 정보주체에게 통지한다는 방침이 함께 확인됩니다. 실제 통지 완료를 의미하지는 않습니다."
        )
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
            check = run_self_check(item, output, context=context, model=model,
                                   llm_call=self_check_call, **options)
            audit["self_check"] = check
            if check.get("error_code"):
                issues.append(issue(check["error_code"], "Self-check 호출/형식 검증 실패"))
            unsupported = check["verdict"] != "SUPPORTED" or bool(check["unsupported_conditions"])
            low_confidence = check["confidence"] < confidence_threshold
            complaint = " ".join([
                str(check.get("reason", "")),
                *[str(value) for value in check.get("unsupported_conditions", [])],
            ])
            recurrence_override = (
                _direct_recurrence_evidence(item, output, context)
                and check.get("verdict") == "UNSUPPORTED"
                and bool(re.search(r"(?:비교|대조).*(?:없|부족|명시)", complaint))
            )
            consistency_override = (
                check.get("consistency_guard", {}).get("decision")
                == "RETAIN_PROPOSED_NOT_MET"
            )
            if recurrence_override:
                audit.setdefault("semantic_guards", []).append({
                    "version": "phase2_explicit_recurrence_v1",
                    "decision": "RETAIN_MET",
                    "reason": "이전·현재 연도와 재발 및 후속대책이 인용 원문에 직접 명시됨",
                })
                unsupported = False
                low_confidence = False
            if consistency_override:
                audit.setdefault("semantic_guards", []).append(check["consistency_guard"])
                unsupported = False
                low_confidence = False
            if unsupported or low_confidence:
                signals.append("SELF_CHECK_" + check["verdict"] if unsupported else "SELF_CHECK_LOW_CONFIDENCE")
                reason = "P2_U_EVIDENCE_CONFLICT" if check["verdict"] == "CONFLICT" else "P2_U_EVIDENCE_INSUFFICIENT"
                output = unknown(item, "인용문만으로 판정을 확정할 수 없어 검토가 필요합니다: " + check["reason"], reason)
    else:
        audit["self_check"] = {"verdict": "NOT_APPLICABLE_UNKNOWN"}
    # Do not replace conflicts, policy failures or technical failures with a generic reason.
    if output.get("reason_codes") == ["P2_U_EVIDENCE_INSUFFICIENT"] and not issues and not signals:
        acknowledgement = _acknowledge_observations(item, output, context)
        if acknowledgement:
            audit.setdefault("semantic_guards", []).append(acknowledgement)
            signals.append("SELF_CHECK_UNCERTAIN")
            final_issues = validate_item(output, item, context, reason_codes,
                                        evidence_id=evidence_id, version=version)
            issues.extend(final_issues)
            if any(entry.get("severity") == "error" for entry in final_issues):
                output = unknown(item, "추가 인용 검증 실패로 검토가 필요합니다.", error_code="E202")
    audit["confidence_threshold"] = confidence_threshold
    return output, audit


def run_control_judgment(payload, control_id, *, catalog, reason_catalog, controls, model,
                         allow_draft=False, critical_policy=None, item_policies=None, as_of=None, progress=None,
                         controls_sha256=None, context_max_chunks=12, context_max_tokens=12000,
                         context_token_counter=None,
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
    catalog_policy = catalog.get("critical_policy", {}).get("runtime_policy")
    policy = deepcopy(critical_policy or catalog_policy or interface_module("overall").default_policy())
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
        context = build_control_evidence_context(
            payload["chunks"],
            [c["chunk_id"] for c in anchors],
            checklist_items,
            anchor_quotes=_anchor_quotes(anchors),
            max_chunks=context_max_chunks,
            max_context_tokens=context_max_tokens,
            token_counter=context_token_counter or _conservative_token_upper_bound,
        )
    except ContextBuildError as exc:
        mapping = {"NO_CHUNKS": "P2E003", "INVALID_CHUNK": "P2E004", "DUPLICATE_CHUNK_ID": "P2E007",
                   "NO_EVIDENCE_ANCHOR": "P2E008", "ANCHOR_CHUNK_NOT_FOUND": "P2E009",
                   "MAX_CHUNKS_TOO_SMALL": "P2E005", "TOKEN_BUDGET_TOO_SMALL": "P2E005"}
        issues.append(issue(mapping.get(exc.code, "P2E005"), str(exc)))
        return stopped("FAILED")
    audit["context"] = context
    audit["context_policy"] = {
        "mode": context.get("selection_audit", {}).get("mode"),
        "max_chunks": context_max_chunks,
        "max_context_tokens": context_max_tokens,
        "token_counter": "injected" if context_token_counter else "conservative_utf8_byte_upper_bound",
    }
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
    result = {"schema_version": output_schema_version(), "evidence_id": payload["evidence_id"],
              "version": payload["version"], "control_id": control_id, "control_name": control["control_name"],
              "checklist_version": catalog.get("draft_version") or catalog.get("version"),
              "items": outputs, **summary}
    result["human_review"] = decide_review(outputs, context, issues, signals=audit["review_signals"], injection=injected)
    result.update(interface_module("errors").provisional_fields(result["human_review"]))
    assembly_errors = validate_output(result, payload, checklist_items, policy)
    if assembly_errors:
        issues.extend(assembly_errors)
        return stopped("FAILED")
    return {"processing_status": "REVIEW_REQUIRED" if result["human_review"]["required"] else "COMPLETED",
            "output": result, "audit": audit}
