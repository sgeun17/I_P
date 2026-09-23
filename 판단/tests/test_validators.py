"""Control ID · Citation · 판단 규칙 검증."""

import json

import pytest
from enums import ErrorCode
from kb import DEFAULT_INDEX
from models import Citation
from output_parser import parse_llm_output
from validators import (
    dedupe_citations,
    detect_adequacy_judgment,
    normalize,
    validate,
    validate_citation,
    validate_citations,
    validate_control_ids,
    validate_reasons,
    validate_rules,
)

from conftest import GOOD_RESPONSE, make_chunk


def codes(issues):
    return [i.code for i in issues]


def parse(data: dict):
    output, issues = parse_llm_output(json.dumps(data, ensure_ascii=False))
    assert not issues, f"이 테스트의 응답은 형식은 정상이어야 한다: {issues}"
    return output


@pytest.fixture
def good(mapping_input):
    return parse(json.loads(GOOD_RESPONSE))


# --------------------------------------------------------------------------
# KB
# --------------------------------------------------------------------------


def test_kb가_101개_로드된다():
    assert len(DEFAULT_INDEX) == 101
    assert DEFAULT_INDEX.exists("2.5.1")
    assert DEFAULT_INDEX.name_of("2.5.1") == "사용자 계정 관리"


def test_표기가_다르면_없는_id다():
    assert not DEFAULT_INDEX.exists("2.5.1.")
    assert not DEFAULT_INDEX.exists(" 2.5.1")
    assert not DEFAULT_INDEX.exists("2.05.1")


# --------------------------------------------------------------------------
# Control ID
# --------------------------------------------------------------------------


def test_정상이면_문제없음(good, mapping_input):
    assert validate_control_ids(good, mapping_input) == []


def test_kb에_없는_id(good, mapping_input):
    good.mapped_controls[0].control_id = "9.9.9"
    assert ErrorCode.CONTROL_ID_NOT_IN_KB in codes(validate_control_ids(good, mapping_input))


def test_끝에_점을_붙이면_걸린다(good, mapping_input):
    good.mapped_controls[0].control_id = "2.5.1."
    assert ErrorCode.CONTROL_ID_NOT_IN_KB in codes(validate_control_ids(good, mapping_input))


def test_후보_밖의_id(good, mapping_input):
    good.mapped_controls[0].control_id = "2.9.3"          # KB에는 있지만 후보에 없음
    good.mapped_controls[0].control_name = "백업 및 복구관리"
    assert ErrorCode.CONTROL_ID_NOT_IN_CANDIDATES in codes(
        validate_control_ids(good, mapping_input)
    )


def test_이름_불일치(good, mapping_input):
    good.mapped_controls[0].control_name = "계정관리"
    assert ErrorCode.CONTROL_NAME_MISMATCH in codes(validate_control_ids(good, mapping_input))


def test_중복_매핑(good, mapping_input):
    good.mapped_controls.append(good.mapped_controls[0].model_copy(deep=True))
    good.mapped_controls[1].relation = "RELATED"
    assert ErrorCode.DUPLICATE_CONTROL_ID in codes(validate_control_ids(good, mapping_input))


# --------------------------------------------------------------------------
# Citation — Validator의 핵심
# --------------------------------------------------------------------------


def test_normalize는_공백만_합친다():
    assert normalize("사용자  계정은\n관리자   승인") == "사용자 계정은 관리자 승인"
    assert normalize("  앞뒤 공백  ") == "앞뒤 공백"
    # 구두점은 지우지 않는다. 지우면 지어낸 문장이 통과한다.
    assert normalize("승인 후 생성한다.") != normalize("승인 후 생성한다")


def test_원문_그대로면_통과():
    chunk = make_chunk(0)
    citation = Citation(chunk_id=chunk.chunk_id, page=1, quote="관리자 승인 후 생성한다")
    assert validate_citation(citation, {chunk.chunk_id: chunk}) == []


def test_공백만_다르면_통과():
    chunk = make_chunk(0)
    citation = Citation(chunk_id=chunk.chunk_id, page=1, quote="관리자   승인 후\n생성한다")
    assert validate_citation(citation, {chunk.chunk_id: chunk}) == []


