"""Diagnostic boundaries, including counterexamples to automatic downgrades."""
from copy import deepcopy

import pytest

from absence_basis import absence_basis_gap


ITEM = {'question': '적용 기준에 따른 활동을 수행하였는가?', 'evidence_rule': {
    'not_met': '실제 미이행이 직접 확인됨. 단순 미제출만으로 확정하지 않는다.',
    'unknown': '근거 미제출·누락 또는 범위 미확인이면 UNKNOWN이다.'}}


def output(reason, *quotes, result='NOT_MET'):
    return {'result': result, 'reason': reason,
            'citations': [{'chunk_id': str(index), 'quote': quote} for index, quote in enumerate(quotes)]}


def test_generated_absence_to_violation_is_diagnostic_even_without_absence_quote():
    value = output('제공된 자료에서는 주간 이관만 보이고 매일 수행 기록은 확인되지 않았다. 이는 주기 기준 위반으로 판단된다.',
                   '주기: 일 1회', '일자: 2026-09-05 | 내용: 주간 복사본 이관')
    diagnostic = absence_basis_gap(ITEM, value)
    assert diagnostic['code'] == 'ABSENCE_TO_NONCOMPLIANCE_ARGUMENT'
    assert diagnostic['safe_as_sole_verdict_rule'] is False
    assert diagnostic['establishes_noncompliance'] is False


def test_generated_violation_word_never_counts_as_source_violation():
    value = output('검토 기록이 없으므로 해당 의무를 위반하였다.', '검토 기준: 매월')
    assert absence_basis_gap(ITEM, value)


@pytest.mark.parametrize('quote', [
    '담당자가 대상 활동을 수행하지 않았다.',
    '점검 결과: 미수행',
    '해당 의무 미준수 사실을 공식 확인하였다.',
    '처리 상태: 미완료',
])
def test_direct_noncompliance_citation_is_preserved(quote):
    value = output('추가 기록이 확인되지 않았다. 따라서 의무 미이행이다.', quote)
    assert absence_basis_gap(ITEM, value) is None


@pytest.mark.parametrize('observed', [
    '현재 보관기간: 30일',
    '실제 주기: 매주',
    '설정값: 0',
    '감사 결과: 대상 전체 저장소의 기록 0건',
])
def test_observed_state_compared_with_standard_is_preserved(observed):
    value = output('별도 준수 기록이 확인되지 않았다. 따라서 기준 미충족이다.', '최소 기준: 90일', observed)
    assert absence_basis_gap(ITEM, value) is None


def test_conditional_policy_violation_text_is_not_independent_observation():
    value = output('기록이 없으므로 의무 위반이다.', '정책: 위반 시 관리자에게 보고한다.')
    assert absence_basis_gap(ITEM, value)


def test_source_denial_of_nonperformance_does_not_become_positive_basis():
    value = output('기록이 없으므로 의무 위반이다.', '미수행 사실은 확인되지 않았다.')
    assert absence_basis_gap(ITEM, value)


@pytest.mark.parametrize('result', ['MET', 'UNKNOWN'])
def test_other_results_never_flagged(result):
    value = output('기록이 없으므로 의무 위반이다.', '정책: 매월', result=result)
    assert absence_basis_gap(ITEM, value) is None


def test_absence_mention_without_explicit_causal_argument_abstains():
    value = output('기록이 확인되지 않았다. 별도 현장 관측도 있다.', '기준: 매월')
    assert absence_basis_gap(ITEM, value) is None


def test_helper_does_not_mutate_inputs():
    value = output('기록이 없으므로 의무 위반이다.', '기준: 매월')
    before = deepcopy((ITEM, value))
    absence_basis_gap(ITEM, value)
    assert (ITEM, value) == before


def test_complete_dated_history_is_a_known_false_diagnostic_not_a_verdict():
    # A complete register may actually prove a missed deadline. The helper has
    # no semantic model of completeness or deadlines and must not downgrade it.
    value = output('기한 내 처리 기록이 확인되지 않았다. 따라서 기한 위반이다.',
                   '요청일: 2026-01-01 | 마감일: 2026-01-03',
                   '등록부 전체: 2026-01-04 처리 완료')
    diagnostic = absence_basis_gap(ITEM, value)
    assert diagnostic is not None
    assert diagnostic['safe_as_sole_verdict_rule'] is False


def test_unrelated_observed_value_is_a_known_abstention_not_support():
    value = output('활동 기록이 없으므로 의무 위반이다.',
                   '활동 기준: 매월', '다른 자산 현재 설정값: 3')
    # Returning None only abstains. It must never be interpreted as validating
    # NOT_MET or establishing that the cited comparison has the correct scope.
    assert absence_basis_gap(ITEM, value) is None
