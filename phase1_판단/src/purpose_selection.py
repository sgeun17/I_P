"""Single-authority purpose transport, with no inference or verdict repair.

Only the new wire format is decoded. A candidate ID explicitly selected by
the model represents CLEAR and MAIN_PURPOSE internally; unresolved choices
never select a candidate. Source/semantic validators remain authoritative.
"""
import json
from copy import deepcopy

from purpose_wire import DIRECT, FIELDS, KINDS, flat_output_contract, flat_purpose_schema
from review_response_contract import unique_object

MARKER = 'SINGLE_PURPOSE_SELECTION_V5'
UNRESOLVED = ('AMBIGUOUS', 'INSUFFICIENT')
NONPRIMARY_ROLES = ('SUPPORTING_SECTION', 'INCIDENT_EXAMPLE', 'TOPIC_ONLY', 'NONE', 'UNRESOLVED')
WIRE_FIELDS = tuple('nonprimary_role' if field == 'document_role' else field for field in FIELDS)
ROOT_FIELDS = ('purpose_span_ids', 'candidate_checks', 'purpose_reason', 'purpose_selection')
MATRIX = {
    'RELATED': (DIRECT, ('SUPPORTING_SECTION', 'INCIDENT_EXAMPLE')),
    'NOT_RELATED': (('TOPIC_ONLY', 'NONE'), ('TOPIC_ONLY', 'NONE')),
    'UNCERTAIN': ((*DIRECT, 'UNRESOLVED'), ('SUPPORTING_SECTION', 'INCIDENT_EXAMPLE', 'UNRESOLVED')),
}


def _candidate_ids(expected_ids):
    ids = list(expected_ids)
    if (not ids or any(not isinstance(cid, str) or not cid or cid in UNRESOLVED for cid in ids)
            or len(ids) != len(set(ids))):
        raise ValueError(MARKER + ': invalid expected candidate identities')
    return ids


def build_single_purpose_schema(mapping_input, *, consistent_labels=False):
    """Flat syntax only; no branch can force a label from earlier evidence."""
    from primary_review import source_spans

    ids = sorted(_candidate_ids(c.control_id for c in mapping_input.candidate_controls))
    schema = flat_purpose_schema(mapping_input)
    props = schema['properties']
    props.pop('purpose_status')
    props.pop('primary_control_id')
    props['purpose_selection'] = {'type': 'string', 'enum': [*ids, *UNRESOLVED]}
    schema['required'] = list(props)
    body_ids = [span['span_id'] for span in source_spans(mapping_input) if span['field'] == 'text']
    for cid, candidate in props['candidate_checks']['properties'].items():
        candidate['properties'] = {
            ('nonprimary_role' if field == 'document_role' else field): (
                {'type': 'string', 'enum': list(NONPRIMARY_ROLES)} if field == 'document_role' else spec)
            for field, spec in candidate['properties'].items()}
        candidate['required'] = list(candidate['properties'])
        if cid == '2.3.2':
            # The shared list is also contract_activity.source_span_ids. Its
            # body-only provenance rule is independent of decision and kind;
            # it must never force a verdict from an earlier reference choice.
            candidate['properties']['evidence_span_ids']['items']['enum'] = body_ids
        if consistent_labels:
            # Every branch has identical evidence domains and complete fields.
            # Only the existing decoder's label combinations are constrained;
            # no branch ties relevance to a source ID or to an expected answer.
            branches = []
            for decision, (bases, roles) in MATRIX.items():
                branch = deepcopy(candidate)
                branch['properties']['decision']['enum'] = [decision]
                branch['properties']['relevance_basis']['enum'] = list(bases)
                branch['properties']['nonprimary_role']['enum'] = list(roles)
                branches.append(branch)
            candidate['anyOf'] = branches
    return schema


def single_purpose_output_contract(mapping_input):
    contract = flat_output_contract(mapping_input)
    ids = sorted(_candidate_ids(c.control_id for c in mapping_input.candidate_controls))
    contract['root_fields'] = list(ROOT_FIELDS)
    contract['candidate_fields'] = list(WIRE_FIELDS)
    contract['reference_rule'] = (
        'Use known source span IDs, at most 8 different IDs per list. '
        'For candidates other than 2.3.2, evidence_span_ids may include heading and body; '
        'RELATED requires actual body (field=text) evidence. '
        'For 2.3.2, every evidence_span_ids entry must have field=text, regardless of decision or kind, '
        'because that list is also the contract observation source. '
        'Keep heading context in purpose_evidence_span_ids, which links document opening and the candidate activity.')
    contract['contract_activity'] = (
        'Only candidate 2.3.2 adds contract_activity={kind:...}; its observation shares evidence_span_ids. '
        'Every shared evidence ID must have field=text; do not remove or replace source IDs after generation. '
        'Public trustee rows, disclosure/change notices, section titles and policy revision history '
        'do not together establish a contract-security procedure. Select actual contract-security '
        'activity evidence when claiming it; no relevance decision is prescribed.')
    contract['decision_rules'] = {decision: {'relevance_basis': list(bases), 'nonprimary_role': list(roles)}
                                  for decision, (bases, roles) in MATRIX.items()}
    contract.pop('purpose_rules')
    contract['purpose_selection'] = {
        'allowed_values': [*ids, *UNRESOLVED],
        'candidate_id': 'Choose the one candidate whose activity best represents the whole document. '
                        'It must independently be RELATED with direct, partial, or nonperformance activity '
                        'and linked opening/body evidence. Other candidates may also be RELATED. '
                        'This does not mean full compliance, only one activity, or an explicit purpose clause.',
        'AMBIGUOUS': 'Choose only when independent document purposes remain genuinely equal after comparison.',
        'INSUFFICIENT': 'Choose when source evidence is insufficient for the comparative selection.',
    }
    contract['nonprimary_role'] = (
        'For every candidate, describe its role IF IT IS NOT SELECTED as the representative. '
        'This hypothetical supporting/incident/topic/none/unresolved role does not select or veto the representative. '
        'The same rule applies to the candidate later named by purpose_selection.')
    contract['authority'] = ('purpose_selection alone encodes the representative selection. '
        'Do not generate a second representation of that choice. Each candidate decision, activity basis, '
        'source references and comparison remains independently validated; no verdict is inferred from selection.')
    return contract


