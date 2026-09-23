"""평가 지표 계산.

지표 코드가 틀리면 발표 숫자가 통째로 틀린다. 손으로 셀 수 있는 사례로 고정한다.
"""

import json
from pathlib import Path

from metrics import CaseOutcome, GoldAnswer, compute, make_outcome
from models import MappingInput, VersionInfo
from service import build_result

GOLDENSET = json.loads(
    (Path(__file__).parent / "fixtures" / "goldenset.json").read_text(encoding="utf-8")
)
CASES = {c["test_id"]: c for c in GOLDENSET["cases"]}
VERSIONS = VersionInfo(prompt_version="p", model_name="m", ruleset_version="r")


def outcome(
    test_id="T", category="single_mapping", gold_ids=("2.5.1",), pred_ids=("2.5.1",),
    gold_primary="2.5.1", pred_primary="2.5.1", gold_status="MATCHED", pred_status="MATCHED",
    processing="COMPLETED", cit_total=1, cit_valid=1, review=False, retrieval_miss=False,
    has_uncertain=False,
):
    return CaseOutcome(
        test_id=test_id,
        category=category,
        gold=GoldAnswer(test_id, gold_status, frozenset(gold_ids), gold_primary, retrieval_miss),
        predicted_ids=frozenset(pred_ids),
        predicted_primary=pred_primary,
        predicted_status=pred_status,
        processing_status=processing,
        validation_passed=True,
        citation_total=cit_total,
        citation_valid=cit_valid,
        review_required=review,
        has_uncertain=has_uncertain,
    )


# --------------------------------------------------------------------------
# 손으로 셀 수 있는 사례
# --------------------------------------------------------------------------


def test_전부_맞히면_1점():
    m = compute([outcome(), outcome(test_id="T2")])
    assert m.precision == 1.0 and m.recall == 1.0 and m.f1 == 1.0
    assert m.exact_match == 1.0


def test_절반만_맞힌_경우():
    # 정답 2개 중 1개만 맞히고, 틀린 것 1개를 더 냈다
    m = compute([outcome(gold_ids=("2.5.1", "2.5.5"), pred_ids=("2.5.1", "2.9.3"))])
    assert m.precision == 0.5      # 낸 것 2개 중 1개 맞음
    assert m.recall == 0.5         # 정답 2개 중 1개 찾음
    assert m.f1 == 0.5
    assert m.exact_match == 0.0


def test_precision과_recall이_다른_경우():
    # 정답 1개인데 3개를 냈다 → P 1/3, R 1/1
    m = compute([outcome(gold_ids=("2.5.1",), pred_ids=("2.5.1", "2.5.5", "2.5.6"))])
    assert round(m.precision, 4) == round(1 / 3, 4)
    assert m.recall == 1.0


def test_primary가_틀리면_relation_em이_떨어진다():
    m = compute([
        outcome(gold_ids=("2.5.1", "2.5.5"), pred_ids=("2.5.1", "2.5.5"),
                gold_primary="2.5.1", pred_primary="2.5.5")
    ])
    assert m.exact_match == 1.0, "집합은 같다"
    assert m.exact_match_with_relation == 0.0, "PRIMARY가 다르다"
    assert m.primary_accuracy == 0.0


def test_no_match_판별():
    rows = [
        outcome(test_id="A", gold_ids=(), pred_ids=(), gold_primary=None, pred_primary=None,
                gold_status="NO_MATCH", pred_status="NO_MATCH", cit_total=0, cit_valid=0),
        outcome(test_id="B", gold_ids=(), pred_ids=("2.5.1",), gold_primary=None,
                gold_status="NO_MATCH", pred_status="MATCHED"),
        outcome(test_id="C", gold_ids=("2.5.1",), pred_ids=(), pred_primary=None,
                gold_status="MATCHED", pred_status="NO_MATCH"),
    ]
    m = compute(rows)
    assert m.no_match_accuracy == 0.5      # NO_MATCH 정답 2건 중 1건
    assert m.false_match_rate == 0.5       # 무관한데 매핑 1건
    assert m.missed_match_rate == 1.0      # MATCHED 정답 1건을 놓침


def test_실패한_건도_분모에_들어간다():
    """실패를 빼고 재면 정확도가 부풀려진다."""
    rows = [
        outcome(test_id="A"),
        outcome(test_id="B", pred_ids=(), pred_primary=None, pred_status="NO_MATCH",
                processing="FAILED", cit_total=0, cit_valid=0),
    ]
    m = compute(rows)
    assert m.n == 2
    assert m.recall == 0.5, "실패한 건은 못 맞힌 것으로 센다"
    assert m.invalid_output_rate == 0.5


