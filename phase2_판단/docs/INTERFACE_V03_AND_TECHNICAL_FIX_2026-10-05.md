# Phase 2 인터페이스 0.3 연결 및 기술 오류 보완

작성일: 2026-10-05  
범위: `phase2_판단`만 수정. 입력·검색·인터페이스 파일은 변경하지 않음.

## 1. 인터페이스팀 요청 반영

- Self-check 신호를 `review_signals`로 공식 helper에 전달한다.
  - `SELF_CHECK_UNSUPPORTED` → `P2R111`
  - `SELF_CHECK_CONFLICT` → `P2R112`
  - `SELF_CHECK_UNCERTAIN` → `P2R205`
- 번역표에 없는 신호는 `P2R113`, `provisional=true`, `provisional_reason`으로 최종 Output에 남긴다.
- 종합 판정 helper는 `phase2_인터페이스/overall_result.py`에서 읽는다.
- Output 버전은 문자열을 중복 하드코딩하지 않고 공식 Schema의 const를 사용한다.
- JSON 형식·자료형·enum 오류의 기존 코드(E202/E203/E204)는 유지한다. 동시에 판정값과 reason code의 의미 조합이 틀리면 P2E502를 함께 남겨 원인을 잃지 않는다.

## 2. 기존 기술 오류 8문항 보완

기존 전체 시험에서 확인된 오류는 인용 원문 불일치 6문항, 판정값·사유 코드 조합 1문항,
Self-check confidence 범위 오류 1문항이었다.

- 인용은 선택적으로 source span ID를 고르게 하고 서버가 신뢰 원문에서 quote/chunk/page를 복원한다.
- 일반 재시도에는 직전 응답과 구체적인 검증 오류를 비신뢰 데이터로 전달한다.
- Self-check의 confidence는 0~1을 엄격히 검증하고, 틀린 값을 임의로 자르지 않고 재시도한다.
- 기술 실패는 UNKNOWN으로 안전 전환하면서 `error_code`를 남겨 근거 부족 UNKNOWN과 구분한다.

source span 방식은 의미 정확도를 보장하지 않는다. 인용문 변형을 막는 기술 장치이며,
판정 의미는 기존 Validator와 Self-check를 다시 통과해야 한다. 전체 255문항 운영 기본값으로
자동 활성화하지 않고, 검증된 실행 경로에서 명시적으로 사용한다.

## 3. 검증 결과

- Phase 2 판단 전체 자동 테스트: 293 passed
- 인터페이스 오류 계약 점검: 통과
- 인터페이스 상태 계약 점검: 통과
- 입력·검색·인터페이스 파일 변경: 없음

이번 검증은 구조·계약·회귀 오류를 확인한 것이며 실제 LLM 의미 정확도 100%를 뜻하지 않는다.
기존 8문항 실측에서는 기술 오류 0건을 확인했지만, 최신 통합본의 전체 255문항 LLM 재실행은 하지 않았다.

## 4. 별도 담당 사항

인터페이스 상태 점검은 `phase1_통합/orchestrator.py`가 여전히 Phase 1의 예전 상태값을 직접 저장한다고
알린다. 이 변경은 판단팀 범위가 아니므로 본 수정에 포함하지 않았다. 통합팀이 Phase 1 결과를
`phase2_status.from_phase1()`로 변환해 VALIDATING/REVIEW_REQUIRED/FAILED를 저장해야 한다.
