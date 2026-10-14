"""Missing activity evidence is not a control-compliance verdict."""
import pytest
from models import LLMMappingOutput
from validators import detect_adequacy_judgment


def output_with_reason(good_response, reason):
    output = LLMMappingOutput.model_validate_json(good_response)
    output.candidate_decisions[1].reason = reason
    return output


@pytest.mark.parametrize('text', [
    '증적 원문에 최고책임자의 지정에 대한 내용이 전혀 나타나지 않으며, 관련 요구사항을 충족하거나 수행한 활동이 명시되어 있지 않다.',
    '요구사항을 충족하는 활동이 명시되어 있지 않다.',
    '인증 기준을 충족하는 기록이 제시되어 있지 않다.',
    '통제항목을 충족한 활동이 직접 기록되어 있지 않다.',
    '요구사항을 충족하거나 수행한 활동이 기재되지 않았다.',
])
def test_absence_of_evidence_is_not_noncompliance(good_response, text):
    assert not detect_adequacy_judgment(output_with_reason(good_response, text))


@pytest.mark.parametrize('text', [
    '통제항목의 요구사항을 충족하지 않는다.',
    '해당 인증 기준을 충족한다.',
    '해당 요구사항은 미충족이다.',
    '요구사항을 충족하지 않으며 활동이 명시되어 있지 않다.',
    '요구사항을 충족하지 않는다. 추가 근거가 명시되어 있지 않다.',
    '요구사항을 충족하는 활동이 명시되어 있지 않다. 통제항목을 충족하지 않는다.',
    '요구사항을 충족하는 활동이 명시되어 있지 않다. 인증 기준은 미흡하다.',
    '요구사항을 충족하는 활동이 명시되어 있지 않으므로 부적합하다.',
    '요구사항을 충족하는 활동이 명시되어 있지 않다. 미이행으로 판정한다.',
    '요구사항을 충족하는 활동이 명시되어 있지 않다. 별도의 요구사항을 충족한다.',
])
def test_real_or_additional_verdict_is_not_hidden(good_response, text):
    assert detect_adequacy_judgment(output_with_reason(good_response, text))
