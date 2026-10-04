"""
phase2_errors.py : Phase 2 오류·재시도·검토 전환

큰 원칙 두 개
    1. Phase 1 것을 최대한 그대로 쓴다.
       phase2_판단/src/judgment_harness.py 가 이미 판단팀 ErrorCode 와 RetryPolicy 를
       import 해서 쓰고 있다. LLM 호출 실패(E1xx)와 출력 형식 오류(E2xx)는
       Phase 1 코드를 그대로 쓰고, 여기서 새로 만들지 않는다.
       Phase 2 에만 있는 상황만 P2E 로 추가한다.

    2. 문항 하나가 실패해도 통제항목 전체를 실패시키지 않는다.
       판단팀이 Phase 1 에서 세운 원칙("판단 불가여도 결과 객체는 반드시 만든다")을 따른다.
       문항 하나가 터지면 그 문항만 UNKNOWN 으로 두고 검토 사유를 붙인다.
       통제항목은 나머지 문항으로 계속 간다.
       한 질문지가 647문항인데 한 문항 때문에 전부 날리면 다시 돌리는 비용이 너무 크다.

"""
from __future__ import annotations

import warnings

SPEC_VERSION = "phase2-errors-0.1"

# ──────────────────────────────────────────────────────────────────────────
# 1. Phase 1 에서 그대로 쓰는 것 (여기서 다시 정의하지 않는다)
# ──────────────────────────────────────────────────────────────────────────
# phase1_판단/src/enums.py 의 ErrorCode 중 Phase 2 에도 그대로 해당하는 값.
# 이 목록은 "우리가 안 만든다"는 선언이다. 값은 판단팀이 소유한다.
REUSED_FROM_PHASE1 = {
    # E1xx LLM 호출
    "E101": "LLM_TIMEOUT",
    "E102": "LLM_CONNECTION_FAILED",
    "E103": "LLM_SERVER_ERROR",
    "E104": "LLM_EMPTY_RESPONSE",
    "E105": "LLM_TRUNCATED_RESPONSE",
    "E106": "LLM_RETRY_EXHAUSTED",
    # E2xx 출력 형식
    "E201": "JSON_PARSE_FAILED",
    "E202": "SCHEMA_INVALID",
    "E203": "REQUIRED_FIELD_MISSING",
    "E204": "INVALID_ENUM_VALUE",
    # E4xx 인용 — Phase 2 의 citations 도 같은 규칙으로 본다
    "E401": "CITATION_MISSING",
    "E402": "CITATION_CHUNK_NOT_FOUND",
    "E403": "CITATION_QUOTE_NOT_IN_SOURCE",
    "E404": "CITATION_PAGE_MISMATCH",
    "E405": "CITATION_EMPTY_QUOTE",
    "E406": "CITATION_FOREIGN_EVIDENCE",
    "E407": "CITATION_PAGE_MISSING",
}