def test_지어낸_문장은_실패():
    chunk = make_chunk(0)
    citation = Citation(chunk_id=chunk.chunk_id, page=1, quote="계정은 승인자가 만든다")
    assert ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE in codes(
        validate_citation(citation, {chunk.chunk_id: chunk})
    )


def test_존재하지_않는_청크():
    chunk = make_chunk(0)
    citation = Citation(chunk_id="E0001_v1_c9999", page=1, quote="아무거나")
    assert ErrorCode.CITATION_CHUNK_NOT_FOUND in codes(
        validate_citation(citation, {chunk.chunk_id: chunk})
    )


def test_페이지_범위_안이면_통과():
    chunk = make_chunk(0, page_start=2, page_end=3)
    for page in (2, 3):
        citation = Citation(chunk_id=chunk.chunk_id, page=page, quote="관리자 승인 후 생성한다")
        assert validate_citation(citation, {chunk.chunk_id: chunk}) == []


def test_페이지_범위_밖이면_실패():
    chunk = make_chunk(0, page_start=2, page_end=3)
    citation = Citation(chunk_id=chunk.chunk_id, page=4, quote="관리자 승인 후 생성한다")
    assert ErrorCode.CITATION_PAGE_MISMATCH in codes(
        validate_citation(citation, {chunk.chunk_id: chunk})
    )


def test_페이지_없는_파일은_page가_null이어야():
    chunk = make_chunk(0, file_type="docx", page_start=None, page_end=None)
    ok = Citation(chunk_id=chunk.chunk_id, page=None, quote="관리자 승인 후 생성한다")
    assert validate_citation(ok, {chunk.chunk_id: chunk}) == []
    bad = Citation(chunk_id=chunk.chunk_id, page=1, quote="관리자 승인 후 생성한다")
    assert ErrorCode.CITATION_PAGE_MISMATCH in codes(validate_citation(bad, {chunk.chunk_id: chunk}))


def test_표_청크_한_줄_인용():
    chunk = make_chunk(
        0,
        "점검번호: SV-001 | 점검대상: WEB-01 | 결과: 양호\n점검번호: SV-002 | 점검대상: WAS-01 | 결과: 취약",
        chunk_type="table",
    )
    citation = Citation(chunk_id=chunk.chunk_id, page=1, quote="점검번호: SV-002 | 점검대상: WAS-01 | 결과: 취약")
    assert validate_citation(citation, {chunk.chunk_id: chunk}) == []


def test_표_청크_여러_줄_인용():
    chunk = make_chunk(0, "성명: 홍길동\n소속: 정보보호부", chunk_type="table")
    citation = Citation(chunk_id=chunk.chunk_id, page=1, quote="성명: 홍길동 소속: 정보보호부")
    assert validate_citation(citation, {chunk.chunk_id: chunk}) == [], "개행은 정규화로 공백이 된다"


def test_표를_재구성한_인용은_실패():
    chunk = make_chunk(0, "점검번호: SV-001 | 점검대상: WEB-01 | 결과: 양호", chunk_type="table")
    citation = Citation(chunk_id=chunk.chunk_id, page=1, quote="SV-001은 양호")
    assert ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE in codes(
        validate_citation(citation, {chunk.chunk_id: chunk})
    )


def test_구분자_공백을_지운_인용은_실패():
    chunk = make_chunk(0, "점검번호: SV-001 | 결과: 양호", chunk_type="table")
    citation = Citation(chunk_id=chunk.chunk_id, page=1, quote="점검번호:SV-001")
    assert ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE in codes(
        validate_citation(citation, {chunk.chunk_id: chunk})
    )


def test_다른_증적의_청크_인용(good, mapping_input):
    good.mapped_controls[0].citations[0].chunk_id = "E0009_v1_c0000"
    assert ErrorCode.CITATION_FOREIGN_EVIDENCE in codes(validate_citations(good, mapping_input))


def test_매핑됐는데_인용이_없음(good, mapping_input):
    good.mapped_controls[0].citations = []
    assert ErrorCode.CITATION_MISSING in codes(validate_citations(good, mapping_input))


