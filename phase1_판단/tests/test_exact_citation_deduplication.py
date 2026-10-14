"""Normalize exact duplicate citations only; preserve IDs, verdicts and scores."""
from copy import deepcopy
import json

import pytest

from models import LLMMappingOutput
from semantic_repair import apply_proposal
from source_claim_review import literal_claim_provenance
from span_repair import catalog


def plan_for(inp):
    spans = catalog(inp)
    first = next(iter(spans))
    copied = deepcopy(spans[first])
    spans['ALIAS_SAME_SOURCE'] = copied
    return ['2.5.1'], spans, first


def wire(ids):
    return json.dumps({'changes': [{'control_id': '2.5.1', 'decision': 'RELATED',
        'relevance_basis': 'DIRECT_ACTIVITY', 'reason': 'Synthetic internal source selection.',
        'source_span_ids': ids}]})


def test_distinct_span_ids_same_public_citation_are_losslessly_deduplicated(mapping_input, good_response):
    output = LLMMappingOutput.model_validate_json(good_response)
    before = output.model_dump(mode='json')
    ids, spans, first = plan_for(mapping_input)
    events = []
    proposal, removed = apply_proposal(wire([first, 'ALIAS_SAME_SOURCE']), mapping_input,
        output, (ids, spans), citation_normalizations=events)
    assert output.model_dump(mode='json') == before and removed == []
    mapped = proposal.mapped_controls[0]
    assert len(mapped.citations) == 1
    assert mapped.llm_confidence == output.mapped_controls[0].llm_confidence
    assert mapped.relation == output.mapped_controls[0].relation
    candidate = next(c for c in proposal.candidate_decisions if c.control_id == '2.5.1')
    assert candidate.decision == output.candidate_decisions[0].decision
    assert literal_claim_provenance(mapping_input, mapped, candidate)['verified']
    assert events[0]['operation'] == 'DEDUPLICATE_IDENTICAL_PUBLIC_CITATION'


def test_same_quote_in_different_chunks_is_not_collapsed(mapping_input, good_response):
    inp = mapping_input.model_copy(deep=True)
    inp.chunks[1].text = inp.chunks[0].text
    output = LLMMappingOutput.model_validate_json(good_response)
    spans = catalog(inp)
    chosen = [sid for sid, span in spans.items() if span['quote'] == next(iter(spans.values()))['quote']]
    assert len(chosen) == 2
    proposal, _ = apply_proposal(wire(chosen), inp, output, (['2.5.1'], spans))
    assert len(proposal.mapped_controls[0].citations) == 2


@pytest.mark.parametrize('selection', [['missing'], ['S00000', 'S00000']])
def test_unknown_or_repeated_span_ids_are_still_errors(mapping_input, good_response, selection):
    output = LLMMappingOutput.model_validate_json(good_response)
    with pytest.raises(ValueError, match='Invalid correction source-span IDs'):
        apply_proposal(wire(selection), mapping_input, output, (['2.5.1'], catalog(mapping_input)))
