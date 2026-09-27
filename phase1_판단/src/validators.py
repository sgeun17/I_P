"""LLM 출력 검증.

    LLM 출력
      ↓  Schema        (Pydantic — output_parser에서 이미 끝남)
      ↓  Control ID    후보 목록·KB 대조, 이름 일치, 중복
      ↓  Citation      청크 존재, 페이지 범위, 원문 일치
      ↓  Rules         PRIMARY 개수, 상한, NO_MATCH 정합성
    ValidationResult

검증은 **고치지 않는다.** 잘못된 것을 찾아서 오류코드로 기록할 뿐이다.
ID를 보정하거나 상한을 넘은 매핑을 잘라내면, 나중에 무엇이 틀렸는지 알 수 없다.
"""

from __future__ import annotations

import re

from enums import Decision, ErrorCode, MatchStatus, Relation
from kb import DEFAULT_INDEX, ControlIndex
from models import Chunk, Citation, LLMMappingOutput, MappingInput, ValidationIssue, ValidationResult

MAX_MAPPED_CONTROLS = 3


# --------------------------------------------------------------------------
# 문자열 정규화
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """인용 대조용 정규화.

    연속된 공백·탭·줄바꿈을 공백 하나로 합치는 것이 전부다.
    구두점을 지우거나 조사를 떼거나 유사도를 계산하지 않는다.
    느슨하게 만들수록 **LLM이 지어낸 문장이 통과한다.**

    표 청크는 줄이 `\\n`으로 이어져 있으므로, 이 정규화로 여러 줄 인용도 대조된다.
    """
    return " ".join((text or "").split())


# --------------------------------------------------------------------------
# Control ID
# --------------------------------------------------------------------------


def validate_control_ids(
    output: LLMMappingOutput,
    mapping_input: MappingInput,
    index: ControlIndex = DEFAULT_INDEX,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    candidate_ids = {c.control_id for c in mapping_input.candidate_controls}

    def check(control_id: str, control_name: str | None, where: str) -> None:
        if not index.exists(control_id):
            issues.append(
                ValidationIssue(
                    code=ErrorCode.CONTROL_ID_NOT_IN_KB,
                    message=f"KB에 없는 통제항목 ID ({where})",
                    control_id=control_id,
                )
            )
            return  # KB에 없으면 이름 대조는 의미가 없다
        if control_id not in candidate_ids:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.CONTROL_ID_NOT_IN_CANDIDATES,
                    message=f"후보 목록에 없는 통제항목을 생성했다 ({where})",
                    control_id=control_id,
                )
            )
        if control_name is not None and not index.name_matches(control_id, control_name):
            issues.append(
                ValidationIssue(
                    code=ErrorCode.CONTROL_NAME_MISMATCH,
                    message=f"KB 명칭은 '{index.name_of(control_id)}'인데 '{control_name}'로 출력했다",
                    control_id=control_id,
                )
            )

    for decision in output.candidate_decisions:
        check(decision.control_id, None, "candidate_decisions")
    for mapped in output.mapped_controls:
        check(mapped.control_id, mapped.control_name, "mapped_controls")

    seen: set[str] = set()
    for mapped in output.mapped_controls:
        if mapped.control_id in seen:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.DUPLICATE_CONTROL_ID,
                    message="같은 통제항목이 두 번 매핑됐다",
                    control_id=mapped.control_id,
                )
            )
        seen.add(mapped.control_id)

    return issues


# --------------------------------------------------------------------------
# Citation
# --------------------------------------------------------------------------


