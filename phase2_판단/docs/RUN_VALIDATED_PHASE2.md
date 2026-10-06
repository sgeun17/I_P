# 검증 포함 Phase 2 실행

기준 저장소: ba8d8d7. Python 3.10 이상 문법 사용. 이번 검증 환경은 Python 3.14이며 다른 Python 버전은 별도 확인 필요.

```powershell
cd C:\Users\User\I_P\phase2_판단
python -m pip install -r requirements.txt
python -m pytest -q
```

실제 실행은 Phase 2 입력 JSON과 Ollama가 필요하다. 아래 경로는 실제 파일 위치로 바꾼다. 입력의 checklist_version과 source_versions.kb_sha256은 사용한 체크리스트/KB와 같아야 한다.

```powershell
python .\tools\run_validated_phase2.py --input "D:\phase1-results\phase2_input.json" --control-id "2.5.1" --model "qwen3:4b" --out-dir "D:\phase2-results\run-001" --allow-draft
```

출력 디렉터리는 새 경로여야 한다. 기존 결과를 덮어쓰지 않는다.

- output.json: 공식 출력 규격. 입력 실패/판정 대상 없음 등은 null이며 audit를 확인한다.
- audit.json: 원응답, 검증 오류, Self-check, 날짜 후보, 미확정 정책, 재현 해시.
- status.json: COMPLETED / REVIEW_REQUIRED / FAILED.

기본 경로는 2026-10-05 승인된 `full_checklist_draft.json` / `full_reason_codes_draft.json`을 사용하므로 기본 실행에는 `--allow-draft`가 필요하지 않다. 과거 미승인 draft를 명시적으로 지정해 개발 검수할 때만 `--allow-draft`를 사용한다. 모든 정상 MET/NOT_MET에 Self-check 호출이 추가되어 모델 호출 수와 시간이 늘어난다. UNKNOWN에는 Self-check를 생략한다.

항목별 정책은 --policies와 --as-of로 주입한다. 임의의 최신성 정책 파일은 제공하지 않는다. JSON 모양은 다음과 같다(6개월 값은 예시일 뿐 실제 기준 아님).

```json
{"2.5.1-Q01": {"freshness": {"max_age_months": 6, "date_label": "document_date"}, "allowed_types": ["pdf", "docx"]}}
```

date_label은 document_date/performed_date/effective_date/deadline/expiry 중 해당 항목의 의미에 맞게 지정한다. 제공하지 않은 항목에는 최신성·형식 판정을 임의로 추가하지 않는다.

기존 하네스를 직접 호출하는 서버에는 `run_control_judgment` 연결이 필요하다. 이번 패키지는 서버/DB 마이그레이션을 자동 수행하지 않는다.


## 조직별 적용범위 프로필

회사별 서비스 범위·업무 특성·내부 정책을 적용 조건 판단에 참고하려면 `--organization-profile`을 사용할 수 있다.

```powershell
python .\tools\run_validated_phase2.py --input "D:\phase1-results\phase2_input.json" --control-id "3.1.1" --model "qwen3:14b" --out-dir "D:\phase2-results\run-age-scope" --organization-profile ".\configs\organization_profile.example.json"
```

프로필은 **적용 조건 해석용 보조 컨텍스트**다. 정책 선언만으로 실제 이행을 증명하지 않으며, 실제 차단·운영 사실은 입력 증적 원문과 대조한다. 위험수용 수준은 법적 의무나 MET/NOT_MET/UNKNOWN 판정을 바꾸는 면제값으로 사용하지 않는다.

현재 공식 결과 enum에는 N/A가 없으므로, 조건부 사건이 발생하지 않았음이 직접 확인되면 `UNKNOWN + P2_U_NO_TRIGGER_EVENT`로 표현한다. 이를 종합점수에서 완전히 제외하는 N/A 정책이 필요하면 기준/인터페이스 팀 계약 변경이 선행되어야 한다.
