# Phase 2 — 조건부 문항·회사 맞춤 프롬프트 보완 분석 및 수정 내역

작성일: 2026-10-06  
기준 저장소: 사용자 제공 최신 `I_P(20261006-070933).zip`  
수정 범위: `phase2_판단/`만 수정

---

## 1. 문제 요약

발견된 사례는 다음과 같다.

> 통제항목 매칭 자체는 정상적으로 이루어졌다.  
> 회사는 "만 14세 미만 이용자는 받지 않는다"라고 정책에 명시했고, 실제 회원가입 단계에서도 연령 제한을 두었다.  
> 그런데 법정대리인 동의서가 없다는 이유로 Phase 2가 근거 부족으로 판단하면서 판정 신뢰가 크게 떨어졌다.

표면적으로는 "법정대리인 동의서가 없음"이 문제처럼 보이지만, 실제 핵심은 **그 증적이 필요한 적용 조건이 먼저 발생했는지를 확인하지 않고 후속 증적을 찾은 것**이다.

---

## 2. 검색팀 문제인지 먼저 확인한 결과

### 결론

**이번 사례의 1차 원인은 Phase 1 검색/매핑팀 문제가 아니다.**

이유는 다음과 같다.

1. 증적은 이미 `3.1.1 개인정보 수집·이용` 계열로 정상 매칭되었다.
2. 최신 Phase 2 전체 체크리스트 r5에는 이미 아래와 같이 조건부 문항이 분리되어 있다.
   - `3.1.1-Q06`: 만 14세 미만 아동의 동의 대상 처리에 대한 법정대리인 고지
   - `3.1.1-Q07`: 만 14세 미만 아동의 동의 대상 처리에 대한 법정대리인 동의 확인
3. 두 문항의 `applicability_condition`도 이미
   `만 14세 미만 아동의 개인정보를 동의에 근거하여 처리한다`
   로 존재한다.
4. 즉 검색팀이 잘못된 통제항목을 연결한 문제가 아니라, **Phase 2가 문항의 적용 조건을 먼저 판별하지 않고 법정대리인 동의 기록을 찾는 방향으로 해석한 문제**에 가깝다.

### 검색팀을 다시 확인해야 하는 경우

다음 경우라면 Phase 1 검색팀 확인이 필요하다.

- "만 14세 미만 가입 제한", "연령 제한", "아동 개인정보 미수집" 같은 표현이 있는데도 `3.1.1` 자체가 후보/PRIMARY로 나오지 않는 경우
- 다른 개인정보 통제항목으로 잘못 매핑되는 경우
- Phase 1 최종 매핑 citation이 아예 무관한 활동을 가리키는 경우

이번 사례처럼 **매칭은 정상인데 문항별 판단에서 문제가 생긴 경우는 Phase 2 쪽을 먼저 고치는 것이 맞다.**

---

## 3. 최신 GitHub에서 이미 해결되어 있던 부분

최신 저장소를 확인했을 때 아래 내용은 이미 반영되어 있었다.

### 3.1 전체 체크리스트에 적용 조건이 존재함

최신 `phase2_기준/full_checklist_draft.json`은 2026-10-05 r5이며 승인 상태다.

`3.1.1-Q07`에는 이미 다음과 같은 구조가 있다.

- 질문: 만 14세 미만 아동의 동의 대상 처리에 대한 법정대리인의 동의가 확인되는가?
- 적용 조건: 만 14세 미만 아동의 개인정보를 동의에 근거하여 처리한다.
- 필요한 문맥: 정보주체 연령, 처리 범위, 적용 조건 발생 여부, 법적 근거 등

즉 **기준팀에서 "이 문항은 모든 회사에 무조건 적용한다"라고 만들어 둔 상태는 아니었다.**

### 3.2 `P2_U_NO_TRIGGER_EVENT` 사유 코드가 이미 존재함

최신 사유 코드에는 조건부 사건이 발생하지 않았음을 표현하기 위한:

```text
P2_U_NO_TRIGGER_EVENT
```

가 이미 존재한다.

따라서 새로운 reason code를 자운이 임의로 만들 필요는 없다.

### 3.3 N/A는 아직 공식 결과값이 아님

현재 공식 결과는:

```text
MET / NOT_MET / UNKNOWN
```

이고 N/A는 없다.

따라서 "우리 회사는 14세 미만 이용자를 받지 않으니 이 문항은 완전 제외"를
판단팀이 임의로 N/A로 추가하면 인터페이스 계약을 깨게 된다.

현재 안전한 표현은:

