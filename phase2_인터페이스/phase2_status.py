"""
phase2_status.py : 증적 하나의 상태 전이

새 상태값을 만들지 않는다. 입력팀 config.VALID_STATUSES 의 8개를 그대로 쓴다.
그중 MAPPING / VALIDATING / COMPLETED 세 개가 정의만 돼 있고 아무도 안 쓰고 있어서,
Phase 2 가 그 자리에 들어간다.

지금(2026-10-01) 실제로 쓰이는 값
    입력팀 pipeline.py   UPLOADED → PREPROCESSING → PREPROCESSED / FAILED
    판단팀 service.to_evidence_status()   FAILED / REVIEW_REQUIRED / COMPLETED
    아무도 안 씀          MAPPING, VALIDATING
"""
from __future__ import annotations

SPEC_VERSION = "phase2-status-0.1"

# ── 상태 8개. 입력팀 config.VALID_STATUSES 와 글자까지 같아야 한다 ──────────
STATUSES = (
    "UPLOADED",         # 파일만 올라왔다
    "PREPROCESSING",    # 파싱·청킹 중
    "PREPROCESSED",     # 청크 적재 끝. Phase 1 분석 대기
    "MAPPING",          # Phase 1 매핑 중          (혜진 WBS 의 'ANALYZING' 이 이것)
    "VALIDATING",       # Phase 2 판정 중 / 판정 대기
    "COMPLETED",        # Phase 2 까지 끝났다
    "REVIEW_REQUIRED",  # 사람 검토 대기
    "FAILED",           # 실패
)

# 각 상태가 어느 단계의 것인지. 화면에서 묶어 보여줄 때와 재시도 지점을 찾을 때 쓴다.
PHASE_OF = {
    "UPLOADED": "upload",
    "PREPROCESSING": "preprocess",
    "PREPROCESSED": "preprocess",
    "MAPPING": "phase1",
    "VALIDATING": "phase2",
    "COMPLETED": "done",
    "REVIEW_REQUIRED": "review",
    "FAILED": "failed",
}

# ── 전이표 ────────────────────────────────────────────────────────────────
# "여기서 갈 수 있는 곳" 만 적는다. 표에 없는 전이는 거부한다.
ALLOWED = {
    "UPLOADED":        {"PREPROCESSING", "FAILED"},
    "PREPROCESSING":   {"PREPROCESSED", "FAILED"},
    "PREPROCESSED":    {"MAPPING", "FAILED"},
    "MAPPING":         {"VALIDATING", "REVIEW_REQUIRED", "FAILED"},
    "VALIDATING":      {"COMPLETED", "REVIEW_REQUIRED", "FAILED"},
    # 검토가 끝나면 멈췄던 자리로 돌아간다. 어디서 왔는지는 resume_from() 으로 찾는다.
    "REVIEW_REQUIRED": {"MAPPING", "VALIDATING", "COMPLETED", "FAILED"},
    # 재시도. 처음부터가 아니라 터진 단계부터 다시 한다.
    "FAILED":          {"PREPROCESSING", "MAPPING", "VALIDATING"},
    # 끝. 같은 파일을 다시 올리면 version 이 올라가고 UPLOADED 로 새로 시작한다.
    # 하나만 예외 — 사람이 끝난 판정을 틀렸다고 보고 다시 열 때. 자동으로는 안 간다.
    # (phase2_review_store 에서 NOT_REQUIRED 판정을 수정·반려할 때만 생긴다)
    "COMPLETED":       {"REVIEW_REQUIRED"},
}

# 정상 흐름에서 더 갈 데가 없는 상태. ALLOWED 의 COMPLETED 에 REVIEW_REQUIRED 가
# 하나 열려 있지만, 그건 사람이 끝난 판정을 틀렸다고 보고 직접 다시 열 때만이다.
# 자동 처리는 여기서 멈춘다.
FINAL = ("COMPLETED",)
TERMINAL = FINAL          # 예전 이름. 쓰던 코드가 있으면 그대로 돈다.


def can(current: str, nxt: str) -> bool:
    """current → nxt 로 가도 되는가."""
    return nxt in ALLOWED.get(current, set())


