# v2 백엔드 — 깃에 올라온 UI 모델에 맞춤

깃의 `IS_SO_UI/dist/app.js` 가 부르는 주소 14개를 실제로 만들고, 그 모델
(회사 = 메일 도메인 / admin·reviewer·member / 초대 / super)로 격리를 맞췄다.
메일 발송은 구현하지 않는다 — 화면의 인증·재발송 단계는 서버가 통과시킨다.

## 1. 폴더 이름 맞추기

압축 안은 ASCII 이름이다. 아래대로 옮긴다.

| 압축 안 | 저장소 |
|---|---|
| `.gitignore` | 루트 (지금 없습니다 — 먼저 넣으세요) |
| `ISSUES.md` | 루트 |
| `auth/` | `auth/` (새 폴더. README·규격 문서까지 같이) |
| `phase1_integration/api.py` | `phase1_통합/api.py` (덮어쓰기) |
| `phase1_input/main.py` | `phase1_입력/main.py` (덮어쓰기) |
| `phase1_input_database/evidence_store.py` | `phase1_입력/database/evidence_store.py` (덮어쓰기) |
| `phase2_integration/*.py` | `phase2_통합/` (`phase2_api.py` 는 새 파일, 나머지 3개 덮어쓰기) |
| `phase2_interface_test/*.py` | `phase2_인터페이스/test/` (덮어쓰기) |
| `test/*.py` | `test/` (새 파일) |

## 2. DB 준비

이미 돌린 적 있으면 건너뛰어도 된다. 여러 번 돌려도 안전하다.

```
mysql -u root -p evidence_db < auth/user_schema.sql
mysql -u root -p evidence_db < auth/company_schema.sql
```

- `user_schema.sql` — `app_user`, `user_session`, `user_login_log`
- `company_schema.sql` — `company`, `invite` 를 만들고 기존 표에 칸을 더한다
  - `app_user` ← `email`, `company_id`, `verified`, `is_super`
  - `evidence`, `evidence_history` ← `company_id`, `uploaded_by`

`evidence.company_id` = 누가 볼 수 있나(격리), `evidence.uploaded_by` = 누가
올렸나(책임추적성). 두 개를 섞지 않는다.

## 3. 띄우기

먼저 꾸러미를 깐다. `api.py` 가 시작하면서 `evidence_store` 를 불러오고,
거기서 `pymysql` 이 필요하다.

```
pip install -r phase1_입력/database/requirements.txt
pip install -r phase1_통합/requirements.txt
```

입력팀 requirements 에는 `easyocr` 이 들어 있어 torch 까지 2GB 가 넘는다.
로그인·회사 쪽만 돌려볼 거면 이 네 개로도 된다.

```
pip install pymysql cryptography python-dotenv fastapi uvicorn python-multipart
```

그리고

```
uvicorn api:app --reload --port 8000     # phase1_통합 폴더에서
```

UI 는 `IS_SO_UI/dist` 를 이 서버가 `/` 로 같이 내보낸다. 따로 띄울 때는
8080 CORS 가 이미 열려 있다.

첫 사람은 화면에서 가입한다. 서비스 운영자(super)는 화면에 없으므로 한 번만
파이썬으로 만든다.

```python
import company_store as cs
cs.bootstrap_super("ops@회사.co.kr", "비밀번호")
```

## 3-1. 초대 링크는 지금 관리자가 직접 확인한다

메일을 안 보내는데, **화면에 링크를 띄우는 코드가 demo 모드에만 있다.**
그래서 실제 서버에서는 관리자가 이렇게 한다.

1. 팀 관리 화면에서 직원을 초대한다 (초대 대기 줄이 뜬다)
2. 브라우저로 `http://서버:8000/api/company/invites` 를 연다
3. 그 줄의 `invite_url` 을 복사해서 당사자에게 전달한다
4. 받는 사람이 그 주소를 열면 초대 수락 화면이 뜬다 (이쪽은 이미 된다)

서버는 준비돼 있다 — `GET /api/company/members` 의 `invites` 에도 같은
`invite_url` 이 들어 있다. UI 초대 행에 "링크 복사" 버튼 한 줄만 들어가면
2·3 단계가 없어진다. 자세한 건 `ISSUES.md` F-1.

## 4. 파일별로 뭘 하나

### `auth/owner.py` — "지금 이 요청은 어느 회사 것만 보나"

