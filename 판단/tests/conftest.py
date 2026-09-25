import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models import (  # noqa: E402
    CandidateControl,
    Chunk,
    MappingInput,
    VersionInfo,
)


def make_chunk(
    index: int = 0,
    text: str = "사용자 계정은 관리자 승인 후 생성한다. 퇴직자 계정은 퇴직일 당일 삭제한다.",
    *,
    evidence_id: str = "E0001",
    version: int = 1,
    file_type: str = "pdf",
    page_start: int | None = 1,
    page_end: int | None = 1,
    chunk_type: str = "text",
    source: str = "parser",
) -> Chunk:
    return Chunk(
        chunk_id=f"{evidence_id}_v{version}_c{index:04d}",
        evidence_id=evidence_id,
        version=version,
        chunk_index=index,
        file_type=file_type,
        source_file="계정관리지침서.pdf",
        chunk_type=chunk_type,
        page_start=page_start,
        page_end=page_end,
        heading="2. 접근통제",
        text=text,
        block_orders=[index + 1],
        source=source,
    )


# 실제 KB에 있는 통제항목으로 구성한다. 가짜 ID를 쓰면 KB 검증 테스트가 무의미해진다.
@pytest.fixture
def mapping_input() -> MappingInput:
    return MappingInput(
        evidence_id="E0001",
        version=1,
        top_k=5,
        chunks=[
            make_chunk(0),
            make_chunk(1, "관리자 권한 부여는 보안팀장의 추가 승인을 받는다.", page_start=2, page_end=2),
        ],
        candidate_controls=[
            CandidateControl(rank=1, control_id="2.5.1", control_name="사용자 계정 관리",
                             similarity_score=0.5833),
            CandidateControl(rank=2, control_id="2.5.6", control_name="접근권한 검토",
                             similarity_score=0.5280),
            CandidateControl(rank=3, control_id="2.5.5", control_name="특수 계정 및 권한 관리",
                             similarity_score=0.5089),
        ],
    )


@pytest.fixture
def versions() -> VersionInfo:
    return VersionInfo(
        prompt_version="phase1_mapping_v0.1",
        model_name="qwen2.5-14b-instruct",
        ruleset_version="mapping_rules_v0.7",
    )


GOOD_RESPONSE = """{
  "match_status": "MATCHED",
  "candidate_decisions": [
    {"control_id": "2.5.1", "decision": "RELATED", "llm_confidence": 0.91,
     "reason": "계정 생성 승인과 삭제 절차가 명시된다.",
     "citations": [{"chunk_id": "E0001_v1_c0000", "page": 1,
                    "quote": "사용자 계정은 관리자 승인 후 생성한다."}]},
    {"control_id": "2.5.6", "decision": "NOT_RELATED", "llm_confidence": 0.86,
     "reason": "권한 검토 주기 언급이 없다.", "citations": []},
    {"control_id": "2.5.5", "decision": "NOT_RELATED", "llm_confidence": 0.80,
     "reason": "특수 계정 구분이 없다.", "citations": []}
  ],
  "mapped_controls": [
    {"control_id": "2.5.1", "control_name": "사용자 계정 관리", "relation": "PRIMARY",
     "llm_confidence": 0.91, "reason": "계정 등록·해지 절차가 문서의 주제다.",
     "citations": [{"chunk_id": "E0001_v1_c0000", "page": 1,
                    "quote": "사용자 계정은 관리자 승인 후 생성한다."}]}
  ]
}"""


@pytest.fixture
def good_response() -> str:
    return GOOD_RESPONSE