# ──────────────────────────────────────────────────────────────────────────
# 2. Phase 2 에만 있는 오류
# ──────────────────────────────────────────────────────────────────────────
# P2E0xx  입력 — 판정을 시작하기 전에 걸러진다 (통제항목 단위)
# P2E3xx  문항·질문지
# P2E5xx  판정 규칙
#
# P2E1xx·P2E2xx·P2E4xx 는 비워둔다. 위 REUSED_FROM_PHASE1 자리다.
ERRORS = {
    # ── P2E0xx 입력 ────────────────────────────────────────────────────
    "P2E001": ("NO_JUDGE_TARGET",
               "judge=true 인 통제항목이 하나도 없다. "
               "RELATED 만 매핑된 증적이다. 실패가 아니라 '증적 없음'으로 낸다."),
    "P2E002": ("CHECKLIST_SCOPE_MISSING",
               "그 통제항목의 질문지가 지금 체크리스트 판에 없다. "
               "실측: 판단팀 기록 46건 중 2건 (둘 다 3.4.1, 3장 항목)."),
    "P2E003": ("NO_CHUNKS",
               "판정할 청크가 0개. phase2_판단 ContextBuildError('NO_CHUNKS') 와 같은 상황."),
    "P2E004": ("INVALID_CHUNK",
               "청크 모양이 잘못됐다. phase2_판단 ContextBuildError('INVALID_CHUNK') 와 같은 상황."),
    "P2E005": ("INPUT_MALFORMED",
               "phase2_input.schema.json 위반."),
    "P2E006": ("CHECKLIST_NOT_APPROVED",
               "미승인 체크리스트로 판정했다. "
               "지금 chapter2_full_checklist_draft.json 의 integration_state.human_approved 가 "
               "false 라 전건에 해당한다. 그래서 검토 사유로는 쓰지 않고 기록만 한다."),
    "P2E007": ("DUPLICATE_CHUNK_ID",
               "같은 chunk_id 가 두 번. phase2_판단 ContextBuildError 와 같은 이름."),
    "P2E008": ("NO_EVIDENCE_ANCHOR",
               "Phase 1 citation 이 가리키는 핵심 청크가 없다. "
               "임의 청크를 근거로 고르지 않는다 — phase2_판단이 세운 규칙을 따른다."),
    "P2E009": ("ANCHOR_CHUNK_NOT_FOUND",
               "Phase 1 citation 의 chunk_id 가 지금 증적 청크에 없다. "
               "증적 version 이 올라갔는데 Phase 1 결과를 그대로 쓴 신호다. 다시 매핑해야 한다."),

    # ── P2E3xx 문항·질문지 ──────────────────────────────────────────────
    "P2E301": ("ITEM_ID_NOT_IN_CHECKLIST",
               "LLM 이 질문지에 없는 item_id 를 만들어냈다."),
    "P2E302": ("ITEM_COUNT_MISMATCH",
               "판정한 문항 수가 질문지의 문항 수와 다르다. 빠뜨렸거나 지어냈다."),
    "P2E303": ("DUPLICATE_ITEM_ID",
               "같은 item_id 가 두 번."),

    # ── P2E5xx 판정 규칙 ────────────────────────────────────────────────
    "P2E501": ("CITATION_REQUIRED_MISSING",
               "result 가 MET 또는 NOT_MET 인데 citations 가 비었다. "
               "기준팀 citation_required_for = ['MET','NOT_MET'] 위반."),
    "P2E502": ("REASON_CODE_MISMATCH",
               "result 와 reason_codes 의 접두사가 안 맞는다. "
               "NOT_MET 이면 P2_NM_*, UNKNOWN 이면 P2_U_*."),
    "P2E503": ("CRITICAL_UNDECIDED",
               "critical 을 정할 수 없다. check_kind 도 critical 도 비어 있는 문항."),
    "P2E504": ("OVERALL_RULE_CONFLICT",
               "들어온 overall_result 가 items 로 계산한 값과 다르다. "
               "종합 판정은 LLM 이 아니라 규칙이 계산한다."),
    "P2E505": ("ADEQUACY_WITHOUT_EVIDENCE",
               "MET 인데 인용이 입력 청크에 없는 문장이다. LLM 이 지어낸 경우."),
    "P2E506": ("REASON_TOO_SHORT",
               "판정 근거가 너무 짧다. 판단팀 validators.REASON_MIN_CHARS(=10)와 같은 기준. "
               "★ 검토로 보내지 않는다 — 틀린 결과가 아니라 근거가 부실한 결과다. "
               "판단팀이 E506 을 그렇게 다루는 것과 맞춘다. 프롬프트 개선 자료로만 쓴다."),

    # ── P2E9xx 배치 ────────────────────────────────────────────────────
    "P2E901": ("BATCH_ABORTED",
               "연속 실패가 많아 배치를 중단했다. 아래 BATCH_ABORT_AFTER 참조."),
}


