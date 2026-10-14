"""Trusted caller date, not a document fact or evidence of completion."""
from datetime import date, datetime
import json


def assessment_date_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError('assessment date must be a date, not a datetime')
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError('assessment date must be an ISO date')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('assessment date must use YYYY-MM-DD')
    return value


def date_prompt(value):
    value = assessment_date_value(value)
    if value is None:
        return ''
    return ('\n[Trusted assessment context — not source evidence]\n'
        + json.dumps({'assessment_date': value}, ensure_ascii=False)
        + '\n평가 기준일은 실행자가 전달한 위 날짜다. 원문의 연도·분기·기간과 함께 '
        + '기한 도래 여부를 검토하되, 날짜가 지났다는 사실만으로 실제 수행·미수행을 추정하지 않는다. '
        + '해당 기간·대상 연결이 불명확하면 그 범위를 특정한다. 이 날짜는 인용 근거가 아니다.')