def validate_citation(citation: Citation, chunk_map: dict[str, Chunk]) -> list[ValidationIssue]:
    """인용 하나를 검사한다."""
    issues: list[ValidationIssue] = []
    chunk = chunk_map.get(citation.chunk_id)

    if chunk is None:
        issues.append(
            ValidationIssue(
                code=ErrorCode.CITATION_CHUNK_NOT_FOUND,
                message="입력에 없는 chunk_id를 인용했다",
                chunk_id=citation.chunk_id,
            )
        )
        return issues

    if not normalize(citation.quote):
        issues.append(
            ValidationIssue(
                code=ErrorCode.CITATION_EMPTY_QUOTE,
                message="인용문이 비어 있다",
                chunk_id=citation.chunk_id,
            )
        )
        return issues

    if not chunk.covers_page(citation.page):
        issues.append(
            ValidationIssue(
                code=ErrorCode.CITATION_PAGE_MISMATCH,
                message=f"청크 페이지 범위({chunk.page_start}~{chunk.page_end}) 밖의 페이지 {citation.page}",
                chunk_id=citation.chunk_id,
            )
        )

    if normalize(citation.quote) not in normalize(chunk.text):
        issues.append(
            ValidationIssue(
                code=ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE,
                message="인용문이 청크 원문에 없다",
                chunk_id=citation.chunk_id,
            )
        )

    return issues


def validate_citations(
    output: LLMMappingOutput, mapping_input: MappingInput
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    chunk_map = mapping_input.chunk_map()

    # 다른 증적·버전의 청크를 인용했는지 본다
    prefix = mapping_input.chunk_id_prefix

    for mapped in output.mapped_controls:
        if not mapped.citations:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.CITATION_MISSING,
                    message="매핑된 통제항목에 인용이 없다",
                    control_id=mapped.control_id,
                )
            )
            continue
        for citation in mapped.citations:
            if not citation.chunk_id.startswith(prefix):
                issues.append(
                    ValidationIssue(
                        code=ErrorCode.CITATION_FOREIGN_EVIDENCE,
                        message=f"다른 증적·버전의 청크를 인용했다 (기대 접두사 {prefix})",
                        control_id=mapped.control_id,
                        chunk_id=citation.chunk_id,
                    )
                )
                continue
            for issue in validate_citation(citation, chunk_map):
                issues.append(issue.model_copy(update={"control_id": mapped.control_id}))

    # RELATED로 판단했는데 근거가 없는 경우도 잡는다
    for decision in output.candidate_decisions:
        if decision.decision == Decision.RELATED and not decision.citations:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.CITATION_MISSING,
                    message="RELATED로 판단했는데 인용이 없다",
                    control_id=decision.control_id,
                )
            )

    return issues


# --------------------------------------------------------------------------
# 판단 규칙
# --------------------------------------------------------------------------


def validate_rules(output: LLMMappingOutput, mapping_input: MappingInput) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    mapped = output.mapped_controls

    if output.match_status == MatchStatus.NO_MATCH and mapped:
        issues.append(
            ValidationIssue(
                code=ErrorCode.NO_MATCH_WITH_CONTROLS,
                message=f"NO_MATCH인데 매핑이 {len(mapped)}개 있다",
            )
        )
    if output.match_status == MatchStatus.MATCHED and not mapped:
        issues.append(
            ValidationIssue(code=ErrorCode.MATCHED_WITHOUT_CONTROLS, message="MATCHED인데 매핑이 없다")
        )

    primary = [m for m in mapped if m.relation == Relation.PRIMARY]
    if output.match_status == MatchStatus.MATCHED and len(primary) != 1:
        issues.append(
            ValidationIssue(
                code=ErrorCode.PRIMARY_COUNT_INVALID,
                message=f"PRIMARY가 {len(primary)}개다. MATCHED면 정확히 1개여야 한다",
            )
        )

    if len(mapped) > MAX_MAPPED_CONTROLS:
        # 자르지 않는다. 잘린 것이 정답일 수 있다.
        issues.append(
            ValidationIssue(
                code=ErrorCode.TOO_MANY_MAPPED_CONTROLS,
                message=f"매핑이 {len(mapped)}개로 상한 {MAX_MAPPED_CONTROLS}개를 넘는다",
            )
        )

    # 후보 전부에 대해 판단을 남겼는지
    decided = {d.control_id for d in output.candidate_decisions}
    missing = [c.control_id for c in mapping_input.candidate_controls if c.control_id not in decided]
    if missing:
        issues.append(
            ValidationIssue(
                code=ErrorCode.REQUIRED_FIELD_MISSING,
                message=f"판단이 빠진 후보가 있다: {', '.join(missing)}",
                field="candidate_decisions",
            )
        )

    # RELATED로 판단한 것과 최종 매핑이 어긋나는지
    related = {d.control_id for d in output.candidate_decisions if d.decision == Decision.RELATED}
    for m in mapped:
        if m.control_id not in related:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.SCHEMA_INVALID,
                    message="candidate_decisions에서 RELATED가 아닌데 매핑했다",
                    control_id=m.control_id,
                )
            )

    return issues


