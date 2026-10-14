"""Conservative opt-in trial: code vetoes, not independently verified semantics."""
import re
from enums import Decision, Relation, ReviewReason, ReviewStatus
from primary_review import contract_security_findings, unsupported_activity_claims, consent_reversal, activity_link_findings
from policy_revision_guard import policy_revision_findings
from review_policy import DEFAULT_THRESHOLDS
from service import detect_injection
from validators import validate

def triggers(output, mapping_input):
    reasons=[]
    if not validate(output,mapping_input).passed:return ['MECHANICAL_VALIDATION_FAILED']
    if not output.mapped_controls:return []
    if detect_injection(mapping_input):reasons.append('INJECTION_SUSPECTED')
    if any(c.decision==Decision.UNCERTAIN for c in output.candidate_decisions):reasons.append('UNCERTAIN_CANDIDATE')
    if any(m.llm_confidence<DEFAULT_THRESHOLDS.low_confidence for m in output.mapped_controls):reasons.append('LOW_MAPPING_CONFIDENCE')
    reasons.extend(b['code'] for b in contract_security_findings(mapping_input,output))
    reasons.extend(b['code'] for b in policy_revision_findings(mapping_input,output))
    chunks=mapping_input.chunk_map()
    whole=[{'text':c.text} for c in mapping_input.chunks]
    candidates={c.control_id:c for c in output.candidate_decisions}
    for m in output.mapped_controls:
        evidence=[chunks[c.chunk_id].text for c in m.citations if c.chunk_id in chunks]
        reasons.extend(activity_link_findings(m.control_id, [c.quote for c in m.citations]))
        # High confidence must not skip a fact check when a report is expanded
        # into approval or a risk-treatment label into a concrete measure.
        for claim in (m.reason, candidates[m.control_id].reason):
            if re.search(r'(보고|평가).{0,35}(승인|의결)', claim):
                reasons.append('COMPOUND_APPROVAL_CLAIM_NEEDS_CHECK')
            if '보호대책' in claim and any('처리방안:' in c.quote and '감소' in c.quote for c in m.citations):
                reasons.append('RISK_COUNTERMEASURE_CLAIM_NEEDS_CHECK')
        for claim in (m.reason,candidates[m.control_id].reason):
            reasons.extend(unsupported_activity_claims(claim,evidence))
            reasons.extend(unsupported_activity_claims(claim,[c.quote for c in m.citations]))
            if consent_reversal(claim,whole):reasons.append('MANDATORY_OPTIONAL_FACT_REVERSED')
            if re.search('주된 목적.{0,12}(어렵|불명|모호)|대표.{0,8}(불명|모호|어렵)',claim):reasons.append('PURPOSE_AMBIGUOUS')
    # Source text/headings only. Unrecognized document families do not get an
    # automatic multi-mapping release in this limited trial.
    if len(output.mapped_controls)>1:
        ordered=sorted(mapping_input.chunks,key=lambda c:c.chunk_index)
        first=re.sub(r'\s+','',(ordered[0].heading or '')+ordered[0].text) if ordered else ''
        opening=''.join((c.heading or '')+c.text for c in ordered[:2])
        opening=re.sub(r'\s+','',opening)
        families=[('개인정보처리방침','3.5.1'),('위험평가','1.2.3'),
                  ('정보보호위원회','1.1.3'),('예산및인력','1.1.6')]
        hints={cid for text,cid in families if text in first} or {cid for text,cid in families if text in opening}
        primary=next((m.control_id for m in output.mapped_controls if m.relation==Relation.PRIMARY),None)
        if len(hints)!=1 or primary not in hints:reasons.append('PURPOSE_SOURCE_ANCHOR_UNRESOLVED')
    return list(dict.fromkeys(reasons))

def adjust_without_second_pass(result,output,mapping_input):
    """Only non-semantic count/gap reasons can be cleared; all vetoes stay."""
    if triggers(output,mapping_input) or not result.validation.passed:return result
    removable={ReviewReason.MULTI_MAPPING_RESULT,ReviewReason.TOO_MANY_CONTROLS,ReviewReason.NARROW_SCORE_GAP}
    kept=[r for r in result.human_review.reasons if r not in removable]
    review=result.human_review.model_copy(update={'required':bool(kept),
        'status':ReviewStatus.PENDING if kept else ReviewStatus.NOT_REQUIRED,'reasons':kept,
        'threshold_profile':result.human_review.threshold_profile+'+selective_trial_v1'})
    return result.model_copy(update={'human_review':review})

def apply_fact_vetoes(result,output,mapping_input):
    """Even a single map or a model's SUPPORTED verdict cannot bypass vetoes."""
    known_prefixes=('MANAGEMENT_','CONTRACT_SECURITY_','MANDATORY_OPTIONAL_','CONSENT_',
                    'TRAINING_TOPIC_','LOG_REVIEW_','RISK_DESCRIPTION_', 'AUTHENTICATION_',
                    'POLICY_REVISION_')
    findings=[r for r in triggers(output,mapping_input) if r.startswith(known_prefixes)]
    if not findings:return result
    review=result.human_review.model_copy(update={'required':True,'status':ReviewStatus.PENDING,
        'reasons':list(dict.fromkeys([*result.human_review.reasons,ReviewReason.UNCERTAIN_DECISION]))})
    return result.model_copy(update={'human_review':review})