# ──────────────────────────────────────────────────────────────────────────
# 3. 사람 검토 사유
# ──────────────────────────────────────────────────────────────────────────
# Phase 1 의 R1xx·R2xx 와 겹치지 않게 P2R 접두사를 쓴다.
#   P2R1xx  무조건 검토
#   P2R2xx  조건부 검토 (임계값에 걸리면)
REVIEW_REASONS = {
    # ── 무조건 ────────────────────────────────────────────────────────
    "P2R101": ("JSON_PARSE_FAILED", "재시도까지 했는데 JSON 이 안 나왔다"),
    "P2R102": ("SCHEMA_INVALID", "출력이 규격에 안 맞는다"),
    "P2R103": ("ITEM_ID_INVALID", "질문지에 없는 문항이거나 문항 수가 다르다"),
    "P2R104": ("CITATION_INVALID", "인용이 청크 원문과 다르다"),
    "P2R105": ("CITATION_MISSING", "MET·NOT_MET 인데 근거가 없다"),
    "P2R106": ("LLM_CALL_FAILED", "LLM 을 끝내 못 불렀다"),
    "P2R107": ("ITEM_NOT_JUDGEABLE", "청크나 질문지가 없어 판정을 시도하지 못했다"),
    "P2R108": ("PROMPT_INJECTION_SUSPECTED", "증적 본문에 지시문처럼 보이는 내용이 있다"),
    "P2R109": ("OVERALL_RULE_CONFLICT", "종합 판정이 규칙과 어긋난다"),
    "P2R110": ("PHASE1_RESULT_STALE",
               "Phase 1 결과가 가리키는 청크가 지금 증적에 없다. 증적 version 이 올라간 것"),
    "P2R111": ("SELF_CHECK_UNSUPPORTED",
               "Self-check 가 그 판정을 뒷받침하지 못한다. 인용문은 원문에 있지만 "
               "그것만으로는 판정이 성립하지 않거나, 체크리스트에 없는 조건을 지어냈다"),
    "P2R112": ("SELF_CHECK_CONFLICT",
               "Self-check 가 같은 범위의 근거끼리 어긋난다고 봤다"),
    "P2R113": ("REVIEW_SIGNAL_UNMAPPED",
               "SELF_CHECK_SIGNALS 에 없는 검토 신호가 왔다. 번역표에 그 신호를 "
               "더하기 전까지 이 결과는 provisional 이다"),

    # ── 조건부 ────────────────────────────────────────────────────────
    "P2R201": ("CRITICAL_NOT_MET",
               "critical 문항이 미충족이다. 결함 판정이라 사람이 한 번 본다"),
    "P2R202": ("MANY_UNKNOWN",
               "UNKNOWN 비율이 임계 이상이다. 증적이 부족하거나 질문지가 안 맞는 것"),
    "P2R203": ("OCR_SOURCE",
               "OCR 청크를 인용했다. 전처리팀이 품질 점수를 주지 않는다. Phase 1 R207 과 같은 이유"),
    "P2R204": ("ALL_UNKNOWN", "문항이 전부 UNKNOWN 이다. 사실상 판정이 안 됐다"),
    "P2R205": ("SELF_CHECK_UNCERTAIN",
               "Self-check 가 확신하지 못했거나 확신도가 임계 미만이다. "
               "'틀렸다'가 아니라 '모르겠다'라서 임계값과 같이 본다"),
    # 근거가 짧은 것(P2E506)은 여기 넣지 않는다. 판단팀이 E506 을 검토로 안 보내는 것과 맞춘다.
}

# 무조건 검토인 것
UNCONDITIONAL = tuple(k for k in REVIEW_REASONS if k.startswith("P2R1"))