# --------------------------------------------------------------------------
# 적정성 판정 감지 (E505)
# --------------------------------------------------------------------------

# Phase 1은 **증적과 통제항목을 연결하는 것까지만** 한다.
# "이 증적이 통제를 충족하는가"는 Phase 2의 일이다.
# 모델이 Phase 1에서 합격/불합격을 내버리면, 아직 검증되지 않은 판정이
# 결과에 섞여 들어가 그대로 보고서로 넘어간다.
#
# **무엇을 보는가** — `reason`만 본다. `quote`는 보지 않는다.
# 인용문은 증적 원문 그대로이고, 증적에는 "미흡", "위반" 같은 말이 얼마든지 나온다.
# 원문에 있는 단어로 모델을 탓하면 안 된다.
# (1) 맥락과 무관하게 감사 판정 어휘인 것
ADEQUACY_PATTERNS = [
    r"부?적정(하|합|함|성)",
    r"부?적합(하|합|함|판정)",
    r"위반(이|을|으로|하|사항|된)",
    r"결함",
    r"미준수|준수(하지|한다|했다|하고 있)",
    r"미이행",
    r"(보완|조치|개선)\s*(가|이)?\s*필요",
    r"인증\s*기준을?\s*(만족|통과|달성)",
]

# (2) 대상이 통제항목·인증기준일 때만 판정인 것
#
# "근거가 불충분하다"는 UNCERTAIN을 설명하는 **정상 문장**이다. 여기 걸리면 안 된다.
# "통제항목을 충족하지 않는다"라야 판정이다. 그래서 대상 단어와 같이 있을 때만 잡는다.
_SUBJECT = r"(통제(항목)?|인증\s*기준|요구\s*사항)"
_VERDICT = r"((미|불)?충족|미흡|불충분)"

_ADEQUACY_RE = re.compile("|".join(ADEQUACY_PATTERNS))
_SCOPED_RE = re.compile(rf"{_SUBJECT}[^.。]{{0,20}}{_VERDICT}")


def detect_adequacy_judgment(output: LLMMappingOutput) -> list[ValidationIssue]:
    """Phase 1 결과에 적정성 판정이 섞였는지 본다.

    오탐이 나도 결과를 버리지 않는다. `E505` → `R102`로 사람에게 한 번 보여줄 뿐이다.
    표현 목록은 임시값이다. **실제 LLM 응답을 보고 조정한다.**
    """
    issues: list[ValidationIssue] = []

    def check(text: str, control_id: str, where: str) -> None:
        found = _ADEQUACY_RE.search(text or "") or _SCOPED_RE.search(text or "")
        if found:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.ADEQUACY_JUDGMENT_DETECTED,
                    message=f"Phase 1에서 적정성 판정으로 읽히는 표현을 썼다: '{found.group()}' ({where})",
                    control_id=control_id,
                    field="reason",
                )
            )

    for decision in output.candidate_decisions:
        check(decision.reason, decision.control_id, "candidate_decisions.reason")
    for mapped in output.mapped_controls:
        check(mapped.reason, mapped.control_id, "mapped_controls.reason")

    return issues


# --------------------------------------------------------------------------
# reason 작성 규칙 (E506 · E507)
# --------------------------------------------------------------------------

# 임시값. 골든셋과 실제 응답을 보고 조정한다.
#
# 짧은 쪽만 잰다. 긴 근거는 읽기 불편할 뿐 틀린 것이 아니다.
# "관련 있음"(5자) 같은 응답을 거르는 것이 목적이다.
REASON_MIN_CHARS = 10


