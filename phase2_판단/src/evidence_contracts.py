"""Question-derived focus and narrow negative evidence-binding checks.

No expected labels, fixed dates, scores or secrets are generated here. Focus
rows are copied from supplied context, not new evidence. A missing guard is
not positive proof. Unrecognised document formats stay with normal reviewers.
"""
import re

VERSION = 'phase2_question_evidence_binding_v1_2_order_facts'

def _compact(text):
    return re.sub(r'\s+', '', str(text))

def _education_target(question):
    q = _compact(question)
    if '교육' not in q:
        return None
    if re.search(r'신규.*(?:채용|입사|임직원)|신입|채용임직원', q):
        return ('신규 채용 임직원', re.compile(r'신규\s*입사|신입|신규\s*채용|채용\s*임직원'))
    if '외부자' in q and re.search(r'신규|업무시작', q):
        return ('신규 외부자', re.compile(r'신규\s*외부자|외부자\s*신규'))
    if re.search(r'직무.*교육|전문교육', q):
        return ('직무 전문교육', re.compile(r'직무\s*전문\s*교육|직무별\s*교육|전문\s*교육'))
    return None

def _crypto_action(item):
    q = _compact(item.get('question', ''))
    if item.get('check_kind') not in {'implementation', 'record'} or '암호키' not in q:
        return None
    if '폐기' in q and item.get('check_kind') == 'implementation':
        return '키 폐기 사건의 실제 대상·삭제 방법·결과'
    if '복구' in q and '사건' in q and item.get('check_kind') == 'record':
        return '키 복구 사건의 실제 승인·수행 및 결과'
    if item.get('check_kind') != 'implementation':
        return None
    if '배포' in q or '복호화권한' in q:
        return '배포·복호화 권한 부여의 실제 대상 및 보호방식'
    if '생성' in q:
        return '키 생성 사건의 실제 생성방법 및 적용 통제'
    return None

def question_focus(item, context):
    """Provide explicit task axes and matching source rows, never a verdict."""
    q = _compact(item.get('question', ''))
    target = _education_target(q)
    plan = '교육' in q and '계획' in q and item.get('check_kind') == 'procedure'
    crypto = _crypto_action(item)
    if not (target or plan or crypto):
        return None
    rows = []
    for chunk in (context or {}).get('chunks', []):
        heading = str(chunk.get('heading') or '')
        for line in str(chunk.get('text', '')).splitlines():
            match = (target and target[1].search(line)) or (plan and '계획' in heading) or (
                crypto and (re.search(r'키ID\s*[:：]|생성\s*[:：]|이용.?배포\s*[:：]|배포일|생성방법|복호화', line)))
            if match:
                rows.append({'chunk_id': chunk['chunk_id'], 'heading': heading, 'source_line': line})
    return {'version': VERSION, 'question': item.get('question'),
            'requested_subject': target[0] if target else ('암호키' if crypto else '연간 교육계획'),
            'requested_activity': crypto or ('계획 수립·내용 정의' if plan else '해당 교육 과정의 실제 이행'),
            'requested_time_relation': ('실제 교육 완료가 업무 시작보다 앞섬; 계획상의 시점만으로 확정하지 않음'
                                        if target and '업무시작전' in q else None),
            'matching_source_rows': rows,
            'event_order_observations': _prework_order_observations(item, context, target),
            'scope': 'FOCUS_HINT_NOT_VERDICT; same event and all evidence_rule conditions still require checking'}


def _prework_order_observations(item, context, target):
    """Copy and compare only same-row source fields; NOT a MET decision."""
    if not target or target[0] != '신규 채용 임직원' or '업무시작전' not in _compact(item.get('question', '')):
        return []
    from datetime import datetime
    date = r'(20\d{2}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)'
    completion = re.compile(r'교육\s*완료일(?:시)?\s*[:：]\s*' + date)
    start = re.compile(r'업무\s*시작일(?:시)?\s*[:：]\s*' + date)
    facts = []
    for chunk in (context or {}).get('chunks', []):
        heading = str(chunk.get('heading') or '')
        if '계획' in heading and not re.search(r'결과|이행|시행|완료|실제', heading):
            continue
        for line in str(chunk.get('text', '')).splitlines():
            course = re.search(r'과정\s*[:：]\s*([^|]+)', line)
            if not target[1].search(course.group(1) if course else line):
                continue
            if re.search(r'예정|계획|하여야|해야|하도록|미완료|완료하지\s*않', line):
                continue
            done, work = completion.search(line), start.search(line)
            if done and work:
                try:
                    done_value, work_value = done.group(1), work.group(1)
                    done_time, work_time = datetime.fromisoformat(done_value), datetime.fromisoformat(work_value)
                except ValueError:
                    continue
                if done_time.date() < work_time.date():
                    order = 'COMPLETION_DATE_PRECEDES_WORK_DATE'
                elif len(done_value) > 10 and len(work_value) > 10:
                    order = 'COMPLETION_PRECEDES_WORK' if done_time < work_time else 'COMPLETION_NOT_BEFORE_WORK'
                else:
                    order = 'TIME_ORDER_NOT_ESTABLISHED_BY_DATE_ONLY'
                facts.append({'chunk_id':chunk['chunk_id'], 'source_line':line,
                    'completion_field':done_value, 'work_start_field':work_value, 'observed_relation':order})
            elif (re.search(r'완료\s*시점\s*[:：].*업무\s*시작\s*(?:전|이전)', line)
                  and re.search(r'이수자\s*[:：]\s*[1-9]\d*', line)):
                facts.append({'chunk_id':chunk['chunk_id'], 'source_line':line,
                              'observed_relation':'SOURCE_STATES_COMPLETION_BEFORE_WORK'})
    return facts

