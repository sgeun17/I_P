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
from judgment_harness import run_item_judgment, citation_edit_issues, explanation_edit_issues
from phase1_runtime import call_llm, DEFAULT_RETRY_POLICY, retry_policy_snapshot
from self_check import run_self_check, SELF_CHECK_VERSION, COMPACT_SELF_CHECK_VERSION
from grounding_prompts import PROMPT_VERSION, COMPACT_PROMPT_VERSION
from evidence_contracts import question_binding_gap, VERSION as BINDING_VERSION
from absence_basis import absence_basis_gap
from reason_citation_binding import inspect_reason_citation_binding
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


def _reason_account_subject_gap(output, context):
    """Veto transferring a known account ID absent from the selected evidence.

    Conservative: only explicit lower-case underscore IDs occurring in the
    supplied source are recognized, never names inferred from layout.
    """
    if output.get('result') not in {'MET', 'NOT_MET'}:
        return []
    pattern = re.compile(r'(?<![A-Za-z0-9_])[a-z][a-z0-9]*_[a-z0-9_]+(?![A-Za-z0-9_])')
    known = set(pattern.findall(' '.join(str(c.get('text', '')) for c in context.get('chunks', []))))
    mentioned = set(pattern.findall(str(output.get('reason', '')))) & known
    cited = set(pattern.findall(' '.join(str(c.get('quote', '')) for c in output.get('citations', []))))
    return sorted(mentioned - cited)


def _notice_style_only_as_complete(item, output):
    """Reject observed style/consent-refusal fragments as exhaustive notice.

    This is not a legal checklist or a completeness classifier. A false return
    only leaves normal validation in place; it never establishes compliance.
    """
    question = str(item.get('question', ''))
    if output.get('result') != 'MET' or not re.search(r'(고지|안내)\s*사항', question):
        return False
    if not re.search(r'빠짐|모두|전체|누락', question):
        return False
    quotes = [str(c.get('quote', '')).strip() for c in output.get('citations', [])]
    if not quotes or any(not quote for quote in quotes):
        return False  # Mechanical validation owns missing citations.
    style = re.compile(r'굵(?:은|게)|밑줄|강조|글씨|글자|색상|표시\s*방법')
    refusal = re.compile(r'동의.{0,16}거부|동의하지\s*않|가입.{0,12}(제한|불가)')
    # Actual field/value rows or additional content must be evaluated normally.
    # Do not treat a style sentence as a field-by-field completeness comparison.
    clauses = [line.strip() for quote in quotes for line in re.split(r'[。.!?\n]', quote) if line.strip()]
    # A refusal-right/consequence sentence is one notice fragment, not proof
    # that all required notice contents were supplied. Adding that fragment
    # must not bypass the style-only veto. Field/value content still follows
    # normal review; this guard does not certify its completeness.
    return bool(clauses) and all((style.search(line) or refusal.search(line))
        and not re.search(r'[:：|]', line) for line in clauses)


