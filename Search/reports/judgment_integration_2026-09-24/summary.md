# 검색→판단팀 코드 연동 검사

- 결과: **PASS** / 2026-09-24T14:33:59.474235+09:00
- 기존 파일 검색 성공 6건 + 검토용 검색 20건: 실제 MappingInput 모델에서 변환·검증 통과.
- 전처리 실패 2건: 판단 입력 변환 거부.
- 판단팀 KB 해시 및 101개 ID·명칭과 검색팀 KB 일치 확인.
- 아래 결과는 실제 판단팀 build_result/Validator/Review 코드에 고정 응답을 넣어 얻음. **LLM 호출 0회**.
- 판단팀 파일 44개 변경 없음.

| 시나리오 | 처리 상태 | 인용·규칙 검증 | 사람 검토 | 검토 사유 |
|---|---|---|---|---|
| single | COMPLETED | True | True | R202 |
| multiple | COMPLETED | True | True | R205 |
| no_match | COMPLETED | True | True | R202, R204 |
| invalid_quote | COMPLETED | False | True | R104, R202 |
| invalid_json | FAILED | False | True | R108 |
| call_failure | FAILED | False | True | R108 |

고정 응답의 confidence=0.9는 시험 입력 상수이며 모델 실측값이 아닙니다. 매핑 결과를 실제 정답·운영 결과로 사용하지 마세요.
검색 점수는 실제 저장된 검색 결과에서 그대로 전달됐습니다. 업로드 API·LLM 호출·화면 통합은 아직 별도입니다.
