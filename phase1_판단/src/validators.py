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

    # page=None 자체는 인용 무효로 만들지 않는다.
    # 페이지형 청크에서 page가 빠진 경우는 별도 warning(E407)으로 기록하고,
    # 실제 page 값이 들어왔는데 범위를 벗어난 경우만 E404로 막는다.
    if citation.page is not None and not chunk.covers_page(citation.page):
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


def validate_citation_warnings(
    output: LLMMappingOutput, mapping_input: MappingInput
) -> list[ValidationIssue]:
    """Citation 메타데이터의 비차단 경고를 모은다.

    `chunk_id + quote`가 유효하면 근거 자체는 성립한다.
    따라서 페이지형 청크에서 `page=null`인 경우 인용 전체를 무효화하지 않고
    E407 경고로 남긴다. 반대로 page 값이 존재하면서 실제 청크 범위를 벗어나면
    `validate_citation()`의 E404가 blocking issue로 처리한다.
    """
    warnings: list[ValidationIssue] = []
    chunk_map = mapping_input.chunk_map()
    seen: set[tuple[str, str | None, str]] = set()

    for control in [*output.mapped_controls, *output.candidate_decisions]:
        for citation in control.citations:
            chunk = chunk_map.get(citation.chunk_id)
            if chunk is None or chunk.page_start is None:
                continue
            if citation.page is not None:
                continue

            key = (control.control_id, citation.chunk_id, normalize(citation.quote))
            if key in seen:
                continue
            seen.add(key)
            warnings.append(
                ValidationIssue(
                    code=ErrorCode.CITATION_PAGE_MISSING,
                    message=(
                        "페이지형 청크를 인용했지만 citation.page가 null이다. "
                        "인용문 자체가 원문과 일치하면 결과를 막지 않고 위치 메타데이터 누락으로 기록한다"
                    ),
                    control_id=control.control_id,
                    chunk_id=citation.chunk_id,
                    field="page",
                )
            )

    return warnings


def validate_citations(
    output: LLMMappingOutput, mapping_input: MappingInput
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    chunk_map = mapping_input.chunk_map()

    # 다른 증적·버전의 청크를 인용했는지 본다
    prefix = mapping_input.chunk_id_prefix
    seen: set[tuple[str, str, int | None, str]] = set()

    def check_citations(control_id: str, citations: list[Citation]) -> None:
        for citation in citations:
            # 같은 인용이 두 출력 목록에 반복돼도 한 번만 검사한다.
            # page가 다르면 반드시 별도로 검사해 잘못된 위치가 숨지 않게 한다.
            key = (control_id, citation.chunk_id, citation.page, normalize(citation.quote))
            if key in seen:
                continue
            seen.add(key)
            if not citation.chunk_id.startswith(prefix):
                issues.append(
                    ValidationIssue(
                        code=ErrorCode.CITATION_FOREIGN_EVIDENCE,
                        message=f"다른 증적·버전의 청크를 인용했다 (기대 접두사 {prefix})",
                        control_id=control_id,
                        chunk_id=citation.chunk_id,
                    )
                )
                continue
            for issue in validate_citation(citation, chunk_map):
                issues.append(issue.model_copy(update={"control_id": control_id}))

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
        check_citations(mapped.control_id, mapped.citations)

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
        # RELATED 외에도 출력에 실제 인용이 있으면 그 출처를 검증한다.
        # UNCERTAIN/NOT_RELATED에 인용을 새로 필수화하지 않는다.
        check_citations(decision.control_id, decision.citations)

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
    # "적정하다/적정함"은 판정이다. 그런데 **"적정성"은 아니다.**
    #
    # "접근권한 적정성을 검토하고 결과를 기록한다"는 증적 원문에 흔히 나오는
    # 업무 설명이고, '적정성 검토'는 ISMS-P 인증기준 본문의 표준 용어다.
    # 명사형 뒤에 점검 활동이 오면 판정이 아니라 업무를 말하는 것이므로 뺀다.
    # (검색팀 2026-10-01 전달 J-LLM-01에서 실제 오탐으로 보고됨)
    r"부?적정(하|합|함)",
    r"부?적정성(?!\s*[을를이가에의]?\s*(검토|점검|평가|확인|심사|여부|기준|절차))",
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

# 뒤에 "나타나지 않는다·없다"가 오면 판정이 아니라 **관련성 설명**이다.
#
#   "통제항목의 요구사항을 충족하는 활동이 명확히 나타나지 않는다"
#
# NOT_RELATED를 설명하는 가장 자연스러운 문장인데, 위 패턴이 '요구사항…충족'까지만
# 보고 끊어서 통째로 걸렸다. qwen3:4b 실측에서 G-TRAP-02 한 사례에만 3건 발생했다.
# 부정이 '충족'이 아니라 '나타나다'에 붙는 경우만 뺀다.
# "요구사항을 충족하지 않는다"는 여전히 판정이므로 그대로 잡힌다.
_NOT_PRESENT = r"((나타나|보이|확인되|드러나|포함되|언급되)지\s*않|없(다|으며|음|고|어|는)|찾을\s*수\s*없)"

_ADEQUACY_RE = re.compile("|".join(ADEQUACY_PATTERNS))
_SCOPED_RE = re.compile(
    rf"{_SUBJECT}[^.。]{{0,20}}{_VERDICT}(?![^.。]{{0,30}}{_NOT_PRESENT})"
)


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

# reason 안에서 원문을 따옴표로 다시 인용하는 경우를 잡기 위한 패턴.
# 이 검사는 결과를 막지 않고 E507 warning으로만 기록한다.
#
# 판단팀 결정(2026-10-01, 검색팀 전달 J-LLM-05): 경고로 두고 **빈도를 센다.**
# 재시도나 검토 전환의 사유로 쓰지 않는다. 원문 복붙은 판단이 틀린 것이 아니라
# 근거가 읽기 나쁜 것이고, 프롬프트로 고칠 문제이기 때문이다.
# 빈도는 metrics.Metrics.reason_warning_rate 로 측정한다. 줄어드는지 보고 판단한다.
_REASON_QUOTE_PATTERNS = (
    re.compile(r'"([^"]+)"'),
    re.compile(r"'([^']+)'"),
    re.compile(r"“([^”]+)”"),
    re.compile(r"‘([^’]+)’"),
)


def _quoted_reason_segments(reason: str) -> list[str]:
    segments: list[str] = []
    for pattern in _REASON_QUOTE_PATTERNS:
        segments.extend(pattern.findall(reason or ""))
    return [normalize(segment) for segment in segments if normalize(segment)]


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

        # reason 전체가 설명 문장이어도, 그 안에 원문을 따옴표로 다시 붙이는 경우가 있다.
        # 인용은 citations.quote에만 두므로, 실제 청크 원문과 일치하는 따옴표 구간이 있으면 E507 경고.
        for segment in _quoted_reason_segments(reason):
            if len(segment) >= REASON_MIN_CHARS and any(
                segment in chunk_text for chunk_text in chunk_texts
            ):
                issues.append(
                    ValidationIssue(
                        code=ErrorCode.REASON_NOT_SPECIFIC,
                        message=f"근거 안에 증적 원문을 따옴표로 다시 인용했다 ({where})",
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

    # 근거 품질/비차단 메타데이터 문제는 경고로만 남긴다.
    # passed와 검토 전환에 영향을 주지 않는다.
    warnings = validate_citation_warnings(output, mapping_input) + validate_reasons(
        output, mapping_input
    )

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
