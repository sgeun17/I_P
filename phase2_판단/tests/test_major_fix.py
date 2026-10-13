import json
from copy import deepcopy
import pytest
from judgment_harness import run_item_judgment
from structured_output_adapter import item_json_schema


@pytest.mark.parametrize('number', range(1, 17))
def test_requested_id_is_constrained_not_rewritten(number):
    item_id = f'3.1.1-Q{number:02d}'
    item = {'item_id':item_id, 'question':'수집 기준이 명시되었는가?', 'evidence_rule':'제공된 원문 확인'}
    schema = item_json_schema()
    before = deepcopy(schema)
    calls = []
    def fake(system, user, model, requested_schema, **kwargs):
        calls.append((user, requested_schema))
        return json.dumps({'item_id':'P2_U_001', 'result':'UNKNOWN',
            'reason':'근거가 부족하여 판정을 확정할 수 없다.', 'reason_codes':[], 'citations':[]})
    result = run_item_judgment(item, {'chunks':[]}, model='mock',
        output_schema=schema, llm_call=fake, sleeper=lambda _:None)
    assert schema == before
    assert calls
    assert all(s['properties']['item_id']['const'] == item_id for _, s in calls)
    assert all(item_id in user for user, _ in calls)
    assert not result.succeeded
    assert 'P2_U_001' in result.raw_response


def test_conflicting_schema_rejected_before_call():
    schema = item_json_schema()
    schema['properties']['item_id']['const'] = 'another-item'
    with pytest.raises(ValueError, match='conflicts'):
        run_item_judgment({'item_id':'3.1.1-Q01'}, {'chunks':[]}, model='mock', output_schema=schema)
