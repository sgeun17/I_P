# 원문 번호 선택형 인용 교정 — 최신 main 통합본

## 범위 및 기본값

통합 기준은 8c9b1f0이다. 팀원이 추가한 retry_prompt.py의 오류 위치·실패 인용 전달과
llm_runner.py의 failed_output=output 호출을 보존한다. 입력팀·검색팀·Phase 2 파일은 수정하지 않는다.
기본은 최신 main의 기존 동작이다. PHASE1_SPAN_REPAIR=1 또는 아래 실행기의 --mode spans로만 켠다.
기존 PRIMARY 실험/이유 문장 교정 실험은 포함하지 않는다. 공개 결과 Schema/Validator/임계값은 그대로다.

## 동작과 한계

첫 판단 프롬프트와 Schema는 기존과 같다. 파싱 가능한 최초 응답에 E401/E403/E404가 있으면
모델은 코드가 부여한 원문 구절 번호만 선택한다. 원문은 줄 단위로 보존하며 코드가 quote/chunk/page를 구성한다.
서로 떨어진 구절은 별도 인용이고 임의로 이어 붙이지 않는다. 이미 유효한 인용과 관련 항목/PRIMARY/신뢰도/reason은 보존한다.
페이지 범위가 여러 페이지인 청크는 줄의 실제 페이지를 알 수 없어 선택지에서 제외한다.
번호 없음/중복/빈 선택/잘못된 필드/호출 실패는 교정을 반영하지 않고 원래 검증 오류를 유지한다.
재시도 비활성(max_retries=0)을 존중하며 교정 추가 호출은 최대 1회다. 모든 Validator를 다시 실행한다.
파싱·구조 오류의 일반 재시도는 팀원의 최신 코드를 그대로 사용한다.

원문에 존재하는 구절이 해당 통제를 실제로 뒷받침하는지는 별도 의미 검토가 필요하다.
NO_MATCH 누락이나 E502/E505 정책 문제를 해결했다고 주장하지 않는다.

## 측정 및 통합 검증

- 이전 서버 원문 선택 실험: 33/33 완료, 31/33 검증통과, 검토필요 24건, 자동처리 후보 9건.
- E0010은 신청/승인/부여/변경·삭제/보안책임 원문 구절 5개로 인용 교정. 정상 2.6.3 인용 보존.
- 남은 E0012/E0023은 매핑 4개로 상한 3개 초과(E502). 상한을 임의 변경하거나 관련 항목을 삭제하지 않는다.
- 이번 최신 main 통합본: Phase 1 판단 테스트 355개, 검색 연결 테스트 17개 통과.
- 서버 저장 응답 33건을 재생해 판단/인용/검증/검토 결과가 모두 일치함을 확인했다.
  최초·교정 요청의 messages/response_format도 측정 실행과 일치했다. 신규 모델 실행 성능 측정은 아니다.
- Phase 2 테스트는 수정 전 8c9b1f0와 통합 후 모두 250통과/21실패, 실패 테스트 동일.
  18개는 phase2_인터페이스/test/overall_result.py 경로 누락, 나머지는 골든셋 출력 Schema 해시와
  reason code 오류 코드 기대값 불일치다. 이번 변경과 분리해 Phase 2 작업에서 확인한다.

## 실행

별도 프로세스에서 실행한다. Ollama context16384는 서버 설정이며 이 도구가 변경하지 않는다.
저장된 검색 결과를 읽을 뿐 업로드/OCR/색인을 다시 수행하지 않는다.

```bash
cd ~/I_P
source phase1_입력/.venv/bin/activate
python3 phase1_판단/tools/run_span_judgments.py --mode spans --source-dir ~/phase1-results/full-20261003 --out-dir ~/phase1-results/spans-integrated-$(date +%Y%m%d-%H%M%S) --model qwen3:14b
```

기존 경로는 --mode baseline이다. 실행기는 환경변수를 설정한 뒤 모듈을 불러오므로 모드별 새 프로세스를 사용한다.
일반 호출은 PHASE1_SPAN_REPAIR를 해제하고 프로세스를 재시작하면 기존 경로로 돌아온다.
과거 실험의 run_primary_experiment.py 대신 이 파일을 사용한다.

## 기록

judgment.json은 최종 결과, retry_audit.json은 첫 응답/selection_plan/교정 응답/반영 여부다.
http_attempts.json에 실제 요청과 응답을 남기고 manifest에 입력·판단 코드 해시를 기록한다.
교정 반영은 의미 정확도를 보장하지 않는다. 교정 요청이 실패해도 원래 검증 실패 결과로 남으므로
summary의 request_or_runtime_failures뿐 아니라 patch_rejected_ids와 감사 파일도 확인한다.
증적 본문이 포함되는 결과/로그는 공개 Git 저장소에 커밋하지 않는다.