# ──────────────────────────────────────────────────────────────────────────
# 3-2. Self-check 신호 → 검토 사유
#
# 판단팀 validated_pipeline.py 는 MET·NOT_MET 이 나오면 Self-check 로 한 번 더
# 되묻고, 그 결과를 "SELF_CHECK_" + verdict 모양의 신호로 audit.review_signals
# 에 남긴다. 그 신호를 공식 P2R 코드로 번역하는 표다.
#
# ★ 이 표가 없어서 2026-10-03 전체 시험에서 4개 증적(E0002·E0010·E0021·E0025)
#   60문항의 최종 출력이 보류됐다 (audit.review_contract_pending = true).
#   판단팀은 이미 신호를 내고 있었고, 받을 코드가 없었던 것이다.
#
# verdict 는 self_check.py 의 enum 4개다 — SUPPORTED / UNSUPPORTED /
# CONFLICT / UNCERTAIN. SUPPORTED 는 검토가 아니므로 표에 없다.
SELF_CHECK_SIGNALS = {
    "SELF_CHECK_UNSUPPORTED": "P2R111",
    "SELF_CHECK_CONFLICT": "P2R112",
    "SELF_CHECK_UNCERTAIN": "P2R205",
    "SELF_CHECK_LOW_CONFIDENCE": "P2R205",   # 확신도가 임계 미만. UNCERTAIN 과 같이 본다
    "SELF_CHECK_NOT_RUN": "P2R106",          # Self-check 를 아예 못 돌렸다 = LLM 호출 실패
}

# 표에 없는 신호가 왔을 때 붙이는 코드와, provisional_reason 에 쓰는 머리말.
UNMAPPED_SIGNAL_REASON = "P2R113"
UNMAPPED_SIGNAL_PREFIX = "UNMAPPED_REVIEW_SIGNAL"


def review_reason_for_signal(signal):
    """
    Self-check 신호 하나를 공식 검토 사유 코드로 바꾼다.
    모르는 신호면 None 을 돌려준다 — 조용히 삼키지 않고 호출한 쪽이 알게 한다.
    """
    return SELF_CHECK_SIGNALS.get(signal)


def review_reasons_for_signals(signals):
    """
    audit.review_signals 목록을 받아 (사유 코드들, 번역 못 한 신호들) 로 나눈다.

        codes, unknown = review_reasons_for_signals(audit["review_signals"])

    unknown 이 비어 있지 않으면 이 표에 값을 더해야 한다는 뜻이다.
    그대로 두면 또 출력 보류가 난다.
    """
    codes, unknown = [], []
    for s in signals or []:
        hit = SELF_CHECK_SIGNALS.get(s)
        if hit is None:
            if s not in unknown:
                unknown.append(s)
        elif hit not in codes:
            codes.append(hit)
    return codes, unknown


# ──────────────────────────────────────────────────────────────────────────
# 4. 오류 → 검토 사유
# ──────────────────────────────────────────────────────────────────────────
# 판단팀 review_policy.BLOCKING_ERROR_CODES 와 같은 역할.
# 여기 없는 오류는 검토로 보내지 않고 기록만 한다.
BLOCKING = {
    # Phase 1 에서 그대로 오는 것
    "E101": "P2R106", "E102": "P2R106", "E103": "P2R106",
    "E104": "P2R106", "E105": "P2R106", "E106": "P2R106",
    "E201": "P2R101",
    "E202": "P2R102", "E203": "P2R102", "E204": "P2R102",
    "E401": "P2R105", "E405": "P2R105",
    "E402": "P2R104", "E403": "P2R104", "E404": "P2R104", "E406": "P2R104",
    # Phase 2 전용
    "P2E003": "P2R107", "P2E004": "P2R107", "P2E005": "P2R107",
    "P2E007": "P2R107", "P2E008": "P2R107",
    "P2E009": "P2R110",          # 버전 어긋남 — 다시 매핑해야 한다
    "P2E301": "P2R103", "P2E302": "P2R103", "P2E303": "P2R103",
    "P2E501": "P2R105",
    "P2E502": "P2R102",
    "P2E504": "P2R109",
    "P2E505": "P2R104",
}

