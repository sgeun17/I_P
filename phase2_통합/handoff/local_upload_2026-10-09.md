# 로컬 업로드·진행 상태 연결 검증

기존 입력팀 `main.py`의 화면/API/전처리를 실제 MySQL에 연결했다. 다른 팀 소스는 수정하지 않았다. 공유 chatgpt.site 데모 화면을 수정하거나 연결한 것은 아니다.

## 실행 환경

- 웹: http://127.0.0.1:18080/ (현재 PC 전용)
- DB: Ubuntu WSL의 별도 MySQL 인스턴스, 127.0.0.1:13306
- DB 데이터: WSL `/var/lib/isms-local-test-20261009`
- 비공개 실행 설정·원본 파일: `개발/tmp/local_upload_2026-10-09/` (Git 밖)
- Docker Desktop 시작 오류를 확인해 초기화하지 않고 WSL MySQL을 사용했다.
- 새 데이터 디렉터리에만 스키마 생성. 기존 입력팀 SQL의 DROP 문은 실행하지 않았다.

저장소 상위 `개발`에서 실행한다. DB가 실행 중이어야 한다.

```powershell
& './tmp/integration-env/Scripts/python.exe' -B -X utf8 I_P/phase2_통합/tools/run_local_upload.py --config tmp/local_upload_2026-10-09/config.json
```

WSL 재시작으로 테스트 DB가 꺼진 경우, **초기화하지 않고** 기존 데이터로 시작한다.

```powershell
wsl -d Ubuntu -u root -- mysqld --user=mysql --datadir=/var/lib/isms-local-test-20261009 --bind-address=127.0.0.1 --port=13306 --socket=/var/lib/isms-local-test-20261009/mysql.sock --pid-file=/var/lib/isms-local-test-20261009/mysql.pid --log-error=/var/lib/isms-local-test-20261009/mysql.log --mysqlx=0 --daemonize
```

이미 실행 중이면 다시 실행하지 않는다. 웹은 실행 터미널에서 Ctrl+C로 종료한다. DB를 중지할 때는 위 테스트 인스턴스의 PID/데이터 경로를 확인해 정상 종료하고 다른 MySQL 프로세스를 일괄 종료하지 않는다. 설정의 암호를 Git에 넣지 않는다.

## 확인 결과

실제 HTTP 업로드 → MySQL evidence 저장 → 백그라운드 전처리 → 진행 조회 running/done → PREPROCESSED → 청크 2개 저장을 확인했다. 별도 DB 접속에서 원본 SHA-256·상태·청크 수를 대조했고 새 HTTP 클라이언트 및 브라우저 화면에서 E0001/PREPROCESSED가 조회됐다. 빈 파일 거부도 확인했다.

보고서: `reports/local_upload_2026-10-09.json`. 재시험: `tools/check_local_upload.py --config <비공개 설정> --output <새 보고서 경로>`. 재시험은 가상 TXT 한 건을 테스트 DB에 추가하며 기존 업로드/실패 자료도 전처리 대상에 포함될 수 있으므로 이 전용 시험 DB에서만 실행한다.

## 완료 범위와 남은 일

**업로드·전처리 진행 상태의 실제 로컬 DB 연결 검증 완료.** 기존 입력팀 구현을 실행·검증한 작업이며 화면 전체를 새로 개발한 것은 아니다.

검색/LLM/Phase2/보고서까지 이어지는 상태 화면은 별도 통합 대상이다. 현재 analysis 작업 이력은 서버 메모리에 있으므로 재시작하면 사라지고, evidence/청크 데이터는 DB에 남는다. 처리 도중 재시작에 대한 복구·인증·역할별 접근·배포 운영은 이번에 검증하지 않았다. 사용한 증적은 가상 TXT 한 건으로 다른 형식의 성공을 일반화하지 않는다.