격리 기준을 정하는 한 군데다. 요청마다 미들웨어가 `use()` 로 심고, 저장소
파일들은 `where()` / `mine()` / `owns()` 로 그걸 SQL 조건으로 받아 쓴다.
`_is_current()` 를 한 함수로 모았던 것과 같은 이유 — 열한 군데에 흩어지면
한 군데가 빠진다.

- `use(company_id, actor, role, is_super)` 요청 범위를 심는다
- `current()` 거를 회사 번호. `None` 이면 안 거른다 (터미널 · super)
- `where("e")` → `e.company_id = 'C0001'`
- `owns(cur, evidence_id)` 이 증적이 우리 회사 것인가
- `blocked()` super 면 참. `where()` · `mine()` 이 `1 = 0` 을 돌려주고
  `owns()` 가 거짓이라, **운영자는 고객 증적을 못 본다** (팀 결정 2026-10-11)
- `applies()` 조건을 붙여야 하나. WHERE 를 손으로 조립하는 자리에서 쓴다.
  `current() is not None` 으로 물으면 super 가 통과해 버린다
- `audit_denied(...)` 운영자가 고객 증적을 건드리려 한 것을 팀원의
  `access_audit.py` 에 `DENIED` 로 남긴다
- 회사 없이 사람만 들어오면 `ValueError` — 안 거르면 모든 회사가 보인다

### `auth/user_store.py` — 비밀번호 · 세션

- PBKDF2-HMAC-SHA256 60만 회. 설치할 게 없어서 폐쇄망에서 그냥 된다
- 없는 아이디도 같은 시간을 쓴다 (`burn_time`) — 안 그러면 응답 시간 차이로
  어떤 메일이 가입돼 있는지 알아낼 수 있다
- 세션은 DB 에 둔다. 비밀번호를 바꾸거나 내보내면 쓰던 토큰이 그 자리에서 끊긴다

### `auth/company_store.py` — 회사 · 멤버 · 초대

- 가입하면 회사가 생기고 가입한 사람이 admin 이 된다
- 회사 = 메일 도메인. 같은 도메인으로 회사를 두 번 못 만든다
- gmail·naver 같은 공용 메일은 회사로 받지 않는다 (UI 와 같은 목록)
- 초대는 7일. 같은 도메인만 초대할 수 있고, 한 번 쓰면 끝난다
- 마지막 admin 은 강등·탈퇴가 막힌다
- `profile()` 이 UI 가 쓰는 칸을 그대로 돌려준다
  (`name · email · role · company · companyId · domain · verified`)

### `auth/auth_api.py` — UI 가 부르는 주소 14개

| 주소 | 하는 일 |
|---|---|
| `POST /api/auth/signup` | 회사 만들며 가입 (admin) |
| `POST /api/auth/login` | 로그인 → 토큰 |
| `POST /api/auth/verify` · `resend` | 메일 인증 단계. 통과시키고 안 보낸다고 알려준다 |
| `GET /api/invites/{token}` | 초대 링크 열기 (로그인 없이) |
| `POST /api/invites/accept` | 초대 받아 가입 |
| `GET /api/company/members` | 멤버 목록 |
| `PATCH`·`DELETE /api/company/members/{email}` | 역할 변경 · 사용 중지 · 내보내기 |
| `GET`·`POST /api/company/invites` | 초대 만들기 · 목록 |
| `POST .../{token}/resend`, `DELETE .../{token}` | 기한 연장 · 취소 |
| `GET /api/admin/users`·`companies`·`audit`·`settings` | super 전용 조회 |
| `PATCH /api/admin/companies/{id}` | 회사 정지 · 해제 |
| `PATCH /api/admin/users/{email}`, `PUT /api/admin/settings` | 501 — 팀 합의 필요 |

`require(action)` 이 UI 의 권한표(`app.js` 의 `perms`)와 같은 규칙으로 막는다.

### `phase1_통합/api.py` — 합쳐진 서버 + 문지기

- **가짜 로그인을 지웠다.** 전에 있던 `/api/auth/login` 은 아무 메일이나
  비밀번호 없이 `role: admin` 으로 통과시키고, 돌려주는 토큰을 어디에도
  저장하지 않아 아무도 검증할 수 없었다
- 입력팀 라우터 + `auth_api` + `phase2_api` 를 한 서버에 올린다. 주소는 안 바뀐다
- 미들웨어가 `PUBLIC` 목록에 없는 모든 주소에 로그인을 건다. **새 주소를
  추가하면 자동으로 잠긴다** — 깜빡해서 열려 있는 쪽보다 낫다