def check(current: str, nxt: str) -> None:
    """못 가는 전이면 ValueError. update_status 앞에 끼워 쓰는 용도."""
    if current not in STATUSES:
        raise ValueError(f"모르는 상태값: {current}")
    if nxt not in STATUSES:
        raise ValueError(f"모르는 상태값: {nxt}")
    if not can(current, nxt):
        raise ValueError(
            f"허용되지 않은 상태 전이: {current} → {nxt}  "
            f"({current} 에서 갈 수 있는 곳: {sorted(ALLOWED[current]) or '없음'})"
        )


def validate_path(seq) -> None:
    """상태가 거쳐온 순서 전체를 검사한다. 테스트와 로그 점검에 쓴다."""
    for a, b in zip(seq, seq[1:]):
        check(a, b)


# ── Phase 결과 → evidence.status ─────────────────────────────────────────
def from_phase1(processing_status: str, review_required: bool,
                phase2_enabled: bool = True) -> str:
    """
    판단팀 to_evidence_status() 를 Phase 2 가 있는 상황에 맞춘 것.

        processing_status = FAILED   → FAILED
        review_required             → REVIEW_REQUIRED
        그 외, Phase 2 를 돌릴 것     → VALIDATING   (★ 판단팀 원본은 COMPLETED)
        그 외, Phase 2 를 안 돌릴 것   → COMPLETED
    """
    if processing_status == "FAILED":
        return "FAILED"
    if review_required:
        return "REVIEW_REQUIRED"
    return "VALIDATING" if phase2_enabled else "COMPLETED"


def from_phase2(processing_status: str, review_required: bool,
                phase1_review_open: bool) -> str:
    """
    Phase 2 판정 결과 → evidence.status. Phase 1 과 같은 모양으로 맞춘다.

        processing_status = FAILED   → FAILED
        review_required             → REVIEW_REQUIRED
        phase1_review_open          → REVIEW_REQUIRED
        그 외                        → COMPLETED

    phase1_review_open 은 Phase 1 결과의 human_review.required 를 그대로 넘긴다.
    """
    if processing_status == "FAILED":
        return "FAILED"
    if review_required or phase1_review_open:
        return "REVIEW_REQUIRED"
    return "COMPLETED"


# ── 검토·실패에서 어디로 돌아가나 ──────────────────────────────────────────
# ★ 미합의 — REVIEW_REQUIRED 와 FAILED 는 Phase 1 에서도 Phase 2 에서도 온다.
#   그런데 evidence.status 는 칸이 하나뿐이라 어느 단계에서 멈췄는지 적을 자리가 없다.
#   status 만 보고는 복구 지점을 알 수 없으므로, 아래 둘 중 하나로 판별한다.
#     (a) 결과물 유무   Phase 2 출력이 있으면 Phase 2 단계에서 멈춘 것   스키마 변경 없음
#     (b) 전이 기록     evidence_status_history 테이블을 새로 만든다      DB 변경 필요
#   지금은 (a) 를 쓰고, 호출하는 쪽이 phase 를 넘겨준다.
RESUME_FROM = {
    "phase1": "MAPPING",       # Phase 1 에서 멈췄으면 매핑부터 다시
    "phase2": "VALIDATING",    # Phase 2 에서 멈췄으면 판정부터 다시
    "preprocess": "PREPROCESSING",
}


def resume_from(phase: str) -> str:
    """REVIEW_REQUIRED·FAILED 에서 다시 시작할 상태."""
    if phase not in RESUME_FROM:
        raise ValueError(f"모르는 단계: {phase} (허용: {sorted(RESUME_FROM)})")
    return RESUME_FROM[phase]


def approve_review(phase: str, modified: bool) -> str:
    """
    사람이 검토를 끝냈을 때 갈 상태.
        고친 게 있다 → 그 단계부터 다시 돌린다
        그대로 승인   → Phase 1 검토였으면 판정으로, Phase 2 검토였으면 끝

    ★ phase 를 먼저 검사한다. 모르는 값이 들어오면 조용히 COMPLETED 를 내지 않고
      ValueError 를 낸다. 오타 하나로 검토 중인 증적이 '완료' 로 넘어가 버리면
      사람이 올린 검토 결과가 사라진다.
    """
    if phase not in RESUME_FROM:
        raise ValueError(f"모르는 단계: {phase} (허용: {sorted(RESUME_FROM)})")
    if modified:
        return resume_from(phase)
    return "VALIDATING" if phase == "phase1" else "COMPLETED"
