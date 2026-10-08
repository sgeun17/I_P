"""Criteria-owned entry point: explicit assessment, validated policies, shared rules.

No changes to judgment-team code. No guessed freshness ages or extension lists.
"""
import argparse
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parents[1]
JUDGMENT = HERE.parent / 'phase2_판단'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('date must be YYYY-MM-DD')
    return date.fromisoformat(value)


def prepare(catalog, control_id, assessment, policies):
    """Validate before loading model or creating output. Return independent copies."""
    if set(assessment) != {'as_of', 'period_start', 'period_end'}:
        raise ValueError('assessment requires as_of, period_start, period_end only')
    dates = {k: iso_date(v) for k, v in assessment.items()}
    if not dates['period_start'] <= dates['period_end'] <= dates['as_of']:
        raise ValueError('require period_start <= period_end <= as_of')
    controls = {c['control_id']: c for c in catalog['controls']}
    if control_id not in controls:
        raise ValueError('unknown control_id')
    all_ids = {i['item_id'] for c in catalog['controls'] for i in c['items']}
    if not isinstance(policies, dict) or set(policies) - all_ids:
        raise ValueError('policies must use existing item IDs')
    for item_id, policy in policies.items():
        if not isinstance(policy, dict) or not policy or set(policy) - {'freshness', 'allowed_types'}:
            raise ValueError(f'{item_id}: invalid policy keys')
        if 'freshness' in policy:
            f = policy['freshness']
            if not isinstance(f, dict) or set(f) != {'max_age_months', 'date_label'}:
                raise ValueError(f'{item_id}: freshness requires age and date role')
            if type(f['max_age_months']) is not int or not 0 <= f['max_age_months'] <= 1200:
                raise ValueError(f'{item_id}: invalid age (supported range 0..1200 months)')
            if f['date_label'] not in {'document_date', 'performed_date', 'effective_date', 'deadline', 'expiry'}:
                raise ValueError(f'{item_id}: invalid date role')
            if dates['as_of'].year * 12 + dates['as_of'].month - 1 - f['max_age_months'] < 12:
                raise ValueError(f'{item_id}: age exceeds calendar range')
        if 'allowed_types' in policy:
            values = policy['allowed_types']
            if (not isinstance(values, list) or not values
                    or any(not isinstance(v, str) or not re.fullmatch('[a-z0-9]+', v) for v in values)
                    or len(values) != len(set(values))):
                raise ValueError(f'{item_id}: allowed_types must be unique lowercase extensions without dots')
    items = controls[control_id]['items']
    if any(type(i.get('critical')) is not bool for i in items):
        raise ValueError('critical must be explicit for every target item')
    selected = {i['item_id']: deepcopy(policies[i['item_id']]) for i in items if i['item_id'] in policies}
    return {
        'assessment': deepcopy(assessment), 'item_policies': selected,
        'critical_policy': {'mode': 'explicit'},
        'coverage': [{
            'item_id': i['item_id'],
            'freshness_check': 'DETERMINISTIC_MONTH_CUTOFF' if 'freshness' in selected.get(i['item_id'], {}) else 'CONTENT_REVIEW_NO_FIXED_AGE',
            'format_check': 'EXTENSION_ALLOWLIST' if 'allowed_types' in selected.get(i['item_id'], {}) else 'CONTENT_REVIEW_NO_EXTENSION_RESTRICTION',
        } for i in items],
    }


def policy_self_check(prepared, common_rules, transport=None):
    """Supply the same trusted evaluation context on every self-check attempt."""
    assessment = json.dumps(prepared['assessment'], ensure_ascii=False, sort_keys=True)
    def call(system, user, model, schema, **kwargs):
        actual = transport
        if actual is None:
            sys.path[:0] = [str(JUDGMENT), str(JUDGMENT / 'src')]
            from phase1_runtime import call_llm
            actual = call_llm
        supplement = ('\n\n[기준팀 실행 정책 및 평가 범위]\n' + common_rules
                      + '\n평가 범위: ' + assessment
                      + '\n이 정보는 평가 기준이며 증적 사실이 아니다. 기존 문항별 예외를 유지한다.')
        return actual(system + supplement, user, model, schema, **kwargs)
    return call


