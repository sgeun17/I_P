# Phase 2 합성 골든셋 (현재 데이터 v0.2)

29건: 정상 기대 결과 23건, 결함 주입 6건. 정상 23건은 MET 8, NOT_MET 3, UNKNOWN 12다.
모든 문항을 UNKNOWN으로 내는 기준선은 12/23=52.2%다. 이 수치는 실제 모델 정확도가 아니다.

결함 주입은 허위 인용 3건, 문항 밖 조건 추가 2건, 증적 내부 지시 실행 1건이다.
생성기는 tools/build_goldenset.py이며 tests/fixtures/goldenset.json과 생성기 일치를 시험한다.
공식 ItemResult 스키마와 실제 체크리스트 item_id/사유 카탈로그에 대조한다.

v0.2에서 인용 오류를 공식 E4xx/P2Exxx로 바꾸고 `expected.requires_model_evaluation`으로 실제 모델 평가가 필요한 사례를 표시했다. semantic_rule_boundary는 이번 Self-check로 연결했고 prompt_injection은 검토 신호로 연결했다. 해당 3건의 의미적 정오 탐지율은 실제 모델을 호출해 별도로 측정해야 한다. 고정 응답 테스트는 연결과 분기만 보장한다.
이 골든셋의 context는 합성 하네스용이다(source=synthetic 등). 공식 전체 입력 JSON 샘플로 그대로 전송하지 않는다. 새 통합 테스트는 공식 입력 스키마의 parser/ocr 값을 사용한다.
