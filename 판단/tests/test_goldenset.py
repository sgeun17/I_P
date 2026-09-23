"""골든셋 28건을 파이프라인에 통과시킨다.

**이 테스트가 측정하는 것과 측정하지 않는 것**

  측정한다
    - 정답대로 온 응답을 파이프라인이 통과시키는가 (거짓 오류가 없는가)
    - 잘못된 응답을 Validator가 잡는가
    - 검토 전환이 의도대로 되는가, 그리고 검토율이 얼마인가

  측정하지 않는다
    - **LLM의 정확도.** ideal 응답은 사람이 쓴 정답이다.
      실제 정확도는 로컬 LLM에 input을 넣고 나온 결과를 expected.controls와
      비교해야 나온다. tools/eval_goldenset.py가 그 자리다.
"""

import json
from pathlib import Path

import pytest
from enums import ErrorCode
from models import MappingInput, VersionInfo
from service import build_result

FIXTURE = Path(__file__).parent / "fixtures" / "goldenset.json"
GOLDENSET = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = GOLDENSET["cases"]

VERSIONS = VersionInfo(
    prompt_version="phase1_mapping_v0.1",
    model_name="goldenset-ideal",
    ruleset_version=GOLDENSET["ruleset_version"],
)


def run(case: dict):
    mapping_input = MappingInput.model_validate(case["input"])
    return build_result(case["llm_response"], mapping_input, VERSIONS)


def ids(case):
    return case["test_id"]


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_입력이_스키마를_지킨다(case):
    """골든셋의 입력 자체가 전처리팀·검색팀 규격을 어기면 안 된다."""
    MappingInput.model_validate(case["input"])


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_처리_상태(case):
    result = run(case)
    assert result.processing_status.value == case["expected"]["processing_status"]


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_매핑_결과(case):
    result = run(case)
    expected = case["expected"]
    assert result.match_status.value == expected["match_status"]

    got = [(m.control_id, m.relation.value) for m in result.mapped_controls]
    want = [(c["control_id"], c["relation"]) for c in expected["controls"]]
    assert sorted(got) == sorted(want)


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_검증_결과(case):
    result = run(case)
    expected = case["expected"]
    assert result.validation.passed == expected["validation_passed"]

    got = {i.code.value for i in result.validation.issues}
    for code in expected["error_codes"]:
        assert code in got, f"{code}를 잡지 못했다. 잡은 것: {sorted(got)}"

    if expected["validation_passed"]:
        assert not got, f"통과해야 하는데 오류가 있다: {sorted(got)}"


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_검토_전환(case):
    result = run(case)
    expected = case["expected"]
    assert result.human_review.required == expected["review_required"]

    got = {r.value for r in result.human_review.reasons}
    for reason in expected["review_reasons"]:
        assert reason in got, f"{reason}가 없다. 나온 것: {sorted(got)}"


def test_정답이_상한을_넘지_않는다():
    """정답 자체가 1:N 상한을 넘으면 상한이 틀린 것이다."""
    over = [c["test_id"] for c in CASES if len(c["expected"]["controls"]) > 3]
    assert not over, f"정답이 4개 이상인 사례가 있다. 상한을 올려야 한다: {over}"


def test_ideal_응답은_형식_오류가_없어야():
    """우리가 쓴 정답 응답에 형식 오류가 있으면 골든셋이 잘못된 것이다."""
    broken = []
    for case in CASES:
        if case["response_kind"] != "ideal":
            continue
        result = run(case)
        schema_errors = [
            i.code.value
            for i in result.validation.issues
            if i.code.value.startswith("E2")
        ]
        if schema_errors:
            broken.append((case["test_id"], schema_errors))
    assert not broken, f"정답 응답에 형식 오류가 있다: {broken}"


def test_후보에_없는_정답이_없다():
    """정답 통제항목은 반드시 Top-K 후보 안에 있어야 한다.

    없다면 그 사례는 판단 문제가 아니라 검색 Recall 문제다.

    faulty 사례의 expected.controls는 "LLM이 잘못 주장한 것"이므로 제외한다.
    """
    bad = []
    for case in CASES:
        if case["response_kind"] != "ideal":
            continue
        candidates = {c["control_id"] for c in case["input"]["candidate_controls"]}
        for control in case["expected"]["controls"]:
            if control["control_id"] not in candidates:
                bad.append((case["test_id"], control["control_id"]))
    assert not bad, f"후보 밖에 정답이 있다: {bad}"


def test_검토율을_보고한다(capsys):
    """실패시키지 않는다. 숫자를 보려고 둔 것이다."""
    rows = []
    for case in CASES:
        result = run(case)
        expected_reasons = set(case["expected"]["review_reasons"])
        got_reasons = {r.value for r in result.human_review.reasons}
        rows.append(
            {
                "test_id": case["test_id"],
                "category": case["category"],
                "required": result.human_review.required,
                "reasons": sorted(got_reasons),
                "extra": sorted(got_reasons - expected_reasons),
            }
        )

    required = sum(1 for r in rows if r["required"])
    rate = required / len(rows)

    with capsys.disabled():
        print(f"\n\n검토 전환율: {required}/{len(rows)} = {rate:.0%}")
        extras = [r for r in rows if r["extra"]]
        if extras:
            print("예상보다 더 걸린 사유 (임계값이 과민한지 확인):")
            for r in extras:
                print(f"  {r['test_id']:18} +{r['extra']}")
        print("검토 불필요로 자동 확정된 사례:")
        for r in rows:
            if not r["required"]:
                print(f"  {r['test_id']:18} {r['category']}")

    assert rate < 1.0, "전부 검토로 가면 도구의 의미가 없다"
