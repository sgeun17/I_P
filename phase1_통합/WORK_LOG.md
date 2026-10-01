# Phase 1 통합 작업 요약

## 완료

1. **입력 → 검색 연결**
   - 입력팀 DB의 `evidence`와 `chunk`를 읽어 검색팀이 요구하는 문서 JSON으로 변환하는 `evidence_to_search_payload()`를 추가했다.
2. **검색 → 판단 연결 재사용**
   - 검색팀의 기존 `retrieve()`와 `judgment_pipeline.run_judgment()`를 그대로 호출해 팀별 코드를 중복 구현하지 않았다.
3. **통합 실행 경로**
   - `inspect_evidence()` 하나로 필요 시 전처리 → 검색 → 판단 → 상태 갱신 → 결과 저장까지 수행하게 했다.
4. **화면 결과 API**
   - `/api/upload-and-inspect`, `/api/inspect/{evidence_id}`, `/api/results/{evidence_id}`를 추가했다.
5. **점검자 화면**
   - 파일 업로드, Top-K/모델 입력, 처리 결과, 매핑 통제항목, 판단 이유, 유사도, LLM confidence, 원문 인용과 페이지, Human Review 사유를 표시한다.
6. **판단팀 화면 규칙 준수**
   - `llm_confidence`를 정확도로 표현하지 않고, 판단팀 최종 스키마 필드와 Human Review 결과를 그대로 표시한다.

## 실행 환경 때문에 여기서 실동작하지 못한 부분

- 검색팀 `retrieve()`는 저장소 내부 Windows 전용 `.venv/Scripts/python.exe`, 로컬 BGE-M3 및 ChromaDB 색인을 요구한다.
- 입력팀은 기존 DB 연결 설정이 필요하다.
- 판단팀은 실제 LLM 서버/모델 설정이 필요하다.

따라서 통합 코드의 정적 문법/구조 검증은 가능하지만, 세 외부 런타임(DB·검색 모델·LLM)을 포함한 실제 E2E 실행은 해당 프로젝트 개발 환경에서 수행해야 한다.

## 2026-10-01 다음 단계 보완

7. **판단 보류 표시 규칙 수정**
   - 판단팀 Human Review 정책의 `R107` 규칙을 반영했다.
   - `NO_MATCH + R107`은 화면에서 `판단 보류`로 표시한다. 원본 `match_status=NO_MATCH` 값은 변경하지 않는다.
8. **최신 결과 버전 조회 보완**
   - 결과 파일을 문자열 정렬하던 방식을 숫자 버전 기준 선택으로 변경했다. (`v10`이 `v9`보다 최신으로 정확히 선택됨)
9. **Top-K 입력 검증 추가**
   - 통합 레이어에서 `1~101` 범위를 벗어난 값을 조기에 차단한다.
10. **실행 준비 상태 점검 추가**
   - `self_check.py` 및 `GET /api/readiness`를 추가했다.
   - 팀 모듈·스키마·BGE-M3 디렉터리·ChromaDB 경로·판단 모델 환경변수·DB 환경설정 존재 여부를 빠르게 확인한다.
