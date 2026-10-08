# 10/7 통합 점검 결과 및 팀 전달

WBS W46 비인가 조회/외부 호출 점검, W47 업로드→보고서 E2E 실행의 사전/부분 점검, X47 오류 처리의 선행 점검에 해당한다. 다른 팀 소스는 수정하지 않았다. 실제 증적·DB·외부 LLM 호출은 사용하지 않았다.

## 완료한 검사

- 결과 폴더를 임시 합성 fixture 폴더로 바꾼 프로세스 내부 TestClient 시험: 인증 헤더 없이 /api/results/E9999를 조회하면 200과 합성 결과가 반환됨. 애플리케이션 자체의 결과 조회 권한 검사가 확인되지 않는다. 실제 운영 프록시/게이트웨이의 인증 유무는 별도다.
- 빈 TXT, 금지 확장자, DOCX 내용 불일치 → 400 및 EMPTY_FILE/INVALID_EXTENSION/CONTENT_MISMATCH. top_k=0 → 422 INVALID_TOP_K. 모델 미설정 → 503 MODEL_NOT_CONFIGURED. 5건 정상 거부.
- 검색→판단 어댑터, LLM 오류·재시도, OCR 메타데이터 계약 검사 22개·하위 사례 10개 통과. HTTP 응답은 모의 전송이므로 실제 LLM 정상 작동/정확도 검증은 아니다.
- Phase1 LLMClientConfig는 https://offline-test.invalid/v1 설정을 허용한다. 설정 객체만 생성했고 요청은 보내지 않았다. 실제 외부 통신 발생을 입증한 것은 아니다.
- 로컬 TCP 3306/8000/11434/11435에 연결되지 않았다. 다른 주소의 DB/서비스가 없다는 뜻은 아니다. 로컬 입력 DB .env 부재와 함께 현재 PC의 실 E2E 전제 조건이 미충족임을 확인했다.

## 통합/판단/입력팀 조치 요청

| 우선순위 | 위치 | 확인 내용 / 요청 |
|---|---|---|
| 높음 | phase1_통합/api.py 결과 조회 라우트 | 인증 주체와 증적별 조회 권한 검사 필요. 상위 게이트웨이 담당이면 그 설정과 역할별 허용/거부 시험을 제공. 공개 health와 증적 결과 접근은 구분 |
| 높음 | phase1_판단/src/llm_config.py | 외부 LLM 주소를 허용함. 폐쇄망 배포의 허용 목적지 정책을 정하고 검증/차단을 연결. 현재 기본 주소는 로컬이며 그 자체로 외부 유출이 발생한 것은 아님 |
| 보통 | phase1_통합/self_check.py | data/chroma 대신 검색 실제 경로 data/chroma_kb 확인 필요. 현재 readiness의 색인 누락 표시는 실제 경로와 다름 |
| 보통 | phase1_입력/ocr_parser.py | EasyOCR Reader 기본 설정에서 가중치 미보유 시 다운로드 가능. 캐시 반입 및 다운로드 비활성 설정을 입력팀과 확인. 이번에는 모델 다운로드/판독을 실행하지 않음 |
| 보통 | phase2_판단/tools/run_validated_phase2.py | critical_policy 전달 옵션이 없어 explicit 정책 적용 경로 확인 필요. 기준 critical 지정과 종합 정책 적용을 구분 |
| 보통 | 통합 실행 전체 | 현재 확인한 웹 오케스트레이터는 Phase1 결과 저장까지. Phase2/최종 보고서의 실제 연결 진입점·DB 환경·LLM 실행 주소를 공유받아 실 E2E 재실행 |

검색 BGE-M3 로드는 local_files_only=True, trust_remote_code=False이며 Chroma는 anonymized_telemetry=False다. 이는 해당 설정 확인이며 전체 프로세스 네트워크 차단 시험은 아니다. setup 준비 단계에는 모델 다운로드 기능이 있어 운영 실행과 분리해야 한다. 외부 통신 전체 패킷 관찰이나 방화벽 검증은 미실행이다.

## 실행 및 범위

개발 폴더에서 tmp/integration-env/Scripts/python.exe -B -X utf8 I_P/phase1_검색/tools/check_integration_boundaries.py --work tmp/새로운시험폴더 로 재현한다. Windows TestClient 내부 소켓이 허용된 환경에서 실행한다. 테스트 소스는 검색팀 tools에 추가했고 결과 fixture는 Git 밖에 둔다. 결과는 reports/integration_boundaries_2026-10-07.json.

계약 검사는 phase1_검색 폴더에서 ../../tmp/integration-env/Scripts/python.exe -B -X utf8 -m pytest tests/test_judgment_adapter.py tests/test_judgment_pipeline.py tests/test_image_pipeline_contract.py -q -p no:cacheprovider로 실행한다. 최초 상위 경로에서 실행했을 때 import 수집 오류가 있었으며 올바른 작업 폴더에서 통과했다.

W46은 접근통제/외부 호출 관련 미충족 사항을 발견한 상태이며 보안 적용 완료가 아니다. W47 실제 업로드→최종 보고서 E2E는 환경과 연결 미확인으로 미완료다. X47은 API 오류 5건 및 모의 LLM 오류/계약 확인의 일부만 완료했다. WBS 원본은 수정하지 않았다.

전달 멘트: “오늘 합성 자료로 확인해보니 결과 조회 API가 인증 없이 200을 반환하고, Phase1 LLM 주소도 외부 주소를 허용해. 인증/권한을 어디서 적용하는지와 폐쇄망 목적지 정책 확인 부탁해. readiness 색인 경로도 chroma_kb로 맞춰야 해. 오류 응답 5건과 연결 검사 22개는 통과했고, 실 E2E는 DB·LLM 환경과 Phase2→보고서 연결 확인이 남았어.”


## 10/7 재확인 정정

최신 run_validated_phase2.py는 전체 체크리스트·사유 코드가 기본값이며 critical_policy={"mode":"explicit"}를 이미 전달한다. 앞의 critical CLI 미연결 요청은 해소된 것으로 정정한다. phase2_통합의 결과 저장·검토 이력 모듈도 존재한다. 웹→Phase2→보고서 전체 실행 완료와는 구분한다. 상세 현황은 backlog_review_2026-10-07.md를 참고한다.
