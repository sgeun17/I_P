# LLM 연결 전 통합 검증

- 결과: PASS / 2026-09-27T18:53:38.163533+09:00
- 실제 파일 11건 중 정상 9건은 최신 파서→청킹→로컬 검색→판단 입력→프롬프트 생성 통과. 빈 파일·손상 파일 2건은 검색 전에 차단.
- PNG/JPG는 합성 OCR 블록 2건으로 청킹 이후 연결만 검증. 실제 이미지 인식은 실행하지 않음.
- 고정 응답 10종으로 실제 검증·검토 코드 실행. LLM 호출 0회. 업로드 API·MySQL·화면 연결은 포함하지 않음.
- UTF-8/CP949 동일 본문의 순위·점수 일치, 긴 문서 분할, PDF 페이지/XLSX 시트/PPTX 슬라이드 1·2 보존 확인.

| 사례 | 구분 | 청크 수 | 위치 | 출처 |
|---|---|---:|---|---|
| TXT | SYNTHETIC_FILE_ACTUAL_PARSER | 1 | [] | ['parser'] |
| CP949 | SYNTHETIC_FILE_ACTUAL_PARSER | 1 | [] | ['parser'] |
| LONG | SYNTHETIC_FILE_ACTUAL_PARSER | 3 | [] | ['parser'] |
| CSV | SYNTHETIC_FILE_ACTUAL_PARSER | 1 | [] | ['parser'] |
| UNRELATED | SYNTHETIC_FILE_ACTUAL_PARSER | 1 | [] | ['parser'] |
| DOCX | SYNTHETIC_FILE_ACTUAL_PARSER | 2 | [] | ['parser'] |
| XLSX | SYNTHETIC_FILE_ACTUAL_PARSER | 2 | [1, 2] | ['parser'] |
| PPTX | SYNTHETIC_FILE_ACTUAL_PARSER | 2 | [1, 2] | ['parser'] |
| PDF | SYNTHETIC_FILE_ACTUAL_PARSER | 1 | [1] | ['parser'] |
| PNG_CONTRACT | SIMULATED_OCR_BLOCKS_NO_IMAGE_RECOGNITION | 1 | [] | ['ocr'] |
| JPG_CONTRACT | SIMULATED_OCR_BLOCKS_NO_IMAGE_RECOGNITION | 1 | [] | ['ocr'] |

| 고정 응답 | 처리 상태 | 검토 필요 | 사유 |
|---|---|---|---|
| single | COMPLETED | False |  |
| multiple | COMPLETED | True | R205 |
| no_match | COMPLETED | True | R202, R204, R208 |
| low_confidence | COMPLETED | True | R201 |
| uncertain | COMPLETED | True | R107, R204, R206 |
| invalid_quote | COMPLETED | True | R104 |
| invalid_json | FAILED | True | R108 |
| call_failure | FAILED | True | R108 |
| PNG_CONTRACT | COMPLETED | True | R207 |
| JPG_CONTRACT | COMPLETED | True | R207 |

프롬프트와 시험 응답에는 실제 모델 결과로 오인하지 않도록 표시했다. 후보 선택과 confidence는 시험 상수이며 검색·LLM의 정확도 지표로 사용하면 안 된다.

## 실제 LLM 연결 시 필요한 것

- 서버 종류·주소·모델 식별자 및 인증 설정
- 요청/응답 구조, JSON Schema 강제 전달 방식, 토큰·생성 설정
- 실제 정상·무관·복수·불확실 사례 실행과 원문 인용 검증
- 타임아웃·오류·재시도와 업로드/DB/화면 흐름 확인