```text
UNKNOWN + P2_U_NO_TRIGGER_EVENT
```

이다.

단, 장기적으로 이런 문항을 종합점수에서 완전히 제외하려면
기준팀/인터페이스팀에서 N/A 또는 applicability-excluded 상태를 공식 계약으로 추가해야 한다.

---

## 4. 최신 코드에서 실제로 남아 있던 문제

### 4.1 Prompt가 `applicability_condition`을 전달만 하고 "먼저 판정하라"고 강제하지 않음

기존 `grounding_prompts.py`는 체크리스트의 `applicability_condition`을 LLM에 전달하고 있었다.

하지만 규칙은 주로:

```text
근거가 충분한가?
MET / NOT_MET / UNKNOWN인가?
```

에 집중되어 있었고,

```text
1. 이 문항이 현재 회사에 적용되는가?
2. 적용될 때만 필요한 후속 증적을 찾는다.
```

라는 순서를 명확하게 강제하지 않았다.

이 때문에 모델이:

```text
법정대리인 동의 기록이 후보 증적이다
→ 동의 기록이 없다
→ 근거 부족
```

처럼 판단할 수 있었다.

### 4.2 회사별 운영 범위를 전달할 정식 보조 입력이 없음

기존 Prompt는:

- checklist item
- judgment rules
- reason codes
- evidence context

만 받았다.

따라서 다음과 같은 회사 특성을 구조적으로 전달할 자리가 없었다.

- 만 14세 미만 회원가입을 받지 않음
- 특정 서비스는 해외이전을 하지 않음
- 재택·원격근무를 허용하지 않음
- 특정 자산/업무는 조직 범위에 없음
- 내부 위험수용 수준

결과적으로 회사 맞춤형 판단을 하려면 이런 정보가 증적 문장 안에 우연히 포함되어 있어야 했다.

### 4.3 판단 하네스 기본 기준 파일이 과거 Chapter 2 draft를 가리킴

최신 기준팀은:

```text
full_checklist_draft.json
full_reason_codes_draft.json
```

의 r5 전체 1·2·3장 카탈로그를 승인했다.

하지만 자운의 직접 실행 하네스 기본값은 아직:

```text
chapter2_full_checklist_draft.json
chapter2_reason_codes_draft.json
```

이었다.

따라서 `3.1.1-Q07` 같은 개인정보 문항은 기본 실행으로 조회할 수 없고
별도 경로를 명시해야 하는 상태였다.

이 부분도 함께 수정했다.

---

## 5. 이번 수정 방향

이번 수정은 **Phase 1 매핑 로직을 바꾸지 않고 Phase 2 판단 단계에서 적용 조건을 먼저 확인하도록 보완**했다.

핵심 처리 순서는 다음과 같다.

```text
Phase 1 통제항목 매핑
        ↓
Phase 2 체크리스트 문항
        ↓
① applicability_condition 확인
        ↓
② 회사별 organization_context 확인
        ↓
③ 실제 evidence_context와 대조
        ↓
④ 적용 조건이 발생한 경우에만 후속 증적 요구
        ↓
⑤ MET / NOT_MET / UNKNOWN
```

---

## 6. 만 14세 미만 사례의 수정 후 판단 방식

### Case A — 정책 + 실제 가입 차단이 모두 확인됨

예:

```text
정책: 만 14세 미만 이용자는 회원가입 대상이 아니다.
실제 화면/설정: 생년월일 입력 결과 만 14세 미만은 다음 단계로 진행할 수 없다.
```

`3.1.1-Q07`의 조건:

```text
만 14세 미만 아동의 개인정보를 동의에 근거하여 처리한다.
```

가 발생하지 않은 것으로 볼 직접 근거가 있다.

따라서:

```text
법정대리인 동의서가 없다
```

를 일반적인 증적 부족으로 해석하지 않는다.

현재 계약에서는 N/A가 없으므로:

```json
{
  "result": "UNKNOWN",
  "reason_codes": ["P2_U_NO_TRIGGER_EVENT"]
}
```

방향으로 표현한다.

reason에는:

```text
만 14세 미만 가입을 받지 않는 정책과 실제 가입 제한이 확인되어
현재 검토 범위에서 법정대리인 동의가 필요한 처리 발생을 확인하지 못했다.
```

처럼 적용 조건 미발생을 설명한다.

### Case B — 정책 문구만 있고 실제 차단은 확인되지 않음

예:

```text
정책서에는 "14세 미만 회원가입 불가"라고 적혀 있음
실제 회원가입 화면/설정은 제출되지 않음
```

