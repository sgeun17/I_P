"""Primitive purpose wire shape; semantic authority stays in post-validation.

No verdict-dependent branches constrain previously emitted observations. A
heading included alongside body evidence must not force a negative decision.
Flattening generation constraints does not authorize inconsistent responses.
"""
import json
from copy import deepcopy

DIRECT = ('DIRECT_ACTIVITY', 'PARTIAL_ACTIVITY', 'NONPERFORMANCE_RECORD')
BASES = (*DIRECT, 'TOPIC_ONLY', 'NONE', 'UNRESOLVED')
ROLES = ('MAIN_PURPOSE', 'SUPPORTING_SECTION', 'INCIDENT_EXAMPLE',
         'TOPIC_ONLY', 'NONE', 'UNRESOLVED')
KINDS = ('DISCLOSURE_ONLY', 'CONTRACT_SECURITY_REQUIREMENT',
         'CONTRACT_SECURITY_PROCEDURE', 'CONTRACT_SECURITY_OMISSION',
         'NONE', 'UNRESOLVED')
FIELDS = ('evidence_span_ids', 'purpose_evidence_span_ids', 'reason',
          'purpose_comparison', 'decision', 'relevance_basis', 'document_role')
TOP_FIELDS = ('purpose_span_ids', 'candidate_checks', 'purpose_reason',
              'purpose_status', 'primary_control_id')


def _object(properties):
    return {'type': 'object', 'additionalProperties': False,
            'properties': properties, 'required': list(properties)}


def flat_purpose_schema(mapping_input):
    from primary_review import source_spans
    ids = sorted(candidate.control_id for candidate in mapping_input.candidate_controls)
    spans = [span['span_id'] for span in source_spans(mapping_input)]
    references = {'type': 'array', 'items': {'type': 'string', 'enum': spans},
                  'maxItems': 8, 'uniqueItems': True}
    text = {'type': 'string', 'minLength': 1}
    checks = {}
    for cid in ids:
        fields = {'evidence_span_ids': deepcopy(references),
                  'purpose_evidence_span_ids': deepcopy(references),
                  'reason': dict(text), 'purpose_comparison': dict(text),
                  'decision': {'type': 'string', 'enum': ['RELATED', 'NOT_RELATED', 'UNCERTAIN']},
                  'relevance_basis': {'type': 'string', 'enum': list(BASES)},
                  'document_role': {'type': 'string', 'enum': list(ROLES)}}
        if cid == '2.3.2':
            fields['contract_activity'] = _object({'kind': {'type': 'string', 'enum': list(KINDS)}})
        checks[cid] = _object(fields)
    return _object({'purpose_span_ids': deepcopy(references), 'candidate_checks': _object(checks),
                    'purpose_reason': dict(text),
                    'purpose_status': {'type': 'string', 'enum': ['CLEAR', 'AMBIGUOUS', 'INSUFFICIENT']},
                    'primary_control_id': {'type': ['string', 'null'], 'enum': [*ids, None]}})


def flat_output_contract(mapping_input):
    """Human-readable semantic instructions travel with the source request."""
    return {
        'format': 'candidate_checks is an object keyed by every candidate ID exactly once',
        'candidate_ids': sorted(c.control_id for c in mapping_input.candidate_controls),
        'reference_rule': 'Use known source span IDs, at most 8 different IDs per list. '
                          'evidence_span_ids may include heading and body; RELATED requires actual body (field=text) evidence. '
                          'purpose_evidence_span_ids links document opening and the candidate activity.',
        'candidate_fields': list(FIELDS),
        'decision_rules': {
            'RELATED': {'relevance_basis': list(DIRECT),
                        'document_role': ['MAIN_PURPOSE', 'SUPPORTING_SECTION', 'INCIDENT_EXAMPLE']},
            'NOT_RELATED': {'relevance_basis': ['TOPIC_ONLY', 'NONE'], 'document_role': ['TOPIC_ONLY', 'NONE']},
            'UNCERTAIN': {'relevance_basis': [*DIRECT, 'UNRESOLVED'],
                          'document_role': ['SUPPORTING_SECTION', 'INCIDENT_EXAMPLE', 'UNRESOLVED']},
        },
        'purpose_rules': {
            'CLEAR': 'Exactly one candidate is RELATED and MAIN_PURPOSE; primary_control_id is that ID. '
                     'Its opening and body evidence must support the comparative choice. Other candidates may also be RELATED.',
            'AMBIGUOUS': 'Genuinely equal independent purposes cannot be ranked; primary_control_id=null and no MAIN_PURPOSE.',
            'INSUFFICIENT': 'Source evidence does not support a purpose comparison; primary_control_id=null and no MAIN_PURPOSE.',
        },
        'contract_activity': 'Only candidate 2.3.2 adds contract_activity={kind:...}; its observation uses the same '
                             'evidence_span_ids, and non-NONE contract observations must cite body spans. '
                             'Public trustee lists are DISCLOSURE_ONLY, not contract-security requirements.',
        'contract_kinds': list(KINDS),
        'authority': 'Schema describes syntax only. All semantic/source constraints are validated after generation. '
                     'Do not select a verdict merely to fit earlier references; choose evidence and labels that agree.',
    }


