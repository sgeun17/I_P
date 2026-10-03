import json
from copy import deepcopy

import httpx
import pytest

from models import LLMMappingOutput
from span_repair import catalog, prepare, apply_patch_response
from validators import validate


def case(mapping_input, good_response):
    data = json.loads(good_response)
    for section in ('candidate_decisions', 'mapped_controls'):
        data[section][0]['citations'][0]['quote'] = 'invented quotation'
    output = LLMMappingOutput.model_validate(data)
    plan, package = prepare(output, mapping_input, validate(output, mapping_input).issues)
    return output, plan, package


def test_catalog_preserves_source_bytes(mapping_input):
    mapping_input.chunks[0].text = '  첫 행\r\n\r\n둘째 행\n끝'
    spans = catalog(mapping_input)
    assert list(spans.values())[0]['quote'] == '  첫 행'
    for span in spans.values():
        chunk = mapping_input.chunk_map()[span['chunk_id']]
        assert chunk.text[span['start']:span['end']] == span['quote']


def test_selection_cannot_change_other_fields(mapping_input, good_response):
    original, plan, package = case(mapping_input, good_response)
    assert 'quote' not in package.output_schema['properties']['2.5.1']['items']
    result = LLMMappingOutput.model_validate_json(apply_patch_response(
        original, mapping_input, plan, '{"2.5.1":["S00000"]}'))
    assert validate(result, mapping_input).passed
    before, after = original.model_dump(mode='json'), result.model_dump(mode='json')
    for section in ('candidate_decisions', 'mapped_controls'):
        for b, a in zip(before[section], after[section]):
            b.pop('citations'); a.pop('citations')
    assert before == after


@pytest.mark.parametrize('raw', ['{}', '[]', '{"2.5.1":[]}', '{"2.5.1":["unknown"]}',
    '{"2.5.1":["S00000","S00000"]}', '{"2.5.1":[0]}', '{"2.5.1":[{}]}',
    '{"2.5.1":["S00000"],"relation":"RELATED"}',
    '{"2.5.1":["S00000"],"2.5.1":["S00001"]}'])
def test_reject_unresolved_or_injected_selection(mapping_input, good_response, raw):
    output, plan, _ = case(mapping_input, good_response)
    with pytest.raises(ValueError):
        apply_patch_response(output, mapping_input, plan, raw)


def test_valid_citations_not_replaced(mapping_input, good_response):
    output, _, _ = case(mapping_input, good_response)
    good = LLMMappingOutput.model_validate_json(good_response).mapped_controls[0].citations[0]
    output.mapped_controls[0].citations.append(good)
    plan, _ = prepare(output, mapping_input, validate(output, mapping_input).issues)
    result = LLMMappingOutput.model_validate_json(apply_patch_response(
        output, mapping_input, plan, '{"2.5.1":["S00001"]}'))
    assert result.mapped_controls[0].citations[0] == good


def test_noncontiguous_lines_become_separate_citations(mapping_input, good_response):
    mapping_input.chunks[0].text = '계정 신청은 승인 후 처리한다.\n중간 행은 생략하지 말 것\n보안책임을 고지한다.'
    output, plan, _ = case(mapping_input, good_response)
    result = LLMMappingOutput.model_validate_json(apply_patch_response(
        output, mapping_input, plan, '{"2.5.1":["S00000","S00002"]}'))
    assert [c.quote for c in result.mapped_controls[0].citations] == [
        '계정 신청은 승인 후 처리한다.', '보안책임을 고지한다.']
    assert validate(result, mapping_input).passed


def test_source_change_and_ambiguous_page(mapping_input, good_response):
    output, plan, _ = case(mapping_input, good_response)
    mapping_input.chunks[0].text = 'source changed'
    with pytest.raises(ValueError):
        apply_patch_response(output, mapping_input, plan, '{"2.5.1":["S00000"]}')
    mapping_input.chunks[0].page_end = 2
    assert all(s['chunk_id'] != mapping_input.chunks[0].chunk_id for s in catalog(mapping_input).values())


@pytest.mark.parametrize('selection', ['valid', 'empty', 'unknown', 'no_spans'])
def test_runner(monkeypatch, mapping_input, good_response, selection):
    import llm_runner
    for key, value in [('SPAN_REPAIR', True)]:
        monkeypatch.setattr(llm_runner, key, value)
    output, _, _ = case(mapping_input, good_response)
    if selection == 'no_spans':
        for chunk in mapping_input.chunks:
            chunk.page_end = chunk.page_start + 1
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        raw = output.model_dump_json() if len(calls) == 1 else json.dumps({'2.5.1': {
            'valid':['S00000'], 'empty':[], 'unknown':['BAD']}[selection]})
        return httpx.Response(200, json={'choices':[{'message':{'content':raw}, 'finish_reason':'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run = llm_runner.run_mapping_llm(mapping_input, model='mock', http_client=client)
    final = LLMMappingOutput.model_validate_json(run.raw_response)
    assert validate(final, mapping_input).passed == (selection == 'valid')
    assert len(calls) == (1 if selection == 'no_spans' else 2)
    assert final.mapped_controls[0].relation == output.mapped_controls[0].relation


def test_irrelevant_span_can_pass_syntax_not_semantics(mapping_input, good_response):
    # Explicit limitation: source membership is NOT semantic relevance.
    mapping_input.chunks[1].text = '구내식당 점심 메뉴는 국수입니다.'
    output, plan, _ = case(mapping_input, good_response)
    result = LLMMappingOutput.model_validate_json(apply_patch_response(
        output, mapping_input, plan, '{"2.5.1":["S00001"]}'))
    assert validate(result, mapping_input).passed
    assert '국수' in result.mapped_controls[0].citations[0].quote


def test_disabled_retry_policy_is_respected(monkeypatch, mapping_input, good_response):
    import llm_runner
    from review_policy import RetryPolicy
    monkeypatch.setattr(llm_runner, 'SPAN_REPAIR', True)
    output, _, _ = case(mapping_input, good_response)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'choices':[{'message':{'content':output.model_dump_json()}, 'finish_reason':'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run = llm_runner.run_mapping_llm(mapping_input, model='mock', http_client=client,
                                        retry_policy=RetryPolicy(max_retries=0))
    assert len(calls) == 1 and run.retry_count == 0


def test_first_prompt_is_identical_when_enabled(monkeypatch, mapping_input):
    import prompts
    before = prompts.build_prompt_package(mapping_input)
    monkeypatch.setattr(prompts, 'SPAN_REPAIR', True)
    monkeypatch.setattr(prompts, 'PROMPT_VERSION', 'phase1_mapping_v0.12-spans')
    after = prompts.build_prompt_package(mapping_input)
    assert (before.system, before.user, before.output_schema) == (after.system, after.user, after.output_schema)
