"""Human Review 큐·수정 이력."""

import json

import pytest
from enums import MatchStatus, ProcessingStatus, ReviewStatus
from models import Citation, MappedControl
from pydantic import ValidationError
from review import (
    ReviewDecision,
    apply_review,
    is_confirmed_for_phase2,
    make_queue_item,
    to_goldenset_draft,
    validate_modification,
)
from review_policy import InvalidTransition
from service import build_result

from conftest import GOOD_RESPONSE


def uncertain_response() -> str:
    data = json.loads(GOOD_RESPONSE)
    data["candidate_decisions"][1]["decision"] = "UNCERTAIN"
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def pending(mapping_input, versions):
    """검토 대기 상태의 결과."""
    return build_result(uncertain_response(), mapping_input, versions)


@pytest.fixture
def auto_confirmed(mapping_input, versions):
    """검토가 필요 없는 결과."""
    return build_result(GOOD_RESPONSE, mapping_input, versions)


# --------------------------------------------------------------------------
# 대기열
# --------------------------------------------------------------------------


def test_검토_불필요하면_대기열에_안_올라간다(auto_confirmed, mapping_input):
    assert make_queue_item(auto_confirmed, mapping_input) is None


def test_대기열_항목(pending, mapping_input):
    item = make_queue_item(pending, mapping_input)
    assert item.evidence_id == "E0001"
    assert item.source_file == "계정관리지침서.pdf"
    assert item.has_blocking_reason, "UNCERTAIN(R107)은 무조건 검토다"
    assert item.priority == 1


def test_처리_실패가_제일_급하다(mapping_input, versions):
    failed = build_result("{깨짐", mapping_input, versions)
    assert make_queue_item(failed, mapping_input).priority == 0


def test_조건부_사유만_있으면_우선순위가_낮다(mapping_input, versions):
    data = json.loads(GOOD_RESPONSE)
    data["mapped_controls"][0]["llm_confidence"] = 0.55   # R201만
    result = build_result(json.dumps(data, ensure_ascii=False), mapping_input, versions)
    item = make_queue_item(result, mapping_input)
    assert not item.has_blocking_reason
    assert item.priority == 2


# --------------------------------------------------------------------------
# 승인 / 반려
# --------------------------------------------------------------------------


def test_승인(pending):
    decision = ReviewDecision(status=ReviewStatus.APPROVED, reviewer_id="reviewer-1")
    confirmed, record = apply_review(pending, decision, record_id="R-1")

    assert confirmed.human_review.status == ReviewStatus.APPROVED
    assert confirmed.human_review.reviewer_id == "reviewer-1"
    assert is_confirmed_for_phase2(confirmed)

    assert record.previous_status == ReviewStatus.PENDING
    assert pending.human_review.status == ReviewStatus.PENDING, "원본은 그대로다"


def test_반려는_사유가_필수():
    with pytest.raises(ValidationError):
        ReviewDecision(status=ReviewStatus.REJECTED, reviewer_id="r1")
    ReviewDecision(status=ReviewStatus.REJECTED, reviewer_id="r1", note="증적이 잘못 올라왔다")


def test_반려는_phase2로_안_간다(pending):
    decision = ReviewDecision(
        status=ReviewStatus.REJECTED, reviewer_id="r1", note="증적이 잘못 올라왔다"
    )
    confirmed, _ = apply_review(pending, decision, record_id="R-2")
    assert not is_confirmed_for_phase2(confirmed)


def test_확정된_결과는_다시_바꿀_수_없다(pending):
    approved, _ = apply_review(
        pending, ReviewDecision(status=ReviewStatus.APPROVED, reviewer_id="r1"), record_id="R-1"
    )
    with pytest.raises(InvalidTransition):
        apply_review(
            approved,
            ReviewDecision(status=ReviewStatus.REJECTED, reviewer_id="r2", note="역시 아니다"),
            record_id="R-2",
        )


def test_검토가_필요없던_결과는_승인_대상이_아니다(auto_confirmed):
    with pytest.raises(InvalidTransition):
        apply_review(
            auto_confirmed,
            ReviewDecision(status=ReviewStatus.APPROVED, reviewer_id="r1"),
            record_id="R-1",
        )


# --------------------------------------------------------------------------
# 수정
# --------------------------------------------------------------------------