def _observed_evidence_gap(item, output, context):
    """Narrow negative guards. No false return establishes MET.

    These separate the observed record types; they neither invent a deadline
    nor treat a single keyword or an absent file as noncompliance.
    """
    if output.get('result') != 'MET':
        return None
    question = re.sub(r'\s+', '', str(item.get('question', '')))
    chunks = context.get('chunks', [])
    by_id = {c['chunk_id']: c for c in chunks}
    citations = output.get('citations', [])
    if not citations:
        return None
    quoted = '\n'.join(str(c.get('quote', '')) for c in citations)
    lines = [line for c in chunks for line in str(c.get('text', '')).splitlines()]
    if (item.get('check_kind') == 'implementation' and '백업' in question
            and '주기' in question and not re.search(r'복구|복원', question)):
        dated_rows = [line for line in quoted.splitlines()
                      if re.search(r'(?:일자|수행일|실행일)\s*[:：]\s*20\d{2}[-./]\d', line)]
        if dated_rows and all(re.search(r'복구|복원', row) and not re.search(r'백업', row)
                              for row in dated_rows):
            return ('RESTORE_RECORD_AS_BACKUP_CADENCE',
                    '선택한 일자별 기록은 복구·복원 활동이며 백업 수행 기록이 아닙니다. 백업 주기 기준과 같은 대상·기간의 실제 백업 기록을 대조해야 합니다.')
    # A narrowly scoped explanation veto, not a classifier of the whole source.
    # A resource-support question cannot be justified only by a support plan.
    # Do not infer that actual support never occurred or manufacture NOT_MET.
    if (item.get('check_kind') == 'implementation'
            and '예산' in question and '인력' in question
            and re.search(r'지원되|지원하였|지원했', question)):
        reason = str(output.get('reason', ''))
        plan = re.search(r'(?:지원|예산|인력).{0,20}계획|예산.{0,12}편성', reason)
        actual = re.search(r'집행|지급|배정|충원|채용|지원되었|지원됐|지원하였|지원했|지원하고\s*있', reason)
        if plan and not actual:
            return ('RESOURCE_PLAN_AS_SUPPORT',
                    '현재 설명은 예산·인력 지원 계획과 현황을 제시하지만, 평가 결과에 따른 실제 지원의 연결을 설명하지 못해 보류합니다. 실제 미지원으로 판정한 것은 아닙니다.')
    if item.get('check_kind') == 'implementation' and re.search(r'직무.*교육|전문교육', question):
        plan_rows = [line for line in quoted.splitlines() if re.search(r'과정\s*[:：].*(?:직무|전문).*교육', line)]
        if plan_rows and all('계획' in str(by_id.get(c.get('chunk_id'), {}).get('heading', '')) for c in citations):
            courses = [re.search(r'과정\s*[:：]\s*([^|]+)', row).group(1).strip() for row in plan_rows]
            completed = any(course in line and re.search(r'이수자\s*[:：]\s*[1-9]\d*|실시하였|시행완료|교육완료', line)
                            for course in courses for line in lines)
            if not completed:
                return ('PLAN_AS_EXECUTION', '직무 전문교육 계획은 확인되지만 같은 과정의 실제 실시·이수 기록이 연결되지 않아 교육 이행을 확정할 수 없습니다.')
    if item.get('check_kind') == 'procedure' and '로그' in question and '이상징후' in question and '대응절차' in question:
        response = re.compile(r'계정\s*잠금|권한\s*(?:회수|축소)|차단|소명\s*요청|신고|보고|대응\s*[:：]')
        procedure = re.compile(r'경우|발견\s*시|탐지\s*시|발생\s*시|절차|단계|하도록|하여야|해야')
        # A past incident action is not itself a prescribed response procedure.
        prescribed = any(response.search(line) and procedure.search(line) for line in lines)
        if '이상징후 기준' in quoted and not response.search(quoted) and not prescribed:
            return ('DETECTION_AS_RESPONSE', '이상징후 탐지 기준과 검토 방법은 확인되지만, 발견 후 조치·보고 등 대응절차의 수립 근거가 연결되지 않습니다.')
    if item.get('check_kind') == 'implementation' and re.search(r'계정|접근권한', question) and '주기' in question and '검토' in question:
        if '검토 주기' in quoted and '조치일' in quoted:
            dated_review = any(re.search(r'검토(?:기간|일|일자|수행일)\s*[:：].*20\d{2}', line) for line in lines)
            ids = set(re.findall(r'PROC-\d+(?:/\d+)?', quoted))
            differentiated = any('1등급' in line and '분기' in line for line in lines)
            grades_linked = all(any(target in line and re.search(r'(?:등급\s*[:：]|[123]등급)', line) for line in lines) for target in ids)
            if not dated_review or (differentiated and (not ids or not grades_linked)):
                return ('RECORD_AS_CADENCE', '검토 기준과 조치 기록은 있으나 대상별 적용 주기 및 실제 검토 수행 시점의 연결이 부족합니다. 조치일을 검토일로 바꾸지 않고 주기 준수 여부를 보류합니다.')
    return None


