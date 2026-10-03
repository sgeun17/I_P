# Phase2 기준

현재 작업 범위는 **1·2·3장 전체 101개 통제항목·883문항**이다. 전체본은 `2026-10-03-r1`이며 팀 검수용 초안(`approved=false`)이다. 기존 2장 `2026-10-01-r3`의 700문항을 그대로 보존하고, 1·3장의 주요 확인사항 133개를 183문항으로 정리했다. 보유 2023년 안내서 기반의 1차 초안이며 세부 설명 전체의 분해·현행 법령 검증·팀 승인을 의미하지 않는다.

KB의 `source.sha256`은 검색팀 `kb_identity.py`와 동일하게 LF/CRLF 개행을 통일한 공유 식별자로 확인한다. 기존 KB 참조 값은 유지하며, 체크리스트·카탈로그·PDF 자체의 무결성 해시는 기존 원본 바이트 검사를 유지한다.

## 먼저 사용할 파일

| 파일 | 용도 |
|---|---|
| [full_checklist_draft.json](full_checklist_draft.json) | 현재 전체 883문항과 문항별 MET·NOT_MET·UNKNOWN 기준 |
| [full_reason_codes_draft.json](full_reason_codes_draft.json) | 전체 883문항 버전에 연결된 사유 코드 13종 |
| [chapter2_full_checklist_draft.json](chapter2_full_checklist_draft.json) | 기존 2장 700문항 호환본 |
| [chapter2_reason_codes_draft.json](chapter2_reason_codes_draft.json) | 기존 2장 버전에 연결된 사유 코드 |
| [checklist_store.py](checklist_store.py) | 기준 JSON 저장·통제항목 및 문항 조회 |
| [judgment_review.py](judgment_review.py) | 검수 입력 생성·응답의 구조, 사유 코드, 인용 검사 |
| [phase1_review_adapter.py](phase1_review_adapter.py) | 확정된 Phase1 결과를 질문 조회에 연결 |
| [reason_codes.py](reason_codes.py) | 사유 코드 조회·호환 검사 |
| [run_phase2_llm_smoke.py](run_phase2_llm_smoke.py) | 현재 700문항 기준 중 대표 3문항·가상 9사례의 로컬 LLM 검수 |

질문과 적용 조건은 [전체 질문 검수본](docs/full_checklist_review.md)에서 볼 수 있다. 세부 판정 근거는 전체 JSON의 `evidence_rule`에 있다. [판단팀 연결 안내](docs/full_checklist_handoff.md)에 버전 선택 방법과 남은 검수 범위를 정리했다.

## 호환 때문에 최상위에 남긴 파일

`checklist_draft.json`, `reason_codes_draft.json`, `review_examples.json`, `reason_code_examples.json`은 **이전 54문항의 실제 연결·검증에 계속 사용**한다. 판단팀이 앞의 두 파일을 직접 읽고 사유 카탈로그가 원본 위치와 해시를 검증하므로 사용하지 않는 파일로 분류하지 않았다. 판단팀의 기존 코드는 수정하지 않았다.

기본 저장·사유 조회 경로는 기존 54문항이다. 기존 LLM 검수 실행기는 2장 700문항 중 대표 사례를 선택한다. **1·2·3장 전체를 사용하려면 `full_checklist_draft.json`과 `full_reason_codes_draft.json`을 반드시 함께 선택한다.** 다른 팀의 기본 경로는 변경하지 않았으며 코드 동기화만으로 자동 전환되지 않는다.

## 폴더 안내

| 폴더 | 내용 |
|---|---|
| [archive](archive/) | 현재 실행에 사용하지 않는 210문항 중간본과 범위 제안 자료 |
| [docs](docs/) | 현재·이전 질문 설명, 사유 코드 설명, 기존 작업 이력 |
| [tools](tools/) | 기준 구조 검증·합성 사례 연결 검사 |
| [tests](tests/) | 자동 테스트. 대표 가상 사례는 `tests/fixtures/` |
| [schemas](schemas/) | 검수용 입출력 JSON Schema |
| [reports](reports/) | 과거 검사 결과·실제 LLM 호출 기록 |
| [drafts](drafts/) | 기준 작성에 사용한 원문 검토 기록 |
| `data/` | 검수용 로컬 저장소 |

## 실행

아래 명령은 `phase2_기준`에서 실행한다.

```powershell
# 파일 연결·자동 테스트
& '../phase1_검색/.venv/Scripts/python.exe' -B -m pytest tests -q -p no:cacheprovider

# 1·2·3장 전체 883문항의 원문·고정 응답 연결 검사 (pypdf 필요, LLM 호출 없음)
& '../phase1_검색/.venv/Scripts/python.exe' -B -X utf8 tools/verify_full_checklist.py --verify-pdf

# 전체 기준 700문항과 대표 9사례의 고정 응답 연결 검사 (LLM 호출 없음)
& '../phase1_검색/.venv/Scripts/python.exe' -B -X utf8 tools/verify_chapter2_review_flow.py

# 실제 호출 전 입력 준비 확인
& '../phase1_검색/.venv/Scripts/python.exe' -B -X utf8 run_phase2_llm_smoke.py --prepare-only
```

기존 2장 대표 사례의 실제 LLM 호출 방법과 오류는 [10/1 검증 보고서](reports/phase2_llm_test_review_2026-10-01.md)에 있다. 1·3장 신규 문항은 실제 LLM·조직 증적 검증 전이다. 전체 883문항 성능 평가·팀 승인, `critical`과 예외 정책, 사유 코드 확정은 남아 있다.

이전 설명과 검증 이력은 [작업 이력](docs/작업이력.md)에 보존했다. 실행 당시 보고서와 소스 사본의 경로·해시는 과거 상태의 기록이다.
