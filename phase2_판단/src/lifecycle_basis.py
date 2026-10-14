"""Review an unsupported lifecycle trigger; never infer vendor dates or verdicts."""
import re

def lifecycle_basis_gap(item, output, context):
    question = str(item.get('question', ''))
    if output.get('result') != 'NOT_MET' or output.get('error_code'):
        return None
    if not re.search(r'지원\s*(?:이\s*)?종료|지원\s*중단|단종|\bEOS\b|\bEOL\b', question, re.I):
        return None
    if not re.search(r'지원\s*(?:이\s*)?종료|지원\s*중단|단종|\bEOS\b|\bEOL\b', str(output.get('reason', '')), re.I):
        return None
    # Any supplied lifecycle observation makes us abstain. This neither proves
    # that it is the same asset/date nor approves the negative conclusion.
    text = '\n'.join(str(c.get('heading') or '')+'\n'+str(c.get('text') or '')
                     for c in (context or {}).get('chunks', []))
    if re.search(r'지원\s*(?:이\s*)?종료|지원\s*중단|단종|\bEOS\b|\bEOL\b', text, re.I):
        return None
    return {'version':'phase2_lifecycle_basis_review_v1',
        'code':'LIFECYCLE_TRIGGER_NOT_SOURCE_BOUND', 'scope':'provided_context_only',
        'requires_source_review':True, 'safe_as_sole_verdict_rule':False,
        'argument_excerpt':str(output.get('reason',''))[:1000],
        'explanation':'원문에는 제품 버전·패치 상태가 있으나 지원 종료의 상태·시점 근거가 연결되지 않습니다. 패치 미적용과 지원 종료를 구분하고 적용 조건을 다시 대조해야 합니다.'}
