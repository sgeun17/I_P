"""Conservative diagnostic for missing-evidence arguments in generated reasons.

This module neither decides compliance nor validates the truth of citations.
It notices an explicit argument that an unavailable record proves a violation.
Quoted independent nonperformance or observed-state comparisons make it abstain.
The caller must retain normal source grounding and semantic review.

Known limits: an exhaustive register, a complete dated event history, or an
indirectly expressed finding may establish nonperformance without using the
source cues recognized here. Such cases can produce a false diagnostic. A cited
comparison can also concern an unrelated subject; abstention is not support for
NOT_MET. Consequently this diagnostic alone must not determine the final result.
"""
import re

VERSION = 'phase2_absence_basis_diagnostic_v1'

_RECORD_ABSENCE = re.compile(
    r'(?:증적|증빙|기록|자료|문서|내역|근거)[^.!?\n;]{0,55}?'
    r'(?:확인되지\s*않|확인할\s*수\s*없|조회되지\s*않|찾을\s*수\s*없|'
    r'제공되지\s*않|제출되지\s*않|미제출|부재|누락|없(?:다|어|으|음|었|는)|부족)'
)
_CAUSAL_LINK = re.compile(
    r'이는|이에\s*따라|이를\s*근거|따라서|그러므로|때문|근거로|인해|'
    r'없으므로|없어서|없어\s|부족하므로|부족하여|않아\s|않으므로'
)
_VIOLATION_CONCLUSION = re.compile(r'위반|미충족|미이행|미준수|충족하지|준수하지|이행하지|수행하지')
_SOURCE_UNCERTAIN = re.compile(r'확인되지|미확인|확인할\s*수\s*없|알\s*수\s*없|판단\s*불가')
_SOURCE_CONDITIONAL = re.compile(r'경우|위반\s*시|미수행\s*시|미준수\s*시|하지\s*않으면')
_DIRECT_SOURCE_ASSERTION = re.compile(
    r'(?:수행|실시|이행|적용|수립|생성|보관|회수|삭제|파기)하지\s*않았|'
    r'(?:미수행|미실시|미이행|미적용|미수립|미준수|위반)\s*(?:사실|상태|으로\s*확인|이\s*확인)|'
    r'(?:결과|판정|상태)\s*[:：]\s*(?:미수행|미실시|미이행|미적용|미수립|미준수|위반|미완료)'
)
_STANDARD = re.compile(r'기준|정책|최소|최대|허용|주기|하여야|해야|이상|이하')
_OBSERVED_STATE = re.compile(
    r'(?:실제|현재|운영|관찰|측정)\s*(?:값|설정|기간|주기|상태|횟수|빈도|보관)|'
    r'설정값|측정값|(?:점검|감사|조사|실사|조회|검토|진단)\s*결과'
)
_STATE_VALUE = re.compile(r'\d|활성|비활성|허용|차단|삭제|보관|사용|공개|매일|매주|매월|주간|월간')


def _independent_cited_basis(quotes):
    """Abstain on plausible independent cited bases; never certify their scope."""
    units = [unit for quote in quotes for unit in re.split(r'[.!?\n;|]', quote)]
    standard_present = any(_STANDARD.search(unit) for unit in units)
    for unit in units:
        if _SOURCE_UNCERTAIN.search(unit) or _SOURCE_CONDITIONAL.search(unit):
            continue
        if _DIRECT_SOURCE_ASSERTION.search(unit):
            return True
        if standard_present and _OBSERVED_STATE.search(unit) and _STATE_VALUE.search(unit):
            return True
    return False


def absence_basis_gap(item, output):
    """Return an evidence-review diagnostic, or None; never return a verdict.

    Only NOT_MET arguments with an explicit evidence-absence premise, a causal
    link, and a violation conclusion are eligible. Generated violation wording
    is never treated as an independent source assertion.
    """
    if output.get('result') != 'NOT_MET' or output.get('error_code'):
        return None
    rule = item.get('evidence_rule') or {}
    boundary = ' '.join(str(rule.get(key, '')) for key in ('not_met', 'unknown'))
    if not any(term in boundary for term in ('미제출', '누락', '확인되지', '판독 불가', '자료 부재')):
        return None
    quotes = [str(citation.get('quote', '')) for citation in output.get('citations', [])]
    if not quotes or any(not quote.strip() for quote in quotes):
        return None  # Existing mechanical validation owns missing citations.
    if _independent_cited_basis(quotes):
        return None
    reason = str(output.get('reason', ''))
    for missing in _RECORD_ABSENCE.finditer(reason):
        argument = reason[missing.start():missing.end()+240]
        suffix = reason[missing.end():missing.end()+240]
        if not _CAUSAL_LINK.search(argument) or not _VIOLATION_CONCLUSION.search(suffix):
            continue
        return {
            'version': VERSION,
            'code': 'ABSENCE_TO_NONCOMPLIANCE_ARGUMENT',
            'scope': 'generated_reason_only',
            'requires_source_review': True,
            'absence_premise': missing.group(0),
            'argument_excerpt': argument,
            'explanation': '제공된 기록의 미확인을 미이행·위반의 근거로 연결하고 있어, 독립적인 실제 미이행 또는 기준 대비 관측값 근거가 있는지 다시 확인해야 합니다.',
            'establishes_noncompliance': False,
            'establishes_compliance': False,
            'safe_as_sole_verdict_rule': False,
        }
    return None