# 검토로 보내지 않고 기록만 하는 오류. 틀린 결과가 아니라서다.
#   P2E001  판정 대상이 없는 것은 정상 결과('증적 없음')다
#   P2E002  질문지가 아직 없는 것은 증적 문제가 아니다
#   P2E006  지금은 전건에 해당해서 검토 신호가 되지 못한다
#   P2E407  페이지 없음은 경고 (Phase 1 과 동일)
#   P2E503  critical 미정은 critical_policy 에 이미 기록된다
#   P2E506  근거가 짧은 것은 틀린 결과가 아니다 (판단팀 E506 과 같은 취급)
#   P2E901  배치 중단은 결과 하나의 문제가 아니라 운영 사건이다
RECORD_ONLY = ("P2E001", "P2E002", "P2E006", "E407", "P2E503",
               "P2E506", "P2E901")


# ──────────────────────────────────────────────────────────────────────────
# 5. 임계값
#    실제로 돌려본 뒤 조정할 값이다. 스키마에 안 들어가고 다른 파트가 보지 않으므로
#    언제든 여기서 고치면 된다. 합의 안건이 아니다. (결정 2026-10-02)
# ──────────────────────────────────────────────────────────────────────────
THRESHOLDS = {
    # 한 통제항목의 문항 중 UNKNOWN 비율이 이 이상이면 P2R202
    "many_unknown_ratio": 0.5,
    # 판정 근거가 이 글자 수보다 짧으면 P2E506 (검토가 아니라 기록만)
    # 판단팀 validators.REASON_MIN_CHARS 와 같은 값을 쓴다. 한국어라 20자는 너무 길었다.
    "min_reason_length": 10,
    # critical 문항이 미충족일 때 검토로 보낼지
    "review_on_critical_not_met": True,
    # 전부 UNKNOWN 일 때 검토로 보낼지
    "review_on_all_unknown": True,
}


# ──────────────────────────────────────────────────────────────────────────
# 6. 재시도
# ──────────────────────────────────────────────────────────────────────────
# 판단팀 DEFAULT_RETRY_POLICY 를 그대로 쓴다. 새 정책을 만들지 않는다.
#   max_retries 1 / timeout 60초 / backoff 2.0초
#   retry_on : LLM_TIMEOUT, LLM_CONNECTION_FAILED, LLM_SERVER_ERROR,
#              LLM_EMPTY_RESPONSE, LLM_TRUNCATED_RESPONSE,
#              JSON_PARSE_FAILED, SCHEMA_INVALID
#
# ── 배치 중단 : 지금은 구현하지 않는다 (결정 2026-10-02) ─────────────
#   계산해보면, LLM 서버가 죽은 상태로 647문항을 돌리면
#   (1+1회 × 60초 타임아웃) 최악 21.6시간을 버린다.
#
#   그래도 넣지 않기로 했다.
#     - 문항마다 결과가 찍히므로 전부 실패하면 5분 안에 눈에 띈다
#     - 멈추고 싶으면 Ctrl+C 로 된다. 이미 되는 걸 코드로 또 만드는 셈이다
#     - 시간만 버릴 뿐 데이터가 깨지지 않는다. 실패한 문항은 UNKNOWN 으로 남는다
#     - 7명이 손으로 돌리는 도구다. 아무도 안 보는 채로 밤새 도는 상황이 없다
#
#   아래 상수는 지운 게 아니라 남겨둔다. 아무도 호출하지 않으면 아무 일도
#   일어나지 않는다. 나중에 실제로 당하거나 무인 실행이 생기면 그때 쓴다.
BATCH_ABORT_AFTER = 5          # 연속 실패가 이만큼이면 배치 중단 (P2E901)
BATCH_ABORT_ON = ("E101", "E102", "E103")   # 서버가 죽은 신호일 때만

RETRY_NOTE = (
    "재시도는 판단팀 DEFAULT_RETRY_POLICY 를 그대로 쓴다 "
    "(max_retries=1, timeout=60s, backoff=2.0s). "
    "배치 중단은 지금 구현하지 않는다 — 상수만 남겨뒀다."
)


