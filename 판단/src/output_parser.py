"""LLM 원본 응답 → LLMMappingOutput.

LLM은 순수한 JSON만 내놓지 않는다. 실제로 자주 보는 것들:

    ```json { ... } ```          코드블록으로 감쌈
    "네, 판단 결과입니다: { ... }"  앞에 말을 붙임
    { ... } 뒤에 설명 문장         뒤에 말을 붙임
    { ... }, }                    닫는 괄호를 하나 더 붙임
    "control_id": "2.5.1",}       마지막에 쉼표

여기서는 **형식만** 본다. 판단 규칙 위반은 validators.py가 따로 잡는다.
파서가 관대하면 안 되는 부분(원문 변형, ID 보정)은 절대 건드리지 않는다.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import ValidationError

from enums import ErrorCode
from models import LLMMappingOutput, ValidationIssue

# ```json ... ``` 또는 ``` ... ```
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
# 마지막 요소 뒤의 쉼표: {"a": 1,} 또는 [1, 2,]
TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def extract_json_text(raw: str) -> Optional[str]:
    """응답에서 JSON 객체로 보이는 부분만 잘라낸다. 못 찾으면 None."""
    if not isinstance(raw, str) or not raw.strip():
        return None

    fenced = FENCE.search(raw)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate:
            return candidate

    # 가장 바깥 중괄호 짝을 찾는다. 문자열 안의 괄호는 세지 않는다.
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]

    # 괄호 짝이 안 맞는다. 닫는 괄호가 하나라도 있으면 "망가진 JSON"이고,
    # 아예 없으면 "잘린 응답"이다. 둘은 원인이 달라서 오류코드를 구분한다.
    last = raw.rfind("}")
    return raw[start : last + 1] if last > start else None


def parse_llm_output(raw: str) -> tuple[Optional[LLMMappingOutput], list[ValidationIssue]]:
    """LLM 원본 응답을 모델로 바꾼다.

    돌려주는 값: (출력 객체 또는 None, 문제 목록)
    실패해도 예외를 던지지 않는다. 판단 파트는 어떤 경우에도 멈추지 않는다.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, [ValidationIssue(code=ErrorCode.LLM_EMPTY_RESPONSE, message="응답이 비어 있다")]

    text = extract_json_text(raw)
    if text is None:
        # 중괄호가 열렸는데 안 닫혔으면 잘린 응답으로 본다.
        code = (
            ErrorCode.LLM_TRUNCATED_RESPONSE
            if "{" in raw
            else ErrorCode.JSON_PARSE_FAILED
        )
        return None, [ValidationIssue(code=code, message="JSON 객체를 찾지 못했다")]

    data = None
    for attempt in (text, TRAILING_COMMA.sub(r"\1", text)):
        try:
            data = json.loads(attempt)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        return None, [
            ValidationIssue(code=ErrorCode.JSON_PARSE_FAILED, message="JSON 파싱 실패")
        ]

    if not isinstance(data, dict):
        return None, [
            ValidationIssue(code=ErrorCode.SCHEMA_INVALID, message="최상위가 객체가 아니다")
        ]

    try:
        return LLMMappingOutput.model_validate(data), []
    except ValidationError as exc:
        return None, [_to_issue(e) for e in exc.errors()]


def _to_issue(error: dict) -> ValidationIssue:
    """Pydantic 오류를 우리 오류코드로 옮긴다."""
    kind = error.get("type", "")
    location = ".".join(str(p) for p in error.get("loc", ()))

    if kind == "missing":
        code = ErrorCode.REQUIRED_FIELD_MISSING
    elif kind == "enum" or "enum" in kind:
        code = ErrorCode.INVALID_ENUM_VALUE
    elif "confidence" in location and kind in {
        "greater_than_equal",
        "less_than_equal",
    }:
        code = ErrorCode.CONFIDENCE_OUT_OF_RANGE
    else:
        code = ErrorCode.SCHEMA_INVALID

    return ValidationIssue(code=code, message=error.get("msg", "형식 오류"), field=location or None)
