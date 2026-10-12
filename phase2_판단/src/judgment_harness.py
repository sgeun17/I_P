"""Phase 2 항목별 판단 개발 하네스.

Context -> Grounding Prompt -> 팀 제공 Structured Output Schema 호출을 수행한다.
기본 문항 출력 계약은 phase2_인터페이스/phase2_output.schema.json의 ItemResult이며,
최종 JSON/ENUM/ID/Citation 검증은 찬우 Validator가 수행한다.

Citation 원문 일치/page 일치와 Human Review 전환은 찬우 Validator 영역이므로 이 하네스가
대체하지 않는다. 여기서는 JSON 파싱과 개발용 최소 구조만 확인한다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from hashlib import sha256
import json
import re
import time
from typing import Any, Callable, Mapping, Sequence

import httpx

from structured_output_adapter import validate_model_generated_item, item_json_schema
from grounding_prompts import PromptPackage, build_prompt_package, build_retry_user_prompt
from evidence_contracts import question_focus
from citation_spans import prepare as prepare_spans, materialize, adapt_prompts, SPAN_VERSION
from phase1_runtime import (
    DEFAULT_RETRY_POLICY,
    ErrorCode,
    GenerationConfig,
    LLMCallError,
    LLMClientConfig,
    LLMRequestRejectedError,
    RetryPolicy,
    call_llm,
)


OutputValidator = Callable[[Any], list[str]]
LLMCall = Callable[..., str]


def result_code_generation_schema(schema, reason_codes):
    """Private generation branches; public validation contract is unchanged.

    if/then constraints are replaced with explicit result branches so model
    generation cannot attach failure codes to MET or invent technical errors.
    Citation choices are shared through refs, not triplicated in the prompt.
    """
    bounded = deepcopy(schema)
    properties = bounded.get('properties', {})
    if 'result' not in properties or 'reason_codes' not in properties:
        return bounded
    for key in ('error_code', 'critical', 'check_kind'):
        properties.pop(key, None)  # Trusted pipeline computes these fields.
    bounded['required'] = [k for k in bounded.get('required', []) if k in properties]
    if 'reason_codes' not in bounded['required']:
        bounded['required'].append('reason_codes')
    catalog = {row['code']: row.get('result') for row in reason_codes or [] if row.get('code')}
    canonical = properties['reason_codes'].get('items', {}).get('enum', [])
    if not catalog:
        catalog = {code: ('NOT_MET' if code.startswith('P2_NM_') else 'UNKNOWN') for code in canonical}
    definitions = bounded.setdefault('$defs', {})
    citation_field = 'citations' if 'citations' in properties else 'citation_span_ids'
    if citation_field in properties:
        array = deepcopy(properties[citation_field])
        definitions['GenerationCitationItem'] = array.get('items', {})
        array['items'] = {'$ref': '#/$defs/GenerationCitationItem'}
        properties[citation_field] = array
    branches = []
    for result in ('MET', 'NOT_MET', 'UNKNOWN'):
        allowed = [code for code, value in catalog.items() if value == result]
        if result != 'MET' and not allowed:
            continue
        branch_properties = deepcopy(properties)
        branch_properties['result'] = {'const': result}
        branch_properties['reason_codes'] = ({'type': 'array', 'maxItems': 0}
            if result == 'MET' else {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                'items': {'type': 'string', 'enum': allowed}})
        if citation_field in branch_properties and result != 'UNKNOWN':
            branch_properties[citation_field]['minItems'] = 1
        branches.append({'type': 'object', 'additionalProperties': False,
            'required': list(bounded['required']), 'properties': branch_properties})
    bounded.pop('allOf', None)
    bounded['anyOf'] = branches
    return bounded


def _generated_field_messages(payload, reason_codes):
    """Reject internally contradictory generated fields without rewriting."""
    messages = []
    catalog = {row['code']: row.get('result') for row in reason_codes or [] if row.get('code')}
    result = payload.get('result')
    codes = payload.get('reason_codes', [])
    if isinstance(codes, list):
        if result == 'MET' and codes:
            messages.append('GEN_RESULT_CODE_CONFLICT: MET requires empty reason_codes; fix the fields, not the evidence')
        if catalog and any(c in catalog and catalog[c] != result for c in codes):
            messages.append('GEN_RESULT_CODE_CONFLICT: reason_codes must belong to the selected result')
    if 'error_code' in payload:
        messages.append('GEN_RESERVED_FIELD: error_code is produced by the execution layer, not the model')
    reason = str(payload.get('reason', '')).strip()
    if reason in catalog or re.fullmatch(r'(?:P2_[A-Z0-9_]+|P2[RE]\d+|E\d+)', reason):
        messages.append('GEN_REASON_CODE_ONLY: reason must explain observed facts and missing conditions in natural language, not a code')
    return messages


def exact_quote_schema(schema, context):
    """Bound generation to trusted whole rows/headings without changing output.

    The validator still rejects wrong chunk/page and checks semantic grounding.
    Outside the bounded context, retain the old schema rather than truncate.
    """
    chunks = context.get('chunks', [])
    if not chunks or len(chunks) > 64:
        return schema
    total = sum(len(str(c.get('text', ''))) + len(str(c.get('heading') or '')) for c in chunks)
    if total > 24000:
        return schema
    branches = []
    for chunk in chunks:
        if chunk.get('role') not in {'evidence', 'context'}:
            continue
        text = chunk.get('text', '')
        if not isinstance(text, str):
            continue
        quotes = list(dict.fromkeys([q for q in [text, *text.splitlines(), chunk.get('heading')] if isinstance(q, str) and q.strip()]))
        if not quotes:
            continue
        page = chunk.get('page_start') if chunk.get('page_start') == chunk.get('page_end') else None
        branches.append({'type': 'object', 'additionalProperties': False,
            'required': ['chunk_id', 'page', 'quote'], 'properties': {
                'chunk_id': {'type': 'string', 'const': chunk['chunk_id']},
                'page': {'enum': [page]}, 'quote': {'type': 'string', 'enum': quotes}}})
    if not branches or sum(len(b['properties']['quote']['enum']) for b in branches) > 512:
        return schema
    bounded = deepcopy(schema)
    bounded.setdefault('properties', {})['citations'] = {
        'type': 'array', 'maxItems': 32, 'items': {'anyOf': branches}}
    return bounded


@dataclass(frozen=True)
class JudgmentRunResult:
    raw_response: str | None
    parsed_output: dict[str, Any] | None
    attempts: int
    retry_count: int
    final_error_code: str | None = None
    validation_messages: tuple[str, ...] = ()
    request_error_status: int | None = None
    request_error_message: str | None = None
    attempt_history: tuple[dict[str, Any], ...] = ()
    citation_generation_mode: str = "verbatim"
    source_spans: dict[str, Any] | None = None
    organization_context_sha256: str | None = None
    organization_profile_version: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.parsed_output is not None and self.final_error_code is None


def _parse_json(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"JSON parse failed: {exc.msg}"]
    if not isinstance(payload, dict):
        return None, ["LLM output must be a JSON object"]
    return payload, []


def _client_config_using_policy(
    client_config: LLMClientConfig | None,
    retry_policy: RetryPolicy,
) -> LLMClientConfig:
    config = client_config or LLMClientConfig.from_env()
    if config.timeout_seconds != float(retry_policy.timeout_seconds):
        config = replace(config, timeout_seconds=float(retry_policy.timeout_seconds))
    return config


def run_item_judgment(
    checklist_item: Mapping[str, Any],
    evidence_context: Mapping[str, Any],
    *,
    model: str,
    reason_codes: Sequence[Mapping[str, Any]] | None = None,
    global_rules: str | None = None,
    organization_context: Mapping[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    output_validator: OutputValidator | None = None,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    client_config: LLMClientConfig | None = None,
    generation: GenerationConfig | None = None,
    http_client: httpx.Client | None = None,
    llm_call: LLMCall = call_llm,
    sleeper: Callable[[float], None] = time.sleep,
    citation_span_selection: bool = False,
) -> JudgmentRunResult:
    """체크리스트 item 하나를 LLM에 요청한다.

    Phase 1 RetryPolicy의 max_retries/timeout/backoff/retry_on을 그대로 사용한다.
    HTTP 4xx는 Phase 1과 동일하게 자동 재시도하지 않는다.
    """
    span_map = None
    prompt_context = evidence_context
    if citation_span_selection:
        prompt_context, span_map, output_schema = prepare_spans(
            evidence_context, str(checklist_item.get("item_id") or ""), reason_codes
        )
    requested_item_id = checklist_item.get("item_id")
    if not isinstance(requested_item_id, str) or not requested_item_id.strip():
        raise ValueError("A non-empty checklist item_id is required")
    output_schema = deepcopy(output_schema) if output_schema is not None else item_json_schema()
    if not citation_span_selection:
        output_schema = exact_quote_schema(output_schema, evidence_context)
    id_schema = output_schema.setdefault("properties", {}).setdefault("item_id", {"type": "string"})
    if id_schema.get("const", requested_item_id) != requested_item_id:
        raise ValueError("Output schema item_id conflicts with requested checklist item")
    id_schema["const"] = requested_item_id
    output_schema.setdefault("required", [])
    if "item_id" not in output_schema["required"]:
        output_schema["required"].append("item_id")
    output_schema = result_code_generation_schema(output_schema, reason_codes)
    organization_payload = dict(organization_context or {})
    organization_context_sha256 = (
        sha256(json.dumps(
            organization_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if organization_payload else None
    )
    run_metadata = {
        "citation_generation_mode": SPAN_VERSION if citation_span_selection else "verbatim",
        "source_spans": span_map,
        "organization_context_sha256": organization_context_sha256,
        "organization_profile_version": (
            str(organization_payload.get("profile_version"))
            if organization_payload.get("profile_version") is not None else None
        ),
    }
    package: PromptPackage = build_prompt_package(
        checklist_item,
        prompt_context,
        reason_codes=reason_codes,
        global_rules=global_rules,
        organization_context=organization_payload,
        output_schema=output_schema,
    )
    package = replace(package, user=package.user + "\n[현재 요청 문항 확인]\n"
        + "item_id는 반드시 " + json.dumps(requested_item_id, ensure_ascii=False)
        + " 이다. 다른 문항 ID나 사유코드를 item_id로 쓰지 않는다. 현재 checklist_item.question과 evidence_rule만 평가한다.\n")
    focus = question_focus(checklist_item, evidence_context)
    if focus:
        package = replace(package, user=package.user + '\n[질문·대상·행위 연결 보조자료: 판정값 아님]\n'
            + json.dumps(focus, ensure_ascii=False))
    if citation_span_selection:
        system, user = adapt_prompts(package.system, package.user)
        package = replace(package, system=system, user=user)
    item_id = str(checklist_item.get("item_id") or "")
    allowed_reason_codes = {
        str(row.get("code")) for row in (reason_codes or []) if row.get("code")
    }

    if output_validator is None:
        output_validator = lambda payload: validate_model_generated_item(
            payload, expected_item_id=item_id, allowed_reason_codes=allowed_reason_codes
        )

    config = _client_config_using_policy(client_config, retry_policy)
    attempts = 0
    retry_count = 0
    current_user = package.user
    last_messages: list[str] = []
    last_code: ErrorCode | None = None
    last_raw: str | None = None
    history: list[dict[str, Any]] = []

    while True:
        attempts += 1
        try:
            raw = llm_call(
                package.system,
                current_user,
                model,
                package.output_schema,
                client_config=config,
                generation=generation,
                http_client=http_client,
            )
            last_raw = raw
        except LLMRequestRejectedError as exc:
            history.append({"attempt": attempts, "request_error_status": exc.status_code})
            return JudgmentRunResult(
                raw_response=None,
                parsed_output=None,
                attempts=attempts,
                retry_count=retry_count,
                request_error_status=exc.status_code,
                request_error_message=str(exc),
                attempt_history=tuple(history),
                **run_metadata,
            )
        except LLMCallError as exc:
            history.append({"attempt": attempts, "error_code": exc.code.value, "message": str(exc)})
            last_code = exc.code
            last_messages = [str(exc)]
            next_retry = retry_count + 1
            if retry_policy.should_retry(exc.code, next_retry):
                retry_count = next_retry
                if retry_policy.backoff_seconds > 0:
                    sleeper(retry_policy.backoff_seconds)
                current_user = build_retry_user_prompt(
                    package.user, error_code=exc.code.value, attempt=retry_count
                )
                continue
            return JudgmentRunResult(
                raw_response=None,
                parsed_output=None,
                attempts=attempts,
                retry_count=retry_count,
                final_error_code=exc.code.value,
                validation_messages=tuple(last_messages),
                attempt_history=tuple(history),
                **run_metadata,
            )

        parsed, parse_messages = _parse_json(raw)
        json_failed = parsed is None
        if parsed is not None and citation_span_selection:
            parsed, parse_messages = materialize(parsed, span_map, package.output_schema)
        if parsed is None:
            last_code = ErrorCode.JSON_PARSE_FAILED if json_failed else ErrorCode.SCHEMA_INVALID
            last_messages = parse_messages
        else:
            validation_messages = list(output_validator(parsed)) + _generated_field_messages(parsed, reason_codes)
            if not validation_messages:
                history.append({"attempt": attempts, "raw_response": raw, "validation_messages": []})
                return JudgmentRunResult(
                    raw_response=raw,
                    parsed_output=parsed,
                    attempts=attempts,
                    retry_count=retry_count,
                    attempt_history=tuple(history),
                    **run_metadata,
                )
            last_code = ErrorCode.SCHEMA_INVALID
            last_messages = validation_messages

        history.append({"attempt": attempts, "raw_response": raw,
                        "validation_messages": list(last_messages), "error_code": last_code.value})
        next_retry = retry_count + 1
        if retry_policy.should_retry(last_code, next_retry):
            retry_count = next_retry
            if retry_policy.backoff_seconds > 0:
                sleeper(retry_policy.backoff_seconds)
            current_user = build_retry_user_prompt(
                package.user, error_code=last_code.value, attempt=retry_count,
                previous_response=raw, validation_messages=last_messages,
            )
            if citation_span_selection:
                current_user += ("\n현재 내부 출력은 citation_span_ids 선택 방식이다. "
                                 "quote를 생성하지 말고 제공된 ID와 올바른 result/사유 코드만 출력하라.")
            continue

        return JudgmentRunResult(
            raw_response=last_raw,
            parsed_output=None,
            attempts=attempts,
            retry_count=retry_count,
            final_error_code=last_code.value,
            validation_messages=tuple(last_messages),
            attempt_history=tuple(history),
            **run_metadata,
        )
