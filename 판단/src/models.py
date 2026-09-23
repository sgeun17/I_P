"""Phase 1 판단 파트 입출력 모델.

모델은 두 단계로 나눈다.

  LLMMappingOutput     LLM이 방금 뱉은 것. 형식만 본다. 규칙 위반도 일단 담을 수 있어야 한다.
  Phase1MappingResult  Validator를 통과해 저장·전달되는 최종 결과.

LLM 출력 모델에 판단 규칙까지 강제하면, 규칙을 어긴 응답이 객체로 만들어지지도 않아서
"무엇이 잘못됐는지" 기록할 수가 없다. 그래서 규칙 검사는 Validator가 따로 한다.

입력 모델(Chunk, CandidateControl)은 전처리팀 `chunk_format.py`와
검색팀 ChromaDB 결과의 실제 필드에 맞췄다. 우리가 정한 이름이 아니다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from enums import (
    ChunkSource,
    ChunkType,
    Decision,
    ErrorCode,
    FileType,
    MatchStatus,
    ProcessingStatus,
    Relation,
    ReviewReason,
    ReviewStatus,
)

SCHEMA_VERSION = "0.3.0"

# 증적 번호 표기는 전처리팀 안에서 아직 갈려 있다 (E0001 / 000001 / 정수 1).
# 어느 쪽으로 정해져도 받을 수 있게 모양만 제한하고, 값 자체는 **문자열로 다룬다.**
# 노션 문서의 지적대로, 숫자로 저장하면 앞의 0이 사라져 DB와 대조가 안 된다.
EVIDENCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"

# 전처리팀 uploaded_at이 ISO 8601 + 09:00이므로 결과 시각도 같은 기준으로 남긴다.
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)

# 전처리팀 chunk_format.py와 맞춘 값
CHUNK_MAX_CHARS = 800
PAGED_TYPES = {FileType.PDF, FileType.XLSX, FileType.PPTX}


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# ==========================================================================
# 입력 — 전처리팀 (chunk_format.py CHUNK_KEYS 그대로)
# ==========================================================================


class Chunk(Base):
    """전처리팀이 넘겨주는 증적 청크. 필드 이름과 순서는 그쪽 규격을 따른다."""

    chunk_id: str = Field(min_length=1)
    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    version: int = Field(ge=1)
    chunk_index: int = Field(ge=0)
    file_type: FileType
    source_file: str = Field(min_length=1)
    chunk_type: ChunkType
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    heading: Optional[str] = None
    text: str = Field(min_length=1, max_length=CHUNK_MAX_CHARS)
    block_orders: list[int] = Field(min_length=1)
    source: ChunkSource = ChunkSource.PARSER

    @model_validator(mode="after")
    def _check_chunk_id(self) -> "Chunk":
        """chunk_id가 evidence_id·version·chunk_index와 맞는지 본다.

        표기 자체를 고정하지 않는 이유는 전처리팀 안에서도 아직 갈렸기 때문이다.
        (`E0001` / `000001` / 정수 1) 어느 쪽으로 정해지든,
        **chunk_id는 반드시 `{evidence_id}_v{version}_c{4자리}` 꼴이어야 한다**는
        관계는 변하지 않는다. 그 관계만 검사한다.
        """
        expected = f"{self.evidence_id}_v{self.version}_c{self.chunk_index:04d}"
        if self.chunk_id != expected:
            raise ValueError(f"chunk_id가 어긋난다. 기대값 {expected!r}, 실제 {self.chunk_id!r}")
        return self

    @model_validator(mode="after")
    def _check_pages(self) -> "Chunk":
        if self.file_type in PAGED_TYPES:
            if self.page_start is None or self.page_end is None:
                raise ValueError(f"{self.file_type.value}는 page_start/page_end가 필요하다")
            if self.page_start > self.page_end:
                raise ValueError("page_start가 page_end보다 클 수 없다")
        elif self.page_start is not None or self.page_end is not None:
            raise ValueError(f"{self.file_type.value}는 page가 null이어야 한다")
        return self

    def covers_page(self, page: Optional[int]) -> bool:
        """Citation의 페이지가 이 청크 범위 안인지. 페이지 없는 파일은 둘 다 null이어야 한다."""
        if self.page_start is None:
            return page is None
        if page is None:
            return False
        return self.page_start <= page <= self.page_end


# ==========================================================================
# 입력 — 검색팀 (ChromaDB query_rows 결과 그대로)
# ==========================================================================


class CandidateControl(Base):
    """검색팀이 넘겨주는 Top-K 후보.

    similarity_score = 1 - cosine_distance. 즉 코사인 유사도 그 자체다.
    BGE-M3 한국어 문장에서는 관련 있어도 0.5~0.6 부근에 몰린다.
    **점수의 절대값으로 관련성을 판단하지 않는다.**
    """

    rank: int = Field(ge=1)
    control_id: str = Field(pattern=r"^[123]\.\d+\.\d+$")
    control_name: str = Field(min_length=1)
    similarity_score: float = Field(ge=-1.0, le=1.0)
    distance: Optional[float] = Field(default=None, description="ChromaDB 코사인 거리 = 1 - score")
    requirement: Optional[str] = Field(
        default=None,
        description="통제항목 요구사항 본문. KB에 있으나 현재 검색 결과에는 실리지 않는다",
    )
    source_chunk_ids: list[str] = Field(
        default_factory=list,
        description="이 후보가 어느 청크 검색에서 나왔는지. 청크별 결과를 합칠 때 채운다",
    )


class MappingInput(Base):
    """판단 파트가 받는 입력 전체."""

    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    version: int = Field(ge=1)
    chunks: list[Chunk] = Field(min_length=1)
    candidate_controls: list[CandidateControl]
    top_k: Optional[int] = Field(default=None, ge=1, le=101)
    kb_sha256: Optional[str] = None
    model_revision: Optional[str] = None

    def chunk_map(self) -> dict[str, Chunk]:
        return {c.chunk_id: c for c in self.chunks}

    @property
    def chunk_id_prefix(self) -> str:
        """이 증적·버전의 청크가 가져야 할 chunk_id 접두사."""
        return f"{self.evidence_id}_v{self.version}_"

    @model_validator(mode="after")
    def _check_chunks_belong(self) -> "MappingInput":
        for c in self.chunks:
            if c.evidence_id != self.evidence_id or c.version != self.version:
                raise ValueError(f"다른 증적·버전의 청크가 섞였다: {c.chunk_id}")
        return self


# ==========================================================================
# 공통 조각
# ==========================================================================


class Citation(Base):
    """판단 근거가 되는 원문 인용."""

    chunk_id: str
    page: Optional[int] = None
    quote: str = Field(min_length=1, description="청크 text 그대로. 요약·의역 금지")


class VersionInfo(Base):
    """결과를 재현하기 위해 반드시 같이 저장한다."""

    schema_version: str = SCHEMA_VERSION
    prompt_version: str
    model_name: str
    ruleset_version: str = Field(description="판단 규칙 문서 버전")
    kb_sha256: Optional[str] = Field(default=None, description="검색팀 메타데이터의 KB 해시")
    embedding_model_revision: Optional[str] = None


# ==========================================================================
# LLM 원본 출력 (형식만 검사)
# ==========================================================================


class CandidateDecision(Base):
    """후보 하나에 대한 LLM의 판단. Top-K 후보 전부에 대해 하나씩 나온다."""

    control_id: str
    decision: Decision
    llm_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)


class LLMMappedControl(Base):
    """LLM이 최종 매핑으로 고른 통제항목."""

    control_id: str
    control_name: str
    relation: Relation
    llm_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)


class LLMMappingOutput(Base):
    """LLM이 반환해야 하는 JSON 구조. 프롬프트에 이 형식을 그대로 명시한다."""

    match_status: MatchStatus
    candidate_decisions: list[CandidateDecision]
    mapped_controls: list[LLMMappedControl] = Field(default_factory=list)


# ==========================================================================
# 검증 결과
# ==========================================================================


class ValidationIssue(Base):
    code: ErrorCode
    message: str
    control_id: Optional[str] = None
    chunk_id: Optional[str] = None
    field: Optional[str] = None


class ValidationResult(Base):
    """검증 결과.

    `issues`와 `warnings`는 성격이 다르다.

    | | 뜻 | passed에 반영 | 검토 전환 |
    |---|---|---|---|
    | `issues` | 결과가 **틀렸다** | 반영 | 대부분 보낸다 |
    | `warnings` | 결과는 맞는데 **근거가 부실하다** | 반영 안 함 | 보내지 않는다 |

    근거가 짧다고 사람을 부르면 검토량만 늘고 정확도는 안 오른다.
    `warnings`는 프롬프트를 고칠 자료로 모은다.
    """

    passed: bool
    schema_valid: bool
    control_ids_valid: bool
    citations_valid: bool
    rules_valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


# ==========================================================================
# Human Review
# ==========================================================================


class HumanReview(Base):
    required: bool
    status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    reasons: list[ReviewReason] = Field(default_factory=list)
    threshold_profile: Optional[str] = None
    reviewer_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_note: Optional[str] = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "HumanReview":
        if self.required and self.status == ReviewStatus.NOT_REQUIRED:
            raise ValueError("required=True인데 status가 NOT_REQUIRED일 수 없다")
        if not self.required and self.status != ReviewStatus.NOT_REQUIRED:
            raise ValueError("required=False인데 검토 상태가 붙을 수 없다")
        if self.required and not self.reasons:
            raise ValueError("검토가 필요하면 사유가 최소 1개 있어야 한다")
        return self


# ==========================================================================
# 최종 결과
# ==========================================================================


class MappedControl(LLMMappedControl):
    """검증을 통과한 최종 매핑. 검색 점수가 함께 붙는다.

    점수는 네 가지를 절대 섞지 않는다.
      similarity_score       검색팀의 코사인 유사도
      llm_confidence         LLM이 스스로 매긴 신뢰도. 정확도가 아니다
      validation.passed      기계적 검증 통과 여부
      human_review.required  사람이 봐야 하는가
    """

    similarity_score: Optional[float] = Field(default=None, ge=-1.0, le=1.0)


class Phase1MappingResult(Base):
    """저장되고 Phase 2로 전달되는 최종 결과."""

    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    version: int = Field(ge=1, description="증적 버전. 재업로드하면 올라간다")
    processing_status: ProcessingStatus
    match_status: MatchStatus
    mapped_controls: list[MappedControl] = Field(default_factory=list)
    candidate_decisions: list[CandidateDecision] = Field(default_factory=list)
    validation: ValidationResult
    human_review: HumanReview
    versions: VersionInfo
    llm_raw_response_ref: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: datetime = Field(default_factory=now_kst)
    processing_time_ms: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_result_shape(self) -> "Phase1MappingResult":
        if self.match_status == MatchStatus.NO_MATCH and self.mapped_controls:
            raise ValueError("NO_MATCH인데 mapped_controls가 비어 있지 않다")
        if (
            self.match_status == MatchStatus.MATCHED
            and not self.mapped_controls
            and self.processing_status == ProcessingStatus.COMPLETED
        ):
            raise ValueError("MATCHED인데 mapped_controls가 비어 있다")

        ids = [c.control_id for c in self.mapped_controls]
        if len(ids) != len(set(ids)):
            raise ValueError("mapped_controls에 중복 control_id가 있다")

        primary = [c for c in self.mapped_controls if c.relation == Relation.PRIMARY]
        if self.match_status == MatchStatus.MATCHED and len(primary) != 1:
            raise ValueError(f"MATCHED면 PRIMARY는 정확히 1개여야 한다 (현재 {len(primary)}개)")
        return self
