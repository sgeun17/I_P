# Phase 2 찬우 담당 시험 시나리오 v0.1

실행: phase2_판단에서 `python -m pytest -q`.
테스트는 합성 입력과 고정 LLM 응답을 사용한다. 실제 모델 정확도나 실제 33개 증적의 성공률을 뜻하지 않는다.

| 범위 | 확인할 사례 | 구현 시험 파일 |
|---|---|---|
| 날짜 | ISO/점/슬래시/한국어, 윤년, 잘못된 날짜, 파일명·부분 날짜 배제 | test_dates.py |
| 최신성 | 날짜 없음, 컷오프 당일/전날, 기준일/다음날, 월말, 복수 날짜, OCR, 정책 없음 | test_dates.py |
| 형식 | 파서 메타데이터 우선, 형식 누락, 허용/불허 | test_dates.py |
| JSON | 필수 필드 누락, enum, 자료형, 사유 코드 카탈로그 | test_validated_pipeline.py |
| 입력 | KB ID/명칭/해시, 증적 버전, 중복 청크, judge 정책, anchor | test_validated_pipeline.py |
| 문항 집합 | 누락·중복·가짜 item_id, 개수, critical·집계 변조 | test_validated_pipeline.py |
| 인용 | 청크/quote/page, 타 증적·버전, OCR 문자/공백 경계 | test_citation_validator.py, test_validation_boundaries.py |
| P2E505 | 허위 인용 MET → 오류 + UNKNOWN + P2R104 | test_validated_pipeline.py |
| Self-check | 인용만 전달, 미지원·상충·저신뢰·새 조건·스키마 실패 | test_validated_pipeline.py |
| Review | OCR 메타데이터 연결, UNKNOWN 비율 경계, 짧은 이유 경고 | test_validated_pipeline.py |
| 전체 경로 | 한 통제항목의 모든 문항 실행, 일부 실패 격리, 재시도·4xx·호출 실패 | test_validation_boundaries.py |
| 골든셋 | 29개 합성 사례(정상 23, 결함 주입 6) | test_goldenset.py |

배포 전 실제 검증은 별도 필요하다: 실제 Ollama 모델로 골든셋 수행, 원래 실패 증적 5개 OCR 재처리, 실제 문서의 의미 판정 정확도, 통합 서버 상태 전이, 전체 문항 실행 시간/메모리.
실제 최신성 기준이 오면 항목별 정책을 채우고 기준일 경계를 다시 시험한다. 기존 6개월 테스트 값은 시험 데이터이며 업무 기준이 아니다.
