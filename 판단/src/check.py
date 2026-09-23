"""샘플 JSON 검증 + JSON Schema 내보내기 + 정책 동작 확인.

    python src/check.py

레포에 올리면 CI에서 그대로 돌릴 수 있다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pydantic import ValidationError  # noqa: E402

from enums import Decision, MatchStatus, Relation, ReviewStatus  # noqa: E402
from models import (  # noqa: E402
    CandidateControl,
    CandidateDecision,
    Chunk,
    Citation,
    LLMMappedControl,
    LLMMappingOutput,
    MappingInput,
    Phase1MappingResult,
    ValidationResult,
)
from enums import ErrorCode as ErrorCode_  # noqa: E402
from review_policy import (  # noqa: E402
    BLOCKING_ERROR_CODES,
    DEFAULT_RETRY_POLICY,
    InvalidTransition,
    decide_review,
    is_confirmed,
    transition,
)

ROOT = Path(__file__).parent.parent
SAMPLES = ROOT / "samples"
SCHEMAS = ROOT / "schemas"

ok = 0
fail = 0


def report(name: str, passed: bool, detail: str = "") -> None:
    global ok, fail
    mark = "PASS" if passed else "FAIL"
    if passed:
        ok += 1
    else:
        fail += 1
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


# --------------------------------------------------------------------------
print("\n== 1. 샘플 JSON이 모델로 파싱되는가 ==")
for path in sorted(SAMPLES.glob("*.json")):
    try:
        result = Phase1MappingResult.model_validate_json(path.read_text(encoding="utf-8"))
        report(
            path.name,
            True,
            f"{result.match_status.value}, 매핑 {len(result.mapped_controls)}개, "
            f"검토 {'필요' if result.human_review.required else '불필요'}",
        )
    except ValidationError as exc:
        report(path.name, False, str(exc).splitlines()[0])


# --------------------------------------------------------------------------
print("\n== 2. 규칙 위반이 실제로 걸리는가 (전부 막혀야 정상) ==")


def chunk_must_fail(name: str, **kw) -> None:
    base = dict(chunk_id="E0001_v1_c0000", evidence_id="E0001", version=1, chunk_index=0,
                file_type="pdf", source_file="a.pdf", chunk_type="text",
                page_start=1, page_end=1, heading=None, text="내용",
                block_orders=[1], source="parser")
    base.update(kw)
    try:
        Chunk(**base)
        report(name, False, "통과해버림")
    except ValidationError:
        report(name, True, "차단됨")


chunk_must_fail("docx인데 page가 있음", file_type="docx")
chunk_must_fail("pdf인데 page가 없음", page_start=None, page_end=None)
chunk_must_fail("page_start > page_end", page_start=5, page_end=2)
chunk_must_fail("chunk_id 형식 위반", chunk_id="CH-001")
chunk_must_fail("chunk_id가 evidence_id와 불일치", chunk_id="E0099_v1_c0000")
chunk_must_fail("chunk_id가 chunk_index와 불일치", chunk_id="E0001_v1_c0007")
chunk_must_fail("text 800자 초과", text="가" * 801)



def must_fail(name: str, payload: dict) -> None:
    try:
        Phase1MappingResult.model_validate(payload)
        report(name, False, "통과해버림 — 규칙이 안 걸린다")
    except ValidationError:
        report(name, True, "차단됨")


base = json.loads((SAMPLES / "single_mapping.json").read_text(encoding="utf-8"))

# PRIMARY 2개
two_primary = json.loads(json.dumps(base))
extra = json.loads(json.dumps(base["mapped_controls"][0]))
extra["control_id"] = "2.5.5"
extra["control_name"] = "특수 계정 및 권한 관리"
two_primary["mapped_controls"].append(extra)
must_fail("PRIMARY 2개", two_primary)

# 중복 control_id
dup = json.loads(json.dumps(base))
second = json.loads(json.dumps(base["mapped_controls"][0]))
second["relation"] = "RELATED"
dup["mapped_controls"].append(second)
must_fail("중복 control_id", dup)

# NO_MATCH인데 매핑이 있음
bad_no_match = json.loads(json.dumps(base))
bad_no_match["match_status"] = "NO_MATCH"
must_fail("NO_MATCH인데 mapped_controls 있음", bad_no_match)

# MATCHED인데 매핑이 없음
empty_matched = json.loads(json.dumps(base))
empty_matched["mapped_controls"] = []
must_fail("MATCHED인데 mapped_controls 없음", empty_matched)

# confidence 범위 초과
bad_conf = json.loads(json.dumps(base))
bad_conf["mapped_controls"][0]["llm_confidence"] = 1.4
must_fail("llm_confidence 1.4", bad_conf)

# 정의되지 않은 필드 (팀원이 마음대로 필드를 추가하는 것을 막는다)
extra_field = json.loads(json.dumps(base))
extra_field["mapped_controls"][0]["confidence"] = 0.9
must_fail("정의되지 않은 필드 confidence", extra_field)

# 검토가 필요한데 사유가 없음
no_reason = json.loads(json.dumps(base))
no_reason["human_review"] = {"required": True, "status": "PENDING", "reasons": []}
must_fail("검토 필요한데 사유 없음", no_reason)


# --------------------------------------------------------------------------
print("\n== 3. Human Review 판단이 의도대로 동작하는가 ==")

clean_validation = ValidationResult(
    passed=True,
    schema_valid=True,
    control_ids_valid=True,
    citations_valid=True,
    rules_valid=True,
)

good_input = MappingInput(
    evidence_id="E0001",
    version=1,
    top_k=5,
    chunks=[
        Chunk(
            chunk_id="E0001_v1_c0000",
            evidence_id="E0001", version=1, chunk_index=0,
            file_type="pdf", source_file="계정관리지침서.pdf",
            chunk_type="text", page_start=1, page_end=1,
            heading="2. 접근통제",
            text="사용자 계정은 관리자 승인 후 생성하며 퇴직 시 즉시 삭제한다.",
            block_orders=[3], source="parser",
        )
    ],
    candidate_controls=[
        CandidateControl(
            rank=1, control_id="2.5.1", control_name="사용자 계정 관리",
            similarity_score=0.5833,
        ),
        CandidateControl(
            rank=2, control_id="2.5.6", control_name="접근권한 검토",
            similarity_score=0.5280,
        ),
    ],
)

good_output = LLMMappingOutput(
    match_status=MatchStatus.MATCHED,
    candidate_decisions=[
        CandidateDecision(
            control_id="2.5.1", decision=Decision.RELATED, llm_confidence=0.91,
            reason="계정 생성 승인 절차가 명시된다.",
            citations=[Citation(chunk_id="E0001_v1_c0000", page=1,
                                quote="사용자 계정은 관리자 승인 후 생성")],
        ),
        CandidateDecision(
            control_id="2.5.6", decision=Decision.NOT_RELATED, llm_confidence=0.85,
            reason="권한 검토 주기 언급 없음.",
        ),
    ],
    mapped_controls=[
        LLMMappedControl(
            control_id="2.5.1", control_name="사용자 계정 관리",
            relation=Relation.PRIMARY, llm_confidence=0.91,
            reason="계정 생성 승인 절차가 명시된다.",
            citations=[Citation(chunk_id="E0001_v1_c0000", page=1,
                                quote="사용자 계정은 관리자 승인 후 생성")],
        )
    ],
)

review = decide_review(good_output, clean_validation, good_input)
report("명확한 단일 매핑 → 검토 불필요", not review.required,
       f"사유 {[r.value for r in review.reasons]}")

# 낮은 confidence
low_conf = good_output.model_copy(deep=True)
low_conf.mapped_controls[0].llm_confidence = 0.55
review = decide_review(low_conf, clean_validation, good_input)
report("낮은 confidence → 검토", review.required and "R201" in [r.value for r in review.reasons])

# UNCERTAIN
uncertain = good_output.model_copy(deep=True)
uncertain.candidate_decisions[1].decision = Decision.UNCERTAIN
review = decide_review(uncertain, clean_validation, good_input)
report("UNCERTAIN 포함 → 검토", review.required and "R107" in [r.value for r in review.reasons])

# 1·2위 점수 차가 작음
narrow = good_input.model_copy(deep=True)
narrow.candidate_controls[1].similarity_score = 0.5800
review = decide_review(good_output, clean_validation, narrow)
report("1·2위 점수 차 0.003 → 검토", review.required and "R202" in [r.value for r in review.reasons])

# OCR 청크를 인용하면 검토 (전처리팀이 품질 점수를 주지 않으므로 source로 판단)
ocr_input = good_input.model_copy(deep=True)
ocr_input.chunks[0].source = "ocr"
review = decide_review(good_output, clean_validation, ocr_input)
report("OCR 청크 인용 → 검토", review.required and "R207" in [r.value for r in review.reasons])

# 페이지 범위 검사
chunk = good_input.chunks[0].model_copy(deep=True)
chunk.page_start, chunk.page_end = 2, 3
report("페이지 범위 안 (2~3에 3)", chunk.covers_page(3))
report("페이지 범위 밖 (2~3에 4)", not chunk.covers_page(4))

# LLM 호출 자체가 실패
review = decide_review(None, clean_validation, good_input)
report("LLM 실패(output None) → 검토", review.required and review.status == ReviewStatus.PENDING)

# 인젝션 의심
review = decide_review(good_output, clean_validation, good_input, injection_suspected=True)
report("인젝션 의심 → 검토", review.required and "R109" in [r.value for r in review.reasons])

# 적정성 판정 (E505) — Phase 1은 연결까지만 한다
from validators import detect_adequacy_judgment, validate_reasons  # noqa: E402

verdict = good_output.model_copy(deep=True)
verdict.mapped_controls[0].reason = "해당 통제항목을 적정하게 이행하고 있다."
report("적정성 판정 → E505", bool(detect_adequacy_judgment(verdict)))

deferred = good_output.model_copy(deep=True)
deferred.mapped_controls[0].reason = "관련 있어 보이나 원문 근거가 불충분하다."
report("'근거 불충분'은 판정이 아니다", not detect_adequacy_judgment(deferred))

# reason 품질 (E506·E507) — 경고만. 검토로 보내지 않는다
short = good_output.model_copy(deep=True)
short.mapped_controls[0].reason = "관련 있음"
report("짧은 근거 → E506", bool(validate_reasons(short, good_input)))
report("근거 경고는 검토 사유가 아니다",
       not {ErrorCode_.REASON_TOO_SHORT, ErrorCode_.REASON_NOT_SPECIFIC}
       & set(BLOCKING_ERROR_CODES))


# --------------------------------------------------------------------------
print("\n== 4. 상태 전이와 확정 판정 ==")

report("PENDING → APPROVED 허용",
       transition(ReviewStatus.PENDING, ReviewStatus.APPROVED) == ReviewStatus.APPROVED)

try:
    transition(ReviewStatus.APPROVED, ReviewStatus.PENDING)
    report("APPROVED → PENDING 차단", False, "통과해버림")
except InvalidTransition:
    report("APPROVED → PENDING 차단", True)

try:
    transition(ReviewStatus.NOT_REQUIRED, ReviewStatus.APPROVED)
    report("NOT_REQUIRED → APPROVED 차단", False, "통과해버림")
except InvalidTransition:
    report("NOT_REQUIRED → APPROVED 차단", True)

sample = Phase1MappingResult.model_validate_json(
    (SAMPLES / "single_mapping.json").read_text(encoding="utf-8")
)
report("검토 불필요 결과는 Phase 2로 전달 가능", is_confirmed(sample.human_review))

pending = Phase1MappingResult.model_validate_json(
    (SAMPLES / "multi_mapping.json").read_text(encoding="utf-8")
)
report("PENDING 결과는 Phase 2로 전달 불가", not is_confirmed(pending.human_review))

rejected = pending.human_review.model_copy(update={"status": ReviewStatus.REJECTED})
report("REJECTED 결과는 Phase 2로 전달 불가", not is_confirmed(rejected))


# --------------------------------------------------------------------------
print("\n== 5. 재시도 정책 ==")
p = DEFAULT_RETRY_POLICY
from enums import ErrorCode  # noqa: E402

report("깨진 JSON 1회차 → 재시도", p.should_retry(ErrorCode.JSON_PARSE_FAILED, 1))
report("깨진 JSON 2회차 → 중단", not p.should_retry(ErrorCode.JSON_PARSE_FAILED, 2))
report("후보 밖 ID → 재시도 안 함",
       not p.should_retry(ErrorCode.CONTROL_ID_NOT_IN_CANDIDATES, 1))
report("Citation 불일치 → 재시도 안 함",
       not p.should_retry(ErrorCode.CITATION_QUOTE_NOT_IN_SOURCE, 1))


# --------------------------------------------------------------------------
print("\n== 6. JSON Schema 내보내기 ==")
SCHEMAS.mkdir(exist_ok=True)
from review import ReviewDecision, ReviewQueueItem, ReviewRecord  # noqa: E402

exports = {
    "phase1_mapping_input.schema.json": MappingInput,
    "phase1_llm_output.schema.json": LLMMappingOutput,
    "phase1_mapping_result.schema.json": Phase1MappingResult,
    "review_decision.schema.json": ReviewDecision,
    "review_record.schema.json": ReviewRecord,
    "review_queue_item.schema.json": ReviewQueueItem,
}
for filename, model in exports.items():
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = model.__name__
    (SCHEMAS / filename).write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report(filename, True, "생성됨")


print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