def single_purpose_system(base_system):
    """Preserve domain guidance while replacing the old generation contract.

    Exact replacements fail closed if the legacy prompt changes, rather than
    silently leaving contradictory output instructions in this opt-in route.
    """
    replacements = (
        ("목적이 대등하면 AMBIGUOUS, 부족하면 INSUFFICIENT로 둔다. 이때 대표는 null이다.\n"
         "CLEAR일 때만 RELATED 후보 하나를 primary_control_id로 고른다. 검색 순서와 점수를",
         "목적이 대등하면 purpose_selection=AMBIGUOUS, 부족하면 INSUFFICIENT로 둔다.\n"
         "한 후보가 문서 전체를 가장 잘 대표하면 purpose_selection에 그 RELATED 후보 ID를 고른다. 검색 순서와 점수를"),
        ("각 후보에 relevance_basis, document_role, purpose_evidence_span_ids,\n"
         "purpose_comparison을 작성한다. MAIN_PURPOSE는 전체 제목·도입부와 본문이\n"
         "함께 지지하는 후보 하나다. 나머지는 부속 절·사건 예시인지 비교 이유를 적는다.",
         "각 후보에 relevance_basis, nonprimary_role, purpose_evidence_span_ids,\n"
         "purpose_comparison을 작성한다. 대표는 전체 제목·도입부와 본문이 함께 지지하는 후보 하나다.\n"
         "nonprimary_role은 그 후보가 대표로 선택되지 않았다고 가정할 때의 역할이다. 비교 이유를 적는다."),
        ("2.3.2 후보에는 contract_activity로 관찰된 기록 종류와 본문 source_span_ids를 먼저 적는다.",
         "2.3.2 후보의 contract_activity에는 관찰된 기록 종류 kind만 적는다. 본문 근거는 그 후보의 evidence_span_ids에만 적는다."),
        ("모든 후보에 새 필드 네 개를 반드시 출력한다.", "모든 후보에 output_contract의 필드를 빠짐없이 출력한다."),
        ("관련성(decision), 근거 유형(relevance_basis), 문서 역할(document_role)은 서로 다른 축이다.",
         "관련성(decision), 근거 유형(relevance_basis), 대표가 아닐 때의 역할(nonprimary_role)은 서로 다른 축이다."),
    )
    system = base_system
    for before, after in replacements:
        if system.count(before) != 1:
            raise ValueError(MARKER + ': legacy prompt anchor changed')
        system = system.replace(before, after)
    system += '''\n출력 필드와 허용값은 사용자 입력의 output_contract를 따른다. candidate_checks는 모든 후보 ID를
정확히 한 번씩 키로 갖는 객체이며 내부 control_id를 생성하지 않는다. 스키마는 문법만 제한한다.
근거와 후보별 비교 이유를 먼저 기록한 뒤 purpose_selection 한 곳에서 대표 후보 ID 또는
AMBIGUOUS/INSUFFICIENT 중 하나만 선택한다. 다른 필드에서 대표 여부를 다시 판정하지 않는다.
nonprimary_role은 모든 후보에 대해 '이 후보가 대표가 아니라면 어떤 역할인가'라는 가정에 답한다.
마지막에 대표로 선택한 후보도 이 가정에 따라 SUPPORTING_SECTION 또는 INCIDENT_EXAMPLE을 쓴다.
대표 후보는 독립적으로 RELATED이고 DIRECT_ACTIVITY/PARTIAL_ACTIVITY/NONPERFORMANCE_RECORD 중
원문에 맞는 근거 유형이어야 한다. 선택에 맞추려고 관련성 판정이나 근거를 바꾸지 않는다.
대표 선택은 문서 전체의 관련성 비교이지 요건 전체 충족이나 유일한 활동의 존재를 뜻하지 않는다.
다른 후보도 직접 관련되면 RELATED일 수 있다. 원문으로 우열을 정할 수 없으면 미확정을 유지한다.
purpose_span_ids에는 도입부 근거를, 선택 후보의 purpose_evidence_span_ids에는 도입부와 본문 활동을
연결하는 근거를 적는다. field=text인 실제 본문 근거 없이 RELATED로 정하지 않는다.
근거 목록은 판정과 독립적으로 고른다. 제목과 본문 근거가 함께 있어도 그 사실만으로 무관하지 않다.
단, 2.3.2의 evidence_span_ids는 계약 관찰과 공유하므로 모든 ID가 field=text여야 한다.
이 본문 제한은 decision과 kind에 무관하다. 제목 근거는 purpose_evidence_span_ids에 적는다.
수탁자 공개표에 절 제목·변경 공개 안내·처리방침 변경이력을 더해도 계약 보안요구 명시 절차가 되지 않는다.
계약 보안 활동을 관찰했다고 적으려면 그 실제 활동을 기록한 본문을 고른다. 판정값은 원문으로 결정한다.
각 참조 목록은 제공된 서로 다른 ID 최대 8개다. 계약 관찰은 동일 후보의 evidence_span_ids를 공유한다.
계약 관찰 NONE은 관찰 근거 없음이지 원문 전체의 실제 부재를 뜻하지 않는다. 모든 판정은 원문으로 검증한다.'''
    return system


