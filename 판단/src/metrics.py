"""Phase 1 판단 평가 지표.

골든셋의 **gold**(사람이 정한 정답)와 실제 판단 결과를 비교한다.

### 지표 정의를 여기 한 곳에만 둔다

발표 자료와 코드가 다른 숫자를 말하는 일이 제일 흔한 사고다.
계산은 전부 이 모듈을 거치고, 문서는 이 정의를 인용한다.

### 무엇을 분모에 넣는가 — 중요

| 상황 | 기본 동작 | 이유 |
|---|---|---|
| LLM 호출·파싱 실패 | **매핑 0개로 세어 분모에 포함** | 실패도 못 맞힌 것이다 |
| 정답이 후보 밖 (`retrieval_miss`) | **제외 옵션 제공** | 판단이 아니라 검색 Recall 문제다 |

실패를 빼고 재면 정확도가 부풀려진다. 그래서 기본은 포함이고,
"파싱된 것만 봤을 때"는 따로 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from enums import Decision, MatchStatus, ProcessingStatus, Relation
from models import Phase1MappingResult


@dataclass(frozen=True)
class GoldAnswer:
    """사람이 정한 정답."""

    test_id: str
    match_status: str
    control_ids: frozenset[str]
    primary_id: Optional[str]
    retrieval_miss: bool = False

    @classmethod
    def from_case(cls, case: dict) -> "GoldAnswer":
        gold = case["gold"]
        controls = gold.get("controls", [])
        primary = next(
            (c["control_id"] for c in controls if c["relation"] == "PRIMARY"), None
        )
        return cls(
            test_id=case["test_id"],
            match_status=gold["match_status"],
            control_ids=frozenset(c["control_id"] for c in controls),
            primary_id=primary,
            retrieval_miss=bool(gold.get("retrieval_miss", False)),
        )


@dataclass
class CaseOutcome:
    """한 건의 비교 결과."""

    test_id: str
    category: str
    gold: GoldAnswer
    predicted_ids: frozenset[str]
    predicted_primary: Optional[str]
    predicted_status: str
    processing_status: str
    validation_passed: bool
    citation_total: int
    citation_valid: int
    review_required: bool
    has_uncertain: bool = False
    processing_time_ms: Optional[int] = None

    @property
    def parsed(self) -> bool:
        return self.processing_status != ProcessingStatus.FAILED.value

    @property
    def true_positives(self) -> int:
        return len(self.predicted_ids & self.gold.control_ids)

    @property
    def exact_match(self) -> bool:
        return self.predicted_ids == self.gold.control_ids

    @property
    def exact_match_with_relation(self) -> bool:
        return self.exact_match and self.predicted_primary == self.gold.primary_id


def make_outcome(case: dict, result: Phase1MappingResult) -> CaseOutcome:
    """판단 결과 하나를 골든셋 케이스와 맞춰 비교 단위로 만든다."""
    predicted_ids = frozenset(m.control_id for m in result.mapped_controls)
    primary = next(
        (m.control_id for m in result.mapped_controls if m.relation == Relation.PRIMARY),
        None,
    )

    # 인용 유효율 — Validator가 잡은 Citation 오류(E4xx)를 센다
    citations = [c for m in result.mapped_controls for c in m.citations]
    bad_citations = sum(
        1 for i in result.validation.issues if i.code.value.startswith("E4")
    )

    return CaseOutcome(
        test_id=case["test_id"],
        category=case["category"],
        gold=GoldAnswer.from_case(case),
        predicted_ids=predicted_ids,
        predicted_primary=primary,
        predicted_status=result.match_status.value,
        processing_status=result.processing_status.value,
        validation_passed=result.validation.passed,
        citation_total=len(citations),
        citation_valid=max(0, len(citations) - bad_citations),
        review_required=result.human_review.required,
        has_uncertain=any(
            d.decision == Decision.UNCERTAIN for d in result.candidate_decisions
        ),
        processing_time_ms=result.processing_time_ms,
    )


@dataclass
class Metrics:
    n: int = 0
    n_excluded_retrieval_miss: int = 0

    # 매핑 정확도
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    macro_precision: float = 0.0
    macro_recall: float = 0.0
    exact_match: float = 0.0
    exact_match_with_relation: float = 0.0
    primary_accuracy: float = 0.0

    # NO_MATCH 판별
    no_match_accuracy: float = 0.0
    no_match_accuracy_strict: float = 0.0   # UNCERTAIN 없이 NO_MATCH를 낸 것만
    false_match_rate: float = 0.0     # 정답이 NO_MATCH인데 매핑을 만들어냄
    missed_match_rate: float = 0.0    # 정답이 MATCHED인데 NO_MATCH를 냄

    # 판단 보류 (UNCERTAIN)
    uncertain_rate: float = 0.0           # 후보 하나라도 UNCERTAIN인 건의 비율
    deferred_no_match_rate: float = 0.0   # NO_MATCH 중 실은 "판단 못 함"인 비율

    # 운영 지표
    citation_valid_rate: float = 0.0
    invalid_output_rate: float = 0.0
    review_required_rate: float = 0.0
    avg_processing_time_ms: Optional[float] = None

    by_category: dict[str, dict[str, Optional[float]]] = field(default_factory=dict)


def _safe(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _ratio(numerator: float, denominator: float) -> Optional[float]:
    """분모가 0이면 None. 0%와 "해당 없음"은 다르다.

    NO_MATCH 사례는 정답도 예측도 비어 있는 것이 정상이다.
    이걸 Precision 0%로 표시하면 성능이 나쁜 것처럼 보인다.
    """
    return numerator / denominator if denominator else None


def compute(
    outcomes: Iterable[CaseOutcome],
    *,
    exclude_retrieval_miss: bool = True,
) -> Metrics:
    """지표를 계산한다.

    exclude_retrieval_miss:
        정답이 Top-K 후보 밖인 사례를 제외한다. 기본 True.
        그 사례에서 LLM이 정답을 못 고른 것은 판단 문제가 아니라 검색 Recall 문제다.
        포함해서 재고 싶으면 False로 둔다.
    """
    all_outcomes = list(outcomes)
    excluded = [o for o in all_outcomes if exclude_retrieval_miss and o.gold.retrieval_miss]
    rows = [o for o in all_outcomes if o not in excluded]

    m = Metrics(n=len(rows), n_excluded_retrieval_miss=len(excluded))
    if not rows:
        return m

    # ---- 매핑 정확도 (micro: (증적, 통제항목) 쌍 단위) ----
    tp = sum(o.true_positives for o in rows)
    predicted = sum(len(o.predicted_ids) for o in rows)
    actual = sum(len(o.gold.control_ids) for o in rows)

    m.precision = _safe(tp, predicted)
    m.recall = _safe(tp, actual)
    m.f1 = _safe(2 * m.precision * m.recall, m.precision + m.recall)

    # macro: 건별로 재고 평균. 건수가 적을 때 큰 증적에 가려지지 않는다
    per_p = [_safe(o.true_positives, len(o.predicted_ids)) for o in rows if o.predicted_ids]
    per_r = [_safe(o.true_positives, len(o.gold.control_ids)) for o in rows if o.gold.control_ids]
    m.macro_precision = _safe(sum(per_p), len(per_p))
    m.macro_recall = _safe(sum(per_r), len(per_r))

    m.exact_match = _safe(sum(1 for o in rows if o.exact_match), len(rows))
    m.exact_match_with_relation = _safe(
        sum(1 for o in rows if o.exact_match_with_relation), len(rows)
    )

    with_primary = [o for o in rows if o.gold.primary_id]
    m.primary_accuracy = _safe(
        sum(1 for o in with_primary if o.predicted_primary == o.gold.primary_id),
        len(with_primary),
    )

    # ---- NO_MATCH 판별 ----
    gold_no_match = [o for o in rows if o.gold.match_status == MatchStatus.NO_MATCH.value]
    gold_matched = [o for o in rows if o.gold.match_status == MatchStatus.MATCHED.value]

    m.no_match_accuracy = _safe(
        sum(1 for o in gold_no_match if o.predicted_status == MatchStatus.NO_MATCH.value),
        len(gold_no_match),
    )
    m.false_match_rate = _safe(
        sum(1 for o in gold_no_match if o.predicted_status == MatchStatus.MATCHED.value),
        len(gold_no_match),
    )
    m.missed_match_rate = _safe(
        sum(1 for o in gold_matched if o.predicted_status == MatchStatus.NO_MATCH.value),
        len(gold_matched),
    )

    # ---- 판단 보류 ----
    #
    # `match_status`에는 UNCERTAIN이 없다. 후보가 전부 UNCERTAIN이어도 결과는 NO_MATCH다.
    # 그래서 "관련 없다고 맞힌 것"과 "판단을 못 한 것"이 위 no_match_accuracy에 섞인다.
    # 여기서 떼어 낸다. 값을 늘리지 않고 구분만 한다.
    m.uncertain_rate = _safe(sum(1 for o in rows if o.has_uncertain), len(rows))

    predicted_no_match = [o for o in rows if o.predicted_status == MatchStatus.NO_MATCH.value]
    m.deferred_no_match_rate = _safe(
        sum(1 for o in predicted_no_match if o.has_uncertain), len(predicted_no_match)
    )
    m.no_match_accuracy_strict = _safe(
        sum(
            1
            for o in gold_no_match
            if o.predicted_status == MatchStatus.NO_MATCH.value and not o.has_uncertain
        ),
        len(gold_no_match),
    )

    # ---- 운영 지표 ----
    cit_total = sum(o.citation_total for o in rows)
    m.citation_valid_rate = _safe(sum(o.citation_valid for o in rows), cit_total)
    m.invalid_output_rate = _safe(sum(1 for o in rows if not o.parsed), len(rows))
    m.review_required_rate = _safe(sum(1 for o in rows if o.review_required), len(rows))

    times = [o.processing_time_ms for o in rows if o.processing_time_ms is not None]
    m.avg_processing_time_ms = _safe(sum(times), len(times)) if times else None

    # ---- 유형별 ----
    categories = sorted({o.category for o in rows})
    for category in categories:
        subset = [o for o in rows if o.category == category]
        sub_tp = sum(o.true_positives for o in subset)
        m.by_category[category] = {
            "n": len(subset),
            "exact_match": _safe(sum(1 for o in subset if o.exact_match), len(subset)),
            "precision": _ratio(sub_tp, sum(len(o.predicted_ids) for o in subset)),
            "recall": _ratio(sub_tp, sum(len(o.gold.control_ids) for o in subset)),
            "review_rate": _safe(sum(1 for o in subset if o.review_required), len(subset)),
        }

    return m


def format_report(m: Metrics, title: str = "Phase 1 판단 평가") -> str:
    lines = [
        f"# {title}",
        "",
        f"대상 {m.n}건"
        + (f" (검색 Recall 실패 {m.n_excluded_retrieval_miss}건 제외)" if m.n_excluded_retrieval_miss else ""),
        "",
        "## 매핑 정확도",
        f"  Precision            {m.precision:6.1%}",
        f"  Recall               {m.recall:6.1%}",
        f"  F1                   {m.f1:6.1%}",
        f"  Macro Precision      {m.macro_precision:6.1%}",
        f"  Macro Recall         {m.macro_recall:6.1%}",
        f"  Exact Match          {m.exact_match:6.1%}   (통제항목 집합이 정확히 일치)",
        f"  Exact Match+relation {m.exact_match_with_relation:6.1%}   (PRIMARY까지 일치)",
        f"  PRIMARY 정확도        {m.primary_accuracy:6.1%}",
        "",
        "## NO_MATCH 판별",
        f"  NO_MATCH 정확도       {m.no_match_accuracy:6.1%}   (무관 증적을 무관으로)",
        f"   └ 판단 보류 제외     {m.no_match_accuracy_strict:6.1%}   (UNCERTAIN 없이 맞힌 것만)",
        f"  오탐률                {m.false_match_rate:6.1%}   (무관한데 매핑을 만듦)",
        f"  미탐률                {m.missed_match_rate:6.1%}   (관련 있는데 NO_MATCH)",
        "",
        "## 판단 보류",
        f"  UNCERTAIN 비율        {m.uncertain_rate:6.1%}   (후보 하나라도 보류)",
        f"  보류형 NO_MATCH       {m.deferred_no_match_rate:6.1%}   (NO_MATCH 중 실은 '판단 못 함')",
        "",
        "## 운영",
        f"  Citation 유효율       {m.citation_valid_rate:6.1%}",
        f"  출력 실패율           {m.invalid_output_rate:6.1%}   (파싱·스키마 실패)",
        f"  검토 전환율           {m.review_required_rate:6.1%}",
    ]
    if m.avg_processing_time_ms is not None:
        lines.append(f"  평균 처리시간         {m.avg_processing_time_ms:6.0f} ms")

    if m.by_category:
        lines += ["", "## 유형별", "", f"  {'유형':22} {'건수':>4} {'EM':>7} {'P':>7} {'R':>7} {'검토':>7}"]
        for category, row in m.by_category.items():

            def pct(value: Optional[float]) -> str:
                # 정답도 예측도 없는 유형(NO_MATCH 계열)은 "—". 0%가 아니다.
                return "      —" if value is None else f"{value:7.0%}"

            lines.append(
                f"  {category:22} {row['n']:4.0f} {row['exact_match']:7.0%} "
                f"{pct(row['precision'])} {pct(row['recall'])} {row['review_rate']:7.0%}"
            )
        lines += ["", "  — 는 정답도 예측도 없는 유형이다 (NO_MATCH 계열). 0%와 다르다."]
    return "\n".join(lines)
