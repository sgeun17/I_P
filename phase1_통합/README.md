# Phase 1 통합 레이어

기존 `phase1_입력`, `phase1_검색`, `phase1_판단` 코드는 수정하지 않고 연결하는 어댑터/API를 추가했다.

## 통합 흐름

`업로드 → 입력팀 전처리/청킹 → DB 청크 조회 → 검색팀 retrieve(top_k) → 판단팀 run_judgment → 결과 저장 → 점검자 화면`

## 추가 API

- `POST /api/upload` : 입력팀 업로드 기능 재사용
- `POST /api/inspect/{evidence_id}` : 기존 증적을 전처리(필요 시)부터 검색·판단까지 실행
- `POST /api/upload-and-inspect` : 업로드부터 최종 판단까지 한 번에 실행
- `GET /api/results/{evidence_id}` : 저장된 판단 결과 조회
- `GET /` : 점검자 화면

## 실행 전제

1. 입력팀 DB 설정과 스키마가 기존 방식대로 준비되어 있어야 한다.
2. 검색팀은 기존 README대로 `.venv`, BGE-M3 모델, ChromaDB 101개 KB 색인이 준비되어 있어야 한다.
3. 판단팀 LLM 서버가 준비되어 있어야 한다.
4. 판단 모델 식별자를 환경변수로 지정한다.

Windows PowerShell 예:

```powershell
$env:PHASE1_JUDGMENT_MODEL="qwen3:14b"
cd phase1_통합
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

브라우저에서 `http://127.0.0.1:8000` 접속.

## 화면 규칙 반영

판단팀 스키마를 임의로 바꾸지 않는다.

- `processing_status` → 처리 상태
- `match_status` → 매핑 여부
- `mapped_controls[].reason` → 판단 이유
- `mapped_controls[].citations` → 원문 인용 / 페이지 / 파일명
- `similarity_score` → 검색 유사도
- `llm_confidence` → LLM confidence (`정확도`라고 표시하지 않음)
- `human_review.required/reasons` → 사람 검토 필요 여부와 사유
- `validation.passed` → 검증 통과 여부

## 결과 저장

`phase1_통합/results/{evidence_id}_v{version}.json`에 판단팀 최종 결과를 저장한다.
입력팀 `evidence.status`는 판단팀 `to_evidence_status()` 결과(`COMPLETED`, `REVIEW_REQUIRED`, `FAILED`)로 갱신한다.
