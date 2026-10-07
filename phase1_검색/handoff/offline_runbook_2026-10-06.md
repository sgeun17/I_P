# 오프라인 설치 및 통합 실행 절차

WBS WBS.1의 T47(10/4 오프라인 설치·실행 절차 작성), U47(10/5 테스트 환경 배포/시연 점검), V47(10/6 종단 시나리오 재실행)에 대응한다. 작성일 2026-10-06. 절차 작성과 로컬 사전 점검을 완료했으며 깨끗한 오프라인 PC 설치·전체 웹 E2E 완료 기록은 아니다.

## 대상 환경과 반입 목록

현재 확인 환경은 Windows x64, Python 3.12.14, 검색팀 로컬 가상환경이다. 검색 worker가 .venv/Scripts/python.exe를 사용하므로 이 안내를 Linux에 그대로 적용하지 않는다. Linux 실행 경로 대응은 검색팀 수정·별도 검증이 필요하다. 다른 PC의 .venv를 복사하지 말고 대상과 같은 OS·아키텍처·Python 버전에서 설치 묶음을 준비한다.

| 반입 항목 | 확인 사항 |
|---|---|
| I_P 소스 전체 | 팀 간 상대 경로 유지. commit과 미커밋 변경 유무 기록 |
| Python 설치 파일, Python wheel 묶음 | 대상과 동일 환경에서 전체 의존성 해석·설치 검증. CPU/GPU torch 구성 기록 |
| phase1_검색/models/bge-m3 전체 | tokenizer·pooling·가중치 모두 필요. 폴더 존재만으로 검증 완료 아님 |
| controls.json 및 판단팀 data/control_index.json | 공유 KB 식별자·101개 ID/명칭 일치 |
| phase1_검색/data/chroma_kb 및 임베딩 캐시 | KB·모델·라이브러리가 다르면 대상 환경에서 재색인 |
| full_checklist_draft.json 및 full_reason_codes_draft.json | r6 쌍으로 반입. 이전 54/700문항 기본 경로와 구분 |
| Ollama 설치 파일 및 qwen3:14b 모델 저장소 | 연결 가능한 준비 환경에서 다운로드 후 blobs/manifests 등 전체 저장소 반입. 실제 실행 사용자·버전·모델 digest 기록 |
| EasyOCR 가중치 및 PDF 관련 실행 의존성 | 입력팀 OCR 캐시 위치·언어 모델을 확인해 반입. 패키지만 설치하면 첫 사용 시 다운로드가 필요할 수 있음 |
| MySQL 및 테스트 DB | 입력팀 schema.sql과 chunk_schema.sql, 승인된 DB 접속 설정, 원본 저장 폴더 쓰기 권한 |
| 비민감 시험 파일과 기대 결과 | 정상 DOCX/TXT, 빈 파일, 손상 파일, OCR 사례. 실증적은 Git에 추가하지 않음 |

현재 setup.ps1/01_setup.cmd는 인터넷 패키지 설치와 모델 다운로드를 수행하므로 폐쇄망 설치 명령으로 사용하지 않는다. SSH로 외부 동아리 서버를 쓰는 시험은 완전 오프라인 시험이 아니다. 폐쇄망에서는 Ollama가 같은 PC 또는 허용된 내부 환경에 있어야 한다.

## 인터넷 가능한 준비 PC

아래 명령의 기준 폴더는 I_P다. 패키지 목록을 한 번에 해석한다. wheel 다운로드 완료는 설치 검증 완료가 아니다. 충돌이나 wheel 부재가 나오면 해결한 뒤 반입한다.

```powershell
py -3.12 -m venv .bundle-env
.\.bundle-env\Scripts\python.exe -m pip download --only-binary=:all: --dest .\offline_bundle\wheels -r .\phase1_검색\requirements-dev.txt -r .\phase1_입력\database\requirements.txt -r .\phase1_통합\requirements.txt -r .\phase1_판단\requirements.txt -r .\phase1_판단\requirements-llm.txt -r .\phase2_판단\requirements.txt -r .\phase2_인터페이스\requirements.txt
```

깨끗한 동일 환경에서 다음 오프라인 설치 명령을 실행해 성공을 확인한 후 pip freeze 결과, wheel·모델 파일 SHA-256 목록, Python/Ollama/모델 버전을 배포 묶음에 기록한다. 현 로컬 환경의 패키지 재고는 reports/offline_readiness_2026-10-06.json이며 통합 설치 잠금 파일이 아니다. 대용량 모델·wheel 묶음은 Git 밖 승인된 배포 위치에서 관리한다.

## 대상 Windows PC 설치

