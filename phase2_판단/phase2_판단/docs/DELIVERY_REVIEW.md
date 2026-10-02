# Phase 2 찬우 담당 보완본 v3

기준 commit: ba8d8d7. 작성일: 2026-10-02. 코드·문서 구현 초안이며 기준팀 승인을 뜻하지 않는다.

## 요청 산출물 대조

| 요청 | 파일 / 연결 |
|---|---|
| 판정 규칙 문서 | docs/phase2_rules_v0.1.md |
| Validator phase 분기 | docs/validator_phase_design.md; 별도 Phase 2 진입점 |
| P2E505 구현 | validation/citations.py → validation/logic.py → src/validated_pipeline.py |
| 인용 정규화와 허용 경계 | validation/citations.py; docs/citation_normalization_v0.1.md |
| 날짜 추출 | src/date_extractor.py; 항목별 정책을 run_validated_item에서 호출 |
| 판정 논리 검증 | validation/logic.py; src/self_check.py |
| JSON·통제항목 Validator | validation/contracts.py; validation/logic.py |
| Human Review | validation/review.py; 공식 phase2_errors.py 재사용 |
| 시험 시나리오 | docs/test_scenarios_v0.1.md; tests/test_dates.py, test_validated_pipeline.py, test_validation_boundaries.py |
| 실행 명령 | tools/run_validated_phase2.py; docs/RUN_VALIDATED_PHASE2.md |

## 검증 결과

- Python 3.14 환경에서 Phase 2 테스트 264개 통과. 실제 모델 호출 없는 단위·연결 시험이다.
- 기존 자운의 10개 테스트와 기존 골든셋/Citation 테스트도 포함한다.
- 인터페이스팀 run_error_check.py와 run_status_check.py 실행 완료. 검사 자체는 통과했으나 아래 기존 상태 전이 연결 문제를 계속 보고한다.
- 실제 KB 101개와 Phase 2 통제항목 64개의 ID/명칭 모두 일치. 검색팀 kb_identity의 sha256-crlf-v1 규칙으로 KB와 체크리스트 source.sha256을 대조한다. LF/CRLF 모두 같은 KB로 확인한다.
- 새 실행기 --help 호출 확인.

## 외부 기준/실제 검증이 남은 부분

1. 항목별 최신성 개월 수와 인정 증적 형식은 미수신이다. 검사 로직·주입 API·경계 테스트는 있지만 실제 정책값은 설정하지 않았다.
2. 체크리스트/사유 코드는 미승인이다. 개발 실행은 --allow-draft가 필요하다.
3. critical은 공식 임시 check_kind 정책을 사용한다. explicit 값이 비어 있으면 검토로 중단한다.
4. Self-check는 별도 LLM 의미 재검토다. 실제 qwen3:4b 탐지율, 실증적 판정 정확도·시간·메모리는 이번 고정 응답 시험으로 알 수 없다.
5. low-confidence/date/self-check 사유는 audit.review_signals에 저장한다. 공용 P2R enum 확장과 검토자 결정 레코드는 인터페이스팀 합의가 필요하다.
6. Phase 1 통합 오케스트레이터는 아직 Phase 1 종료를 COMPLETED로 쓰는 기존 경로가 있다. 통합팀이 from_phase1() 및 새 Phase 2 진입점을 연결해야 서버에서 이어 실행된다. 이번 패키지는 판단팀 범위의 CLI/API까지 연결했다.
7. 문장별 페이지 좌표가 없으므로 page는 청크 범위 검사다. 날짜 표지어 역시 추출 후보이며 표 구조/의미상 날짜 역할을 완전히 증명하지 않는다.

## 적용과 커밋

ZIP은 저장소 전체가 아니라 ba8d8d7 기준 Phase 2 변경 파일이다. 원래 I_P의 phase2_판단 폴더에 병합한다. 폴더 전체를 삭제하거나 교체하지 않는다.
PATCH_MANIFEST.json에는 ZIP 원본 sha256과 줄바꿈 차이를 제외한 lf_sha256/baseline_lf_sha256이 있다. 현재 파일의 LF 정규화 해시가 baseline_lf_sha256 또는 이미 적용된 lf_sha256과 다르면 다른 변경이 있으므로 diff를 먼저 확인한다. 새로운 git 업데이트 위에 무조건 덮어쓰지 않는다.
기존 Phase 1 및 phase2_기준/phase2_인터페이스 파일은 패키지에서 수정하지 않는다. 해당 파일들은 원래 저장소에 있어야 한다.

권장 커밋 메시지:

```text
feat: add phase2 validation pipeline, self-check and review routing
```

commit/push는 수행하지 않았다.
