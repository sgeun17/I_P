"""Bounded recovery of an otherwise valid MATCHED output with no PRIMARY.

Eligibility grants a source review, never authority to choose a control. Only
the independently checked purpose selection can supply the missing role.
"""
from enums import MatchStatus, Relation
from validators import validate


VERSION = 'phase1_missing_primary_recovery_v1'


def missing_primary_eligible(output, validation):
    """Allow exactly E501 caused by zero PRIMARY roles; no other error."""
    return bool(
        output is not None
        and output.match_status == MatchStatus.MATCHED
        and output.mapped_controls
        and all(item.relation == Relation.RELATED for item in output.mapped_controls)
        and validation.schema_valid and validation.control_ids_valid
        and validation.citations_valid
        and len(validation.issues) == 1
        and validation.issues[0].code.value == 'E501'
    )


def _without_roles(output):
    value = output.model_dump(mode='json')
    for item in value['mapped_controls']:
        item.pop('relation')
    return value


def recovery_audit(original, candidate, mapping_input, *, selection_supported):
    """Revalidate both snapshots and prove the only change is the role.

This is also called on audit replay. Unresolved purpose preserves the entire
original output and E501. A successful selection must remove that error by
satisfying the ordinary validator, not by filtering its issues.
"""
    before = validate(original, mapping_input)
    after = validate(candidate, mapping_input)
    if not missing_primary_eligible(original, before):
        raise ValueError('Missing-primary recovery requires only E501 with zero PRIMARY')
    if _without_roles(original) != _without_roles(candidate):
        raise ValueError('Missing-primary recovery may change only existing mapping roles')
    primary = [item.control_id for item in candidate.mapped_controls
               if item.relation == Relation.PRIMARY]
    if selection_supported:
        if not after.passed or len(primary) != 1:
            raise ValueError('Missing-primary recovery must pass full validation after selection')
        status = 'RECOVERED'
    else:
        if candidate.model_dump(mode='json') != original.model_dump(mode='json'):
            raise ValueError('Unresolved purpose must preserve the original missing-primary output')
        if after.model_dump(mode='json') != before.model_dump(mode='json'):
            raise ValueError('Unresolved purpose must preserve the original validation')
        status = 'UNRESOLVED'
    return {
        'version': VERSION,
        'status': status,
        'before_validation': before.model_dump(mode='json'),
        'after_validation': after.model_dump(mode='json'),
        'before_primary_ids': [],
        'after_primary_ids': primary,
        'role_only_change_verified': True,
        'automatic_review_bypass': False,
    }