# ──────────────────────────────────────────────────────────────────────────
# 7. 문항 하나가 실패했을 때
# ──────────────────────────────────────────────────────────────────────────
# 기술 실패 문항에 붙일 기준팀 사유 코드 (임시).
#   13종 중 기술 실패에 가장 가까운 값. '근거를 읽어 판정하지 못했다' 는 뜻으로 쓴다.
#   기준팀이 전용 코드를 새로 만들어 주면 이 한 줄만 바꾸면 된다.
TECH_REASON_CODE = "P2_U_EVIDENCE_UNREADABLE"


def failed_item(item_id, error_code, message, check_kind=None):
    """
    문항 하나가 끝내 실패했을 때 만들 결과.

    ★ 통제항목 전체를 실패시키지 않는다. 그 문항만 UNKNOWN 으로 둔다.
      - NOT_MET 이 아니다. 요건을 안 지켰다고 확인한 게 아니라 확인을 못 한 것이다.
      - citations 는 비운다. UNKNOWN 은 비워도 되는 유일한 값이다.

    ★ reason_codes 는 비우지 않는다.
      기준팀 reason_codes.py _assignment() 이
      "NOT_MET·UNKNOWN 에는 사유 코드가 필요합니다" 로 빈 배열을 거부한다.
      기술 실패에 딱 맞는 코드가 13종 안에 없어서 TECH_REASON_CODE 로 임시 매핑한다.
      ─ 기준팀에 '기술 실패용 사유 코드' 신설 여부를 물어야 한다 (ISSUES).
      정확한 원인은 error_code 가 들고 있으므로 정보가 사라지지는 않는다.

    ★ check_kind 는 모를 때 키 자체를 뺀다.
      출력 스키마가 enum(procedure·record·implementation) 이라
      null 을 넣으면 그 문항이 규격 위반이 된다.
    """
    name = (ERRORS.get(error_code, (None,))[0]
            or REUSED_FROM_PHASE1.get(error_code)
            or error_code)
    item = {
        "item_id": item_id,
        "result": "UNKNOWN",
        "reason": f"판정하지 못했습니다 ({name}): {message}"[:300],
        "reason_codes": [TECH_REASON_CODE],
        "citations": [],
        "error_code": error_code,
    }
    if check_kind is not None:
        item["check_kind"] = check_kind
    return item


# ──────────────────────────────────────────────────────────────────────────
# 8. 검토 필요 여부 판정
# ──────────────────────────────────────────────────────────────────────────
def decide_review(items, errors=(), thresholds=None, injection_suspected=False,
                  review_signals=None):
    """
    통제항목 하나의 결과를 보고 사람 검토가 필요한지 정한다.
    판단팀 review_policy.decide() 와 같은 모양으로 맞춘다.

    items          : 문항별 판정 결과
    errors         : 처리 중 생긴 오류 코드 목록
    review_signals : 판단팀 audit["review_signals"] 목록 (선택).
                     Self-check 신호를 공식 사유 코드로 바꿔 함께 담는다.
    돌려주는 값 : {"required": bool, "reasons": [코드...]}
    """
    th = {**THRESHOLDS, **(thresholds or {})}
    reasons = []

    def add(code):
        if code not in reasons:
            reasons.append(code)

    # 1. 무조건 — 오류에서 온 것
    for code in errors:
        hit = BLOCKING.get(code)
        if hit:
            add(hit)

    # 1-2. 무조건 — Self-check 신호에서 온 것
    #   번역 못 한 신호는 결과에 남긴다. 경고만 내면 결과 JSON 에 흔적이 없다.
    sc_codes, sc_unknown = review_reasons_for_signals(review_signals)
    for code in sc_codes:
        add(code)
    if sc_unknown:
        add(UNMAPPED_SIGNAL_REASON)
        warnings.warn(
            "모르는 Self-check 신호가 있습니다: " + ", ".join(sc_unknown) +
            " — phase2_errors.SELF_CHECK_SIGNALS 에 추가해야 합니다.",
            stacklevel=2)

    if injection_suspected:
        add("P2R108")

    if not items:
        # ★ 문항이 없는 두 경우를 가른다. evidence_outcome() 과 같은 기준을 쓴다.
        #   P2E001 (judge=true 가 없다) · P2E002 (질문지가 없다) 만 있으면
        #   정상 결과 '증적 없음' 이다 — 사람이 볼 게 없으므로 검토로 올리지 않는다.
        #   그 밖의 이유로 문항이 비었으면 판정을 시도했다가 못 한 것이므로 검토다.
        soft_only = bool(errors) and all(c in ("P2E001", "P2E002") for c in errors)
        if not soft_only:
            add("P2R107")
        return _build(reasons, sc_unknown)

    total = len(items)
    unknown = [i for i in items if i.get("result") == "UNKNOWN"]

    # 2. 조건부
    if th["review_on_all_unknown"] and unknown and len(unknown) == total:
        add("P2R204")
    elif unknown and len(unknown) / total >= th["many_unknown_ratio"]:
        add("P2R202")

    if th["review_on_critical_not_met"]:
        if any(i.get("critical") and i.get("result") == "NOT_MET" for i in items):
            add("P2R201")

    # OCR 청크를 인용했으면 — 전처리팀이 품질 점수를 주지 않는다
    for i in items:
        for c in i.get("citations") or []:
            if c.get("source") == "ocr":
                add("P2R203")

    return _build(reasons, sc_unknown)


