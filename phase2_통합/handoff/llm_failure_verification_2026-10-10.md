# LLM 실패·재시도 통합 검증 (10/10)

WBS X47(10/8 빈/손상/OCR 실패/LLM 오류 처리)의 남은 LLM 통합 검증을 수행했다.
로컬 테스트 환경 기준으로 완료 처리 가능하다. 공동 배포 환경 검증이나 실제 모델 장애율 측정은 아니다.

## 방법

실제 HTTP 업로드·MySQL·BGE를 사용하고, 별도 loopback HTTP 서버가 LLM 오류를 주입했다.
복구 및 Phase2 장애 시나리오의 정상 Phase1 응답은 실제 Qwen3:14B를 호출했다.
Ollama 서비스 자체를 중단하지 않았다. 합성 증적만 사용했다.

| 상황 | 최종 확인 |
|---|---|
| 계속 HTTP503 | 호출 2회·재시도 1회, DB FAILED, Phase2 판정 0건 |
| 잘못된 JSON | 호출 2회·재시도 1회, DB FAILED, Phase2 판정 0건 |
| 응답 시간 초과 | 실제 60초 제한 경과·재시도 1회, E101, DB FAILED, Phase2 판정 0건 |
| HTTP400 요청 거부 | 호출 1회·재시도 없음, DB FAILED, Phase2 판정 0건, 실패 실행 로그 보존 |
| 첫 요청503 후 정상 Qwen 응답 | 재시도 1회 후 Phase2 저장 성공, DB REVIEW_REQUIRED 유지 |
| Phase1 정상·Phase2 계속503 | 9문항 모두 UNKNOWN, 판정 1건 저장, DB REVIEW_REQUIRED. MET 자동 생성 없음 |

## 수정한 문제

처음 HTTP400에서는 API가 오류를 반환해도 DB에 PREPROCESSED가 남고 실행 로그도 없었다.
우리 `tools/run_system_demo.py`에서 요청 거부 실행 로그를 보존하고,
판단 단계 예외 시 FAILED 및 제외 사유를 저장하도록 보완했다.
판단팀·기존 통합팀 소스는 수정하지 않았다. 재검증 결과 E0013에서 FAILED와 호출 1회를 확인했다.

HTTP200은 처리 결과를 반환했다는 뜻이며 모델 판정 성공을 뜻하지 않는다.
오류 결과를 소비하는 화면은 DB 상태·Phase1/Phase2 결과와 검토 필요 여부를 확인해야 한다.

## 증빙과 재실행

- reports/llm_faults_2026-10-10_before.json: 수정 전 5개 시나리오. HTTP400 실패 상태 누락을 그대로 보존.
- reports/llm_faults_2026-10-10_after.json: HTTP400 수정 확인 + Phase2 장애 확인, 두 건 통과.
- 관련 기존 권한·외부 연결·판단 파이프라인 회귀 테스트 21개 통과.
- 원본 응답/모델 로그는 Git 밖 `tmp/local_upload_2026-10-09/faults_*`에 보관.

```powershell
.\tmp\integration-env\Scripts\python.exe -B -X utf8 I_P\phase2_통합\tools\check_llm_fault_integration.py --private tmp\local_upload_2026-10-09 --output tmp\new_fault_report.json --cases 503 400 malformed timeout recover phase2_503
```

기존 시험 설정·DB·Qwen 터널이 필요하다. 새 합성 증적을 업로드한다.
시험 도구가 실행한 임시 API·오류 주입 서버는 종료하며 기존 시연 서버는 재시작하지 않는다.
기존에 켜둔 시연 서버에 수정 코드를 적용하려면 그 프로세스를 재시작해야 한다.

이로써 앞서 로컬 기준 완료 7개/부분 완료 3개에서 **완료 8개/부분 완료 2개**로 갱신한다.
남은 두 항목은 W46 전체 외부 통신 점검, Y47 승인 정답셋 기반 최종 설정 확정이다.
WBS Excel 파일은 수정하지 않았다.
