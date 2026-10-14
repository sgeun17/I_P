"""Do not confuse the source's own nonperformance record with our verdict."""
import pytest

from models import LLMMappingOutput
from validators import detect_adequacy_judgment, validate


def fixture(mapping_input, good_response):
    inp = mapping_input.model_copy(deep=True)
    inp.chunks[0].text = '2026년 운영현황 미이행 2건에 대한 완료 지시를 의결했다.'
    out = LLMMappingOutput.model_validate_json(good_response)
    for row in (out.candidate_decisions[0], out.mapped_controls[0]):
        row.citations[0].quote = inp.chunks[0].text
        row.reason = '원문 기록: ' + row.citations[0].quote
    return inp, out


def test_exact_source_nonperformance_is_attributed_not_own_verdict(mapping_input, good_response):
    inp, out = fixture(mapping_input, good_response)
    assert not detect_adequacy_judgment(out, inp)
    assert validate(out, inp).passed
    assert detect_adequacy_judgment(out)  # no source means no exception


@pytest.mark.parametrize('mutation', ['added_verdict', 'fabricated_quote', 'changed_negation',
    'wrong_page', 'unknown_chunk', 'no_citation', 'prefix_only', 'source_not_parser'])
def test_unverified_or_generated_judgments_still_block(mapping_input, good_response, mutation):
    inp, out = fixture(mapping_input, good_response)
    for row in (out.candidate_decisions[0], out.mapped_controls[0]):
        if mutation == 'added_verdict':
            row.reason += '\n따라서 통제항목 미이행으로 판정한다.'
        elif mutation == 'fabricated_quote':
            row.citations[0].quote = '인증 기준 위반사항이 있고 통제항목 미이행이다.'
            row.reason = '원문 기록: ' + row.citations[0].quote
        elif mutation == 'changed_negation':
            row.citations[0].quote = inp.chunks[0].text.replace('의결했다', '의결하지 않았다')
            row.reason = '원문 기록: ' + row.citations[0].quote
        elif mutation == 'wrong_page':
            row.citations[0].page = 99
        elif mutation == 'unknown_chunk':
            row.citations[0].chunk_id = 'E0001_v1_c0099'
        elif mutation == 'no_citation':
            row.citations = []
        elif mutation == 'prefix_only':
            row.reason = '원문 기록: 통제항목 미이행으로 판정한다.'
    if mutation == 'source_not_parser':
        inp.chunks[0].source = 'llm'
    assert len(detect_adequacy_judgment(out, inp)) == 2
    assert not validate(out, inp).passed


def test_one_verified_row_does_not_hide_other_rows_verdict(mapping_input, good_response):
    inp, out = fixture(mapping_input, good_response)
    out.candidate_decisions[1].reason = '해당 통제항목은 미이행 상태이므로 부적합하다.'
    issues = detect_adequacy_judgment(out, inp)
    assert len(issues) == 1 and issues[0].control_id == '2.5.6'


def test_entire_record_must_match_all_citations_in_order(mapping_input, good_response):
    inp, out = fixture(mapping_input, good_response)
    for row in (out.candidate_decisions[0], out.mapped_controls[0]):
        row.citations.append(row.citations[0].model_copy(update={'chunk_id': inp.chunks[1].chunk_id,
            'page': 2, 'quote': inp.chunks[1].text}))
    assert len(detect_adequacy_judgment(out, inp)) == 2