def _build(reasons, unmapped=()):
    r = {"required": bool(reasons), "reasons": reasons}
    if unmapped:
        r["unmapped_signals"] = list(unmapped)
    return r


def provisional_fields(review):
    """
    decide_review() 결과를 받아 출력에 붙일 provisional 두 필드를 돌려준다.
    번역 못 한 신호가 없으면 빈 dict 이므로 그대로 update() 하면 된다.

        out.update(provisional_fields(review))
    """
    unmapped = (review or {}).get("unmapped_signals") or []
    if not unmapped:
        return {}
    return {"provisional": True,
            "provisional_reason": UNMAPPED_SIGNAL_PREFIX + ": " + ", ".join(unmapped)}


def is_unconditional(reason_code):
    return reason_code in UNCONDITIONAL


# ──────────────────────────────────────────────────────────────────────────
# 9. 통제항목 전체가 실패인가, 검토인가
# ──────────────────────────────────────────────────────────────────────────
def evidence_outcome(items, errors=(), review=None):
    """
    상태 전이(phase2_status.from_phase2)에 넘길 값을 정한다.

        FAILED      판정을 아예 시작하지 못했다 (입력 불량·배치 중단)
        그 외       문항 결과가 하나라도 있으면 결과를 낸다.
                   검토가 필요하면 REVIEW_REQUIRED, 아니면 COMPLETED.

    ★ 문항 일부가 실패한 것은 FAILED 가 아니다. 그 문항만 UNKNOWN 으로 남는다.
    """
    fatal = ("P2E005", "P2E901")        # 입력 불량 / 배치 중단
    if any(c in fatal for c in errors):
        return {"processing_status": "FAILED",
                "review_required": False,
                "note": "판정을 시작하지 못했습니다."}
    if not items:
        # 질문지가 없거나 판정 대상이 없는 경우. 실패가 아니라 '증적 없음'이다.
        soft = [c for c in errors if c in ("P2E001", "P2E002")]
        if soft:
            return {"processing_status": "COMPLETED",
                    "review_required": False,
                    "note": "판정할 항목이 없습니다 (증적 없음)."}
        return {"processing_status": "FAILED",
                "review_required": True,
                "note": "문항 결과가 없습니다."}
    review = review if review is not None else decide_review(items, errors)
    return {"processing_status": "COMPLETED",
            "review_required": review["required"],
            "note": ""}
