"""Share narrow source exclusions between generation and runtime validation.

Excluding a quoted activity substitution is not evidence of document-wide
absence. All source remains visible and an independent negative is mandatory.
"""
import re


def focused_activity_witnesses(control_id, records):
    """Positive activity anchors, NOT an absence/negative classification.

    Full source is still sent to the reviewer. A positive needs both a target
    object and its own activity, not generic approvals or another asset's row.
    Unrecognized/implicit links remain UNCERTAIN, never forced NOT_RELATED.
    This is used only by the guarded, four-control focused review.
    """
    rows = [(r['span_id'], r.get('chunk_id'), '\n'.join(r.get('texts', [r.get('text', '')]))) for r in records]
    cloud = r'클라우드|cloud|AWS|Azure|GCP|VPC|보안\s*그룹|security\s*group|\b(?:EC2|S3|IAM)\b'
    auth = r'인증|비밀번호|패스워드|password|OTP|MFA|2FA|SSO|SAML|FIDO|passkey|PIN|kerberos|SSH.{0,10}key|certificate|다중요소|본인확인|생체|계정\s*잠금|로그인.{0,15}(실패|제한|잠금|차단)'
    contract = r'계약|약정|협정|contract|agreement'
    security = r'보안|정보보호|개인정보보호|개인정보\s*보호|비밀유지|접근통제|목적\s*외|재위탁|반환|파기|누출|유출|감독|손해배상'
    cloud_action = r'암호화|접근\s*(?:통제|제한)|권한\s*(?:제한|검토|관리)|보안\s*설정|포트\s*(?:차단|제한)|MFA|백업|키\s*관리|책임\s*(?:분담|분계)|계약|보안\s*점검|보안\s*요구|보안그룹.{0,20}(?:적용|설정|제한|차단)|보호대책.{0,15}(?:수립|이행|미수립|미이행|계획)'
    negative = r'미실시|미수행|미설정|미적용|미도입|미이행|하지\s*않|하지\s*못'
    cloud_refs = set()
    for _, _, text in rows:
        if re.search(cloud, text, re.I):
            cloud_refs.update(re.findall(r'\b(?:[A-Z]+-\d+(?:-\d+)*)\b', text))
    witnesses = []
    for sid, chunk, text in rows:
        same_chunk = '\n'.join(t for _, c, t in rows if c == chunk) if chunk is not None else text
        ok = False
        if control_id == '2.5.3':
            ok = bool(re.search(auth, text, re.I))
        elif control_id == '2.3.2':
            ok = bool(re.search(contract, same_chunk, re.I) and re.search(security, text, re.I))
            # A contract-title alone cannot stand in for a security clause.
        elif control_id == '2.9.5':
            ok = bool(re.search(r'로그|접속\s*기록|SIEM|\blog\b', same_chunk, re.I)
                      and re.search(r'점검|검토|분석|모니터링|이상징후|review|audit|monitor|analy', text, re.I))
        elif control_id == '2.10.2':
            bound = re.search(cloud, text, re.I) or any(ref in text for ref in cloud_refs)
            # A narrative under a cloud-specific heading may carry its action.
            if not bound and not re.search(r'위험ID\s*:', text):
                bound = any(c == chunk and re.search(cloud, t, re.I)
                            and not re.search(r'위험ID\s*:', t) for _, c, t in rows)
            risk_direction = bool(re.search(r'위험ID\s*:', text) and re.search(r'처리방안\s*:\s*감소(?:\(보호대책 적용\))?\s*$', text))
            ok = bool(bound and not risk_direction and re.search(cloud_action+'|'+negative, text, re.I))
        if ok:
            witnesses.append(sid)
    excluded = {x['span_id'] for x in positive_source_exclusions(control_id, records)}
    body = {r['span_id'] for r in records if r.get('field', 'text') == 'text'}
    return [sid for sid in witnesses if sid not in excluded and sid in body]


def positive_source_exclusions(control_id, records):
    from primary_review import activity_link_findings, _vendor_disclosure_only
    exclusions = []
    for record in records:
        texts = record.get('texts', [record.get('text', '')])
        codes = activity_link_findings(control_id, texts)
        if control_id == '2.5.3' and any(re.search(
                r'MFA|2FA|SSO|SAML|FIDO|passkey|password|생체|다중요소|인증|계정\s*잠금', text, re.I)
                for text in texts):
            # A mixed record may contain both acknowledgement and a real
            # authentication plan/nonperformance. Do not narrow its decoder
            # choices merely because the old keyword guard is incomplete.
            codes = []
        if control_id == '2.3.2' and texts:
            def disclosure(text):
                # A change-notification/disclosure sentence is no more a
                # contractual security clause than the trustee list itself.
                if _vendor_disclosure_only(text):
                    return True
                compact = re.sub(r'\s+', '', text)
                return (bool(re.search(r'수탁자|위탁업무|업무위탁', compact))
                        and bool(re.search(r'공개|별도안내', compact))
                        and not re.search(r'계약|협정|약정|보안|비밀유지|정보보호요구|'
                            r'개인정보보호요구|접근통제|목적외|의무|금지|미명시|미체결', compact))
            if all(disclosure(text) for text in texts):
                codes = ['CONTRACT_SOURCE_IS_DISCLOSURE_ONLY']
        if codes:
            exclusions.append({'span_id': record['span_id'], 'codes': list(codes)})
    return exclusions
