# 외부 통신 점검 보완 (W46, 10/10)

대상은 우리 `tools/run_system_demo.py`와 이 실행기가 사용하는 검색 worker다.
기존 팀 API를 직접 실행하거나 공개 데모 사이트를 사용하는 경로에는 자동 적용되지 않는다.

## 확인·수정

- BGE는 local_files_only=True, trust_remote_code=False로 로컬 모델을 읽는다.
- 검색 Chroma 클라이언트에는 anonymized_telemetry=False가 설정돼 있다.
- 통합 실행기는 Hugging Face 오프라인 환경변수를 설정하고 OCR download_enabled=False를 사용한다.
- LLM 주소는 loopback /v1만 허용한다. 기존 판단팀 자체 클라이언트는 환경 프록시를 읽을 수 있으므로, 우리 네트워크 정책 설치 시 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY의 대소문자 변형을 제거하고 NO_PROXY를 고정한다. PC 전체 설정은 변경하지 않고 해당 프로세스와 상속 worker에만 적용한다.
- 기존 getaddrinfo/TCP/UDP 차단에 gethostbyname/gethostbyaddr/getnameinfo 및 sendmsg 이벤트 처리를 추가했다.
- 판단팀·입력팀·기존 통합팀 소스는 수정하지 않았다.

## 검증

네트워크·권한·worker 관련 22 tests / 4 subtests 통과.
외부 DNS 정방향/역방향 조회·외부 TCP 연결·UDP 전송은 호출 전에 차단됐다.
실제 loopback HTTP 서버에 정상 접속했고, 그 서버의 외부 주소 리다이렉트는 차단됐다.
잘못된 loopback 프록시를 환경에 넣어도 정상 로컬 HTTP 접속이 가능함을 확인했다.
sendmsg는 Windows에서 직접 실행 시험하지 않았으며 코드상 이벤트 처리를 추가한 범위다.
시험은 별도 프로세스에서 실행해 기존에 켜진 서버의 설정을 변경하지 않았다.

## 아직 남은 경계

Python audit hook은 OS 방화벽이 아니다. 네이티브 라이브러리, 별도 Ollama/SSH 프로세스,
허용된 로컬 서비스가 대신 수행하는 외부 통신까지 통제하거나 관측하지 못한다.
따라서 전체 패킷 관측 또는 최종 환경의 OS 수준 차단 시험 전에는 W46 전체 완료로 표시하지 않는다.
이번 보완 적용을 위해 기존 시연 프로세스는 다음 실행 시 새 코드를 읽도록 재시작해야 한다.
운영체제 방화벽·네트워크 설정을 임의 변경하지 않았다.

## 실제 TCP 관측 후속

최신 코드로 별도 실행한 업로드→보고서 합성 시연(E0016)이 통과했다.
Windows TCP 연결표 66회 표본에서 수집 오류는 0건이었다. 앱·하위 검색 프로세스에서
관측된 TCP 목적지는 loopback이며 MySQL 13306, LLM 터널 11435 등이 포함됐다.
최초 실행은 스크립트 실행 정책 때문에 관측되지 않아 성공으로 집계하지 않았다.

주기 표본의 SSH 범위는 처음에 listener 프로세스뿐이었으므로, 별도 스냅샷으로
그 하위 jump 프로세스가 기존 동아리 점프 호스트의 외부 TCP22에 연결된 것도 확인했다.
관측 도구에는 이후 SSH 하위 프로세스를 포함하도록 보완했다.
결과: reports/network_observation_2026-10-10.json.

이는 모든 패킷을 수집한 것이 아니다. 짧은 TCP 연결, UDP, 원격 서버의 Ollama 동작은
여전히 관측 범위 밖이다. W46은 부분 완료를 유지한다. SSH 경유 시험을 완전 오프라인으로 분류하지 않는다.

## PktMon 교정 결과

관리자 창에서 TCP18191·UDP18192 필터로 counters-only를 실행하고 각 10건의
시험 데이터 송수신·응답을 애플리케이션에서 확인했다. 사용자가 제공한
`pktmon counters --include-hidden`과 `pktmon stop` 결과는 모두 0이었다.
모든 시험 필터 제거도 확인했다. 해당 설정은 알려진 루프백 트래픽을 관측하지 못했으므로
외부 통신 없음의 증거로 사용하지 않는다. 원인은 확정하지 않았고 같은 시험을 반복하지 않는다.
기록: reports/pktmon_calibration_result_2026-10-10.json.

Linux 후속: 사용자가 제공한 터미널 출력에서 독립 Ollama PID17972의 Qwen3:14B 생성이
정상 종료(OK., 약17.1초, 모델 로딩 약17.0초)했다. 제공된 ss 관측 일부 구간에는
loopback 연결만 보였다. 30초 전체 출력이나 모든 하위 프로세스 관측은 확보하지
못했으므로 전체 외부 통신 없음으로 확정하지 않는다.
기록: reports/linux_ollama_observation_2026-10-10.json. W46 부분 완료 유지.
다음 검증에는 루프백 관측을 지원하고 알려진 트래픽으로 교정된 별도 관측 방법,
또는 통제된 서버 환경에서의 관측이 필요하다. 기존 TCP 표본 관측의 한계는 그대로 유지한다.