def modified_control(control_id="2.5.5", name="특수 계정 및 권한 관리", relation="PRIMARY",
                     quote="관리자 권한 부여는 보안팀장의 추가 승인을 받는다.",
                     chunk_id="E0001_v1_c0001", page=2):
    return MappedControl(
        control_id=control_id, control_name=name, relation=relation,
        llm_confidence=0.0, reason="검토자가 수정함",
        citations=[Citation(chunk_id=chunk_id, page=page, quote=quote)],
    )


def test_수정(pending):
    decision = ReviewDecision(
        status=ReviewStatus.MODIFIED, reviewer_id="r1", note="2.5.5가 맞다",
        modified_match_status=MatchStatus.MATCHED,
        modified_controls=[modified_control()],
    )
    confirmed, record = apply_review(pending, decision, record_id="R-3")

    assert [c.control_id for c in confirmed.mapped_controls] == ["2.5.5"]
    assert is_confirmed_for_phase2(confirmed)
    assert [c.control_id for c in pending.mapped_controls] == ["2.5.1"], "원본은 그대로다"
    assert record.decision.modified_controls[0].control_id == "2.5.5"


def test_수정본도_구조_규칙을_지켜야_한다():
    """사람이 고쳐도 Phase 2가 받는 모양은 같아야 한다."""
    with pytest.raises(ValidationError):   # PRIMARY 2개
        ReviewDecision(
            status=ReviewStatus.MODIFIED, reviewer_id="r1",
            modified_match_status=MatchStatus.MATCHED,
            modified_controls=[modified_control(), modified_control(control_id="2.5.6",
                                                                   name="접근권한 검토")],
        )
    with pytest.raises(ValidationError):   # NO_MATCH인데 매핑이 있음
        ReviewDecision(
            status=ReviewStatus.MODIFIED, reviewer_id="r1",
            modified_match_status=MatchStatus.NO_MATCH,
            modified_controls=[modified_control()],
        )


def test_수정본_없이_MODIFIED는_안_된다():
    with pytest.raises(ValidationError):
        ReviewDecision(status=ReviewStatus.MODIFIED, reviewer_id="r1")


def test_사람이_고치면_실패_상태가_풀린다(mapping_input, versions):
    failed = build_result("{깨짐", mapping_input, versions)
    assert failed.processing_status == ProcessingStatus.FAILED

    decision = ReviewDecision(
        status=ReviewStatus.MODIFIED, reviewer_id="r1", note="직접 매핑함",
        modified_match_status=MatchStatus.MATCHED,
        modified_controls=[modified_control()],
    )
    confirmed, _ = apply_review(failed, decision, record_id="R-4")
    assert confirmed.processing_status == ProcessingStatus.COMPLETED
    assert is_confirmed_for_phase2(confirmed)


def test_수정본_인용이_틀리면_경고는_나오되_막지는_않는다(pending, mapping_input):
    decision = ReviewDecision(
        status=ReviewStatus.MODIFIED, reviewer_id="r1", note="수정",
        modified_match_status=MatchStatus.MATCHED,
        modified_controls=[modified_control(quote="원문에 없는 문장")],
    )
    confirmed, _ = apply_review(pending, decision, record_id="R-5")
    warnings = validate_modification(confirmed, mapping_input)
    assert any("E403" in w for w in warnings), "인용이 원문에 없다는 경고"
    assert is_confirmed_for_phase2(confirmed), "경고가 있어도 사람 결정은 유효하다"


# --------------------------------------------------------------------------
# 골든셋 재료
# --------------------------------------------------------------------------


def test_수정_사례를_골든셋_초안으로_뽑는다(pending, mapping_input):
    decision = ReviewDecision(
        status=ReviewStatus.MODIFIED, reviewer_id="r1", note="2.5.5가 맞다",
        modified_match_status=MatchStatus.MATCHED,
        modified_controls=[modified_control()],
    )
    confirmed, _ = apply_review(pending, decision, record_id="R-6")

    draft = to_goldenset_draft(pending, confirmed, mapping_input)
    assert draft["model_said"]["controls"][0]["control_id"] == "2.5.1"
    assert draft["human_said"]["controls"][0]["control_id"] == "2.5.5"
    assert "합성" in draft["warning"]


def test_승인만_한_건은_골든셋_초안이_아니다(pending, mapping_input):
    confirmed, _ = apply_review(
        pending, ReviewDecision(status=ReviewStatus.APPROVED, reviewer_id="r1"), record_id="R-7"
    )
    assert to_goldenset_draft(pending, confirmed, mapping_input) is None