def decode_single_purpose(raw, expected_ids):
    """Decode explicit selection; preserve every independently generated fact.

    This is not compatible with old wire, even when an old contradictory
    response could be mechanically made consistent. No candidate is inferred.
    """
    ids = _candidate_ids(expected_ids)
    value = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != set(ROOT_FIELDS):
        raise ValueError(MARKER + ': invalid root fields; explicit single-selection wire required')
    checks = value['candidate_checks']
    if not isinstance(checks, dict) or set(checks) != set(ids):
        raise ValueError(MARKER + ': cover every candidate exactly once')
    selection = value['purpose_selection']
    if not isinstance(selection, str) or selection not in [*ids, *UNRESOLVED]:
        raise ValueError(MARKER + ': unknown or missing purpose selection')

    def references(items):
        if (not isinstance(items, list) or len(items) > 8 or
                any(not isinstance(item, str) or not item for item in items) or len(items) != len(set(items))):
            raise ValueError(MARKER + ': invalid reference list')

    references(value['purpose_span_ids'])
    if not isinstance(value['purpose_reason'], str) or not value['purpose_reason'].strip():
        raise ValueError(MARKER + ': purpose comparison text required')
    for cid in ids:
        row = checks[cid]
        fields = set(WIRE_FIELDS) | ({'contract_activity'} if cid == '2.3.2' else set())
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError(MARKER + ': invalid candidate fields for ' + cid)
        decision, basis, role = row['decision'], row['relevance_basis'], row['nonprimary_role']
        if (not all(isinstance(item, str) for item in (decision, basis, role)) or
                decision not in MATRIX or basis not in MATRIX[decision][0] or role not in MATRIX[decision][1]):
            raise ValueError(MARKER + ': inconsistent decision/basis/nonprimary role for ' + cid
                + '; observed=' + repr({'decision': decision, 'relevance_basis': basis, 'nonprimary_role': role})
                + '; allowed_combinations=' + repr(MATRIX)
                + '; nonprimary_role is hypothetical even for the selected representative; '
                  'recheck all three labels against the source, do not change decision merely to fit a role')
        for field in ('evidence_span_ids', 'purpose_evidence_span_ids'):
            references(row[field])
        if any(not isinstance(row[field], str) or not row[field].strip() for field in ('reason', 'purpose_comparison')):
            raise ValueError(MARKER + ': candidate comparison text required')
        if cid == '2.3.2':
            observation = row['contract_activity']
            if (not isinstance(observation, dict) or set(observation) != {'kind'} or
                    not isinstance(observation['kind'], str) or observation['kind'] not in KINDS):
                raise ValueError(MARKER + ': shared contract kind required')
    selected = None if selection in UNRESOLVED else selection
    if selected is not None and (checks[selected]['decision'] != 'RELATED' or checks[selected]['relevance_basis'] not in DIRECT):
        raise ValueError(MARKER + ': selected candidate must independently be RELATED with direct activity basis')
    result = deepcopy(value)
    result.pop('purpose_selection')
    result['purpose_status'] = 'CLEAR' if selected is not None else selection
    result['primary_control_id'] = selected
    events = [{'operation': 'DECODE_SINGLE_PURPOSE_SELECTION', 'source_field': 'purpose_selection',
               'source_value': selection, 'purpose_status': result['purpose_status'], 'primary_control_id': selected}]
    for cid in ids:
        row = result['candidate_checks'][cid]
        nonprimary_role = row.pop('nonprimary_role')
        row['document_role'] = 'MAIN_PURPOSE' if cid == selected else nonprimary_role
        events.append({'operation': 'DERIVE_ROLE_FROM_EXPLICIT_SELECTION', 'control_id': cid,
                       'source_selection': selection, 'nonprimary_role': nonprimary_role,
                       'document_role': row['document_role']})
    return json.dumps(result, ensure_ascii=False), events