def _destruction_timing_missing(item, output, context):
    """Hold dated destruction records missing a linked trigger/deadline.

    No fixed legal grace period is introduced. False means only that this
    absence guard did not apply; normal semantic validation is still needed.
    """
    question = re.sub(r'\s+', '', str(item.get('question', '')))
    if output.get('result') != 'MET' or '파기' not in question or not re.search(r'지체없이|기한|기간내',question):
        return False
    chunks = context.get('chunks', [])
    by_id = {chunk.get('chunk_id'): chunk for chunk in chunks}
    dated = re.compile(r'파기일\s*[:：]\s*(20\d{2}[-./]\d{1,2}[-./]\d{1,2})')
    target_field = re.compile(r'(?:^|[|])\s*대상\s*[:：]\s*([^|\n]+)')
    source_rows = [(line, chunk) for chunk in chunks
                   for line in str(chunk.get('text', '')).splitlines()]
    cited_rows = [(line, by_id.get(c.get('chunk_id'), {}))
                  for c in output.get('citations', [])
                  for line in str(c.get('quote', '')).splitlines()]
    records = [(line, chunk) for line, chunk in cited_rows if dated.search(line)]
    if not records:
        # A policy-only quote must not hide execution rows that it describes.
        # Resolve its explicit category within the same source, rather than
        # treating every dated event in the context as this question's scope.
        categories = []
        for line, chunk in cited_rows:
            category = re.search(r'(?:^|[|])\s*(?:구분|대상)\s*[:：]\s*([^|\n]+)', line)
            if category and re.search(r'보유기간\s*[:：]|파기\s*방법\s*[:：]', line):
                value = category.group(1).strip()
                if len(value) >= 2:
                    categories.append((value, chunk.get('source_file')))
        for line, chunk in source_rows:
            target = target_field.search(line)
            if not dated.search(line) or not target:
                continue
            for category, source_file in categories:
                if source_file and chunk.get('source_file') != source_file:
                    continue
                # Delimited category matching avoids transferring a shorter
                # subject name to a different, longer subject identifier.
                if re.search(r'(?<![\w])' + re.escape(category) + r'(?![\w])', target.group(1)):
                    records.append((line, chunk))
                    break
    if not records:
        return False  # Other evidence forms remain with the normal reviewers.
    trigger = re.compile(r'(?:탈퇴일(?:자)?|탈퇴시점|보유기간\s*(?:만료일|종료일|경과일)|'
                         r'처리목적\s*(?:달성일|종료일)|파기대상\s*(?:발생일|확정일)|기산일|파기기한)'
                         r'\s*[:：]?\s*20\d{2}[-./]\d{1,2}[-./]\d{1,2}')
    # Direct same-event statements may establish relative timing without two
    # literal dates. A future policy alone is not a completed-event statement.
    relative = re.compile(r'탈퇴\s*당일|지체\s*없이|기한\s*내|즉시')
    completed = re.compile(r'파기\s*완료|파기하였|파기했다|삭제\s*완료')
    for record, record_chunk in records:
        if trigger.search(record):
            continue
        target = target_field.search(record)
        target_value = target.group(1).strip() if target else None
        event_date = dated.search(record).group(1)
        # A companion row must name the same target; dates of unrelated assets
        # or another event for that target cannot satisfy this requirement.
        same_target = []
        for line, chunk in source_rows:
            if (record_chunk.get('source_file') and chunk.get('source_file')
                    and record_chunk['source_file'] != chunk['source_file']):
                # A different file may still directly describe this event;
                # require its explicit event date as well as the target below.
                other_date = dated.search(line)
                if not other_date or other_date.group(1) != event_date:
                    continue
            named = target_field.search(line)
            if target_value and ((named and named.group(1).strip() == target_value)
                                 or (not named and target_value in line)):
                same_target.append(line)
        event_dates = {match.group(1) for line in same_target if (match := dated.search(line))}
        companions = [record]
        for line in same_target:
            companion_date = dated.search(line)
            if ((companion_date and companion_date.group(1) == event_date)
                    or (not companion_date and len(event_dates) <= 1)):
                companions.append(line)
        if any(trigger.search(line) for line in companions):
            continue
        if any(relative.search(line) and completed.search(line)
               and not re.search(r'미완료|하지않|하지\s*않|예정|해야|하여야|하도록',line) for line in companions):
            continue
        return True
    return False


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