def test_related인데_인용이_없음(good, mapping_input):
    good.candidate_decisions[0].citations = []
    assert ErrorCode.CITATION_MISSING in codes(validate_citations(good, mapping_input))


# --------------------------------------------------------------------------
# 중복 인용 정리 — 청크 겹침 때문에 필요하다
# --------------------------------------------------------------------------


def test_다른_청크_같은_문장은_하나로():
    citations = [
        Citation(chunk_id="E0001_v1_c0000", page=1, quote="퇴직자 계정은 퇴직일 당일 삭제한다"),
        Citation(chunk_id="E0001_v1_c0001", page=1, quote="퇴직자  계정은 퇴직일 당일 삭제한다"),
    ]
    assert len(dedupe_citations(citations)) == 1, "겹침 구간의 같은 문장은 근거 하나다"


def test_다른_문장은_둘_다_남는다():
    citations = [
        Citation(chunk_id="E0001_v1_c0000", page=1, quote="퇴직자 계정은 퇴직일 당일 삭제한다"),
        Citation(chunk_id="E0001_v1_c0001", page=2, quote="관리자 권한 부여는 보안팀장의 추가 승인을 받는다"),
    ]
    assert len(dedupe_citations(citations)) == 2


# --------------------------------------------------------------------------
# 판단 규칙
# --------------------------------------------------------------------------


def test_primary가_두_개(good, mapping_input):
    second = good.mapped_controls[0].model_copy(deep=True)
    second.control_id = "2.5.5"
    second.control_name = "특수 계정 및 권한 관리"
    good.mapped_controls.append(second)
    assert ErrorCode.PRIMARY_COUNT_INVALID in codes(validate_rules(good, mapping_input))


def test_no_match인데_매핑이_있음(good, mapping_input):
    good.match_status = "NO_MATCH"
    assert ErrorCode.NO_MATCH_WITH_CONTROLS in codes(validate_rules(good, mapping_input))


def test_matched인데_매핑이_없음(good, mapping_input):
    good.mapped_controls = []
    assert ErrorCode.MATCHED_WITHOUT_CONTROLS in codes(validate_rules(good, mapping_input))


def test_상한_초과는_자르지_않고_기록한다(good, mapping_input):
    base = good.mapped_controls[0]
    for cid, name in [("2.5.6", "접근권한 검토"), ("2.5.5", "특수 계정 및 권한 관리"),
                      ("2.5.2", "사용자 식별")]:
        extra = base.model_copy(deep=True)
        extra.control_id, extra.control_name, extra.relation = cid, name, "RELATED"
        good.mapped_controls.append(extra)
    issues = validate_rules(good, mapping_input)
    assert ErrorCode.TOO_MANY_MAPPED_CONTROLS in codes(issues)
    assert len(good.mapped_controls) == 4, "Validator는 결과를 고치지 않는다"


def test_후보_판단이_빠짐(good, mapping_input):
    good.candidate_decisions.pop()
    issues = validate_rules(good, mapping_input)
    assert ErrorCode.REQUIRED_FIELD_MISSING in codes(issues)


def test_not_related인데_매핑함(good, mapping_input):
    good.candidate_decisions[0].decision = "NOT_RELATED"
    assert ErrorCode.SCHEMA_INVALID in codes(validate_rules(good, mapping_input))


# --------------------------------------------------------------------------
# 전체
# --------------------------------------------------------------------------


def test_정상_결과는_전부_통과(good, mapping_input):
    result = validate(good, mapping_input)
    assert result.passed
    assert result.schema_valid and result.control_ids_valid
    assert result.citations_valid and result.rules_valid


def test_어느_단계에서_걸렸는지_구분된다(good, mapping_input):
    good.mapped_controls[0].citations[0].quote = "지어낸 문장"
    result = validate(good, mapping_input)
    assert not result.passed
    assert not result.citations_valid
    assert result.control_ids_valid, "인용이 틀려도 ID는 정상이다"


def test_출력이_없으면_전부_실패(mapping_input):
    result = validate(None, mapping_input)
    assert not result.passed
    assert result.issues


