# Phase2 기준

현재 작업 범위는 **1·2·3장 전체 101개 통제항목·883문항**이다. 전체본은 `2026-10-06-r6`이며 현재 기준 사용 승인본(`approved=true`, `status=APPROVED_FOR_USE`)이다. 파일명 `draft`는 기존 경로 호환을 위해 유지한다. 기존 질문·ID는 유지하며 r6에서는 5개 문항의 근거 인정 범위를 보완했다. **2.5·2.6·2.10·2.11 모든 문항 critical=true(341개), 나머지 false(542개)**를 담당자 결정으로 반영했다. 기록(record) 문항도 포함한다. [critical 정책](critical_policy.json)과 [10/5 확인요청 처리 결과](docs/team_requests_2026-10-05.md)를 참고한다. 보유 2023년 안내서 기반이며 최신 법령 검증·개별 LLM 결과 승인을 의미하지 않는다.

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

기존 2장 대표 사례의 실제 LLM 호출 방법과 오류는 [10/1 검증 보고서](reports/phase2_llm_test_review_2026-10-01.md)에 있다. 전달받은 10/4 팀 보고서는 r1 중 255문항 개발 시험을 기록하며 전체 883문항 검증은 아니다. 이번 r6 승인 반영 후 실제 LLM 재실행은 하지 않았다. 최신성·형식·예외 정책은 별도 확인이 필요하다. 판단팀에서 `critical_policy={"mode":"explicit"}`를 전달해야 확정된 값을 사용한다.

이전 설명과 검증 이력은 [작업 이력](docs/작업이력.md)에 보존했다. 실행 당시 보고서와 소스 사본의 경로·해시는 과거 상태의 기록이다.

승인 범위: 전체 r6 체크리스트·사유 코드의 사용 승인이다. 과거 54/700문항 호환본과 과거 결과는 미승인 상태를 그대로 보존한다. 내부 review API의 `allow_draft=True`와 결과 `approved=false`는 검수 실행/응답에 대한 표시이며 기준 파일 승인과 구분한다. 최신성·파일 형식 정책이 없으면 `POLICY_MISSING`은 계속 발생할 수 있다.

## r5 문항별 예외

3.3.2-Q02는 위탁 미발생·사전 통지 방침이 모두 확인되는 경우, 2.10.8-Q05는 관리표·작성 지침의 미적용 사유·보완대책 관리 기준이 확인되는 경우 MET 예외를 적용한다. 일반 UNKNOWN 규칙보다 이 두 문항의 evidence_rule을 우선한다. 기존 질문 ID·문구는 유지했다. [적용 조건과 타 팀 재검증 요청](docs/item_exceptions_2026-10-05.md)에 상세 내용을 기록했다. 원본 증적·LLM 재검증은 수행하지 않았다.

현재 r6는 r5의 두 예외를 보존하고 5개 문항의 인정 근거를 명확히 했다. 10/6 후속 요청 처리 결과는 [후속 작업](docs/followup_2026-10-06.md)를 참고한다. 실제 모델 재검증과 조직 정책 확정은 별도다.
