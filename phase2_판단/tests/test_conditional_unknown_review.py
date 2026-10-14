"""Event absence is not an automatic pass or an automatic bad UNKNOWN."""
import json
import pytest
from test_compact_pipeline import data
import validated_pipeline as vp


@pytest.mark.parametrize('verdict', ['UNSUPPORTED', 'CONFLICT'])
def test_disputed_unknown_keeps_reason_and_full_review_in_audit(verdict):
    item, context, reasons, payload = data()
    original_reason = '신청 기록은 확인되지만 해지 처리 사건과 실제 처리일을 연결할 수 없습니다.'
    review_reason = 'UNKNOWN이 적절하지만 적용 사건이 없으므로 UNKNOWN은 부적절하다.'
    def generate(*args, **kwargs):
        return json.dumps({'item_id': item['item_id'], 'result': 'UNKNOWN',
            'reason': original_reason, 'reason_codes': ['P2_U_EVIDENCE_INSUFFICIENT'],
            'citations': []}, ensure_ascii=False)
    def review(system, user, model, schema, **kwargs):
        assert '[UNKNOWN와 적용 조건의 검토]' in system
        assert '어떤 UNKNOWN도 자동 지지하지' in system
        return json.dumps({'verdict': verdict, 'reason': review_reason, 'confidence': .8,
            'unsupported_conditions': ['적용 사건 미확인']}, ensure_ascii=False)
    output, audit = vp.run_validated_item(item, context, model='mock', reason_codes=reasons,
        evidence_id=payload['evidence_id'], version=payload['version'],
        prompt_profile='compact_v2', llm_call=generate, self_check_call=review)
    assert output['result'] == 'UNKNOWN'
    assert output['reason'].startswith(original_reason)
    assert '해석 이견' in output['reason']
    assert review_reason not in output['reason']
    assert audit['self_check']['reason'] == review_reason
    assert audit['self_check']['verdict'] == verdict
    assert 'SELF_CHECK_' + verdict in audit['review_signals']
    assert audit['review_hold']['prior_output']['reason'] == original_reason