def route_pending_reviews(result):
    """Use existing unmapped-signal fallback, never invent an official review enum."""
    pending = result['audit']['criteria_execution_policy']['pending_item_reviews']
    if not pending or result.get('output') is None or result['processing_status'] == 'FAILED':
        return
    sys.path[:0] = [str(JUDGMENT), str(JUDGMENT / 'src')]
    from validation.contracts import interface_module
    errors = interface_module('errors')
    signal = 'CRITERIA_PENDING_ITEM_REVIEW'
    signals = result['audit'].setdefault('review_signals', [])
    if signal not in signals:
        signals.append(signal)
    # Retain existing reasons, e.g. injection/OCR/critical flags from the pipeline.
    review = result['output'].setdefault('human_review', {'required': False, 'reasons': []})
    if errors.UNMAPPED_SIGNAL_REASON not in review['reasons']:
        review['reasons'].append(errors.UNMAPPED_SIGNAL_REASON)
    unmapped = review.setdefault('unmapped_signals', [])
    if signal not in unmapped:
        unmapped.append(signal)
    review['required'] = True
    result['output'].update(errors.provisional_fields(review))
    result['processing_status'] = 'REVIEW_REQUIRED'


def execute(payload, control_id, catalog, reasons, controls, kb_hash, prepared, model, *, runner=None, self_check_transport=None):
    base_rules = (JUDGMENT / 'docs/phase2_rules_v0.1.md').read_text(encoding='utf-8')
    common_rules = (HERE / 'docs/runtime_common_rules.md').read_text(encoding='utf-8')
    rules = base_rules + '\n\n' + common_rules
    if runner is None:
        sys.path[:0] = [str(JUDGMENT), str(JUDGMENT / 'src')]
        from validated_pipeline import run_control_judgment
        runner = run_control_judgment
    result = runner(
        payload, control_id, catalog=catalog, reason_catalog=reasons, controls=controls,
        controls_sha256=kb_hash, model=model, critical_policy=prepared['critical_policy'],
        item_policies=prepared['item_policies'], as_of=prepared['assessment']['as_of'],
        organization_context={
            'profile_version': 'criteria-execution-v1',
            'assessment': prepared['assessment'],
            'criteria_policy': common_rules,
        },
        self_check_call=policy_self_check(prepared, common_rules, self_check_transport),
        context_max_chunks=12, context_max_tokens=12000,
    )
    result.setdefault('audit', {})['criteria_execution_policy'] = {
        **deepcopy(prepared), 'version': 'criteria-execution-v2',
        'effective_rules_sha256': sha256(rules.encode('utf-8')).hexdigest(),
        'period_check': 'LLM_SEMANTIC_REVIEW_NOT_DETERMINISTIC',
        'policy_delivery': 'ORGANIZATION_CONTEXT; existing system/checklist rules retain precedence',
        'self_check_policy_delivery': 'SYSTEM_SUPPLEMENT_WITH_SAME_ASSESSMENT',
        'note': 'Original pipeline POLICY_MISSING signals are preserved; no fabricated FRESH/ALLOWED result.',
    }
    review_codes = {'P2_U_NO_TRIGGER_EVENT', 'P2_U_NOT_YET_DUE', 'P2_U_EVIDENCE_CONFLICT'}
    result['audit']['criteria_execution_policy']['pending_item_reviews'] = [
        {'item_id': item['item_id'], 'reason_codes': sorted(review_codes.intersection(item.get('reason_codes', [])))}
        for item in (result.get('output') or {}).get('items', [])
        if item.get('result') == 'UNKNOWN' and review_codes.intersection(item.get('reason_codes', []))
    ]
    route_pending_reviews(result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control-id', required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--period-start', required=True)
    p.add_argument('--period-end', required=True)
    p.add_argument('--policies', help='Existing judgment per-item freshness/allowed_types JSON')
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--input')
    p.add_argument('--model')
    p.add_argument('--out-dir')
    args = p.parse_args()
    catalog = read(HERE / 'full_checklist_draft.json')
    prepared = prepare(catalog, args.control_id, {
        'as_of': args.as_of, 'period_start': args.period_start, 'period_end': args.period_end,
    }, read(args.policies) if args.policies else {})
    if args.prepare_only:
        print(json.dumps(prepared, ensure_ascii=False, indent=2))
        return 0
    if not all((args.input, args.model, args.out_dir)):
        p.error('--input, --model, --out-dir required unless --prepare-only')
    payload = read(args.input)
    reasons = read(HERE / 'full_reason_codes_draft.json')
    kb_path = HERE.parent / 'phase1_검색/controls.json'
    raw = read(kb_path)
    rows = raw if isinstance(raw, list) else raw['controls']
    sys.path[:0] = [str(JUDGMENT), str(JUDGMENT / 'src')]
    from validation.contracts import interface_module
    kb_hash = interface_module('kb_identity').kb_sha256(kb_path.read_bytes())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=False)
    result = execute(payload, args.control_id, catalog, reasons,
                     {r['control_id']: r['control_name'] for r in rows}, kb_hash, prepared, args.model)
    for name, value in [('audit.json', result['audit']), ('output.json', result['output']),
                        ('status.json', {'processing_status': result['processing_status']})]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(result['processing_status'])
    return int(result['processing_status'] == 'FAILED')


if __name__ == '__main__':
    raise SystemExit(main())