아래는 신규 대상 PC에서 실행한다. 기존 개발 가상환경을 덮어쓰지 않는다. offline_bundle은 반입한 묶음 경로로 바꿀 수 있다.

```powershell
py -3.12 -m venv .\phase1_검색\.venv
.\phase1_검색\.venv\Scripts\python.exe -m pip install --no-index --find-links .\offline_bundle\wheels -r .\phase1_검색\requirements-dev.txt -r .\phase1_입력\database\requirements.txt -r .\phase1_통합\requirements.txt -r .\phase1_판단\requirements.txt -r .\phase1_판단\requirements-llm.txt -r .\phase2_판단\requirements.txt -r .\phase2_인터페이스\requirements.txt
.\phase1_검색\.venv\Scripts\python.exe -m pip check
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:HF_HUB_DISABLE_TELEMETRY='1'
.\phase1_검색\.venv\Scripts\python.exe -B -X utf8 .\phase1_검색\tools\check_offline_readiness.py --output .\offline-check-new.json
```

이 환경변수는 Hugging Face 오프라인 설정이며 모든 라이브러리의 외부 통신을 차단하는 설정은 아니다. 외부 호출 점검은 W47에서 별도 수행한다. 입력팀 .env.example의 키를 참고해 database/.env를 대상 PC에 작성한다. 비밀번호는 안내서·Git·보고서에 넣지 않는다. DB 스키마 적용은 기존 DB를 보존하는 방식으로 DB 담당자가 수행한다.

모델과 색인이 맞지 않는 경우 검색팀 chroma_index.py를 대상 환경에서 실행해 다시 생성한다. 재색인은 기존 색인에 쓰기 작업을 하므로 배포용 복사본에 실행한다.

```powershell
.\phase1_검색\.venv\Scripts\python.exe -X utf8 .\phase1_검색\chroma_index.py
.\phase1_검색\.venv\Scripts\python.exe -X utf8 .\phase1_검색\chunk_retriever.py --input .\phase1_검색\examples\docx_input.json --output .\offline-search-new.json --top-k 5
.\phase1_검색\.venv\Scripts\python.exe -X utf8 .\phase1_검색\judgment_adapter.py --input .\offline-search-new.json --output .\offline-mapping-new.json
```

## LLM과 웹 실행

Ollama 서비스와 반입 모델 준비 후 `ollama list` 및 `ollama ps`로 모델 태그·실행 상태를 확인한다. models 목록 조회만으로 실제 생성 성공을 판정하지 않는다.

```powershell
$env:LLM_PROVIDER='ollama'
$env:LLM_BASE_URL='http://127.0.0.1:11434/v1'
$env:LLM_MODEL='qwen3:14b'
$env:PHASE1_JUDGMENT_MODEL='qwen3:14b'
Invoke-RestMethod http://127.0.0.1:11434/v1/models
Push-Location .\phase1_통합
..\phase1_검색\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
Pop-Location
```

웹은 http://127.0.0.1:8000, 상태는 /health 및 /api/readiness다. 현재 웹의 확인된 범위는 Phase1 업로드→전처리→검색→매핑→결과 저장이다. Phase2와 최종 보고서까지 자동 연결됐다는 의미가 아니다. readiness에는 아래 알려진 경로 오류가 있으므로 ready 값만으로 성공/실패를 확정하지 않는다.

## Phase2 실행과 승인 경계

최신 r6 입력을 생성하고 체크리스트·사유 코드 쌍을 명시해야 한다. 이전 시험 입력의 버전 문자열만 바꾸어 재사용하지 않는다. run_validated_phase2.py의 --input, --control-id, --model, --out-dir, --checklist, --reason-codes, --policies, --as-of를 사용한다. out-dir은 새 경로여야 한다. --policies와 --as-of에는 실제 합의된 값만 넣는다.

현재 CLI는 critical_policy를 전달하는 옵션이 없다. src/validated_pipeline.py의 run_control_judgment 호출에 critical_policy={"mode":"explicit"}를 전달하는 통합 연결이 필요하다. 기준 파일 critical=true만으로 종합 판정 적용을 보장하지 않는다. 판단팀 코드는 변경하지 않았다. 최신성·형식 정책이 없으면 POLICY_MISSING을 숨기지 않는다.

## 종단 시험 시나리오와 통과 기준