정책 선언만으로 실제 운영을 추정하지 않는다.

따라서 적용 조건 미발생을 확정하지 않고 UNKNOWN으로 남긴다.

### Case C — 실제 14세 미만 개인정보 처리 사실이 있음

이 경우에는 `3.1.1-Q07`의 적용 조건이 발생한다.

따라서 정상적으로 법정대리인 동의 근거를 확인한다.

단순히 동의서가 제출되지 않았다는 이유만으로 바로 NOT_MET은 아니며,
실제 동의를 받지 않았다는 직접 근거가 없으면 UNKNOWN 원칙을 유지한다.

---

## 7. 위험수용 수준은 어떻게 넣었나

회사 맞춤형 도구를 만들기 위해 선택적으로:

```text
organization_context
```

를 Prompt에 넣을 수 있게 했다.

예:

```json
{
  "profile_version": "company-a-v1",
  "applicability_facts": [
    {
      "fact_id": "AGE-001",
      "statement": "만 14세 미만 회원가입을 허용하지 않는다.",
      "status": "ORGANIZATION_DECLARED"
    }
  ],
  "risk_acceptance": {
    "level": "LOW"
  }
}
```

다만 위험수용 수준은 다음처럼 제한했다.

```text
위험수용 수준
    ↓
보완 우선순위 / Human Review 참고

위험수용 수준
    X
법적 의무 면제
    X
ISMS-P 요구사항 무효화
    X
UNKNOWN/NOT_MET을 MET으로 변경
```

즉 회사 맞춤형은 만들되 **법적 의무를 회사가 위험을 수용했다는 이유로 통과시키는 구조는 만들지 않았다.**

---

## 8. organization_context의 신뢰 경계

회사 프로필은 "조직이 선언한 사실"과 "실제 이행 증거"를 구분한다.

### 프로필만으로 사용 가능한 것

- 서비스 범위 설명
- 회사의 정책 방향
- 적용 대상 후보
- 위험수용 수준
- 어떤 운영 근거를 확인해야 하는지에 대한 힌트

### 프로필만으로 증명할 수 없는 것

- 실제 가입 제한이 동작했다
- 실제 동의를 받았다
- 실제 보안조치를 수행했다
- 실제 정책이 현장에서 적용됐다

실제 이행은 계속 `evidence_context`의 원문으로 확인한다.

프로필과 증적이 충돌하면 프로필을 우선하지 않는다.

---

## 9. 수정 파일

### 기존 파일 수정

| 파일 | 수정 내용 |
|---|---|
| `src/grounding_prompts.py` | 적용 조건 우선 판정, 조직 맞춤 컨텍스트, 위험수용 경계 규칙 추가. Prompt v0.5 |
| `src/judgment_harness.py` | `organization_context` 전달 지원, 프로필 버전/SHA-256 audit 메타데이터 추가 |
| `src/checklist_adapter.py` | 기본 기준을 승인된 전체 r5 체크리스트/사유 코드로 변경 |
| `src/versions.py` | 승인된 전체 기준 파일을 사용하는 현재 상태에 맞게 설명 정리 |
| `tools/run_item_judgment.py` | `--organization-profile` 옵션 추가 |
| `tools/run_validated_phase2.py` | 전체 r5를 기본값으로 변경하고 `--organization-profile` 전달 옵션 추가 |
| `docs/phase2_rules_v0.1.md` | applicability-first와 회사 프로필/위험수용 규칙 명문화 |
| `docs/RUN_VALIDATED_PHASE2.md` | 전체 r5 기본값과 조직 프로필 실행법 설명 |
| `tests/test_grounding_prompts.py` | 14세 미만 적용조건/위험수용 Prompt 테스트 추가 |
| `tests/test_judgment_harness.py` | 회사 프로필 전달 및 hash 기록 테스트 추가 |
| `tests/test_versions.py` | r5 전체 승인 카탈로그 기준으로 기대값 갱신 |

### 신규 파일

```text
configs/organization_profile.example.json
```

회사별 프로필 작성 예시다.

---

## 10. Prompt의 핵심 변경 문장

이번 수정의 핵심은 아래 원칙이다.

> 증적의 충분성을 보기 전에 checklist_item.applicability_condition이 현재 조직·서비스·기간에 실제로 발생했는지 먼저 확인한다.

그리고:

> 적용 조건이 발생하지 않았음이 직접 확인되면, 그 조건이 발생했을 때만 필요한 후속 증적을 요구하지 않는다.

따라서:

```text
14세 미만 처리 없음
        ↓
법정대리인 동의가 필요한 사건 자체가 없음
        ↓
"동의서가 없다"를 근거 부족으로 먼저 판단하지 않음
```

구조가 된다.

---

## 11. 검색/Context 쪽에서 아직 확인해야 할 부분

Prompt를 고쳐도 **"만 14세 미만 가입 차단" 문장이 LLM에게 전달되지 않으면 판단할 수 없다.**

현재 `context_builder.py`는 기본적으로:

```text
Phase 1 mapping citation chunk
+ 앞뒤 인접 chunk
```

를 중심으로 context를 구성한다.

따라서 연령 제한 문구가 문서의 다른 위치에 있는데
Phase 1 citation과 멀리 떨어져 있다면 Phase 2 Prompt에서 보이지 않을 수 있다.

이 경우의 원인은:

```text
Phase 1 통제항목 검색 실패
```

라기보다는:

```text
Phase 2 문항별 applicability 근거 context 누락
```

에 가깝다.

### 다음 확인 순서

1. 문제 사례의 최종 `evidence_context`에 "14세 미만 가입 제한" 문장이 있는지 확인
2. 있으면 → 이번 Prompt 수정으로 재시험
3. 없으면 → Phase 2 Context Builder에서 `applicability_condition` 기반 보조 청크 선택을 추가
4. 그래도 3.1.1 자체가 매핑되지 않으면 → 그때 Phase 1 검색팀 확인

이번 수정에서는 Phase 1 검색 알고리즘은 건드리지 않았다.

---

## 12. N/A가 필요한가?

장기적으로는 검토할 가치가 있다.

현재:

```text
조건 미발생
→ UNKNOWN + P2_U_NO_TRIGGER_EVENT
```

이기 때문에 회사가 정상적으로 업무 범위에서 제외한 조건부 문항도
종합 결과에서 UNKNOWN으로 보일 수 있다.

이를 완전히 해결하려면 다음 중 하나가 팀 계약으로 필요하다.

### 방안 1 — N/A 추가

```text
MET / NOT_MET / UNKNOWN / NOT_APPLICABLE
```

### 방안 2 — applicability 상태 별도 추가

예:

```json
{
  "applicability": "NOT_APPLICABLE",
  "result": null
}
```

### 방안 3 — 기준팀이 문항별 대체 MET 조건을 정의

최신 기준팀은 일부 다른 문항에서
"사건 미발생 + 유효한 사전 방침"을 MET으로 인정하는 문항별 예외를 이미 사용하고 있다.

14세 미만 문항에도 같은 정책을 적용할지는
**판단팀이 임의로 결정하지 않고 기준팀과 합의해야 한다.**

---

## 13. 테스트 결과

### 이번 수정 관련 집중 테스트

```text
8 passed
```

검사 범위:

- applicability-first Prompt 문구
- 조직별 profile 전달
- 14세 미만 사례
- 위험수용이 판정을 덮어쓰지 않는지
- Prompt version
- organization profile SHA-256 기록
- 최신 full r5 checklist/reason code version

### 전체 Phase 2 판단 테스트

```text
295 passed
1 failed
3 warnings
```

남은 1개 실패는 이번 수정과 무관한 기존 OS 경로 표현 테스트다.

```text
test_overall_helper_is_loaded_from_interface_root
```

테스트가 Windows 경로:

```text
phase2_인터페이스\overall_result.py
```

만 허용하도록 작성되어 있어 Linux 실행환경의:

```text
phase2_인터페이스/overall_result.py
```

에서 실패한다.

실제 모듈 로딩 경로 자체는 올바르다.

경고 3건도 기존 Human Review provisional signal 관련 경고로 이번 Prompt 수정에서 새로 발생한 것이 아니다.

---

## 14. 최종 판단

이번 문제를 한 문장으로 정리하면:

> **매칭 오류가 아니라, 조건부 요구사항에서 '적용 조건 확인'보다 '필요 증적 존재 여부'를 먼저 본 Phase 2 판단 문제다.**

따라서 이번 수정은:

```text
검색팀 매핑 수정 X

Phase 2 적용 조건 우선 Prompt O
회사별 organization_context O
위험수용 정보 전달 O
법적 의무 면제 X
최신 전체 r5 기준 연결 O
```

로 처리했다.

다만 실제 문제 증적의 연령 제한 문장이 현재 `evidence_context`에 들어오지 않는다면
Prompt만으로 해결되지 않는다. 그 경우 다음 보완 대상은 Phase 1 검색팀이 아니라
**Phase 2의 문항별 applicability 근거 Context 선택 로직**이다.
