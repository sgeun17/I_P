# 시스템 통합 10/3~10/9 처리 결과

10/10 후속: X47의 실제 LLM 실패·재시도·복구·Phase2 장애 검증 및 HTTP400 실패 상태 누락 수정 완료. `llm_failure_verification_2026-10-10.md` 참조. 로컬 기준 완료 8개, 부분 완료 2개(W46/Y47).

범위: WBS.xlsx / WBS.1 / S46:Y47, 유빈·채은 담당. **별도 로컬 통합 시연 환경에서 구현·검증한 결과**다. 공동 서버/공유 웹 배포 완료와 구분한다. 다른 팀 기존 소스는 수정하지 않았다.

| 날짜·셀 | 작업 | 이번 상태와 근거 |
|---|---|---|
| 10/3 S46 | 원본 접근 역할·권한 적용 | 로컬 적용·HTTP 검증. 서버가 검증한 토큰에 역할 연결. 익명/위조 역할 차단, 원본 접근과 다운로드 허용 여부 구분 |
| 10/4 T46 | 조회/다운로드/검토 이력 기록 | 로컬 적용·실제 저장 검증. 원본 ID/버전·주체·행위·결과는 SQLite, 검토 상세는 기존 MySQL review_history. 승인자/검토자 역할 분리 |
| 10/5 U46 | 원본·모델 로그 보존 규칙 적용 | 사용자 결정인 자동 삭제 없는 보존 적용. 모델 실행마다 고유 파일 저장. 보존 일수는 미정이며 임의 기간/삭제 작업 없음 |
| 10/7 W46 | 비인가 조회/외부 호출 점검 | HTTP 비인가 차단, 외부 LLM 주소 설정 거부, Python DNS/TCP/UDP 외부 연결 사전 차단 시험. 검색 하위 프로세스에도 적용. OS 전체/네이티브 라이브러리 패킷 차단 보장은 아님 |
| 10/4 T47 | 오프라인 설치·실행 절차 작성 | 기존 절차에 아래 실제 실행 경로 추가. Windows 전용 wheel 128개·버전 고정 파일·SHA-256 목록 준비 |
| 10/5 U47 | 테스트 환경 배포/시연 점검 | 실제 MySQL·BGE·Qwen3:14B 연결. 새 가상환경에 --no-index 설치, pip check 및 주요 모듈 import 통과. OCR 모델 사전 준비 및 다운로드 없는 실제 인식·DB 적재 통과 |
| 10/6 V47 | 종단 시나리오 재실행 | 짧은 합성 증적 정상 경로 2회 통과. E0003 약 92초, E0005 약 100초 |
| 10/7 W47 | 업로드→보고서 E2E 실행 | HTTP 업로드→청킹→BGE→Qwen Phase1→Phase2 판정→MySQL→HTML 보고서 조회. 2회 모두 REVIEW_REQUIRED 유지. 운영 승인/판정 정확도 검증으로 해석하지 않음 |
| 10/8 X47 | 빈/손상/OCR 실패/LLM 오류 처리 | 빈 TXT·손상 DOCX·금지 확장자 HTTP 거부. 실제 OCR 가중치 누락 시 HTTP503 + DB FAILED. 도달 불가 LLM에서 실제 E101 timeout. 재시도/파싱 오류는 기존 모의 HTTP 회귀 검사도 통과 |
| 10/9 Y47 | Top-K·청킹·임계값 조정 | 기존 72회 청크 비교·Top3/5/7/10 반복 평가 근거 유지. API 후보 수 조절 제공. 근거 없이 임계값 변경하지 않고 현재 기본 Top5 유지; 승인 정답셋 기반 최종 튜닝은 별도 |

## 실제 실행

작업 폴더는 개발(저장소 I_P의 상위)이다. 로컬 시험 DB는 loopback:13306 / evidence_db, 기존 입력 테이블에 Phase2 테이블을 비파괴 추가했다.
새 실행기는 기존 업로드·판단·저장·보고서 함수를 연결하며 기존 웹과 다른 포트 18081을 사용한다.

```powershell
.\tmp\integration-env\Scripts\python.exe -B -X utf8 I_P\phase2_통합\tools\run_system_demo.py --config tmp\local_upload_2026-10-09\system_config.json
```

system_config.json / system_tokens.json은 Git 밖 비공개 시험 설정이다. 실제 토큰을 문서·Git에 복사하지 않는다.
Bearer 인증된 API: POST /upload, POST /inspect/{eid}?top_k=5, GET /evidence/{eid}/result, /report, /original, /download, POST /review/{result_id}/approve·modify·reject.
이 실행기는 시연 API이며 기존 팀 UI를 교체하지 않는다. 브라우저 주소창만으로 인증된 API를 사용할 수 없다.