def test_검색_recall_실패는_기본적으로_제외된다():
    rows = [outcome(test_id="A"), outcome(test_id="B", retrieval_miss=True, pred_ids=())]
    assert compute(rows).n == 1
    assert compute(rows, exclude_retrieval_miss=False).n == 2


def test_분모가_0이면_퍼센트가_아니라_None():
    """NO_MATCH 유형은 정답도 예측도 없다. 0%로 표시하면 오해한다."""
    rows = [
        outcome(category="no_match", gold_ids=(), pred_ids=(), gold_primary=None,
                pred_primary=None, gold_status="NO_MATCH", pred_status="NO_MATCH",
                cit_total=0, cit_valid=0)
    ]
    row = compute(rows).by_category["no_match"]
    assert row["precision"] is None and row["recall"] is None
    assert row["exact_match"] == 1.0


def test_citation_유효율():
    rows = [outcome(cit_total=3, cit_valid=2), outcome(test_id="B", cit_total=1, cit_valid=1)]
    assert compute(rows).citation_valid_rate == 0.75


# --------------------------------------------------------------------------
# 실제 파이프라인과 연결
# --------------------------------------------------------------------------


def run(test_id: str):
    case = CASES[test_id]
    mapping_input = MappingInput.model_validate(case["input"])
    result = build_result(case["llm_response"], mapping_input, VERSIONS)
    return make_outcome(case, result)


def test_정답_응답은_정답과_일치한다():
    o = run("G-SINGLE-01")
    assert o.exact_match and o.exact_match_with_relation
    assert o.gold.control_ids == {"2.5.1"}


def test_다중매핑_정답():
    o = run("G-MULTI-01")
    assert o.gold.control_ids == {"2.5.1", "2.5.5", "2.5.6"}
    assert o.exact_match
    assert o.gold.primary_id == "2.5.1" and o.predicted_primary == "2.5.1"


def test_허위인용_사례는_매핑은_맞지만_인용이_깨진다():
    o = run("G-BADCITE-01")
    assert o.exact_match, "통제항목 자체는 맞게 골랐다"
    assert o.citation_valid < o.citation_total, "인용은 검증에서 걸린다"


def test_표기변형_id는_틀린_것으로_센다():
    o = run("G-BADID-02")
    assert o.gold.control_ids == {"2.7.1"}
    assert o.predicted_ids == {"2.7.1."}
    assert not o.exact_match, "점 하나 붙었다고 맞다고 보면 안 된다"


def test_깨진_json은_예측_0개():
    o = run("G-BADJSON-01")
    assert not o.parsed
    assert o.predicted_ids == frozenset()


# --------------------------------------------------------------------------
# 판단 보류 (UNCERTAIN)
# --------------------------------------------------------------------------


def no_match_case(test_id, has_uncertain):
    """정답도 예측도 NO_MATCH인 건."""
    return outcome(
        test_id=test_id, category="no_match",
        gold_ids=(), pred_ids=(), gold_primary=None, pred_primary=None,
        gold_status="NO_MATCH", pred_status="NO_MATCH",
        cit_total=0, cit_valid=0, has_uncertain=has_uncertain,
    )


def test_보류형_no_match를_따로_센다():
    """`match_status`만 보면 둘 다 NO_MATCH다. 하나는 '판단 못 함'이다."""
    m = compute([no_match_case("A", False), no_match_case("B", True)])

    assert m.no_match_accuracy == 1.0, "값 자체는 둘 다 NO_MATCH로 맞다"
    assert m.no_match_accuracy_strict == 0.5, "실제로 무관하다고 판단한 것은 절반뿐이다"
    assert m.deferred_no_match_rate == 0.5
    assert m.uncertain_rate == 0.5


def test_uncertain이_없으면_두_정확도가_같다():
    m = compute([no_match_case("A", False), no_match_case("B", False)])
    assert m.no_match_accuracy == m.no_match_accuracy_strict == 1.0
    assert m.uncertain_rate == 0.0
    assert m.deferred_no_match_rate == 0.0


def test_골든셋의_uncertain_건이_집계된다():
    outcomes = [run(test_id) for test_id in CASES]
    uncertain = [o for o in outcomes if o.has_uncertain]
    assert len(uncertain) == 3, "골든셋 uncertain 유형 3건"
    assert all(o.predicted_status == "NO_MATCH" for o in uncertain), (
        "UNCERTAIN만 남으면 match_status는 NO_MATCH다"
    )


def test_골든셋_전체가_지표_계산을_통과한다():
    outcomes = [run(test_id) for test_id in CASES]
    m = compute(outcomes)
    assert m.n == len(CASES) - 2, "검색 Recall 실패 2건 제외"
    assert 0.0 <= m.f1 <= 1.0
    assert m.by_category