- 로그인한 뒤 `owner.use(...)` 를 심는다. 조회는 전부 거기를 지나간다
- `/api/results` 는 파일을 열기 전에 `is_visible()` 로 DB 에 한 번 묻고,
  super 면 `audit_super` 로 감싼다
- MySQL 이 꺼져 있으면 추적 내용이 섞인 500 대신 503 `DB_UNAVAILABLE`
- `/api/readiness` 는 로그인 전에도 열리지만(UI 의 "준비 상태 확인") 서버
  안쪽 경로는 보내지 않는다

### `phase1_입력/main.py`, `database/evidence_store.py`

- `actor_of(request)` · `company_of(request)` — 세션에서 올린 사람·회사를 꺼낸다
- 업로드·교체할 때 `uploaded_by` · `company_id` 를 같이 적는다
- 목록·상세·삭제·파일 경로가 모두 `owner` 조건을 지나간다. 남의 번호를 직접
  넣으면 403 이 아니라 **404** — 403 은 "있긴 있다" 를 알려주는 셈이다
- 증적 삭제는 admin 만. 문지기의 `ROLE_RULES` 에서 건다
- `is_visible(evidence_id)` — 결과 파일을 열기 전에 묻는 자리

### `phase2_통합/phase2_api.py` 외 3개

- 검토 주소에 `require("review")` 를 걸었다. member 는 조회만 된다
- super 도 `require()` 를 통과하지 못한다. 운영자가 할 수 있는 것은
  `/api/admin/*` (회사·사용자 관리) 뿐이다
- 매핑 조회(`_fetch`)와 검토 이력 조회도 회사를 확인한다
- `actor` · `role` 을 본문으로 받지 않는다 (넣어 보내면 422). 세션에서만 온다
- 판정·제외·매핑 조회가 `owner` 조건을 지나간다

## 5. 어떻게 굴러가나

```
① 가입      화면 → POST /api/auth/signup
            company 한 줄 + app_user 한 줄 (admin, ACTIVE, verified=1)

② 로그인    화면 → POST /api/auth/login
            user_session 한 줄 → 토큰. 화면이 저장해서 Authorization 에 붙인다

③ 모든 요청  미들웨어
            토큰 → 세션 → profile() → owner.use(회사, 사람, 역할)
            PUBLIC 아니면 토큰 없으면 401, 회사 없으면 403

④ 업로드    POST /api/upload
            actor_of/company_of → evidence(uploaded_by, company_id)

⑤ 조회·판정  list_evidence / by_evidence / summary ...
            전부 owner.where() 가 붙어 자기 회사 것만 나온다

⑥ 결과 열기  GET /api/results/{id}
            is_visible() 로 DB 에 먼저 묻고 → 파일 읽기
            운영자(super)면 안 열리고 access_audit 에 DENIED 가 남는다

⑦ 초대      POST /api/company/invites → 토큰
            화면이 링크를 띄우고 사람이 전달 (메일 안 보냄)
            받는 사람 → POST /api/invites/accept → 같은 회사 멤버
```

## 6. 시험

```
python test/run_gate_check.py        80   잠겼나 + 화면이 쓰는 method 까지
python test/run_company_check.py     80   회사 격리 · 초대 · 운영자 범위 · 화면 계약
python test/run_auth_check.py        56   비밀번호 · 세션
python test/run_api_check.py         47   검토 권한 · super 는 못 고친다
python test/run_isolation_check.py   51   저장소 함수 하나하나 · 매핑 · 이력
python test/run_uploader_check.py    19   올린 사람 · 회사 · 중복 확인 범위
python test/run_real_check.py        39   ★ 진짜 MySQL 로 전체 한 바퀴
```

`run_gate_check` 는 주소 이름만 보지 않고 **method 까지** 본다. 경로만 맞고
POST/PATCH 가 다르면 화면에서 405 가 나는데, 그걸 놓쳤던 적이 있다.

앞 6개는 메모리 sqlite 라 MySQL 없이 돈다. `run_real_check.py` 는 진짜
MySQL 을 쓰고 **시작할 때 시험용 표를 비운다** — 남겨둘 자료가 있으면 돌리지 말 것.

기존 시험도 그대로 통과한다 (`phase2_통합/test` 364건, `phase2_인터페이스/test`,
팀원 `pytest` 13건).
