"""LLM 응답 파싱 — 실제로 자주 보는 망가진 응답들."""

import json

from enums import ErrorCode
from output_parser import extract_json_text, parse_llm_output

from conftest import GOOD_RESPONSE


def codes(issues):
    return [i.code for i in issues]


def test_순수한_json():
    output, issues = parse_llm_output(GOOD_RESPONSE)
    assert not issues
    assert output.match_status.value == "MATCHED"
    assert len(output.mapped_controls) == 1


def test_코드블록으로_감싼_응답():
    output, issues = parse_llm_output(f"```json\n{GOOD_RESPONSE}\n```")
    assert not issues and output is not None


def test_앞뒤에_말을_붙인_응답():
    raw = f"네, 판단 결과입니다.\n\n{GOOD_RESPONSE}\n\n추가 설명이 필요하시면 말씀해 주세요."
    output, issues = parse_llm_output(raw)
    assert not issues and output is not None


def test_마지막_쉼표():
    broken = GOOD_RESPONSE.replace('"mapped_controls": [', '"mapped_controls": [').rstrip()
    broken = broken[:-1] + ",}"   # 최상위 객체 끝에 쉼표
    output, issues = parse_llm_output(broken)
    assert output is not None, "마지막 쉼표는 한 번 더 시도해서 복구한다"


def test_빈_응답():
    output, issues = parse_llm_output("")
    assert output is None
    assert ErrorCode.LLM_EMPTY_RESPONSE in codes(issues)


def test_json이_아예_없음():
    output, issues = parse_llm_output("죄송합니다. 판단할 수 없습니다.")
    assert output is None
    assert ErrorCode.JSON_PARSE_FAILED in codes(issues)


def test_잘린_응답():
    # 중간에서 끊기면 닫는 괄호가 없다 → 잘린 응답
    output, issues = parse_llm_output('{"match_status": "MATC')
    assert output is None
    assert ErrorCode.LLM_TRUNCATED_RESPONSE in codes(issues)


def test_중간에_끊긴_긴_응답():
    output, issues = parse_llm_output(GOOD_RESPONSE[: len(GOOD_RESPONSE) // 2])
    assert output is None, "복구하지 않는다. 잘린 결과를 추측해 채우면 안 된다"


def test_깨진_json():
    output, issues = parse_llm_output('{"match_status": "MATCHED", "candidate_decisions": [{]}')
    assert output is None
    assert ErrorCode.JSON_PARSE_FAILED in codes(issues)


def test_필수_필드_누락():
    data = json.loads(GOOD_RESPONSE)
    del data["mapped_controls"][0]["reason"]
    output, issues = parse_llm_output(json.dumps(data, ensure_ascii=False))
    assert output is None
    assert ErrorCode.REQUIRED_FIELD_MISSING in codes(issues)


def test_정의되지_않은_enum():
    data = json.loads(GOOD_RESPONSE)
    data["candidate_decisions"][0]["decision"] = "MAYBE"
    output, issues = parse_llm_output(json.dumps(data, ensure_ascii=False))
    assert output is None
    assert ErrorCode.INVALID_ENUM_VALUE in codes(issues)


def test_confidence_범위_초과():
    data = json.loads(GOOD_RESPONSE)
    data["mapped_controls"][0]["llm_confidence"] = 1.5
    output, issues = parse_llm_output(json.dumps(data, ensure_ascii=False))
    assert output is None
    assert ErrorCode.CONFIDENCE_OUT_OF_RANGE in codes(issues)


def test_본문_속_중괄호가_파싱을_깨지_않는다():
    data = json.loads(GOOD_RESPONSE)
    data["mapped_controls"][0]["reason"] = '규정 3조 {가}항에 "승인" 절차가 있다'
    raw = "설명: " + json.dumps(data, ensure_ascii=False) + " 이상입니다."
    output, issues = parse_llm_output(raw)
    assert not issues and output is not None


def test_extract_json_text는_객체만_잘라낸다():
    assert extract_json_text('앞 {"a": 1} 뒤') == '{"a": 1}'
    assert extract_json_text("괄호 없음") is None