def _crypto_source_kind(chunk):
    text = str(chunk.get('text', ''))
    # Explicit event/method fields make this NOT a policy-only source, even if
    # a policy heading is attached. They still need normal semantic review.
    event = re.search(r'생성방법\s*[:：]|입회자\s*[:：]|배포일\s*[:：]|권한\s*부여일\s*[:：]|'
                      r'배포\s*완료|권한\s*부여\s*완료|생성\s*완료|생성하였|배포하였|'
                      r'(?:폐기일|파기일|복구일|복구방법|파기방법)\s*[:：]|'
                      r'(?:폐기|파기|복구)\s*(?:완료|하였|했)', text)
    if event:
        return 'event_or_unclassified'
    stages = re.findall(r'^(?:생성|이용.?배포|보관|변경|복구|파기)\s*[:：]', text, re.M)
    if len(stages) >= 3 and not re.search(r'키ID\s*[:：]', text):
        return 'policy'
    if re.search(r'키ID\s*[:：]', text) and re.search(r'생성일\s*[:：]|교체\s*예정일\s*[:：]|보관\s*위치\s*[:：]', text):
        return 'inventory'
    return 'event_or_unclassified'


def _prework_timing_gap(item, output, context, target):
    """Hold the observed plan + aggregate completion table without event order.

    Accepting a supported evidence form here only avoids this narrow veto;
    ordinary semantic review still owns scope, events and the final verdict.
    Different people/dates are never stitched together. Same-date-only fields
    cannot establish before/after. Unrecognised formats are not classified.
    """
    q = _compact(item.get('question', ''))
    if target[0] != '신규 채용 임직원' or '업무시작전' not in q:
        return None
    quotes = [str(c.get('quote', '')) for c in output.get('citations', [])]
    def same_course(line):
        course = re.search(r'과정\s*[:：]\s*([^|]+)', line)
        return bool(target[1].search(course.group(1) if course else line))
    lines = [line for quote in quotes for line in quote.splitlines() if same_course(line)]
    by_id = {c['chunk_id']: c for c in (context or {}).get('chunks', [])}
    event_lines = []
    for citation in output.get('citations', []):
        chunk = by_id.get(citation.get('chunk_id'), {})
        heading = str(chunk.get('heading') or '')
        if '계획' in heading and not re.search(r'결과|이행|시행|완료|실제', heading):
            continue
        for line in str(citation.get('quote', '')).splitlines():
            if same_course(line) and not (re.search(r'시기\s*[:：]|주요\s*내용\s*[:：]', line)
                                              and not re.search(r'이수자\s*[:：]', line)):
                event_lines.append(line)
    table_rows = [line for line in lines if re.search(r'과정\s*[:：]', line)]
    aggregate = any(re.search(r'이수자\s*[:：]\s*[1-9]\d*', line) for line in table_rows)
    if not aggregate:
        return None  # Other formats remain under normal semantic review.
    from datetime import datetime
    field_date = r'(20\d{2}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)'
    completion_date = re.compile(r'교육\s*완료일(?:시)?\s*[:：]\s*' + field_date)
    work_date = re.compile(r'업무\s*시작일(?:시)?\s*[:：]\s*' + field_date)
    future = re.compile(r'예정|계획|하여야|해야|하도록|미완료|완료하지\s*않|이수하지\s*않')
    before = re.compile(r'업무\s*시작\s*(?:전|이전)|업무\s*개시\s*(?:전|이전)')
    completed = re.compile(r'교육\s*(?:이수\s*)?완료|이수\s*완료|교육을.{0,12}완료(?:했다|하였)')
    for line in event_lines:
        if future.search(line):
            continue
        # A direct completed-event statement or a recorded completion point
        # may express order without timestamps. A policy heading cannot.
        if before.search(line) and completed.search(line):
            return None
        if re.search(r'완료\s*(?:시점|순서)\s*[:：].*업무\s*시작\s*(?:전|이전)', line) and re.search(r'이수자\s*[:：]\s*[1-9]\d*', line):
            return None
        completion = completion_date.search(line)
        work = work_date.search(line)
        if completion and work:
            try:
                finish_value, start_value = completion.group(1), work.group(1)
                finish, start = datetime.fromisoformat(finish_value), datetime.fromisoformat(start_value)
            except ValueError:
                continue
            # Different dates can establish date-level order. On the same
            # date, BOTH fields need a time: midnight is not an observed time.
            if finish.date() < start.date():
                return None
            if len(finish_value) > 10 and len(start_value) > 10 and finish < start:
                return None
    return ('EDUCATION_PREWORK_TIMING_UNLINKED',
            '신규 입사자 교육 계획과 이수 결과는 확인되지만, 같은 대상의 실제 교육 완료가 업무 시작보다 앞섰다는 기록이 연결되지 않습니다. 계획상의 업무 시작 전 교육과 실제 이수 시점을 구분하여 검토가 필요합니다.')

