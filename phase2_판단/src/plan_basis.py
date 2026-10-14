"""Flag an explicit plan-to-execution argument, never decide a verdict.

A planning document may contain actual results, and an allocation may itself
be support. Neither its title nor the mere word 'plan' is a failure condition.
This narrow diagnostic only notices an implementation answer explicitly using
planned work as the reason that actual work was performed. Fresh source review
must resolve it; absence of this diagnostic does not certify implementation.
"""
import re

_PLAN_PREMISE = re.compile(r'계획(?:되어|돼|되었|된|한|하고|으로)[^.!?\n]{0,75}')
_EXECUTION_INFERENCE = re.compile(
    r'(?:이는|이를|따라서|그러므로|때문|이므로)[^.!?\n]{0,100}'
    r'(?:시행|이행|수행|실시)(?:한|된|되었|했)[^.!?\n]{0,40}'
    r'(?:판단|볼\s*수|확인|의미|입증)')


def plan_execution_argument(item, output):
    if (item.get('check_kind') != 'implementation'
            or output.get('result') != 'MET' or output.get('error_code')):
        return None
    reason = str(output.get('reason', ''))
    if not _PLAN_PREMISE.search(reason) or not _EXECUTION_INFERENCE.search(reason):
        return None
    return {
        'version': 'phase2_plan_execution_argument_v1',
        'code': 'PLAN_TO_EXECUTION_ARGUMENT',
        'argument_excerpt': reason[:1000],
        'scope': 'generated_reason_only',
        'requires_source_review': True,
        'safe_as_sole_verdict_rule': False,
        'explanation': '계획된 업무를 실제 시행의 근거로 연결한 설명입니다. 전체 원문에서 같은 업무의 실제 이행 기록과 계획을 구분해 다시 대조해야 합니다.',
    }