```powershell
.\tmp\integration-env\Scripts\python.exe -B -X utf8 I_P\phase2_통합\tools\check_system_demo.py --private tmp\local_upload_2026-10-09 --output tmp\new_system_run.json --short
```

재실행은 새 합성 증적을 업로드하고 실제 모델을 호출한다. 사람이 검토한 기존 결과를 재실행하는 명령이 아니다.
공동 서버의 계정/권한 설정은 시험용 REVIEWER/APPROVER/VIEWER 계정을 그대로 배포하지 말고 실제 사용자에 매핑해야 한다.

## 오프라인 설치 검증

Git 밖 `tmp/offline_bundle_2026-10-09/`에 requirements.lock, wheels 128개, manifest.json, 다운로드·설치 로그가 있다.
새 환경 `tmp/offline_install_2026-10-09`에 로컬 wheel만 설치했다. 제한 환경에서 중단된 설치를 권한이 있는 실행 환경에서 재개했으며 pip check와 주요 모듈 import를 검증했다.

```powershell
python -m venv .offline-env
.\.offline-env\Scripts\python.exe -m pip install --no-index --find-links <반입경로>\wheels -r <반입경로>\requirements.lock
.\.offline-env\Scripts\python.exe -m pip check
```

OCR의 craft_mlt_25k.pth / korean_g2.pth는 시험 실행 폴더 system_demo/ocr_models에 준비했다. 새 환경에서 다운로드를 끄고 합성 영문 이미지의 ACCOUNT ACCESS REVIEW 인식·실제 DB 청크 저장을 확인했다. 한국어 인식 정확도 평가는 아니다.
BGE 모델/색인·기준 JSON은 manifest에서 해시를 확인하며 아직 wheel 폴더에 복사된 것은 아니다. Qwen·Ollama는 기존 서버를 사용했다. Python/MySQL/Ollama 설치 파일은 wheel 묶음에 포함되지 않는다.

## 검증 기록

- reports/system_demo_2026-10-09.json: 첫 다문장 합성 증적은 Qwen 인용 오류로 HOLD_PHASE1_INVALID. 원문 중간 문장 생략으로 인용 검증 실패, Phase2가 보류한 정상 방어 사례. 성공 사례만 남기지 않았다.
- reports/system_demo_short_2026-10-09.json / system_demo_repeat_2026-10-09.json: 실제 정상 종단 2회.
- reports/system_errors_2026-10-09.json / system_review_2026-10-09.json: HTTP/DB 오류·검토 이력.
- reports/offline_ocr_2026-10-09.json: 새 오프라인 설치 환경의 OCR·DB 시험.
- 권한·외부 연결·worker·LLM 오류·이미지 계약 회귀: 38 tests / 6 subtests 통과.
- 원본·모델 원문 로그·SQLite 감사는 tmp/local_upload_2026-10-09 아래에만 보관. 본문/토큰은 접근 감사에 기록하지 않으며 모델 로그에는 응답 내용이 있으므로 공개하지 않는다.

## 아직 전체 완료라고 할 수 없는 범위

1. 공동 Linux 서버/공유 웹의 실제 진입점에 이 기능 적용 및 운영 사용자 역할 매핑. 현재 통합팀 원본 API를 직접 실행하면 이 별도 실행기의 인증은 적용되지 않는다.
2. 모든 네트워크를 차단한 최종 배포 환경에서 재시연. 이번 Qwen은 공개망 SSH 터널 너머 서버를 사용했으므로 **전체 시스템의 완전 오프라인 시연이 아니다**. 내부망 또는 서버 자체 실행 환경이 필요하다.
3. Linux용 패키지·GPU 환경 설치 검증. 검색 실행 경로는 대응했지만 Windows wheel을 Linux에 복사해 설치할 수 없다.
4. 평가 기간/최신성 정책 등 조직별 실행 입력. 기존 Phase2 runner를 사용하며 POLICY_MISSING·사람 검토 보류를 숨기지 않는다. 기준팀 공통 실행 정책을 주입하는 호출 경로는 별도 연결해야 한다.
5. 감사 SQLite와 검토 MySQL은 동일 트랜잭션이 아니다. 시작 기록 후 작업/로그 오류는 조사 대상이며 전체 감사 변조 방지나 다운로드 수신 완료를 보장하지 않는다.

WBS 원본의 완료 표시는 바꾸지 않았다. 구현·로컬 검증 완료와 공동 환경 배포 완료를 같은 의미로 체크하지 않는다.
