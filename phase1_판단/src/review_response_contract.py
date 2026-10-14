"""Lossless set normalization of evidence references, never verdict repair.

Only documented reference-list fields are sets. Repeated controls, duplicate
JSON keys, unknown IDs and malformed values remain errors in the validators.
The caller retains the original wire response and the normalization events.
"""
import json
from copy import deepcopy

SET_FIELDS = frozenset({'purpose_span_ids', 'evidence_span_ids',
    'purpose_evidence_span_ids', 'contract_activity_source_span_ids',
    'source_span_ids', 'claim_source_span_ids', 'unsupported_public_claim_ids'})

def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate review JSON key: '+key)
        value[key] = item
    return value

def normalize_reference_sets(raw):
    value = json.loads(raw, object_pairs_hook=unique_object)
    changes = []
    def visit(node, path=''):
        if isinstance(node, dict):
            for key, item in node.items():
                location = path+'/'+key
                if key in SET_FIELDS and isinstance(item, list) and all(isinstance(x, str) for x in item):
                    unique = list(dict.fromkeys(item))
                    if unique != item:
                        changes.append({'path': location, 'before_count': len(item),
                                        'after_count': len(unique), 'operation': 'DEDUPLICATE_IDENTICAL_REFERENCE'})
                        node[key] = unique
                else:
                    visit(item, location)
        elif isinstance(node, list):
            for i, item in enumerate(node): visit(item, path+'/'+str(i))
    visit(value)
    return json.dumps(value, ensure_ascii=False), changes

def bound_reference_arrays(schema, limit=8):
    """Decoders need maxItems: uniqueItems alone does not prevent repetition."""
    if isinstance(schema, dict):
        for key, node in schema.get('properties', {}).items():
            if key in SET_FIELDS and node.get('type') == 'array':
                node['maxItems'] = min(limit, node.get('maxItems', limit))
        for node in schema.values(): bound_reference_arrays(node, limit)
    elif isinstance(schema, list):
        for node in schema: bound_reference_arrays(node, limit)
    return schema


def identity_map_to_rows(raw, field, expected):
    """Decode keyed control identity without inventing or merging any row."""
    value = json.loads(raw, object_pairs_hook=unique_object)
    rows = value.get(field) if isinstance(value, dict) else None
    if not isinstance(rows, dict) or set(rows) != set(expected):
        raise ValueError(field+' must be an object with every requested control exactly once')
    result = []
    for cid in expected:
        row = rows[cid]
        if not isinstance(row, dict) or 'control_id' in row:
            raise ValueError('Control identity belongs in the object key only')
        result.append({'control_id':cid, **row})
    value[field] = result
    return json.dumps(value, ensure_ascii=False)


def _single_contract_source_variants(variant):
    """One generated evidence set, not two independently sampled references.

    Only the v9 wire changes. The decoder restores the existing internal
    ContractActivity record so all historical source/semantic guards still run.
    """
    observation = variant['properties'].get('contract_activity')
    if observation is None:
        return [variant]
    variants = []
    for kind_branch in observation['anyOf']:
        item = deepcopy(variant)
        kind = kind_branch['properties']['kind']['const']
        source = kind_branch['properties']['source_span_ids']
        item['properties']['contract_activity'] = {
            'type': 'object', 'additionalProperties': False,
            'required': ['kind'], 'properties': {'kind': {'const': kind}}}
        if kind != 'NONE':
            evidence = item['properties']['evidence_span_ids']
            allowed = [sid for sid in evidence['items']['enum']
                       if sid in source['items']['enum']]
            minimum = max(evidence.get('minItems', 0), source.get('minItems', 0))
            if minimum and not allowed:
                continue
            evidence['minItems'] = minimum
            evidence['items'] = {'type': 'string', 'enum': allowed} if allowed else {'type': 'string'}
            if not allowed:
                evidence['maxItems'] = 0
        variants.append(item)
    return variants


