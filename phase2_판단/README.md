# Phase 2 판단 — 자운·찬우 구현

2026-10-05 갱신: 공식 phase2-input-0.2 / phase2-output-0.3 기반 검증 실행기를 구현했다.
현재 진입점은 `src/validated_pipeline.py`의 `run_control_judgment()`와 `tools/run_validated_phase2.py`다.
기존 하네스를 내부 호출하고 JSON·ID·인용 검증, Self-check, 날짜 추출, Human Review를 연결한다.

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python tools/run_validated_phase2.py --help
```

최신 문서는 다음과 같다.

- `docs/RUN_VALIDATED_PHASE2.md`: 실행과 결과 파일
- `docs/phase2_rules_v0.1.md`: 판정 규칙
- `docs/validator_phase_design.md`: E505/P2E505 분리와 연결
- `docs/citation_normalization_v0.1.md`: 인용 허용 경계
- `docs/test_scenarios_v0.1.md`: 시험 범위
- `docs/DELIVERY_REVIEW.md`: 납품 범위와 검증 결과
- `docs/INTERFACE_V03_AND_TECHNICAL_FIX_2026-10-05.md`: 인터페이스 0.3 연결과 기술 오류 8문항 보완

최신성 개월 수·인정 형식은 수신한 항목별 정책을 주입한다. 기본 업무 기준은 만들지 않는다.
Self-check 테스트의 고정 응답 통과가 실제 모델 정확도를 의미하지 않는다.
DB/HTTP 오케스트레이터 연결, 기준팀 승인, 최종 critical, 검토자 결정 기록은 별도 통합·합의 사항이다.

---

아래는 2026-10-01의 기존 개발 기록이며 현재 실행 안내는 위 문서를 따른다.

2026-10-01 기준, **다른 팀의 최종 운영 Schema/규칙이 없어도 독립적으로 구현 가능한 부분**을 모아 둔 디렉터리다.

현재 구현은 다음 원칙을 따른다.

- Phase 1에서 이미 검증한 LLM HTTP 전송부와 RetryPolicy를 복사하지 않고 재사용한다.
- Phase 2 체크리스트/사유 코드는 `phase2_기준`의 **미승인 draft**를 개발용으로만 읽는다.
- 최종 `phase2_rules_v0.1.md`가 오기 전에는 Prompt 코드에 새로운 판정 정책을 만들어 넣지 않는다.
- 최종 Phase 2 Input/Output JSON Schema가 오기 전에는 `draft_contract.py`를 **DEV ONLY** 계약으로 사용한다.
- Citation 원문 일치, JSON/ENUM/ID Validator, Human Review 전환은 찬우 영역을 대체하지 않는다.

## 현재 구현된 흐름

```text
Phase 1 확정 매핑 + 원본 chunks
            │
            ▼
 context_builder.py
 - Phase 1 citation chunk 선택
 - 앞/뒤 인접 chunk 추가
 - chunk_id/page 유지
 - 토큰 초과 처리
            │
            ▼
 grounding_prompts.py
 - System/User 분리
 - 원문만 사용
 - Prompt Injection 방어
 - checklist evidence_rule 주입
            │
            ▼
 judgment_harness.py
 - Structured JSON LLM 호출
 - Phase 1 RetryPolicy/Timeout 재사용
 - JSON parse/DEV 최소 구조 검사
            │
            ├──▶ remediation_guides.py
            │     draft reason code 기반 보완/추가확인 가이드
            │
            └──▶ versions.py
                  Prompt/Context/Checklist/Reason code/Retry 버전 기록
```

## 실행 확인

```bash
cd phase2_판단
pytest -q
python tools/inspect_phase2_ready.py
```

`pytest -q`는 현재 10개 테스트를 실행한다. 실제 LLM 서버 없이도 Context/Prompt/Retry 연결을 확인한다.

실제 LLM 호출은 `src/judgment_harness.py`의 `run_item_judgment()`를 사용한다. 기본값은 Phase 1과 같은 Ollama OpenAI-compatible 전송부를 재사용한다.

## 현재 남은 승인·통합 사항

Input 0.2 / Output 0.3, 오류·검토 코드, Self-check 신호와 provisional 전달은 연결했다.
아래 항목은 코드가 임의로 정하지 않고 담당 팀의 확정값을 기다린다.

- 기준팀: 체크리스트·사유 코드 최종 승인, 항목별 최신성·인정 형식, 최종 critical 값
- 통합팀: Phase 1 완료 후 DB 상태를 `from_phase1()`로 VALIDATING에 연결
- 운영: 검토자 결정 저장과 서버/API 연결
- 품질: 실제 정답 근거가 확정된 문항으로 의미 정확도 측정

자세한 구현 범위와 교체 지점은 `IMPLEMENTATION_2026-10-01.md`를 본다.