def _run_validated_item_once(item, context, *, evidence_id, version, model, reason_codes,
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
    mismatched_accounts = _reason_account_subject_gap(output, context)
    if mismatched_accounts:
        audit.setdefault('semantic_guards', []).append({
            'version': 'phase2_evidence_repair_v1', 'decision': 'ACCOUNT_REASON_CITATION_MISMATCH',
            'accounts': mismatched_accounts, 'original_output': deepcopy(output)})
        citations = deepcopy(output.get('citations', []))
        output = unknown(item, '설명에 등장하는 계정과 선택한 인용의 계정이 일치하지 않아 사건 연결을 확인해야 합니다.',
                         'P2_U_SUBJECT_OR_EVENT_UNLINKED')
        output['citations'] = citations
        signals.append('SELF_CHECK_UNCERTAIN')
    binding_gap = question_binding_gap(item, output, context)
    if binding_gap:
        code, message = binding_gap
        audit.setdefault('semantic_guards', []).append({
            'version': BINDING_VERSION, 'code': code, 'decision': 'MET_TO_UNKNOWN',
            'original_output': deepcopy(output)})
        citations = deepcopy(output.get('citations', []))
        output = unknown(item, message, 'P2_U_SUBJECT_OR_EVENT_UNLINKED')
        output['citations'] = citations
        signals.append('SELF_CHECK_UNCERTAIN')
    gap = _observed_evidence_gap(item, output, context)
    if gap:
        code, message = gap
        audit.setdefault('semantic_guards', []).append({
            'version': 'phase2_observed_record_boundaries_v1', 'code': code,
            'decision': 'MET_TO_UNKNOWN', 'original_output': deepcopy(output)})
        citations = deepcopy(output.get('citations', []))
        output = unknown(item, message)
        output['citations'] = citations
        signals.append('SELF_CHECK_UNCERTAIN')
    if _destruction_timing_missing(item, output, context):
        audit.setdefault('semantic_guards', []).append({
            'version':'phase2_destruction_timing_binding_v2_source_scope',
            'decision':'MET_TO_UNKNOWN',
            'reason':'파기일은 확인되나 같은 대상의 파기 대상 발생일·기한 또는 직접 기한 준수 기록이 연결되지 않음',
            'original_output':deepcopy(output),
        })
        citations = deepcopy(output.get('citations', []))
        output = unknown(item,
            '파기 실시 기록은 확인되지만, 같은 대상의 탈퇴일·보유기간 종료일 등 파기 대상 발생 시점과 '
            '파기일의 관계가 확인되지 않아 지체 없이 파기했는지는 확정할 수 없습니다. '
            '파기 실시 여부와 기한 준수 여부를 구분하여 검토가 필요합니다.',
            'P2_U_TIME_OR_VERSION_UNCLEAR')
        output['citations'] = citations
        signals.append('SELF_CHECK_UNCERTAIN')
    if _notice_style_only_as_complete(item, output):
        audit.setdefault('semantic_guards', []).append({
            'version':'phase2_notice_completeness_fragments_v1_1',
            'decision':'MET_TO_UNKNOWN',
            'reason':'표시 방식과 동의 거부·가입 제한 등 일부 안내만으로 고지사항 전체 충족을 입증할 수 없음',
            'original_output':deepcopy(output),
        })
        citations = deepcopy(output.get('citations', []))
        output = unknown(item,
            '인용에서 강조 표시 또는 동의 거부·가입 제한 등 일부 안내는 확인되지만, '
            '그 인용만으로 적용 고지사항이 빠짐없이 포함되고 고지되었는지는 확인할 수 없습니다. '
            '해당 동의 화면·양식의 실제 고지 내용과 적용 기준의 항목별 대조가 필요합니다.')
        output['citations'] = citations
        signals.append('SELF_CHECK_UNCERTAIN')
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
    absence_argument = absence_basis_gap(item, output)
    if absence_argument:
        # Diagnostic, not a keyword-based verdict. Complete event histories
        # can establish violations despite similar wording. Keep a first-pass
        # result provisional, and do not auto-accept a repair using this basis.
        audit["absence_basis_review"] = absence_argument
        signals.append("SELF_CHECK_UNCERTAIN")
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
    review_unknown = (
        harness_kwargs.get('prompt_profile') == 'compact_v2'
        and output.get('result') == 'UNKNOWN'
        and not output.get('error_code') and not issues and not signals
        and not audit.get('semantic_guards')
    )
    if output["result"] in {"MET", "NOT_MET"} or review_unknown:
        if self_check_call is None:
            audit["self_check"] = {"verdict": "NOT_RUN"}
            signals.append("SELF_CHECK_NOT_RUN")
            output = unknown(item, "인용 근거의 의미상 충분성을 재검토하지 못했습니다.")
        else:
            options = {k: v for k, v in harness_kwargs.items()
                       if k in {"client_config", "generation", "http_client", "retry_policy", "sleeper", "prompt_profile"}}
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
                prior_output = deepcopy(output)
                # Keep the model opinion in audit, but distinguish a failed
                # review contract or low confidence from an evidence finding.
                # In particular, a positive low-confidence review is not an
                # UNKNOWN explanation saying that MET has been established.
                if check.get("error_code") or check.get("request_error_status") is not None:
                    hold_basis = "REVIEW_EXECUTION_OR_CONTRACT_FAILURE"
                    public_reason = (
                        "재검토 호출 또는 응답 검증에 실패하여 판정을 보류합니다. "
                        "이는 증적에 해당 활동이 없거나 실제 미이행이라는 판단이 아닙니다."
                    )
                elif check.get("verdict") == "SUPPORTED" and check.get("unsupported_conditions"):
                    hold_basis = "REVIEW_INTERNAL_CONTRADICTION"
                    public_reason = (
                        "재검토가 판정을 지지하면서 동시에 미확인 조건을 제시하여 결론이 일치하지 않습니다. "
                        "원문과 조건의 연결을 다시 확인해야 합니다."
                    )
                elif low_confidence and not unsupported:
                    hold_basis = "REVIEW_CONFIDENCE_ONLY"
                    public_reason = (
                        "재검토 의견은 판정을 지지하지만 검토 신뢰도가 설정된 기준보다 낮아 보류합니다. "
                        "확인된 근거가 없다는 뜻은 아니며 추가 확인이 필요합니다."
                    )
                else:
                    hold_basis = "REVIEW_EVIDENCE_DISAGREEMENT"
                    public_reason = "인용문만으로 판정을 확정할 수 없어 검토가 필요합니다: " + check["reason"]
                audit["review_hold"] = {
                    "basis": hold_basis, "prior_output": prior_output,
                    "review_reason_preserved_in_self_check": True,
                }
                output = unknown(item, public_reason, reason)
                output["citations"] = deepcopy(prior_output.get("citations", []))
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
    if harness_kwargs.get('prompt_profile') == 'compact_v2' and not output.get('error_code'):
        binding = inspect_reason_citation_binding(item, output, context)
        audit['reason_citation_binding'] = binding
        if binding.get('findings'):
            # A fact elsewhere in the source is not missing evidence. Retain
            # the result while exposing the selection/quotation mismatch for
            # one bounded correction or human review, never force UNKNOWN.
            if 'SELF_CHECK_UNCERTAIN' not in signals:
                signals.append('SELF_CHECK_UNCERTAIN')
    audit["confidence_threshold"] = confidence_threshold
    return output, audit


def run_validated_item(item, context, *, semantic_repair=False, **kwargs):
    """Opt-in bounded repair. Every candidate runs the SAME validators and guards.

    Never loop until MET; never repair transport/policy/conflict failures. Audit
    holds both attempts and an explicit total so rejected work is not hidden.
    """
    original, audit = _run_validated_item_once(item, context, **kwargs)
    def calls(report):
        return ((report.get('run') or {}).get('attempts', 0)
                + (report.get('self_check') or {}).get('attempts', 0))
    audit['total_model_calls'] = calls(audit)
    if not semantic_repair:
        return original, audit
    audit['semantic_repair'] = {'version': 'phase2_evidence_repair_v1', 'status': 'SKIPPED'}
    check = audit.get('self_check', {})
    explanation_correction = (
        kwargs.get('prompt_profile') == 'compact_v2'
        and original.get('result') == 'UNKNOWN'
        and check.get('verdict') == 'NOT_APPLICABLE_UNKNOWN'
        and bool(audit.get('issues'))
        and all(row.get('code') == 'P2E506' and row.get('severity') == 'warning'
                for row in audit['issues'])
        and not audit.get('review_signals')
        and not audit.get('reason_citation_binding', {}).get('findings')
        and not audit.get('run', {}).get('final_error_code')
        and audit.get('run', {}).get('request_error_status') is None
    )
    binding_correction = (
        kwargs.get('prompt_profile') == 'compact_v2'
        and original.get('result') == 'MET'
        and bool(audit.get('reason_citation_binding', {}).get('findings'))
        and check.get('verdict') == 'SUPPORTED'
        and not check.get('unsupported_conditions')
        and not (set(audit.get('review_signals', [])) - {'SELF_CHECK_UNCERTAIN'})
    )
    regular_repair = (original.get('result') == 'UNKNOWN'
                      and check.get('verdict') in {'NOT_APPLICABLE_UNKNOWN', 'UNSUPPORTED'})
    blocked = (not (regular_repair or binding_correction or explanation_correction) or original.get('error_code')
        or (audit.get('issues') and not explanation_correction) or audit.get('semantic_guards')
        or injection_suspected(context)
        or (kwargs.get('freshness_policy') is not None and audit.get('freshness', {}).get('status') != 'FRESH')
        or (kwargs.get('format_policy') is not None and audit.get('evidence_format', {}).get('status') != 'ALLOWED'))
    if blocked:
        audit['semantic_repair']['reason'] = 'NOT_ELIGIBLE_OR_EXISTING_GUARD'
        return original, audit
    candidate_kwargs = dict(kwargs)
    candidate_kwargs['semantic_feedback'] = {
        'prior_output': deepcopy(original),
        'review': {k: check.get(k) for k in ('verdict', 'reason', 'unsupported_conditions')},
    }
    repair_version = 'phase2_evidence_repair_v1'
    correction_mode = None
    if explanation_correction:
        repair_version = 'phase2_short_reason_repair_v1'
        correction_mode = 'REASON_EXPLANATION_CORRECTION'
        audit['semantic_repair'].update(version=repair_version, mode=correction_mode)
        candidate_kwargs['semantic_feedback'].update(
            mode=correction_mode, quality_diagnostic=deepcopy(audit['issues']))
    elif binding_correction:
        repair_version = 'phase2_citation_correction_v1'
        correction_mode = 'REASON_CITATION_CORRECTION'
        audit['semantic_repair'].update(version=repair_version, mode='REASON_CITATION_CORRECTION')
        candidate_kwargs['semantic_feedback'].update(
            mode='REASON_CITATION_CORRECTION',
            binding_diagnostic=deepcopy(audit['reason_citation_binding']))
    candidate, candidate_audit = _run_validated_item_once(item, context, **candidate_kwargs)
    edit_issues = []
    if explanation_correction:
        edit_issues = explanation_edit_issues(original, candidate)
        candidate_audit['explanation_edit_validation'] = {
            'preserved_fields': ['item_id', 'result', 'reason_codes', 'citations'],
            'issues': edit_issues,
        }
    elif binding_correction:
        edit_issues = citation_edit_issues(item, original, candidate, context,
                                         audit.get('reason_citation_binding'))
        candidate_audit['citation_edit_validation'] = {
            'diagnostic_only': True, 'issues': edit_issues,
        }
    acceptable_results = {'MET', 'NOT_MET', 'UNKNOWN'} if kwargs.get('prompt_profile') == 'compact_v2' else {'MET', 'NOT_MET'}
    accepted = (candidate.get('result') in acceptable_results
        and (not binding_correction or candidate.get('result') == original.get('result'))
        and not edit_issues
        and not candidate.get('error_code') and not candidate_audit.get('issues')
        and not candidate_audit.get('review_signals')
        and not candidate_audit.get('semantic_guards')
        and candidate_audit.get('self_check', {}).get('verdict') == 'SUPPORTED'
        and not candidate_audit.get('self_check', {}).get('unsupported_conditions'))
    total = calls(audit) + calls(candidate_audit)
    # Retain the final attempt's familiar top-level audit shape for consumers.
    if accepted:
        final_audit = candidate_audit
        initial_audit = deepcopy(audit)
        initial_audit.pop('semantic_repair', None)
        final_audit['semantic_repair'] = {
            'version': repair_version, 'status': 'ACCEPTED',
            'initial_output': original, 'initial_audit': initial_audit,
            'result_changed': original.get('result') != candidate.get('result'),
        }
        if correction_mode:
            final_audit['semantic_repair']['mode'] = correction_mode
        if explanation_correction:
            final_audit['semantic_repair']['explanation_changed'] = True
        final_audit['total_model_calls'] = total
        return candidate, final_audit
    audit['semantic_repair'].update(status='RETAINED_ORIGINAL',
        candidate_output=candidate, candidate_audit=candidate_audit)
    audit['total_model_calls'] = total
    return original, audit


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
    profile = item_kwargs.get("prompt_profile", "legacy")
    if profile not in {"legacy", "compact_v2"}:
        raise ValueError(f"Unknown prompt_profile: {profile}")
    audit["prompt_profile"] = profile
    audit["prompt_version"] = COMPACT_PROMPT_VERSION if profile == "compact_v2" else PROMPT_VERSION
    audit["self_check_version"] = COMPACT_SELF_CHECK_VERSION if profile == "compact_v2" else SELF_CHECK_VERSION
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
