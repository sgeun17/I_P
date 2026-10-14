"""Reuse a verified purpose only across citation/explanation-only corrections.

No scope, selection, confidence, decision, source input or model verdict is
invented. New public claims still require a fresh independent source review.
"""
from copy import deepcopy


def _scope(output):
    value = output.model_dump(mode='json')
    for key in ('mapped_controls', 'candidate_decisions'):
        for row in value[key]:
            row.pop('reason', None)
            row.pop('citations', None)
    return value


def reuse_proof(audit, mapping_input, before, after):
    from primary_review import checked_assessment, digest
    from validators import validate
    if (not isinstance(audit, dict) or audit.get('purpose_reuse')
            or audit.get('purpose_contract') != 'SINGLE_PURPOSE_SELECTION_V5'):
        raise ValueError('Purpose reuse requires a non-reused original V5 audit')
    if _scope(before) != _scope(after):
        raise ValueError('Purpose reuse cannot change mapping scope, roles, decisions or scores')
    if not validate(before, mapping_input).passed or not validate(after, mapping_input).passed:
        raise ValueError('Purpose reuse requires two mechanically valid outputs')
    checked = checked_assessment(audit, mapping_input, before)
    if (not checked or not checked.get('primary_selection_supported')
            or checked.get('mapped_conflicts')
            or any(b.get('code', '').startswith(('DOCUMENT_', 'PURPOSE_'))
                   for b in checked.get('semantic_blockers', []))):
        raise ValueError('Purpose reuse requires independently supported unchanged purpose')
    return {'version': 'phase1_explanation_scope_v1',
            'scope': 'EXPLANATIONS_AND_CITATIONS_ONLY',
            'input_sha256': digest(mapping_input),
            'before_output': before.model_dump(mode='json'),
            'before_output_sha256': digest(before),
            'after_output_sha256': digest(after),
            'prior_audit': deepcopy(audit),
            'purpose_model_calls': 0,
            'fresh_source_review_required': True}


def verify_reuse(proof, mapping_input, output):
    from models import LLMMappingOutput
    if not isinstance(proof, dict):
        raise ValueError('Invalid purpose reuse proof')
    before = LLMMappingOutput.model_validate(proof.get('before_output'))
    expected = reuse_proof(proof.get('prior_audit'), mapping_input, before, output)
    if proof != expected:
        raise ValueError('Purpose reuse proof differs from revalidated immutable scope')
    return proof['prior_audit']
