# Phase 2 인터페이스 (phase2_입력)

Phase 1 결과를 Phase 2 가 받을 수 있는 모양으로 맞추고, Phase 2 가 내놓을 결과의
모양·상태 전이·오류 처리를 정한 곳. **판정 자체는 하지 않는다** (그건 `phase2_판단`).

상태 DRAFT. 

---

## 폴더

```
phase2_입력/
├── README.md
├── requirements.txt
├── .gitignore
│
├── phase2_input.schema.json              Phase 2 가 받는 입력 규격     
├── phase2_output.schema.json             Phase 2 가 내는 출력 규격    
├── phase2_input_sample.json              입력 예시 — 판정 진행
├── phase2_input_sample_no_primary.json   입력 예시 — 판정할 게 없는 경우
│
├── phase2_status.md                      상태 전이 규격              
├── phase2_status.py                      상태 8개의 전이표
├── phase2_errors.md                      오류·재시도·검토 전환 규격 
├── phase2_errors.py                      오류 코드·검토 사유·재시도
│
└── test/
    ├── build_phase2_input.py             Phase 1 결과 → Phase 2 입력 조립
    ├── overall_result.py                 문항별 판정 → 통제항목 종합 결론
    ├── run_integration.py                Phase 1 결과 연동 시험        
    ├── run_output_validation.py          출력 규격·판정 규칙 검증      
    ├── run_status_check.py               상태 전이 검사
    └── run_error_check.py                오류·검토 흐름 검사
```

`test/` 안의 `build_phase2_input.py` 와 `overall_result.py`, 그리고 루트의
`phase2_status.py`·`phase2_errors.py` 는 **테스트용이 아니라 본 코드**다.
판정 파이프라인이 import 해서 쓴다. `run_*.py` 넷만 검사 스크립트다.

## 돌리는 법

검사 스크립트 넷 다 **DB·LLM 에 붙지 않는다.** 저장소 파일만 읽으므로 몇 초 걸린다.

```bash
pip install jsonschema

cd phase2_입력/test
python run_status_check.py          # 저장소를 알아서 찾음
python run_integration.py
python run_output_validation.py
python run_error_check.py
```

저장소를 못 찾으면 위치를 직접 넘긴다. `python run_status_check.py C:\I_P\I_P-main`

## 다른 파트에서 쓸 때

```python
import sys
sys.path.insert(0, "phase2_입력")          # phase2_status, phase2_errors
sys.path.insert(0, "phase2_입력/test")     # build_phase2_input, overall_result

from build_phase2_input import build, judge_targets
from overall_result import compute, is_critical
from phase2_status import from_phase1, from_phase2, check, approve_review
from phase2_errors import decide_review, failed_item, evidence_outcome
```

| 함수 | 하는 일 |
|---|---|
| `build(phase1_result, chunks, scope, version)` | Phase 1 결과 → Phase 2 입력 |
| `judge_targets(phase2_input)` | 실제로 판정할 통제항목만 골라낸다 |
| `compute(items, policy)` | 문항별 판정 → `overall_result` 와 집계 |
| `from_phase1(...)` / `from_phase2(...)` | 판정 결과 → `evidence.status` |
| `check(현재, 다음)` | 못 가는 상태 전이면 `ValueError` |
| `decide_review(items, errors)` | 사람 검토가 필요한지 + 사유 코드 |
| `failed_item(...)` | 실패한 문항을 `UNKNOWN` 으로 만든다 |
| `evidence_outcome(items, errors)` | 통제항목 전체의 결말 |

## 규격 버전

| | |
|---|---|
| 입력 | `phase2-input-0.2` |
| 출력 | `phase2-output-0.2` |
| 상태 | `phase2-status-0.1` |
| 오류 | `phase2-errors-0.1` |
| 종합 판정 규칙 | `overall-v0.1-checkkind` |

결과마다 `schema_version`·`rule_version`·`judge_policy`·`critical_policy` 를
찍어둔다. 규칙이 바뀌어도 어느 기준으로 나온 결과인지 구분되고, 값이 다른 결과는
섞어서 집계하지 않는다.

## 두 가지 핵심 규칙

**① 상태값을 새로 만들지 않는다.** 입력팀 `config.VALID_STATUSES` 8개 중
`MAPPING`·`VALIDATING` 이 정의만 돼 있고 아무도 안 쓰던 자리다. 거기에 들어간다.

**② 문항 하나가 실패해도 통제항목 전체를 실패시키지 않는다.** 실패한 문항만
`UNKNOWN` 으로 둔다 (`NOT_MET` 이 아니다 — 안 지켰다고 확인한 게 아니라
확인을 못 한 것이다). 판단팀이 Phase 1 에서 세운 원칙을 그대로 따른다.


## 합의가 필요한 것

###  `judge_policy` — PRIMARY 만 판정할지, 전부 할지

작업 시간이 갈린다. 기술 결정이 아니라 일정 결정이라 혼자 정하지 않았다.

| | 대상 | 문항 | 걸리는 시간 |
|---|---|---|---|
| `primary_only` (지금) | 44개 | 647 | 약 367분 |
| `all_mapped` | 70개 | 992 | 약 562분 (1.5배) |

지금은 PRIMARY 만 판정하고 RELATED 는 참고 증적으로 연결만 한다.
그 항목에 PRIMARY 증적이 생기면 RELATED 청크도 같이 근거로 쓴다.
PRIMARY 가 끝까지 없으면 `증적 없음` 으로 낸다 — `판단 불가` 와 구분한다.