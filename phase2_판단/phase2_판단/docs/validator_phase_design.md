# Validator phase 분기 설계 v0.1

2026-10-02 / 구현 초안.

| 구간 | 실행 위치 | 책임 |
|---|---|---|
| Phase 1 | 기존 Phase 1 Validator | 통제항목 관련성; E505로 적정성 단정 표현 차단 |
| Phase 2 입력 | validation.logic.validate_input | 공식 스키마, KB ID/명칭/해시, 버전, 중복, Phase 1 anchor, judge 정책 |
| Phase 2 문항 | run_validated_item → 기존 run_item_judgment의 output_validator | JSON/ENUM/ID/코드/인용; 기존 재시도 정책 재사용 |
| Phase 2 의미 | self_check.run_self_check | 인용만으로 결과·이유가 성립하는지 별도 호출 |
| Phase 2 조립 | validation.logic.validate_output | 문항 집합·개수·중복, critical, 집계, 종합 결과 재계산 |
| Phase 2 검토 | validation.review.decide_review | 공식 P2R 코드 및 별도 audit 신호 |

## E505와 P2E505

E505는 Phase 1의 관련성 판단에서 “요구사항을 충족” 같은 적정성 표현을 금지한다.
Phase 2의 MET/NOT_MET에는 적정성 판단이 필요하므로 Phase 1의 표현 금지 Validator를 재사용하지 않는다.
P2E505는 공식 Phase 2 정의인 ADEQUACY_WITHOUT_EVIDENCE다. 원문에 없는 문장·청크·다른 증적·빈 인용으로 MET을 내면 E4xx 상세 코드와 함께 반환한다.
인용 자체가 없는 MET/NOT_MET은 P2E501이다. 페이지 오류만 있는 경우 E404이며 P2E505로 의미를 확대하지 않는다.
진짜 인용이 있으나 논리적으로 뒷받침되지 않는 경우는 Self-check의 별도 audit 신호다. P2E505의 공식 정의와 혼동하지 않는다.

## 연결 방법

새 통합 진입점은 src/validated_pipeline.py의 run_control_judgment다. 기존 자운 하네스를 내부에서 호출하므로 전송·retry를 복제하지 않는다.
tools/run_validated_phase2.py가 실제 CLI 진입점이다. 기존 src/judgment_harness.py 직접 호출은 프롬프트 개발용이며 전체 검증 결과가 아니다.
반환은 {processing_status, output, audit}. output만 phase2-output-0.2로 전송한다. audit와 status는 별도 저장한다.
원응답이 잘못됐을 때 output에는 UNKNOWN이 들어가지만 audit.run.raw_response와 원래 오류를 보존한다. UNKNOWN을 “검증에 성공한 적정성 판정”으로 통계에 포함하지 않는다.

## 통합팀 연결 지점

- Phase 1 종료 후 from_phase1()을 이용해 VALIDATING으로 진입한 뒤 새 진입점을 호출한다.
- 처리 상태와 output을 별도로 저장한다. 항목별 검증 실패는 검토 상태이며 정상 NO_MATCH가 아니다.
- 현재 DB/HTTP 서버 오케스트레이터는 이번 패키지에서 변경하지 않았다. 새 CLI/API 실행 경로로 검증하며 서버 연결은 통합팀 작업이다.
- low-confidence/date/self-check 상세 사유는 audit에 있다. 공식 P2R enum 추가와 Human Review 담당자·의견·결정 기록은 후속 계약이다.
