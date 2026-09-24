"""Human Review 전환 정책과 상태 머신.

임계값은 전부 임시값이다. 골든셋 평가 뒤에 조정한다.
값을 바꾸면 thresholds.yaml만 고치고 이 코드는 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from enums import (
    ChunkSource,
    Decision,
    ErrorCode,
    MatchStatus,
    ReviewReason,
    ReviewStatus,
)
from models import HumanReview, LLMMappingOutput, MappingInput, ValidationResult

# --------------------------------------------------------------------------
# 판단 규칙 상수
# --------------------------------------------------------------------------

MAX_MAPPED_CONTROLS = 3          # 1:N 매핑 상한. 이 이상이면 대개 LLM이 헤매고 있다
PRIMARY_COUNT_WHEN_MATCHED = 1   # MATCHED면 PRIMARY는 정확히 1개


# --------------------------------------------------------------------------
# 임계값 (임시값 — thresholds_v0.1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    profile_name: str = "thresholds_v0.4"

    # `llm_confidence`의 뜻을 규칙으로 정의했기 때문에 이 값에 근거가 생겼다
    # (mapping_rules 2.2). 0.70은 구간 경계다.
    #
    #   0.70 이상  관련이 분명하다
    #   0.70 미만  다른 통제항목으로도 읽힌다  ← 여기부터 사람이 본다
    #
    # 정의가 프롬프트에 들어간 뒤 실제 분포를 보고 다시 조정한다.
    low_confidence: float = 0.70

    # BGE-M3 실측: 관련 있어도 0.5~0.6에 몰리고, 무관한 항목도 0.49가 나온다.
    #
    # 골든셋 29건 측정 결과:
    #   0.03 → 17건에서 발동하지만 검토를 단독으로 결정한 것은 1건뿐이다.
    #          나머지 16건은 이미 다른 사유로 검토행이었다. 사유 목록만 지저분해진다.
    #   0.01 → 2건 발동, 결정 1건. 검토율은 같고 사유가 선명해진다.
    # 그래서 0.01로 낮춘다.
    narrow_score_gap: float = 0.01

    # 같은 이유로 0.85는 영영 발동하지 않는다. 실측 1위 점수 수준으로 낮춘다.
    high_similarity: float = 0.55

    min_chunk_length: int = 30         # 청크가 이보다 짧으면 판단 근거가 부족하다고 본다

    # 결과 유형 자체를 검토로 보낼지 (초기에는 켜두고, 검토량을 보고 끈다)
    review_on_no_match: bool = True
    review_on_multi_mapping: bool = True


DEFAULT_THRESHOLDS = Thresholds()


# 무조건 검토로 보내는 오류 코드
BLOCKING_ERROR_CODES: dict[ErrorCode, ReviewReason] = {
    ErrorCode.INPUT_NO_CHUNKS: ReviewReason.INPUT_NOT_JUDGEABLE,
    ErrorCode.INPUT_NO_CANDIDATES: ReviewReason.INPUT_NOT_JUDGEABLE,
    ErrorCode.INPUT_DUPLICATE_CANDIDATE: ReviewReason.INPUT_NOT_JUDGEABLE,
    ErrorCode.INPUT_MALFORMED: ReviewReason.INPUT_NOT_JUDGEABLE,
    ErrorCode.JSON_PARSE_FAILED: ReviewReason.JSON_PARSE_FAILED,
    ErrorCode.SCHEMA_INVALID: ReviewReason.SCHEMA_INVALID,
    ErrorCode.REQUIRED_FIELD_MISSING: ReviewReason.SCHEMA_INVALID,
    ErrorCode.INVALID_ENUM_VALUE: ReviewReason.SCHEMA_INVALID,
    ErrorCode.CONFIDENCE_OUT_OF_RANGE: ReviewReason.SCHEMA_INVALID,
    ErrorCode.CONTROL_ID_NOT_IN_KB: ReviewReason.CONTROL_ID_INVALID,
    ErrorCode.CONTROL_ID_NOT_IN_CANDIDATES: ReviewReason.CONTROL_ID_INVALID,
    ErrorCode.CONTROL_NAME_MISMATCH: ReviewReason.CONTROL_ID_INVALID,
    ErrorCode.DUPLICATE_CONTROL_ID: ReviewReason.CONTROL_ID_INVALID,
    ErrorCode.CITATION_MISSING: ReviewReason.CITATION_MISSING,
    ErrorCode.CITATION_CHUNK_NOT_FOUND: ReviewReason.CITATION_INVALID,
    ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE: ReviewReason.CITATION_INVALID,
    ErrorCode.CITATION_PAGE_MISMATCH: ReviewReason.CITATION_INVALID,
    ErrorCode.CITATION_EMPTY_QUOTE: ReviewReason.CITATION_INVALID,
    ErrorCode.CITATION_FOREIGN_EVIDENCE: ReviewReason.CITATION_INVALID,
    ErrorCode.PRIMARY_COUNT_INVALID: ReviewReason.PRIMARY_COUNT_INVALID,
    ErrorCode.TOO_MANY_MAPPED_CONTROLS: ReviewReason.TOO_MANY_CONTROLS,
    ErrorCode.NO_MATCH_WITH_CONTROLS: ReviewReason.SCHEMA_INVALID,
    ErrorCode.MATCHED_WITHOUT_CONTROLS: ReviewReason.SCHEMA_INVALID,
    ErrorCode.ADEQUACY_JUDGMENT_DETECTED: ReviewReason.SCHEMA_INVALID,
    ErrorCode.LLM_RETRY_EXHAUSTED: ReviewReason.LLM_CALL_FAILED,
    ErrorCode.LLM_TIMEOUT: ReviewReason.LLM_CALL_FAILED,
    ErrorCode.LLM_CONNECTION_FAILED: ReviewReason.LLM_CALL_FAILED,
    ErrorCode.LLM_SERVER_ERROR: ReviewReason.LLM_CALL_FAILED,
    ErrorCode.LLM_EMPTY_RESPONSE: ReviewReason.LLM_CALL_FAILED,
    ErrorCode.LLM_TRUNCATED_RESPONSE: ReviewReason.LLM_CALL_FAILED,
}


# --------------------------------------------------------------------------
# 검토 여부 판단
# --------------------------------------------------------------------------


def decide_review(
    output: LLMMappingOutput | None,
    validation: ValidationResult,
    mapping_input: MappingInput | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    injection_suspected: bool = False,
) -> HumanReview:
    """검증 결과와 임계값을 보고 Human Review 필요 여부를 정한다.

    output이 None이면 LLM 호출이나 파싱 자체가 실패한 것이므로 무조건 검토다.
    """
    reasons: list[ReviewReason] = []

    # 1. 무조건 검토 — 검증에서 잡힌 오류
    for issue in validation.issues:
        reason = BLOCKING_ERROR_CODES.get(issue.code)
        if reason and reason not in reasons:
            reasons.append(reason)

    if injection_suspected:
        reasons.append(ReviewReason.PROMPT_INJECTION_SUSPECTED)

    if output is None:
        if not reasons:
            reasons.append(ReviewReason.LLM_CALL_FAILED)
        return _build(reasons, thresholds)

    # 2. 무조건 검토 — UNCERTAIN 판단이 하나라도 있으면
    if any(d.decision == Decision.UNCERTAIN for d in output.candidate_decisions):
        reasons.append(ReviewReason.UNCERTAIN_DECISION)

    # 3. 조건부 검토 — 낮은 신뢰도
    confidences = [c.llm_confidence for c in output.mapped_controls]
    if confidences and min(confidences) < thresholds.low_confidence:
        reasons.append(ReviewReason.LOW_CONFIDENCE)

    # 4. 조건부 검토 — 1위와 2위 후보의 검색 점수 차가 작음
    if mapping_input and len(mapping_input.candidate_controls) >= 2:
        scores = sorted(
            (c.similarity_score for c in mapping_input.candidate_controls),
            reverse=True,
        )
        if scores[0] - scores[1] < thresholds.narrow_score_gap:
            reasons.append(ReviewReason.NARROW_SCORE_GAP)

    # 5. 조건부 검토 — 결과 유형
    if output.match_status == MatchStatus.NO_MATCH and thresholds.review_on_no_match:
        reasons.append(ReviewReason.NO_MATCH_RESULT)
    if len(output.mapped_controls) >= 2 and thresholds.review_on_multi_mapping:
        reasons.append(ReviewReason.MULTI_MAPPING_RESULT)
    if len(output.mapped_controls) > MAX_MAPPED_CONTROLS:
        reasons.append(ReviewReason.TOO_MANY_CONTROLS)

    # 6. 조건부 검토 — 검색 점수는 높은데 전부 무관하다고 판단
    if mapping_input and output.match_status == MatchStatus.NO_MATCH:
        top = max(
            (c.similarity_score for c in mapping_input.candidate_controls),
            default=0.0,
        )
        if top >= thresholds.high_similarity:
            reasons.append(ReviewReason.RETRIEVER_LLM_CONFLICT)

    # 7. 조건부 검토 — 판단 근거의 품질
    #
    # 인용이 있으면 인용된 청크를 본다.
    # 인용이 없으면(NO_MATCH) 증적 전체를 본다. "근거가 부실해서 못 고른 것"과
    # "정말 관련이 없는 것"을 사람이 구분해야 하기 때문이다.
    # 길이는 청크 하나가 아니라 전체 합계로 잰다. 긴 문서에 짧은 청크 하나가
    # 섞였다고 검토로 보내면 과민해진다.
    if mapping_input:
        cited = {
            cit.chunk_id
            for mc in output.mapped_controls
            for cit in mc.citations
        }
        targets = (
            [c for c in mapping_input.chunks if c.chunk_id in cited]
            if cited
            else list(mapping_input.chunks)
        )

        # 전처리팀은 OCR 품질 점수를 주지 않는다. source 값 자체를 신호로 쓴다.
        if any(c.source == ChunkSource.OCR for c in targets):
            reasons.append(ReviewReason.OCR_SOURCE)

        total_length = sum(len(c.text.strip()) for c in targets)
        if total_length < thresholds.min_chunk_length:
            reasons.append(ReviewReason.CHUNK_TOO_SHORT)

    return _build(reasons, thresholds)


def _build(reasons: list[ReviewReason], thresholds: Thresholds) -> HumanReview:
    unique = list(dict.fromkeys(reasons))
    if not unique:
        return HumanReview(
            required=False,
            status=ReviewStatus.NOT_REQUIRED,
            threshold_profile=thresholds.profile_name,
        )
    return HumanReview(
        required=True,
        status=ReviewStatus.PENDING,
        reasons=unique,
        threshold_profile=thresholds.profile_name,
    )


# --------------------------------------------------------------------------
# 상태 머신
# --------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[ReviewStatus, set[ReviewStatus]] = {
    ReviewStatus.NOT_REQUIRED: set(),
    ReviewStatus.PENDING: {
        ReviewStatus.APPROVED,
        ReviewStatus.MODIFIED,
        ReviewStatus.REJECTED,
    },
    ReviewStatus.APPROVED: set(),
    ReviewStatus.MODIFIED: set(),
    ReviewStatus.REJECTED: set(),
}


class InvalidTransition(Exception):
    pass


def transition(current: ReviewStatus, target: ReviewStatus) -> ReviewStatus:
    """검토 상태를 바꾼다. 허용되지 않은 전이는 예외를 낸다.

    한 번 확정된 결과(APPROVED/MODIFIED/REJECTED)는 다시 바꾸지 않는다.
    다시 검토해야 하면 새 판단을 만들고 이전 결과는 이력으로 남긴다.
    """
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"{current} -> {target} 는 허용되지 않는다")
    return target


FINAL_STATUSES = {ReviewStatus.APPROVED, ReviewStatus.MODIFIED}


def is_confirmed(review: HumanReview) -> bool:
    """Phase 2로 넘겨도 되는 결과인지.

    REJECTED는 넘기지 않는다. 검토가 필요 없었던 결과는 그대로 확정이다.
    """
    if not review.required:
        return True
    return review.status in FINAL_STATUSES


@dataclass
class RetryPolicy:
    """LLM 호출 실패 처리.

    깨진 JSON은 한 번 더 시도해볼 가치가 있지만, 두 번째도 깨지면 프롬프트나 모델 문제다.
    계속 재시도해도 같은 결과가 나오고 시간만 쓴다.
    """

    max_retries: int = 1
    timeout_seconds: int = 60
    backoff_seconds: float = 2.0
    retry_on: set[ErrorCode] = field(
        default_factory=lambda: {
            ErrorCode.LLM_TIMEOUT,
            ErrorCode.LLM_CONNECTION_FAILED,
            ErrorCode.LLM_SERVER_ERROR,
            ErrorCode.LLM_EMPTY_RESPONSE,
            ErrorCode.LLM_TRUNCATED_RESPONSE,
            ErrorCode.JSON_PARSE_FAILED,
            ErrorCode.SCHEMA_INVALID,
        }
    )

    def should_retry(self, code: ErrorCode, attempt: int) -> bool:
        return attempt <= self.max_retries and code in self.retry_on


DEFAULT_RETRY_POLICY = RetryPolicy()
