# Phase1 검색

증적 청크로 관련 ISMS-P 통제항목 후보를 검색하고 판단팀 입력으로 전달한다. 최상위에는 현재 연결에서 사용하는 코드·KB·실행 파일·설치 설정을 둔다.

공유 `kb_sha256`은 [kb_identity.py](kb_identity.py)의 `sha256-crlf-v1` 규칙으로 계산한다. LF/CRLF 개행만 CRLF로 통일한 뒤 SHA-256을 계산하며 기존 식별자 `6421a840…803be`를 유지한다. 저장·checkout은 `.gitattributes`로 LF를 사용한다. 따라서 파일 원본 바이트의 해시와 공유 KB 식별자가 다를 수 있다. 다른 내용·서식 변경과 오래된 인덱스는 계속 거부한다. [수정 및 판단팀 동기화 안내](handoff/10월2일_KB해시_줄바꿈불일치_확인.md)를 참고한다.

## 먼저 사용할 파일

| 파일 | 용도 |
|---|---|
| [chunk_retriever.py](chunk_retriever.py) | 청크 입력 → 통제항목 후보 검색. 반복 요청에는 `LocalDocumentRetriever`로 모델 재사용 |
| [judgment_pipeline.py](judgment_pipeline.py) | 검색 결과를 판단팀 호출·응답 검증으로 연결 |
| [judgment_adapter.py](judgment_adapter.py) | 검색 결과를 판단팀 입력 형식으로 변환 |
| [controls.json](controls.json) | 검색 대상 통제항목 101개의 KB |
| [01_setup.cmd](01_setup.cmd) | 검색 실행 환경 준비 |
| [04_index_chroma.cmd](04_index_chroma.cmd) | 전체 KB 색인 |
| [05_search_chunks.cmd](05_search_chunks.cmd) | 샘플 청크 검색 실행 |

`retriever.py`, `chroma_index.py`, `chroma_sample.py`, `worker_client.py`는 현재 실행 코드가 참조하는 모듈이다. `chroma_sample.py`는 전체 색인에서도 공통 함수를 사용하므로 단순한 폐기 샘플이 아니다. `02_search.cmd`는 기존 한 줄 검색 진입점이며, `validate_kb.py`는 KB 검증 도구다.

## 폴더 안내

| 폴더 | 내용 |
|---|---|
| [docs](docs/) | 실행 안내와 이전 작업 이력 |
| [tools](tools/) | Chroma 샘플 점검 실행 파일·검수 스냅샷 기록 도구 |
| [tests](tests/) | 자동 테스트와 연결 검사 |
| [schemas](schemas/) / [examples](examples/) | 청크 입출력 규격과 샘플 |
| [reports](reports/) / [handoff](handoff/) | 실행 결과와 팀 전달 자료 |
| [releases](releases/) | 과거 검수 스냅샷. 운영 최종 승인본을 의미하지 않음 |
| `data/`, `models/`, `.venv/` | 실행 데이터·모델·환경. 실행에 필요하므로 유지 |

Chroma 샘플 점검은 [tools/03_check_chroma.cmd](tools/03_check_chroma.cmd)에서 실행한다. 상세 사용법은 [전체 색인 안내](docs/ChromaDB_전체색인_안내.md), [샘플 실행 안내](docs/ChromaDB_실행안내.md), [기존 작업 이력](docs/작업이력.md)을 참고한다.

이번 폴더 정리에서는 검색 로직·KB·모델을 변경하지 않았다. 테스트 도구나 설치 설정도 실제로 사용하므로 파일명이 오래됐다는 이유로 보관 폴더로 옮기지 않는다.