def question_binding_gap(item, output, context):
    """Return an observed reason for holding MET, never rewrite it as MET."""
    if output.get('result') != 'MET':
        return None
    citations = output.get('citations', [])
    if not citations:
        return None  # mechanical citation validation owns this failure
    q = _compact(item.get('question', ''))
    reason = str(output.get('reason', ''))
    quoted = '\n'.join(str(c.get('quote', '')) for c in citations)
    target = _education_target(q)
    if target and item.get('check_kind') == 'implementation':
        if not target[1].search(reason) or not target[1].search(quoted):
            return ('EDUCATION_SUBJECT_MISMATCH',
                    f'문항은 {target[0]}의 교육 이행을 묻지만 설명과 인용이 해당 과정에 함께 연결되지 않습니다. 다른 과정의 이수율로 대신 판정하지 않고 검토가 필요합니다.')
        course_rows = [line for line in quoted.splitlines() if re.search(r'과정\s*[:：]', line) and target[1].search(line)]
        if course_rows and not any(re.search(r'이수자\s*[:：]\s*[1-9]\d*|실시하였|교육\s*완료|시행\s*완료', line) for line in course_rows):
            # Only the recognised course-table pattern is covered here. A
            # free-text event needs normal semantic review, not this veto.
            other_event = any(target[1].search(line) and re.search(r'실시하였|교육\s*완료|시행\s*완료',line)
                              for line in quoted.splitlines())
            if not other_event:
                return ('EDUCATION_COURSE_EVENT_UNLINKED',
                        f'{target[0]}의 계획 행은 있으나 같은 과정의 실제 실시·이수 행이 인용에 연결되지 않습니다. 다른 과정의 완료 기록으로 대신할 수 없습니다.')
        timing_gap = _prework_timing_gap(item, output, context, target)
        if timing_gap:
            return timing_gap
    if '교육' in q and '계획' in q and item.get('check_kind') == 'procedure':
        body = re.split(r'에\s*따르면|에\s*따라', reason, maxsplit=1)[-1]
        planning = re.search(r'계획.{0,24}(?:수립|정의|마련|정한|정했|포함|확인|명시)|'
                             r'(?:시기|대상|내용|방법).{0,24}계획', body)
        if not planning:
            return ('EDUCATION_ACTIVITY_MISMATCH',
                    '문항은 연간 교육계획 수립과 계획 내용을 묻지만 설명은 교육 이수·결과 또는 미이수자 조치를 근거로 삼고 있습니다. 계획 수립의 근거와 연결된 설명이 필요합니다.')
    crypto = _crypto_action(item)
    if crypto:
        by_id = {c['chunk_id']: c for c in (context or {}).get('chunks', [])}
        selected = [by_id.get(c.get('chunk_id')) for c in citations]
        if any(c is None for c in selected):
            return None
        kinds = [_crypto_source_kind(c) for c in selected]
        if crypto.startswith(('키 폐기 사건', '키 복구 사건')):
            # A mixed policy/result document may have event evidence elsewhere.
            # Abstain rather than reject an answer merely for selected citations;
            # abstention is not verification of the event or target linkage.
            complete_kinds = [_crypto_source_kind(c) for c in (context or {}).get('chunks', [])]
            if any(k == 'event_or_unclassified' for k in complete_kinds):
                return None
        if 'policy' in kinds and all(k in {'policy', 'inventory'} for k in kinds):
            return ('CRYPTO_POLICY_AS_EXECUTION',
                    f'암호키 관리 기준 또는 목록은 확인되지만, {crypto}가 같은 키 사건에 연결되지 않습니다. 기준 존재와 실제 이행을 구분하여 검토가 필요합니다.')
    return None
