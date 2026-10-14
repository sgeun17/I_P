"""Synthetic citation-domain regressions: no review suppression or invented facts."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from models import LLMMappingOutput
from primary_review import source_spans
from source_claim_review import (_bound_records, _source_quote_range, prepare_source_review,
                                 validate_source_review, VERSION_V4)
from validators import validate_citation


def sample(mapping_input, good_response, text, quote):
    inp = mapping_input.model_copy(deep=True)
    inp.chunks[0].text = text
    out = LLMMappingOutput.model_validate_json(good_response)
    for row in [out.mapped_controls[0], out.candidate_decisions[0]]:
        row.citations[0].quote = quote
    return inp, out


@pytest.mark.parametrize('separator', ['\n', '\r\n', '\t', '   ', '\u00a0'])
def test_validator_accepted_whitespace_is_bound_to_original_source(
        mapping_input, good_response, separator):
    text = '계정은 승인 후' + separator + '생성한다.\n검토 완료라는 주장은 별도 기록이다.'
    inp, out = sample(mapping_input, good_response, text, '계정은 승인 후 생성한다.')
    assert not validate_citation(out.mapped_controls[0].citations[0], inp.chunk_map())
    records = _bound_records(inp, source_spans(inp), out.mapped_controls[0].citations)
    assert records
    clipped = ' '.join(fragment for record in records for fragment in record['texts'])
    assert ' '.join(clipped.split()) == '계정은 승인 후 생성한다.'
    assert '검토 완료' not in clipped
    for record in records:
        for fragment in record['texts']:
            assert fragment in inp.chunk_map()[record['chunk_id']].text


def test_partial_multiline_quote_cannot_import_uncited_prefix_or_tail(mapping_input, good_response):
    text = '미실시 여부와 무관하게 계정을 신청한다.\n승인 뒤 발급한다. 검토를 완료했다.'
    inp, out = sample(mapping_input, good_response, text, '계정을 신청한다. 승인 뒤 발급한다.')
    records = _bound_records(inp, source_spans(inp), out.mapped_controls[0].citations)
    assert records
    fragments = ' '.join(t for record in records for t in record['texts'])
    assert '미실시' not in fragments and '검토를 완료' not in fragments


@pytest.mark.parametrize('quote', ['계정은 승인 후 생성하지 않는다.', '계정 승인 후 생성한다.',
                                  '계정은 승인 후 생성한다!', '계정은 승인 후 삭제한다.'])
def test_words_negation_and_punctuation_are_not_repaired(mapping_input, good_response, quote):
    inp, out = sample(mapping_input, good_response, '계정은 승인 후\n생성한다.', quote)
    assert not _bound_records(inp, source_spans(inp), out.mapped_controls[0].citations)


def test_ambiguous_whitespace_match_stays_unresolved():
    assert _source_quote_range('승인 후\n발급한다. 승인 후\t발급한다.', '승인 후 발급한다.') is None


def test_existing_exact_repeated_quote_path_is_preserved(mapping_input, good_response):
    inp, out = sample(mapping_input, good_response,
                      '계정을 승인한다.\n계정을 승인한다.', '계정을 승인한다.')
    assert len(_bound_records(inp, source_spans(inp), out.mapped_controls[0].citations)) == 2


def test_ambiguous_page_is_not_guessed(mapping_input, good_response):
    inp, out = sample(mapping_input, good_response, '계정은 승인 후\n생성한다.', '계정은 승인 후 생성한다.')
    inp.chunks[0].page_end = 2
    assert not _bound_records(inp, source_spans(inp), out.mapped_controls[0].citations)


@pytest.mark.parametrize('id_only,keyed,robust', [(False, False, False), (True, False, False),
                                               (True, True, False), (True, True, True)])
def test_empty_source_domain_has_valid_schema_and_no_positive_branch(
        mapping_input, good_response, id_only, keyed, robust):
    inp, out = sample(mapping_input, good_response, '계정은 승인 후 생성한다.', '존재하지 않는 문장이다.')
    _, payload, schema = prepare_source_review(inp, out, source_spans(inp), id_only=id_only,
                                              keyed_controls=keyed, robust_contract=robust)
    Draft202012Validator.check_schema(schema)
    def walk(value):
        if isinstance(value, dict):
            if 'enum' in value:
                assert value['enum']
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)
    branches = (schema['properties']['checks']['properties']['2.5.1']['anyOf'] if keyed else
                schema['properties']['checks']['items']['anyOf'])
    for branch in branches:
        props = branch['properties']
        assert props['source_span_ids']['maxItems'] == 0
        assert props['claim_source_span_ids']['maxItems'] == 0
        basis = props.get('activity_assessment', props.get('source_activity_basis'))
        assert basis.get('const') not in ('DIRECT_ACTIVITY', 'PARTIAL_ACTIVITY', 'NONPERFORMANCE_RECORD')
        assert not set(basis.get('enum', [])) & {'DIRECT_ACTIVITY', 'PARTIAL_ACTIVITY', 'NONPERFORMANCE_RECORD'}
        assert props['public_claim_verdict'].get('const') != 'SUPPORTED'


def test_empty_schema_does_not_make_validator_accept_invented_evidence(mapping_input, good_response):
    inp, out = sample(mapping_input, good_response, '계정은 승인 후 생성한다.', '존재하지 않는 문장이다.')
    wire = {'version': VERSION_V4, 'checks': {'2.5.1': {
        'activity_assessment': 'DIRECT_ACTIVITY', 'activity_kind': 'PROCEDURE',
        'source_span_ids': ['P00001'], 'public_claim_verdict': 'SUPPORTED',
        'claim_source_span_ids': ['P00001'], 'unsupported_public_claim_ids': [], 'reason': 'Synthetic'}}}
    with pytest.raises(ValueError):
        validate_source_review(wire, inp, out, source_spans(inp))