def require_flat_purpose_contract(raw, mapping_input):
    """Validate expanded internal JSON; never change a label, ID or verdict."""
    from primary_review import PurposeCheck, opening_chunk_index, source_spans
    from review_response_contract import unique_object
    value = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != set(TOP_FIELDS):
        raise ValueError('FLAT_PURPOSE_CONTRACT: invalid purpose fields')
    rows = value['candidate_checks']
    expected = {c.control_id for c in mapping_input.candidate_controls}
    if (not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('control_id'), str) for row in rows)
            or len(rows) != len(expected)
            or {row.get('control_id') for row in rows} != expected):
        raise ValueError('FLAT_PURPOSE_CONTRACT: cover every candidate exactly once')
    spans = {span['span_id']: span for span in source_spans(mapping_input)}

    def references(ids):
        if (not isinstance(ids, list) or len(ids) > 8
                or any(not isinstance(sid, str) or sid not in spans for sid in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('FLAT_PURPOSE_CONTRACT: invalid or excessive source IDs')

    references(value['purpose_span_ids'])
    for row in rows:
        required = {'control_id', *FIELDS}
        if row['control_id'] == '2.3.2':
            required.add('contract_activity')
        if set(row) != required:
            raise ValueError('FLAT_PURPOSE_CONTRACT: invalid candidate fields')
        references(row['evidence_span_ids'])
        references(row['purpose_evidence_span_ids'])
        if any(not isinstance(row[key], str) or not row[key].strip()
               for key in ('reason', 'purpose_comparison')):
            raise ValueError('FLAT_PURPOSE_CONTRACT: source comparison text is required')
        decision, basis, role = row['decision'], row['relevance_basis'], row['document_role']
        permitted = {
            'RELATED': (DIRECT, ('MAIN_PURPOSE', 'SUPPORTING_SECTION', 'INCIDENT_EXAMPLE')),
            'NOT_RELATED': (('TOPIC_ONLY', 'NONE'), ('TOPIC_ONLY', 'NONE')),
            'UNCERTAIN': ((*DIRECT, 'UNRESOLVED'), ('SUPPORTING_SECTION', 'INCIDENT_EXAMPLE', 'UNRESOLVED')),
        }
        if (not all(isinstance(v, str) for v in (decision, basis, role))
                or decision not in permitted or basis not in permitted[decision][0] or role not in permitted[decision][1]):
            raise ValueError('FLAT_PURPOSE_CONTRACT: inconsistent decision/basis/role for ' + row['control_id'])
        if decision == 'RELATED' and not any(spans[sid]['field'] == 'text' for sid in row['evidence_span_ids']):
            raise ValueError('FLAT_PURPOSE_CONTRACT: RELATED requires body evidence')
        if row['control_id'] == '2.3.2':
            observation = row['contract_activity']
            if not isinstance(observation, dict) or set(observation) != {'kind', 'source_span_ids'}:
                raise ValueError('FLAT_PURPOSE_CONTRACT: expanded contract observation is required')
            references(observation['source_span_ids'])
    # Pydantic validates all scalar domains/types. Existing contract-activity
    # source/decision validation still runs immediately afterward in run_check.
    check = PurposeCheck.model_validate(value)
    if not check.purpose_reason.strip():
        raise ValueError('FLAT_PURPOSE_CONTRACT: purpose comparison text is required')
    by_id = {candidate.control_id: candidate for candidate in check.candidate_checks}
    main = [candidate.control_id for candidate in check.candidate_checks if candidate.document_role == 'MAIN_PURPOSE']
    if check.purpose_status != 'CLEAR':
        if check.primary_control_id is not None or main:
            raise ValueError('FLAT_PURPOSE_CONTRACT: unresolved purpose cannot select a primary')
        return
    selected = by_id.get(check.primary_control_id)
    if selected is None or main != [selected.control_id] or selected.decision != 'RELATED':
        raise ValueError('FLAT_PURPOSE_CONTRACT: CLEAR requires one matching RELATED MAIN_PURPOSE')
    first_indexes = sorted(c.chunk_index for c in mapping_input.chunks)[:3]
    if not check.purpose_span_ids or not any(spans[sid]['chunk_index'] in first_indexes for sid in check.purpose_span_ids):
        raise ValueError('FLAT_PURPOSE_CONTRACT: CLEAR requires document opening evidence')
    purpose_ids = selected.purpose_evidence_span_ids
    opening_ids = set(purpose_ids) | set(check.purpose_span_ids)
    body_ids = set(selected.evidence_span_ids)
    first_index = opening_chunk_index(mapping_input)
    bound = (bool(purpose_ids)
             and any(spans[sid]['chunk_index'] == first_index for sid in opening_ids)
             and any(spans[sid]['field'] == 'text' for sid in body_ids)
             and (any(spans[sid]['chunk_index'] == first_index for sid in purpose_ids)
                  or any(spans[sid]['field'] == 'text' for sid in set(purpose_ids) & body_ids)))
    if not bound:
        raise ValueError('FLAT_PURPOSE_CONTRACT: CLEAR lacks linked opening and body evidence')
