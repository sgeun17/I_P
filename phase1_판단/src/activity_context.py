"""Source-only question locators, not classification rules or model verdicts.

The same locator can accompany independent purpose/fact/correction requests.
It accepts only source rows, so prior decisions, scores and generated reasons
cannot enter through this interface. All source stays in the caller's payload.
"""
from __future__ import annotations

import re


_CONTEXT = {
    '2.1.1': (
        r'정책|처리방침|policy|개정|유지관리',
        '정책 유지관리의 일부 활동과 정기 검토 전체를 구분한다. 일회성 개정 또는 직접 '
        '미개정 기록도 읽되, 제목이나 법규 점검이라는 주된 목적만으로 연결을 결정하지 않는다.',
        ['이 구절은 정책·처리방침을 개정한 기록, 개정 계획, 개정하지 않은 기록 중 무엇인가?',
         '구절에 주기적 검토가 실제로 적혀 있는가, 아니면 일회성 변경까지만 적혀 있는가?']),
    '1.2.4': (
        r'보호대책|이행|실행|계획|분기|일정|예산|인력|의결|승인',
        '보호대책의 선정·계획 수립·계획 심의 또는 승인·실제 구현 완료를 분리한다. '
        '이행계획을 원안 의결했다는 회의 기록은 계획 내용 자체와 다른 관찰 사실이다. '
        '설명이 그 의결 사실만 주장하면 상세 일정·담당·자원이 없다는 이유로 사실을 부정하지 않는다. '
        '그 사실과 대상 활동의 일부 관련성은 별도로 검사한다. 일반 예산·인력 표나 위험 처리 방향만으로 '
        '특정 보호대책의 계획·승인을 추론하지 않는다. 승인 대상을 서로 바꾸지 않는다.',
        ['직접 기록된 행위는 계획 수립, 계획 심의·승인, 실제 구현 중 무엇이며 그 대상은 무엇인가?',
         '설명이 주장하는 범위는 의결 사실인가, 상세 계획 내용이나 구현 완료까지인가?',
         '상세 일정·담당·자원의 부재를 설명이 주장하지 않은 요건으로 추가하고 있지는 않은가?']),
    '1.3.1': (
        r'계획|예정|승인|보고|실적|평가|이행|검토|점검|성과|경영진|대표이사|CISO',
        '계획 승인, 향후 이행, 과거 추진실적 평가, 경영진의 실제 결과 확인을 서로 구분한다. '
        '시점·행위자·대상이 다른 구절을 하나의 완료 주장으로 합치지 않는다. 일부 직접 활동과 '
        '전체 요건 충족은 별개이며 기록 부재도 사실 범위를 넘겨 단정하지 않는다.',
        ['이 구절의 시점은 향후 계획인가, 이미 수행한 실적 또는 평가인가?',
         '승인·보고의 대상은 계획인가 실제 이행결과인가, 그 행위자와 수행 여부가 명시돼 있는가?']),
    '2.3.2': (
        r'위탁|수탁|계약|약정|협정|보안요구|비밀유지|재위탁',
        '공개된 수탁자·업무 목록과 계약·보안약정에 요구사항을 명시한 활동은 다르다. '
        '목록을 실제 계약 체결·보안 요구 명시의 근거로 바꾸지 않는다. 구체적인 계약 보안 '
        '조항 또는 조항 미명시 기록이 다른 원문 구절에 있는지도 독립적으로 확인한다.',
        ['이 구절은 수탁자·위탁업무 공개 목록인가, 실제 계약·약정과 연결된 보안 요구사항인가?',
         '계약서에 보안 요구를 명시하도록 하는 절차가 직접 적혀 있는가, 실제 명시 또는 누락 기록인가?',
         '계약상 보안 요구 명시 또는 미명시가 직접 기록돼 있는가, 단순한 관련 주제 언급인가?']),
    '2.10.2': (
        r'클라우드|cloud|AWS|Azure|GCP|VPC|보안그룹|보호대책',
        '클라우드 위험 식별·위험도·처리 방향과 구체적인 보안 보호대책의 수립·계획·이행 '
        '또는 직접 미수행 기록을 구분한다. 위험대장 처리방안의 “감소”만으로 어떤 보호대책을 '
        '수립·구현했다고 만들지 않는다. 다른 항목의 요구사항을 이 항목에 가져오지 않는다.',
        ['이 구절은 위험 시나리오와 처리 방향만 나타내는가, 구체적인 클라우드 보호대책을 나타내는가?',
         '보호대책의 대상·조치·계획 또는 직접 미수행 내용이 실제로 기록된 구절은 어디인가?']),
}


def review_context(control_id: str, rows: list[dict]) -> dict:
    """Return literal-source locators and open questions; never a verdict.

    A match is only a reading aid. It proves neither relatedness nor absence.
    In particular, a negated activity remains in the observations unchanged.
    """
    spec = _CONTEXT.get(control_id)
    if spec is None:
        return {'instructions': '', 'source_observations': []}
    pattern, instruction, questions = spec
    observations = []
    for row in rows:
        text = row.get('text', '')
        if not isinstance(text, str) or not re.search(pattern, text, re.I):
            continue
        observations.append({
            'span_id': row['span_id'], 'field': row.get('field', 'text'),
            'text': text, 'questions': list(questions),
        })
    return {
        'instructions': instruction + ' 아래는 원문 위치와 질문만 제시한다. 매칭은 판정이나 '
            '부재 증명이 아니며, 전체 원문을 읽고 어느 판정값도 강제하지 말고 독립적으로 검사한다.',
        'source_observations': observations,
    }
