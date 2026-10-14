"""A bounded review of explicit plan-as-execution reasoning, not a verdict rule."""
from copy import deepcopy
import json
import pytest
from plan_basis import plan_execution_argument
from test_compact_pipeline import data
import validated_pipeline as vp

BAD = '1분기 위험평가가 계획되어 있으며, 이는 실제 업무를 시행한 것으로 판단된다.'
SOURCE = '연간 추진 계획\n1분기: 위험평가 및 보호대책 이행계획 수립 (정보보호정책팀)'


def test_explicit_logical_inference_is_diagnostic_only():
    output = {'result': 'MET', 'reason': BAD}
    saved = deepcopy(output)
    diagnostic = plan_execution_argument({'check_kind': 'implementation'}, output)
    assert diagnostic['code'] == 'PLAN_TO_EXECUTION_ARGUMENT'
    assert not diagnostic['safe_as_sole_verdict_rule']
    assert output == saved


@pytest.mark.parametrize('reason', [
    '연간 계획서에 3월 2일 실제 위험평가 완료 결과가 기록되어 있다.',
    '연간 계획에 3분기 점검이 있고, 결과표에서 같은 점검의 9월 완료가 확인된다.',
    '2026년 예산 9.8% 배정 및 전담 인력 운영이 확인된다.',
    '계획은 있으나 실제 실시 기록은 확인되지 않으므로 확정할 수 없다.',
])
def test_no_blanket_plan_document_or_allocation_rejection(reason):
    assert plan_execution_argument({'check_kind': 'implementation'},
        {'result': 'MET', 'reason': reason}) is None


@pytest.mark.parametrize('kind,result', [('procedure', 'MET'),
    ('implementation', 'UNKNOWN'), ('implementation', 'NOT_MET')])
def test_other_kinds_or_results_untouched(kind, result):
    assert plan_execution_argument({'check_kind': kind}, {'result': result, 'reason': BAD}) is None


@pytest.mark.parametrize('candidate_result', ['UNKNOWN', 'MET'])
def test_one_reassessment_with_normal_validation_and_review(candidate_result):
    item, context, reasons, payload = data()
    item, context = deepcopy(item), deepcopy(context)
    item['check_kind'] = 'implementation'
    context['chunks'][0]['text'] = SOURCE
    quote = {'chunk_id': context['chunks'][0]['chunk_id'],
             'page': context['chunks'][0].get('page'), 'quote': SOURCE.split('\n')[1]}
    calls = []
    def generate(system, user, model, schema, **kwargs):
        calls.append(user)
        result = 'MET' if len(calls) == 1 else candidate_result
        if len(calls) > 1:
            assert '[계획과 실제 이행의 구분]' in user
        reason = BAD if result == 'MET' else '일정 계획은 확인되나 같은 업무의 실제 완료 기록은 확인할 수 없습니다.'
        return json.dumps({'item_id': item['item_id'], 'result': result,
            'reason': reason, 'reason_codes': [] if result == 'MET' else ['P2_U_EVIDENCE_INSUFFICIENT'],
            'citations': [quote]}, ensure_ascii=False)
    def review(*args, **kwargs):
        return json.dumps({'verdict': 'SUPPORTED', 'reason': '원문을 대조했습니다.',
            'confidence': .95, 'unsupported_conditions': []}, ensure_ascii=False)
    output, audit = vp.run_validated_item(item, context, model='mock', reason_codes=reasons,
        evidence_id=payload['evidence_id'], version=payload['version'],
        semantic_repair=True, prompt_profile='compact_v2', llm_call=generate, self_check_call=review)
    assert len(calls) == 2
    if candidate_result == 'UNKNOWN':
        assert output['result'] == 'UNKNOWN'
        assert audit['semantic_repair']['status'] == 'ACCEPTED'
    else:
        assert output['result'] == 'MET'  # Repeated faulty inference is retained but reviewed.
        assert audit['semantic_repair']['status'] == 'RETAINED_ORIGINAL'
        assert 'SELF_CHECK_UNCERTAIN' in audit['review_signals']