def expand_contract_activity_sources(raw):
    """Decode v9's explicitly shared reference field; never union two claims.

    Calling this on old wire with a separate source list is an error. Callers
    must retain both the original wire and these mechanical derivation events.
    No decision, explanation, ID selection or score is changed here.
    """
    value = json.loads(raw, object_pairs_hook=unique_object)
    rows = value.get('candidate_checks') if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Shared contract-source decoder requires candidate rows')
    events = []
    kinds = {'DISCLOSURE_ONLY', 'CONTRACT_SECURITY_REQUIREMENT',
             'CONTRACT_SECURITY_PROCEDURE', 'CONTRACT_SECURITY_OMISSION',
             'NONE', 'UNRESOLVED'}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid purpose candidate row')
        if row.get('control_id') != '2.3.2':
            if 'contract_activity' in row:
                raise ValueError('Contract activity observation belongs only to 2.3.2')
            continue
        observation = row.get('contract_activity')
        if not isinstance(observation, dict) or set(observation) != {'kind'}:
            raise ValueError('Shared contract activity must contain kind only; use candidate evidence_span_ids')
        kind = observation['kind']
        if not isinstance(kind, str) or kind not in kinds:
            raise ValueError('Unknown shared contract activity kind')
        ids = row.get('evidence_span_ids')
        if not isinstance(ids, list) or any(not isinstance(sid, str) for sid in ids):
            raise ValueError('Shared contract evidence must be a list of source IDs')
        observation['source_span_ids'] = [] if kind == 'NONE' else list(ids)
        events.append({'control_id': '2.3.2',
                       'operation': 'DERIVE_CONTRACT_SOURCES_FROM_CANDIDATE_EVIDENCE',
                       'source_field': 'candidate.evidence_span_ids' if kind != 'NONE' else 'NONE_KIND_EMPTY_SOURCE',
                       'source_span_ids': list(observation['source_span_ids'])})
    return json.dumps(value, ensure_ascii=False), events


def keyed_purpose_schema(schema, ids, *, single_contract_source=False):
    schema = deepcopy(schema)
    for key, definition in schema['$defs'].items():
        if not key.startswith('Candidate') or key == 'CandidateCheck': continue
        if single_contract_source:
            definition['anyOf'] = [expanded for variant in definition['anyOf']
                                   for expanded in _single_contract_source_variants(variant)]
        for variant in definition['anyOf']:
            variant['properties'].pop('control_id')
            variant['required'].remove('control_id')
    for branch in schema['anyOf']:
        checks = branch['properties']['candidate_checks']
        variants = checks['items']['anyOf']
        assert len(variants) == len(ids)
        branch['properties']['candidate_checks'] = {'type':'object','additionalProperties':False,
            'required':list(ids),'properties':dict(zip(ids,variants))}
    return bound_reference_arrays(schema)


def evidence_first_purpose_schema(schema):
    """Order observations before verdicts without changing allowed responses.

    The decoder must not commit to a primary/null branch before emitting the
    source observations and comparative reasons. This is a generation-only
    ordering hint: all branches, source enums and validators remain unchanged.
    Existing wire responses remain valid and are never relabelled here.
    """
    schema = deepcopy(schema)
    root_order = ('purpose_span_ids', 'candidate_checks', 'purpose_reason',
                  'purpose_status', 'primary_control_id')
    candidate_order = ('evidence_span_ids', 'purpose_evidence_span_ids', 'reason',
                       'purpose_comparison', 'contract_activity', 'decision',
                       'relevance_basis', 'document_role')

    def visit(node):
        if isinstance(node, dict):
            props = node.get('properties', {})
            order = (root_order if set(root_order).issubset(props) else
                     candidate_order if {'evidence_span_ids', 'purpose_comparison',
                                         'decision', 'document_role'}.issubset(props)
                     else ())
            if order:
                keys = [key for key in order if key in props]
                keys.extend(key for key in props if key not in keys)
                node['properties'] = {key: props[key] for key in keys}
                if 'required' in node:
                    required = node['required']
                    node['required'] = [key for key in keys if key in required]
                    node['required'].extend(key for key in required if key not in keys)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(schema)
    return schema


def keyed_repair_schema(schema, ids):
    schema = deepcopy(schema)
    rows = {cid:[] for cid in ids}
    for variant in schema['properties']['changes']['items']['anyOf']:
        cid = variant['properties'].pop('control_id')['const']
        variant['required'].remove('control_id')
        rows[cid].append(variant)
    schema['properties']['changes'] = {'type':'object','additionalProperties':False,
        'required':list(ids),'properties':{cid:{'anyOf':variants} for cid,variants in rows.items()}}
    return bound_reference_arrays(schema)
