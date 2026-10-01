"""Phase2 문항 판단의 검수용 입출력과 제공된 응답 검증.

실제 LLM 호출·운영 API·DB 저장·종합 등급 계산은 하지 않는다. 기술 검증
실패를 UNKNOWN 판정으로 바꾸지 않으며, 통과해도 판정 의미의 정답은 미검증이다.
현재 한 Phase1 증적/버전의 청크만 받는다. 여러 문서의 범위·사건 연결은 별도 과제다.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from checklist_store import ChecklistStore
from phase1_review_adapter import ReviewPlanError, prepare_review_plan
from models import Chunk, Citation
from reason_codes import ReasonCatalog, ReasonCodeError
from validators import normalize, validate_citation

CONTRACT_VERSION = "phase2-judgment-review-0.1"
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PositiveInt = Annotated[int, Field(ge=1)]
ResultValue = Literal["MET", "NOT_MET", "UNKNOWN"]


class ReviewBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewContext(ReviewBase):
    """호출자가 제공한 검토 정보. 실제 적용 관계의 확인 완료를 뜻하지 않는다."""
    subject: Text | None = None
    period_or_event: Text | None = None
    applicable_basis: Text | None = None
    effective_basis: Text | None = None


class EvidenceRule(ReviewBase):
    candidate_evidence: list[Text]
    met: Text
    not_met: Text
    unknown: Text
    citation_required_for: list[ResultValue]
    scope_rule: Text
    required_context: list[Text]


class SourceReference(ReviewBase):
    source_id: Text
    pdf_page: PositiveInt
    printed_page: PositiveInt
    section: Text


class ReviewQuestion(ReviewBase):
    item_id: Text
    control_id: Text
    source_clause: Text
    question: Text
    check_kind: Literal["procedure", "implementation", "record"]
    critical: None
    critical_status: Literal["UNDECIDED"]
    evidence_rule: EvidenceRule
    review_status: Literal["SOURCE_REVIEWED_DRAFT"]
    source_refs: list[SourceReference]
    review_note: Text
    applicability_condition: Text | None = None


class JudgmentReviewInput(ReviewBase):
    contract_version: Literal["phase2-judgment-review-0.1"] = CONTRACT_VERSION
    review_only: Literal[True] = True
    approved: Literal[False] = False
    checklist_version: Text
    checklist_source_sha256: Sha256
    kb_sha256: Sha256
    catalog_version: Text
    catalog_sha256: Sha256
    evidence_id: Text
    version: PositiveInt
    source_phase1_result: dict
    phase1_validation_overridden_by_review: bool
    phase1_validation_scope: Literal["STORED_RESULT_ONLY"]
    mapped_control_ids: list[Text]
    available_question_count: PositiveInt
    partial_review: bool
    context: ReviewContext
    questions: list[ReviewQuestion] = Field(min_length=1)
    chunks: list[Chunk] = Field(min_length=1)
    reason_definitions: list[dict]
    source_documents: list[dict]

    @model_validator(mode="after")
    def _chunk_scope(self):
        ids = [chunk.chunk_id for chunk in self.chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("청크 ID가 중복되었습니다.")
        if any(chunk.evidence_id != self.evidence_id or chunk.version != self.version
               for chunk in self.chunks):
            raise ValueError("다른 증적 또는 버전의 청크가 섞였습니다.")
        return self


class ReviewCitation(ReviewBase):
    chunk_id: Text
    page: PositiveInt | None
    quote: Text


class ItemJudgment(ReviewBase):
    item_id: Text
    control_id: Text
    result: ResultValue
    reason: Text
    reason_codes: list[Text]
    citations: list[ReviewCitation]


class JudgmentReviewOutput(ReviewBase):
    checklist_version: Text
    catalog_version: Text
    evidence_id: Text
    version: PositiveInt
    item_results: list[ItemJudgment]


class JudgmentReviewError(ValueError):
    def __init__(self, code, message, *, details=None):
        self.code = code
        self.details = deepcopy(details or {})
        super().__init__(message)


def _require(passed, code, message, **details):
    if not passed:
        raise JudgmentReviewError(code, message, details=details)


def _model(model, value, code):
    payload = value.model_dump(mode="python") if isinstance(value, model) else deepcopy(value)
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise JudgmentReviewError(code, "검수용 입력 규격이 잘못됐습니다.", details={
            "errors": [{"field": ".".join(map(str, row["loc"])), "type": row["type"]}
                       for row in error.errors()]}) from error


def _catalog_snapshot(catalog):
    try:
        listing = catalog.list_codes(allow_draft=True)
        raw = catalog.path.read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == catalog.sha256,
                 "CATALOG_CHANGED", "검토 중 사유 코드 카탈로그가 변경됐습니다.")
        document = json.loads(raw.decode("utf-8"))
    except ReasonCodeError as error:
        raise JudgmentReviewError(error.code, str(error)) from error
    except (OSError, UnicodeError, ValueError) as error:
        if isinstance(error, JudgmentReviewError):
            raise
        raise JudgmentReviewError("CATALOG_UNAVAILABLE", "사유 코드를 읽을 수 없습니다.") from error
    return listing, document


def _catalog_or_default(catalog):
    if catalog is not None:
        return catalog
    try:
        return ReasonCatalog()
    except ReasonCodeError as error:
        raise JudgmentReviewError(error.code, str(error)) from error


def prepare_judgment_review(phase1_result, store: ChecklistStore, checklist_version,
                            chunks, *, context=None, item_ids=None, catalog=None,
                            allow_draft=False):
    """기존 확정 게이트를 통과한 질문·청크·사유 정의를 검수용으로 묶는다.

    item_ids를 주면 부분 검토임을 표시한다. 전체 매핑의 질문 조회를 먼저 수행하므로
    1:N 중 미지원 통제항목을 부분 선택으로 숨길 수 없다. 실제 판단은 수행하지 않는다.
    """
    _require(allow_draft is True, "DRAFT_NOT_APPROVED", "allow_draft=True가 필요합니다.")
    try:
        plan = prepare_review_plan(phase1_result, store, checklist_version, allow_draft=True)
    except ReviewPlanError as error:
        raise JudgmentReviewError(error.code, str(error), details=error.details) from error
    _require(plan["question_count"] > 0, "NO_PHASE2_QUESTIONS",
             "NO_MATCH는 판단할 문항이 없습니다. 충족 판정으로 바꾸지 않습니다.")
    catalog = _catalog_or_default(catalog)
    listing, catalog_document = _catalog_snapshot(catalog)
    source = next(row for row in catalog_document["source_files"]
                  if row["path"] == catalog.checklist_source)
    _require(catalog_document["checklist_version"] == checklist_version
             and source["sha256"] == plan["checklist_source_sha256"],
             "CATALOG_CHECKLIST_MISMATCH", "체크리스트 버전·해시와 사유 코드 출처가 다릅니다.")
    available = [item for control in plan["controls"] for item in control["checklist"]["items"]]
    if item_ids is not None:
        _require(isinstance(item_ids, list) and bool(item_ids)
                 and all(isinstance(item, str) and item.strip() for item in item_ids),
                 "INVALID_ITEM_SELECTION", "문항 선택은 비어 있지 않은 문자열 배열이어야 합니다.")
        _require(len(item_ids) == len(set(item_ids)), "INVALID_ITEM_SELECTION", "선택 문항이 중복됐습니다.")
        missing = sorted(set(item_ids) - {item["item_id"] for item in available})
        _require(not missing, "ITEM_NOT_IN_PLAN", "현재 확정 매핑의 활성 문항만 선택할 수 있습니다.",
                 item_ids=missing)
    selected = available if item_ids is None else [item for item in available if item["item_id"] in item_ids]
    _require(isinstance(chunks, list) and bool(chunks), "INVALID_CHUNKS", "청크 배열이 필요합니다.")
    validated_chunks = [_model(Chunk, chunk, "INVALID_CHUNKS") for chunk in chunks]
    _require(len({chunk.chunk_id for chunk in validated_chunks}) == len(validated_chunks),
             "DUPLICATE_CHUNK_ID", "청크 ID가 중복됐습니다.")
    _require(all(chunk.evidence_id == plan["evidence_id"] and chunk.version == plan["version"]
                 for chunk in validated_chunks), "CHUNK_SCOPE_MISMATCH", "다른 증적 또는 버전의 청크입니다.")
    context = _model(ReviewContext, {} if context is None else context, "INVALID_REVIEW_CONTEXT")
    documents = []
    for control in plan["controls"]:
        for document in control["source_documents"]:
            if document not in documents:
                documents.append(deepcopy(document))
    return _model(JudgmentReviewInput, {
        "checklist_version": checklist_version,
        "checklist_source_sha256": plan["checklist_source_sha256"],
        "kb_sha256": plan["source_versions"]["kb_sha256"],
        "catalog_version": listing["catalog_version"], "catalog_sha256": catalog.sha256,
        "evidence_id": plan["evidence_id"], "version": plan["version"],
        "source_phase1_result": plan["source_phase1_result"],
        "phase1_validation_overridden_by_review": plan["phase1_validation_overridden_by_review"],
        "phase1_validation_scope": plan["phase1_validation_scope"],
        "mapped_control_ids": [control["checklist"]["control_id"] for control in plan["controls"]],
        "available_question_count": plan["question_count"], "partial_review": len(selected) != len(available),
        "context": context, "questions": selected, "chunks": validated_chunks,
        "reason_definitions": listing["codes"], "source_documents": documents,
    }, "INVALID_REVIEW_INPUT")


def _verified_request(request, store, catalog):
    request = _model(JudgmentReviewInput, request, "INVALID_REVIEW_INPUT")
    rebuilt = prepare_judgment_review(
        request.source_phase1_result, store, request.checklist_version, request.chunks,
        context=request.context, item_ids=[item.item_id for item in request.questions],
        catalog=catalog, allow_draft=True)
    _require(rebuilt.model_dump(mode="json") == request.model_dump(mode="json"),
             "REVIEW_INPUT_CHANGED", "조회한 기준·출처·검토 범위와 입력이 다릅니다.")
    return request


def check_review_output(request, response, store: ChecklistStore, *, catalog=None,
                        allow_draft=False):
    """제공된 응답의 형식·문항 대응·사유 종류·인용만 검사한다.

    잘못된 요청은 JudgmentReviewError, 잘못된 응답은 validation.issues에 남긴다.
    parsed_output은 형식만 통과한 응답도 담을 수 있으므로 validation.passed를 확인한다.
    응답을 수정·보완·재판정하지 않는다. 반환 자료는 항상 사람 검토가 필요한 초안이다.
    """
    _require(allow_draft is True, "DRAFT_NOT_APPROVED", "allow_draft=True가 필요합니다.")
    catalog = _catalog_or_default(catalog)
    request = _verified_request(request, store, catalog)
    flags = {key: False for key in ("schema_valid", "question_coverage_valid", "reasons_valid", "citations_valid")}
    issues, warnings = [], []

    def issue(code, message, *, group=None, **details):
        issues.append({"code": code, "message": message, **details})
        if group is not None:
            flags[group] = False

    parsed = None
    try:
        if isinstance(response, str):
            # 중복 JSON 키는 뒤의 값으로 덮어쓰지 않고 거부한다.
            def unique_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("중복 JSON 키")
                    value[key] = item
                return value
            response = json.loads(response, object_pairs_hook=unique_object)
        parsed = _model(JudgmentReviewOutput, response, "OUTPUT_SCHEMA_INVALID")
    except (JudgmentReviewError, ValueError) as error:
        issue("OUTPUT_SCHEMA_INVALID", "응답이 검수용 JSON 규격과 다릅니다.",
              errors=getattr(error, "details", {}).get("errors", []))
    if parsed is not None:
        flags = {key: True for key in flags}
        for field in ("checklist_version", "catalog_version", "evidence_id", "version"):
            if getattr(parsed, field) != getattr(request, field):
                issue("OUTPUT_SOURCE_MISMATCH", "응답의 버전 또는 증적이 요청과 다릅니다.",
                      group="question_coverage_valid", field=field)
        questions = {item.item_id: item for item in request.questions}
        result_ids = [row.item_id for row in parsed.item_results]
        if len(result_ids) != len(set(result_ids)):
            issue("DUPLICATE_ITEM_RESULT", "같은 문항의 결과가 중복됐습니다.", group="question_coverage_valid")
        missing = sorted(set(questions) - set(result_ids))
        extra = sorted(set(result_ids) - set(questions))
        if missing:
            issue("ITEM_RESULTS_MISSING", "요청한 문항의 결과가 누락됐습니다.",
                  group="question_coverage_valid", item_ids=missing)
        if extra:
            issue("ITEM_RESULTS_EXTRA", "요청하지 않은 문항의 결과가 있습니다.",
                  group="question_coverage_valid", item_ids=extra)
        chunks = {chunk.chunk_id: chunk for chunk in request.chunks}
        for row in parsed.item_results:
            question = questions.get(row.item_id)
            if question is not None and row.control_id != question.control_id:
                issue("ITEM_CONTROL_MISMATCH", "문항의 통제항목 ID가 다릅니다.",
                      group="question_coverage_valid", item_id=row.item_id)
            try:
                catalog.validate_assignment(row.result, row.reason_codes, allow_draft=True)
            except ReasonCodeError as error:
                # 사유 파일이 바뀐 것은 응답 오류가 아니라 검토 입력의 무효화다.
                if error.code in {"SOURCE_CHANGED", "SOURCE_UNAVAILABLE", "INVALID_JSON"}:
                    raise JudgmentReviewError(error.code, str(error)) from error
                issue(error.code, str(error), group="reasons_valid", item_id=row.item_id)
            if (question is not None and row.result in question.evidence_rule.citation_required_for
                    and not row.citations):
                issue("CITATION_REQUIRED", "이 판정에는 증적 인용이 필요합니다.",
                      group="citations_valid", item_id=row.item_id)
            for citation in row.citations:
                source_citation = Citation.model_validate(citation.model_dump(mode="python"))
                for error in validate_citation(source_citation, chunks):
                    issue(error.code.value, error.message, group="citations_valid",
                          item_id=row.item_id, chunk_id=citation.chunk_id)
                chunk = chunks.get(citation.chunk_id)
                if (chunk is not None and chunk.page_start is not None and citation.page is None
                        and normalize(citation.quote) in normalize(chunk.text)):
                    warnings.append({"code": "E407", "message": "페이지형 증적의 인용 페이지가 미기재입니다.",
                                     "item_id": row.item_id, "chunk_id": citation.chunk_id})
        # 검사 중 카탈로그 또는 원본의 교체도 감지한다.
        _catalog_snapshot(catalog)
    missing_context = [key for key, value in request.context.model_dump().items() if value is None]
    if missing_context:
        warnings.append({"code": "REVIEW_CONTEXT_INCOMPLETE", "message": "검토 대상·시점·적용 정보의 확인이 필요합니다.",
                         "fields": missing_context})
    return {"contract_version": CONTRACT_VERSION, "review_only": True, "approved": False,
            "human_approved": False, "llm_executed": False,
            "llm_execution_scope": "THIS_FUNCTION_ONLY", "review_required": True,
            "response_origin": "CALLER_PROVIDED_SOURCE_UNVERIFIED", "partial_review": request.partial_review,
            "phase1_validation_overridden_by_review": request.phase1_validation_overridden_by_review,
            "phase1_validation_scope": request.phase1_validation_scope,
            "validation": {"passed": all(flags.values()) and not issues, **flags,
                           "semantic_judgment_checked": False, "issues": issues, "warnings": warnings},
            "parsed_output": parsed.model_dump(mode="json") if parsed is not None else None}


def review_schemas():
    """API 계약 확정 전의 Pydantic JSON Schema를 반환한다."""
    return {"input": JudgmentReviewInput.model_json_schema(),
            "output": JudgmentReviewOutput.model_json_schema()}