# --------------------------------------------------------------------------
# 적정성 판정 감지 (E505)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "계정 관리 통제항목을 적정하게 이행하고 있다.",
        "승인 절차가 없어 부적합하다고 판단된다.",
        "접근권한 관리 기준을 위반하는 내용이다.",
        "통제항목 요구사항을 충족하지 않는다.",
        "비밀번호 정책을 준수하지 않고 있다.",
        "권한 검토 주기에 보완이 필요하다.",
    ],
)
def test_적정성_판정을_잡는다(good, mapping_input, reason):
    good.mapped_controls[0].reason = reason
    assert ErrorCode.ADEQUACY_JUDGMENT_DETECTED in codes(detect_adequacy_judgment(good))


@pytest.mark.parametrize(
    "reason",
    [
        "계정 생성 승인 절차가 문서의 주제다.",
        "관련 있어 보이나 원문 근거가 불충분하다.",       # UNCERTAIN 설명
        "약어만 있어 판단 근거가 미흡하다.",              # 근거 이야기지 판정이 아니다
        "접근통제 절차를 다루는 문서로 보인다.",
    ],
)
def test_근거_이야기는_판정이_아니다(good, mapping_input, reason):
    """'불충분·미흡'은 UNCERTAIN을 설명하는 정상 표현이다. 여기 걸리면 안 된다."""
    good.mapped_controls[0].reason = reason
    assert not detect_adequacy_judgment(good)


def test_인용문에_들어_있는_판정_어휘는_안_잡는다(good, mapping_input):
    """증적 원문에는 '미흡', '위반'이 얼마든지 나온다. quote는 검사 대상이 아니다."""
    good.mapped_controls[0].citations[0].quote = "점검 결과 통제가 미흡하여 위반으로 판정한다."
    assert not detect_adequacy_judgment(good)


def test_적정성_판정은_검토로_간다(mapping_input, versions):
    from enums import ReviewReason
    from service import build_result

    data = json.loads(GOOD_RESPONSE)
    data["mapped_controls"][0]["reason"] = "해당 통제항목을 적정하게 이행하고 있다."
    result = build_result(json.dumps(data, ensure_ascii=False), mapping_input, versions)

    assert not result.validation.passed
    assert ReviewReason.SCHEMA_INVALID in result.human_review.reasons


# --------------------------------------------------------------------------
# reason 작성 규칙 (E506 · E507) — 경고만 한다
# --------------------------------------------------------------------------


def test_근거가_너무_짧으면_경고(good, mapping_input):
    good.mapped_controls[0].reason = "관련 있음"
    assert ErrorCode.REASON_TOO_SHORT in codes(validate_reasons(good, mapping_input))


def test_근거가_원문_복붙이면_경고(good, mapping_input):
    good.mapped_controls[0].reason = "사용자 계정은 관리자 승인 후 생성한다."
    assert ErrorCode.REASON_NOT_SPECIFIC in codes(validate_reasons(good, mapping_input))


def test_근거가_통제항목_명칭이면_경고(good, mapping_input):
    good.mapped_controls[0].control_name = "특수 계정 및 권한 관리"
    good.mapped_controls[0].reason = "특수 계정 및 권한 관리"
    assert ErrorCode.REASON_NOT_SPECIFIC in codes(validate_reasons(good, mapping_input))


def test_정상_근거는_경고가_없다(good, mapping_input):
    assert not validate_reasons(good, mapping_input)


def test_근거_경고는_검토로_보내지_않는다(mapping_input, versions):
    """근거가 부실한 것은 결과가 틀린 것과 다르다. 사람을 부르지 않는다."""
    from service import build_result

    data = json.loads(GOOD_RESPONSE)
    for m in data["mapped_controls"]:
        m["reason"] = "관련 있음"
    for d in data["candidate_decisions"]:
        d["reason"] = "무관"
    result = build_result(json.dumps(data, ensure_ascii=False), mapping_input, versions)

    assert result.validation.warnings, "경고는 남는다"
    assert result.validation.passed, "경고는 passed를 깨지 않는다"
    assert not result.human_review.required, "경고만으로는 검토로 가지 않는다"
