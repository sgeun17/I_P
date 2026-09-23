"""Human Review 큐와 수정 이력.

### 원칙 — 판단 결과를 덮어쓰지 않는다

사람이 고쳐도 **원본 판단 결과는 그대로 남긴다.** 수정은 별도 레코드로 쌓고,
"지금 유효한 결과"는 원본 + 수정 이력을 합쳐서 계산한다.

덮어쓰면 세 가지를 잃는다.

1. 모델이 무엇을 틀렸는지 — 평가 지표를 다시 계산할 수 없다
2. 사람이 무엇을 고쳤는지 — **이게 골든셋의 가장 좋은 재료다**
3. 언제 왜 바뀌었는지 — 나중에 결과가 달라진 이유를 못 밝힌다

### 이력은 덧붙이기만 한다

`ReviewRecord`는 수정하지 않는다. 검토자가 마음을 바꾸면 새 레코드를 쌓는다.
가장 마지막 레코드가 현재 상태다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from enums import MatchStatus, ProcessingStatus, Relation, ReviewReason, ReviewStatus
from models import Base, MappedControl, MappingInput, Phase1MappingResult, now_kst
from review_policy import ALLOWED_TRANSITIONS, InvalidTransition
from validators import validate

# 무조건 검토(R1xx)가 조건부(R2xx)보다 급하다. 오류가 있는 결과이기 때문이다.
BLOCKING_PREFIX = "R1"


class ReviewDecision(Base):
    """검토자가 내린 결정 하나."""

    status: ReviewStatus
    reviewer_id: str = Field(min_length=1)
    note: Optional[str] = None
    decided_at: datetime = Field(default_factory=now_kst)

    # MODIFIED일 때만 채운다. 사람이 고친 최종 매핑이다.
    modified_match_status: Optional[MatchStatus] = None
    modified_controls: Optional[list[MappedControl]] = None

    @model_validator(mode="after")
    def _check(self) -> "ReviewDecision":
        if self.status not in {ReviewStatus.APPROVED, ReviewStatus.MODIFIED, ReviewStatus.REJECTED}:
            raise ValueError("검토 결정은 APPROVED / MODIFIED / REJECTED 중 하나여야 한다")

        if self.status == ReviewStatus.MODIFIED:
            if self.modified_match_status is None or self.modified_controls is None:
                raise ValueError("MODIFIED면 고친 결과를 같이 줘야 한다")
            _check_shape(self.modified_match_status, self.modified_controls)
        elif self.modified_controls is not None or self.modified_match_status is not None:
            raise ValueError("MODIFIED가 아닌데 수정본이 붙었다")

        if self.status == ReviewStatus.REJECTED and not (self.note or "").strip():
            raise ValueError("반려는 사유를 반드시 남긴다")
        return self


def _check_shape(match_status: MatchStatus, controls: list[MappedControl]) -> None:
    """사람이 고친 결과도 판단 규칙의 **구조**는 지켜야 한다.

    Phase 2가 받는 모양이 사람 손을 탔다고 달라지면 안 되기 때문이다.
    다만 인용 원문 대조는 여기서 막지 않는다. 사람이 최종 권한을 갖고,
    인용 문제는 경고로 기록한다(`review_validation`).
    """
    if match_status == MatchStatus.NO_MATCH and controls:
        raise ValueError("NO_MATCH인데 매핑이 있다")
    if match_status == MatchStatus.MATCHED and not controls:
        raise ValueError("MATCHED인데 매핑이 없다")

    ids = [c.control_id for c in controls]
    if len(ids) != len(set(ids)):
        raise ValueError("같은 통제항목이 두 번 있다")

    primary = [c for c in controls if c.relation == Relation.PRIMARY]
    if match_status == MatchStatus.MATCHED and len(primary) != 1:
        raise ValueError(f"MATCHED면 PRIMARY는 정확히 1개여야 한다 (현재 {len(primary)}개)")


class ReviewRecord(Base):
    """검토 이력 한 줄. **덧붙이기만 하고 고치지 않는다.**"""

    record_id: str = Field(min_length=1)
    evidence_id: str
    version: int = Field(ge=1)
    decision: ReviewDecision
    previous_status: ReviewStatus
    ruleset_version: str
    threshold_profile: Optional[str] = None


class ReviewQueueItem(Base):
    """검토 대기열에 보이는 한 줄.

    화면에 필요한 것만 담는다. 전체 판단 결과는 evidence_id로 따로 읽는다.
    """

    evidence_id: str
    version: int
    source_file: str
    match_status: MatchStatus
    processing_status: ProcessingStatus
    control_ids: list[str] = Field(default_factory=list)
    reasons: list[ReviewReason] = Field(default_factory=list)
    priority: int = Field(ge=0, description="작을수록 급하다")
    has_blocking_reason: bool
    created_at: datetime


def make_queue_item(
    result: Phase1MappingResult, mapping_input: MappingInput
) -> Optional[ReviewQueueItem]:
    """검토가 필요한 결과를 대기열 항목으로 만든다. 필요 없으면 None."""
    if not result.human_review.required:
        return None

    reasons = result.human_review.reasons
    blocking = any(r.value.startswith(BLOCKING_PREFIX) for r in reasons)

    # 우선순위: 처리 실패 → 무조건 검토 → 조건부 검토
    if result.processing_status == ProcessingStatus.FAILED:
        priority = 0
    elif blocking:
        priority = 1
    else:
        priority = 2

    return ReviewQueueItem(
        evidence_id=result.evidence_id,
        version=result.version,
        source_file=mapping_input.chunks[0].source_file,
        match_status=result.match_status,
        processing_status=result.processing_status,
        control_ids=[c.control_id for c in result.mapped_controls],
        reasons=list(reasons),
        priority=priority,
        has_blocking_reason=blocking,
        created_at=result.created_at,
    )


def apply_review(
    result: Phase1MappingResult,
    decision: ReviewDecision,
    *,
    record_id: str,
    mapping_input: Optional[MappingInput] = None,
) -> tuple[Phase1MappingResult, ReviewRecord]:
    """검토 결정을 반영한 **새** 결과와 이력 레코드를 만든다.

    원본 `result`는 건드리지 않는다. 돌려주는 것은 새 객체다.

    mapping_input을 주면 사람이 고친 매핑도 Validator를 돌려 본다.
    **막지는 않는다.** 사람이 최종 권한을 갖되, 인용이 원문과 다르면 기록은 남긴다.
    """
    previous = result.human_review.status
    if decision.status not in ALLOWED_TRANSITIONS[previous]:
        raise InvalidTransition(f"{previous.value} -> {decision.status.value} 는 허용되지 않는다")

    review = result.human_review.model_copy(
        update={
            "status": decision.status,
            "reviewer_id": decision.reviewer_id,
            "reviewed_at": decision.decided_at,
            "review_note": decision.note,
        }
    )

    if decision.status == ReviewStatus.MODIFIED:
        confirmed = result.model_copy(
            update={
                "match_status": decision.modified_match_status,
                "mapped_controls": list(decision.modified_controls or []),
                "human_review": review,
            }
        )
        # 사람이 고친 결과라도 처리 실패 상태는 풀어준다.
        # 사람이 직접 매핑을 채웠으므로 "판단 못 함"이 아니다.
        if confirmed.processing_status == ProcessingStatus.FAILED:
            confirmed = confirmed.model_copy(
                update={"processing_status": ProcessingStatus.COMPLETED}
            )
    else:
        confirmed = result.model_copy(update={"human_review": review})

    record = ReviewRecord(
        record_id=record_id,
        evidence_id=result.evidence_id,
        version=result.version,
        decision=decision,
        previous_status=previous,
        ruleset_version=result.versions.ruleset_version,
        threshold_profile=result.human_review.threshold_profile,
    )
    return confirmed, record


def validate_modification(
    confirmed: Phase1MappingResult, mapping_input: MappingInput
) -> list[str]:
    """사람이 고친 결과를 다시 검증하고 **경고 목록**을 돌려준다.

    막지 않는다. 검토 화면에 "이 인용은 원문에서 못 찾았습니다"를 띄우는 용도다.
    """
    from models import LLMMappedControl, LLMMappingOutput

    output = LLMMappingOutput(
        match_status=confirmed.match_status,
        candidate_decisions=confirmed.candidate_decisions,
        mapped_controls=[
            LLMMappedControl(**c.model_dump(exclude={"similarity_score"}))
            for c in confirmed.mapped_controls
        ],
    )
    validation = validate(output, mapping_input)
    return [f"{i.code.value} {i.message}" for i in validation.issues]


def is_confirmed_for_phase2(result: Phase1MappingResult) -> bool:
    """Phase 2로 넘겨도 되는가.

    REJECTED는 "이 매핑은 틀렸다"는 뜻이므로 넘기지 않는다.
    """
    review = result.human_review
    if not review.required:
        return True
    return review.status in {ReviewStatus.APPROVED, ReviewStatus.MODIFIED}


def to_goldenset_draft(
    original: Phase1MappingResult,
    confirmed: Phase1MappingResult,
    mapping_input: MappingInput,
) -> Optional[dict]:
    """사람이 고친 사례를 골든셋 케이스 초안으로 뽑는다.

    **사람이 고친 것이 곧 정답이다.** 모델이 틀린 사례를 골든셋에 넣으면
    다음 프롬프트·모델이 같은 실수를 반복하는지 회귀로 잡을 수 있다.

    그대로 쓰지 말고 사람이 한 번 더 확인한 뒤 `build_goldenset.py`에 옮긴다.
    증적 본문이 실제 문서라면 **합성으로 바꿔야 한다.**
    """
    if confirmed.human_review.status != ReviewStatus.MODIFIED:
        return None

    return {
        "source": "human_review",
        "evidence_id": original.evidence_id,
        "version": original.version,
        "reviewer_id": confirmed.human_review.reviewer_id,
        "reviewed_at": (
            confirmed.human_review.reviewed_at.isoformat()
            if confirmed.human_review.reviewed_at
            else None
        ),
        "model_said": {
            "match_status": original.match_status.value,
            "controls": [
                {"control_id": c.control_id, "relation": c.relation.value}
                for c in original.mapped_controls
            ],
        },
        "human_said": {
            "match_status": confirmed.match_status.value,
            "controls": [
                {"control_id": c.control_id, "relation": c.relation.value}
                for c in confirmed.mapped_controls
            ],
        },
        "input": mapping_input.model_dump(mode="json"),
        "warning": "증적 본문이 실제 문서라면 합성으로 바꾼 뒤 골든셋에 넣을 것",
    }
