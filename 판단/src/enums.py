"""Phase 1 판단 파트 공통 Enum.

여기 있는 값은 판단팀이 소유한다. 변경하려면 판단팀 리뷰가 필요하다.
다른 파트는 이 모듈을 import해서 쓰고, 같은 의미의 문자열을 따로 정의하지 않는다.
"""

from enum import Enum


class StrEnum(str, Enum):
    """JSON 직렬화 시 문자열로 나가는 Enum."""

    def __str__(self) -> str:
        return self.value


# --------------------------------------------------------------------------
# 입력 쪽 값 — 전처리팀 chunk_format.py와 맞춘다. 우리가 바꾸지 않는다.
# --------------------------------------------------------------------------


class FileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    TXT = "txt"
    CSV = "csv"


class ChunkType(StrEnum):
    TEXT = "text"
    TABLE = "table"


class ChunkSource(StrEnum):
    PARSER = "parser"
    OCR = "ocr"          # OCR로 읽은 청크. 품질 점수가 없으므로 이 값 자체가 검토 신호다


# --------------------------------------------------------------------------
# 판단 결과
# --------------------------------------------------------------------------


class MatchStatus(StrEnum):
    """증적 하나에 대한 최종 매핑 결과 상태."""

    MATCHED = "MATCHED"        # 관련 통제항목이 1개 이상
    NO_MATCH = "NO_MATCH"      # 관련 통제항목 없음 (후보는 있었으나 전부 무관)


class Relation(StrEnum):
    """매핑된 통제항목 하나가 증적과 맺는 관계."""

    PRIMARY = "PRIMARY"        # 증적의 목적과 가장 직접적으로 연결. MATCHED면 정확히 1개
    RELATED = "RELATED"        # 일부 내용이 직접 관련되는 추가 통제항목. 0개 이상


class Decision(StrEnum):
    """Top-K 후보 하나에 대한 LLM의 판단값."""

    RELATED = "RELATED"
    NOT_RELATED = "NOT_RELATED"
    UNCERTAIN = "UNCERTAIN"    # 판단 보류. 무조건 Human Review로 보낸다


# --------------------------------------------------------------------------
# 상태 (처리 상태와 검토 상태는 반드시 분리한다)
# --------------------------------------------------------------------------


class ProcessingStatus(StrEnum):
    """판단 파이프라인의 기계적 처리 상태."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EvidenceStatus(StrEnum):
    """전처리팀 DB `evidence.status`. **우리가 소유하지 않는다.**

    입력팀 규격에는 처리 상태와 검토 상태가 한 칸에 섞여 있다(REVIEW_REQUIRED).
    판단 파트는 둘을 나눠서 관리하고, 백엔드에 넘길 때만 이 값으로 옮긴다.
    변환 규칙은 service.to_evidence_status()에 있다.
    """

    UPLOADED = "UPLOADED"
    PREPROCESSING = "PREPROCESSING"
    PREPROCESSED = "PREPROCESSED"
    MAPPING = "MAPPING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"


class ReviewStatus(StrEnum):
    """사람의 검토 상태."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"


# --------------------------------------------------------------------------
# 오류 코드
# --------------------------------------------------------------------------


class ErrorCode(StrEnum):
    """판단 파트에서 발생하는 오류.

    코드 체계
      E1xx  LLM 호출 실패
      E2xx  출력 형식 오류
      E3xx  통제항목 ID 오류
      E4xx  Citation 오류
      E5xx  판단 규칙 위반
    """

    # E1xx — LLM 호출
    LLM_TIMEOUT = "E101"
    LLM_CONNECTION_FAILED = "E102"
    LLM_SERVER_ERROR = "E103"
    LLM_EMPTY_RESPONSE = "E104"
    LLM_TRUNCATED_RESPONSE = "E105"
    LLM_RETRY_EXHAUSTED = "E106"

    # E2xx — 출력 형식
    JSON_PARSE_FAILED = "E201"
    SCHEMA_INVALID = "E202"
    REQUIRED_FIELD_MISSING = "E203"
    INVALID_ENUM_VALUE = "E204"
    CONFIDENCE_OUT_OF_RANGE = "E205"

    # E3xx — 통제항목 ID
    CONTROL_ID_NOT_IN_KB = "E301"
    CONTROL_ID_NOT_IN_CANDIDATES = "E302"
    CONTROL_NAME_MISMATCH = "E303"
    DUPLICATE_CONTROL_ID = "E304"

    # E4xx — Citation
    CITATION_MISSING = "E401"
    CITATION_CHUNK_NOT_FOUND = "E402"
    CITATION_QUOTE_NOT_IN_SOURCE = "E403"
    CITATION_PAGE_MISMATCH = "E404"
    CITATION_EMPTY_QUOTE = "E405"
    CITATION_FOREIGN_EVIDENCE = "E406"

    # E5xx — 판단 규칙
    PRIMARY_COUNT_INVALID = "E501"
    TOO_MANY_MAPPED_CONTROLS = "E502"
    NO_MATCH_WITH_CONTROLS = "E503"
    MATCHED_WITHOUT_CONTROLS = "E504"
    ADEQUACY_JUDGMENT_DETECTED = "E505"  # Phase 1에서 적정성 판정을 생성한 경우

    # 아래 둘은 **검토로 보내지 않는다.** 틀린 결과가 아니라 근거가 부실한 결과다.
    # BLOCKING_ERROR_CODES에 넣지 않고 기록만 해서 프롬프트 개선 자료로 쓴다.
    REASON_TOO_SHORT = "E506"            # 판단 근거가 너무 짧다
    REASON_NOT_SPECIFIC = "E507"         # 근거가 인용문 복붙이거나 통제항목 명칭 반복


class ReviewReason(StrEnum):
    """Human Review로 보내는 사유."""

    # 무조건 검토
    JSON_PARSE_FAILED = "R101"
    SCHEMA_INVALID = "R102"
    CONTROL_ID_INVALID = "R103"
    CITATION_INVALID = "R104"
    CITATION_MISSING = "R105"
    PRIMARY_COUNT_INVALID = "R106"
    UNCERTAIN_DECISION = "R107"
    LLM_CALL_FAILED = "R108"
    PROMPT_INJECTION_SUSPECTED = "R109"

    # 조건부 검토
    LOW_CONFIDENCE = "R201"
    NARROW_SCORE_GAP = "R202"
    TOO_MANY_CONTROLS = "R203"
    NO_MATCH_RESULT = "R204"
    MULTI_MAPPING_RESULT = "R205"
    RETRIEVER_LLM_CONFLICT = "R206"
    OCR_SOURCE = "R207"          # OCR 청크를 인용. 전처리팀이 품질 점수를 주지 않는다
    CHUNK_TOO_SHORT = "R208"
