"""Narrow source guards; no inferred relevance verdict or source rewriting.

The linkage predicate is shared with the existing full-source review veto.
The disclosure guard recognizes a closed set of disclosure records and their
context. Unknown text remains eligible for independent source verification.
"""
import re


DIRECT_KINDS = frozenset({'CONTRACT_SECURITY_REQUIREMENT',
                          'CONTRACT_SECURITY_PROCEDURE', 'CONTRACT_SECURITY_OMISSION'})
_CONTRACT = re.compile(r'계약|협정|약정')
_SECURITY = re.compile(
    r'비밀유지|출입통제|접근통제|접근제한|접근권한|계정회수|보안서약|'
    r'목적외.{0,12}(금지|이용|처리)|파기|반환|반납|사고.{0,16}(통지|보고)|'
    r'재위탁.{0,12}(제한|금지)|보안약정|정보보호요구사항|개인정보보호요구사항')
_OBLIGATION = re.compile(
    r'명시|포함|규정|합의|의무|준수|금지|제한|제출|실시|운영|'
    r'해야|하여야|한다|합니다|누락|미반영|미명시|미체결|미작성|하지않|'
    r'(보안요구사항|정보보호요구사항|보안조항|비밀유지조항).{0,8}없')
_POTENTIAL_TERMS = re.compile(
    r'계약|협정|약정|보안|비밀유지|정보보호요구|개인정보보호요구|'
    r'목적외|접근(통제|제한|권한)|계정회수|의무|준수|금지|제한|누락|미반영|미명시|미체결')


def _compact(text):
    return re.sub(r'\s+', '', text)


def contract_security_link(text, *, contract_document=False):
    """Existing narrow linkage heuristic, not semantic approval or a verdict."""
    value = _compact(text)
    return bool((_CONTRACT.search(value) or contract_document)
                and _SECURITY.search(value) and _OBLIGATION.search(value))


def contract_document_files(chunks):
    """Contract context comes from source text/headings, never filenames."""
    return {chunk.source_file for chunk in chunks
            if re.search(r'계약서|협정서|약정서',
                         _compact(chunk.text + '\n' + (chunk.heading or '')))}


def _vendor_row(text):
    return bool(re.match(r'^수탁자[:：]', text)
                and re.search(r'\|위탁업무[:：]', text)
                and re.search(r'\|재위탁[:：]', text)
                and not _POTENTIAL_TERMS.search(text))


def _disclosure_context(text):
    """Recognize only source-local disclosure context, not arbitrary padding."""
    if _POTENTIAL_TERMS.search(text):
        return False
    if re.fullmatch(r'(?:제\d+조)?(?:개인정보)?처리(?:업무)?의?위탁', text):
        return True
    history = re.fullmatch(
        r'버전[:：][^|]+\|시행일[:：]\d{4}-\d{2}-\d{2}\|변경사유및내용[:：](.+)', text)
    if history:
        # A named trustee may occur in parentheses, but the record must still
        # describe only a disclosure or retention-policy revision.
        return all(re.fullmatch(
            r'(?:수탁자(?:추가|변경|삭제)(?:\([^()]*\))?|'
            r'재위탁(?:내용)?(?:추가|변경|삭제)|보유기간(?:근거)?(?:명확화|변경|정정))', part)
            for part in re.split(r'[,，]', history.group(1)))
    sentences = [part for part in re.split(r'[.!?。]', text) if part]
    if not sentences:
        return False
    notice_patterns = (
        r'수탁자(?:또는|및)위탁업무가변경되는경우(?:본|해당)?처리방침을통해'
        r'(?:지체없이|즉시)?공개(?:합니다|한다)',
        r'홍보[·ㆍ]판매권유업무위탁시에는문자[·ㆍ]이메일로별도안내(?:합니다|한다)',
    )
    return all(any(re.fullmatch(pattern, sentence) for pattern in notice_patterns)
               for sentence in sentences)


def disclosure_only_contract_observation(kind, source_texts):
    """Veto a direct-contract claim supported only by padded disclosure rows.

    At least one recognizable trustee row is required. Neutral history alone,
    unknown material, and actual contractual clauses escape this narrow veto;
    that is eligibility for the ordinary independent review, never approval.
    The function neither changes kind/decision nor infers document-wide absence.
    """
    if kind not in DIRECT_KINDS:
        return False
    texts = [_compact(text) for text in source_texts if text.strip()]
    if not texts or any(contract_security_link(text) for text in texts):
        return False
    return (any(_vendor_row(text) for text in texts)
            and all(_vendor_row(text) or _disclosure_context(text) for text in texts))
