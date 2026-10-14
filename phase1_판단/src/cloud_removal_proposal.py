"""Narrow eligibility for a cloud-risk-only removal proposal, never a verdict.

This does not establish document-wide absence, remove mappings, or approve a
document. A fresh independent full-source negative judgment remains mandatory.
"""
import re

from enums import Relation
from primary_review import activity_link_findings


_CLOUD = re.compile(r'클라우드|cloud|AWS|Azure|GCP|VPC|보안\s*그룹|security\s*group|'
                    r'네트워크\s*ACL|\b(?:EC2|S3|IAM)\b', re.I)
_RISK_FIELDS = {'위험ID', '자산', '위협시나리오', '위협', '취약점', '위험도', '처리방안'}
_CODE = 'CLOUD_SECURITY_SOURCE_IS_RISK_DIRECTION_ONLY'


def _risk_fields(text):
    fields = [part.split(':', 1) for part in text.replace('：', ':').split('|')]
    if not fields or any(len(part) != 2 for part in fields):
        return None
    values = {re.sub(r'\s+', '', key): value.strip() for key, value in fields}
    if (len(values) != len(fields) or not set(values) <= _RISK_FIELDS
            or not {'자산', '위험도', '처리방안'} <= set(values)
            or not {'위협시나리오', '위협'} & set(values)):
        return None
    return values


def _risk_only(text):
    # Proposal eligibility is stricter than the citation-only guard. Even a
    # risk-table cell may explicitly record a planned or missing activity.
    compact = re.sub(r'\s+', '', text)
    possible_activity = re.search(
        r'계획|예정|완료|미실시|미점검|미검토|미설정|미수행|미적용|미도입|'
        r'미구현|미이행|미완료|하지않|하지못', compact)
    return not possible_activity and _CODE in activity_link_findings('2.10.2', [text])


def may_propose_cloud_removal(mapping_input, output, mapping):
    """Return only proposal eligibility after conservative source checks.

    Exact original citations must be known risk-direction substitutions. Every
    cloud-labelled source row must have that same narrow form; other cloud
    records, measures, plans, nonperformance and ambiguous cross-references
    defeat eligibility. Generic DoA plan approval is not treated as either a
    cloud measure or proof that cloud measures are absent.
    """
    if mapping.control_id != '2.10.2' or mapping.relation == Relation.PRIMARY:
        return False
    if not any(m.model_dump(mode='json') == mapping.model_dump(mode='json')
               for m in output.mapped_controls):
        return False
    if not mapping_input.chunks or any(
            str(getattr(chunk.source, 'value', chunk.source)).lower() != 'parser'
            for chunk in mapping_input.chunks):
        return False
    chunks = mapping_input.chunk_map()
    if not mapping.citations or any(
            citation.chunk_id not in chunks or not citation.quote.strip()
            or citation.quote not in chunks[citation.chunk_id].text
            or not chunks[citation.chunk_id].covers_page(citation.page)
            for citation in mapping.citations):
        return False
    if _CODE not in activity_link_findings('2.10.2', [c.quote for c in mapping.citations]):
        return False

    rows = [(chunk.chunk_id, line.strip()) for chunk in mapping_input.chunks
            for line in ((chunk.heading or '') + '\n' + chunk.text).splitlines()
            if line.strip()]
    cloud_rows = [(chunk_id, text) for chunk_id, text in rows if _CLOUD.search(text)]
    if not cloud_rows or any(not _risk_only(text) for _, text in cloud_rows):
        return False

    # Track the actual source's risk/asset identifiers so a later measure cannot
    # evade this proposal guard merely by omitting the word "cloud".
    identifiers = set()
    for _, text in cloud_rows:
        fields = _risk_fields(text)
        if fields is None:
            return False
        if fields.get('위험ID'):
            identifiers.add(re.sub(r'\s+', '', fields['위험ID']).casefold())
        identifiers.update(m.group(0).casefold() for m in re.finditer(
            r'[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9_-]+', fields['자산']))
    cloud_chunks = {chunk_id for chunk_id, _ in cloud_rows}
    for chunk_id, text in rows:
        compact = re.sub(r'\s+', '', text)
        if any(identifier in compact.casefold() for identifier in identifiers) and not _risk_only(text):
            return False
        # Unlabelled extra material in a cloud-risk chunk could be its plan or
        # measure. Other complete, separately identified risk rows are allowed.
        if chunk_id in cloud_chunks and not _risk_fields(text):
            return False
        if (re.search(r'해당|상기|전술|동\s*위험|이\s*위험|위의', text)
                and re.search(r'대책|조치|계획|설정|접근|권한|암호화|인증|적용|구현', text)):
            return False
        # A detached action field without an object is ambiguous, not evidence
        # of absence. Ordinary narrative DoA approval does not match this form.
        if re.match(r'\s*(?:조치(?:계획|내용)|보호대책|개선계획|이행계획|보안설정)\s*[:：]', text):
            return False
    return True