| 시나리오 | 확인할 결과 | 현재 실행 상태 |
|---|---|---|
| 정상 TXT/DOCX 업로드 | 증적 ID/버전→청크→Top-5→판단 인용→저장 결과 ID가 연결 | 통합 환경 미준비, 미실행 |
| Phase2 및 보고서 | r6·사유 코드·critical 정책·원문 참조·검토 대기가 끝까지 유지 | 웹 연결 확인 필요, 미실행 |
| E0004 | Top-5에서 1.1.6 누락은 알려진 미해결 문제. 임의 MET 금지 | 검색 단독 재현 완료, E2E 아님 |
| 빈/손상 파일 | 명시적 실패 코드, 성공 결과를 남기지 않음 | X47 시험 준비 |
| OCR 판독 실패 | 원본 참조·오류 또는 사람 검토 상태 유지 | OCR 환경 준비 후 시험 |
| LLM 중단/시간 초과 | 제한된 재시도·실패/검토 상태, 이전 성공 결과와 혼동 없음 | 서비스 준비 후 시험 |

각 실행에 소스 commit/변경사항, KB·기준 해시, 모델 태그/digest, 입력 ID/버전, 소요시간, 단계별 상태, 검토 사유, 결과 위치를 기록한다. 인용 검증 성공과 의미 정확도를 구분한다. 실제 기대값 미승인인 시험은 정확도 수치로 발표하지 않는다.

## 오늘 점검 결과와 팀 전달

- 검색 모델 필수 파일·data/chroma_kb/chroma.sqlite3 존재 확인. 최근 검색 단독 실행은 성공했으나 이번 재고 점검 자체는 색인 무결성 검사가 아니다.
- 검색용 Python 환경에는 통합에 필요한 10패키지가 없다: fastapi, python-multipart, pymysql, cryptography, filetype, pdfplumber, openpyxl, python-pptx, python-docx, easyocr. 별도 통합 가상환경 보유 여부는 미확인이다.
- 입력팀 database/.env가 없다. 전체 업로드 시험은 DB 설정·스키마·접속 확인 후 실행해야 한다.
- 통합팀 self_check.py는 data/chroma를 확인하지만 실제 검색은 data/chroma_kb를 사용한다. 경로 수정 요청. 다른 팀 파일은 수정하지 않았다.
- Phase2 CLI critical 정책 전달과 웹→Phase2→보고서 연결 확인을 요청한다.
- T47 절차 문서는 작성 완료. U47 배포/시연 및 V47 종단 재실행은 완료 처리하지 않는다. WBS 원본은 수정하지 않았다.


## 통합 시험 환경 준비 및 부분 실행 결과 추가

2026-10-06 별도 가상환경 개발/tmp/integration-env에 각 팀 requirements를 함께 설치했다. 기존 검색 환경은 유지했다. pip check 결과 No broken requirements found. 입력·웹 패키지 미설치 문제는 이 별도 환경에서 해소했다. 최초 환경 재고 기록은 검색 전용 환경의 당시 상태다.

검사 12개 모두 통과: 웹 /health·/api/readiness·첫 화면 응답(검사 묶음 1개), easyocr/torchvision/pdfplumber/docx/pptx/openpyxl 모듈 로드 6개, 정상 TXT·DOCX 파싱/청킹 2개, 빈 TXT·손상 DOCX/PDF 오류 처리 3개. 웹은 TestClient를 통한 프로세스 내부 요청으로 검증했으며 브라우저 렌더링이나 외부 HTTP 서버 배포 검증은 아니다. 손상 파일 로그의 예외 문구는 의도한 시험 입력을 처리한 기록이고 최종 errors 코드 반환을 확인했다.

보고서와 전체 패키지 버전: ../reports/integration_smoke_2026-10-06.json. 재실행 도구: ../tools/check_integration_smoke.py. --work에는 새 임시 폴더를 지정한다. 실제 DB/LLM 요청이나 OCR 모델 다운로드는 수행하지 않는다. Windows 제한 실행 환경에서는 TestClient 내부 socketpair 생성이 대기할 수 있으며 이번 검사는 로컬 소켓이 허용된 실행 환경에서 완료됐다.

남은 항목: DB 접속·스키마/LLM 설정, OCR 실제 판독, 검색/판단/Phase2/보고서 전체 연결. 준비 상태 API는 chroma_index·judgment_model_env·input_db_env 때문에 false를 반환한다. chroma_index 경로는 앞서 기록한 통합팀 수정 사항이다. critical 정책 CLI 전달도 기존 요청으로 유지한다. FastAPI TestClient의 httpx 관련 deprecation 경고는 있었지만 이번 시험 실패는 아니었다.

WBS U47 테스트 환경 배포/시연 점검의 환경 설치·부분 기능 확인, X47 빈/손상/OCR 실패/LLM 오류 처리의 빈 TXT·손상 DOCX/PDF 파서 단계 검증을 진행했다. V47 전체 종단 재실행, U47 실제 배포 시연, X47 전체 오류 처리는 완료 처리하지 않는다.