def validate_reasons(
    output: LLMMappingOutput, mapping_input: MappingInput
) -> list[ValidationIssue]:
    """`reason`이 판단 근거 구실을 하는지 본다.

    **검토로 보내지 않는다.** 근거가 부실한 것은 결과가 틀린 것과 다르다.
    여기서 잡힌 것은 프롬프트를 고칠 자료로 쓴다.
    """
    issues: list[ValidationIssue] = []
    chunk_texts = [normalize(c.text) for c in mapping_input.chunks]

    def check(reason: str, control_id: str, control_name: str | None, where: str) -> None:
        text = normalize(reason)

        if len(text) < REASON_MIN_CHARS:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.REASON_TOO_SHORT,
                    message=f"판단 근거가 {len(text)}자다. 최소 {REASON_MIN_CHARS}자 ({where})",
                    control_id=control_id,
                    field="reason",
                )
            )
            return

        # 원문을 그대로 옮겨 적은 것은 근거가 아니다. 인용은 citations가 따로 담는다.
        if any(text and text in chunk_text for chunk_text in chunk_texts):
            issues.append(
                ValidationIssue(
                    code=ErrorCode.REASON_NOT_SPECIFIC,
                    message=f"근거가 증적 원문을 그대로 옮긴 것이다 ({where})",
                    control_id=control_id,
                    field="reason",
                )
            )
            return

        # 통제항목 이름만 되풀이한 것도 근거가 아니다.
        if control_name and text == normalize(control_name):
            issues.append(
                ValidationIssue(
                    code=ErrorCode.REASON_NOT_SPECIFIC,
                    message=f"근거가 통제항목 명칭과 같다 ({where})",
                    control_id=control_id,
                    field="reason",
                )
            )

    for decision in output.candidate_decisions:
        check(decision.reason, decision.control_id, None, "candidate_decisions.reason")
    for mapped in output.mapped_controls:
        check(mapped.reason, mapped.control_id, mapped.control_name, "mapped_controls.reason")

    return issues


# --------------------------------------------------------------------------
# 중복 인용 정리
# --------------------------------------------------------------------------


def dedupe_citations(citations: list[Citation]) -> list[Citation]:
    """같은 근거를 하나로 합친다.

    글 청크는 인접한 것끼리 100자가 겹치므로, `chunk_id`가 달라도 같은 문장일 수 있다.
    정규화한 `quote`가 같으면 같은 근거로 보고 처음 것만 남긴다.
    별개로 세면 근거가 2개인 것처럼 보인다.
    """
    seen: set[str] = set()
    out: list[Citation] = []
    for citation in citations:
        key = normalize(citation.quote)
        if key in seen:
            continue
        seen.add(key)
        out.append(citation)
    return out


# --------------------------------------------------------------------------
# 전체 검증
# --------------------------------------------------------------------------


def validate(
    output: LLMMappingOutput | None,
    mapping_input: MappingInput,
    parse_issues: list[ValidationIssue] | None = None,
    index: ControlIndex = DEFAULT_INDEX,
) -> ValidationResult:
    """파싱 결과부터 판단 규칙까지 한 번에 검사한다.

    output이 None이면 파싱 단계에서 이미 실패한 것이다.
    """
    issues = list(parse_issues or [])

    if output is None:
        return ValidationResult(
            passed=False,
            schema_valid=False,
            control_ids_valid=False,
            citations_valid=False,
            rules_valid=False,
            issues=issues or [
                ValidationIssue(code=ErrorCode.JSON_PARSE_FAILED, message="파싱 결과가 없다")
            ],
        )

    id_issues = validate_control_ids(output, mapping_input, index)
    citation_issues = validate_citations(output, mapping_input)
    rule_issues = validate_rules(output, mapping_input) + detect_adequacy_judgment(output)
    issues += id_issues + citation_issues + rule_issues

    # 근거 품질은 경고로만 남긴다. passed와 검토 전환에 영향을 주지 않는다.
    warnings = validate_reasons(output, mapping_input)

    schema_valid = not any(i.code.value.startswith("E2") for i in issues)

    return ValidationResult(
        passed=not issues,
        schema_valid=schema_valid,
        control_ids_valid=not id_issues,
        citations_valid=not citation_issues,
        rules_valid=not rule_issues,
        issues=issues,
        warnings=warnings,
    )
