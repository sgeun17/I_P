# 로그인 · 회사 API 규격 (UI 계약)

`IS_SO_UI/dist/app.js` 가 부르는 주소 그대로다. 모든 주소는
`phase1_통합/api.py` 한 서버에서 나온다 (기본 8000).

- 토큰은 `Authorization: Bearer <token>`
- 오류는 `{"detail": {"error_code": "...", "message": "..."}}`.
  단 **"아직 지원하지 않습니다" 두 자리**는 `detail` 이 글자다 — 화면의
  `api()` 가 객체면 JSON 을 그대로 toast 에 찍기 때문이다
- 목록은 **`items` 로 준다.** 화면이 `c.items || c` 로 받는다. 예전 키
  (`companies` · `users` · `events` · `invites`)도 같이 넣어 둔다
- **상태값은 소문자로 나간다** (`active` · `suspended` · `unverified` ·
  `disabled`). DB 안은 대문자 그대로다
- 역할은 `admin` · `reviewer` · `member`, 서비스 운영자는 `super`

## 권한표 (`app.js` 의 `perms` 와 같다)

| 할 일 | admin | reviewer | member |
|---|---|---|---|
| 업로드 · 점검 | O | O | O |
| 조회 | O | O | O |
| 검토(승인·수정·반려) | O | O | X |
| 삭제 | O | X | X |
| 팀(초대 · 역할) | O | X | X |

`super`(서비스 운영자)는 **회사·사용자 관리만** 한다 — `/api/admin/*`.
증적과 판정에는 접근하지 않고(404), 건드리려 한 시도는 감사 기록에 `DENIED`
로 남는다 (팀 결정 2026-10-11, 화면 문구와 같음).

## 로그인 없이 되는 주소

`/health` · `/classic` · `/docs` · `/redoc` · `/openapi.json` ·
`/favicon.ico` · `/api/readiness` · `/api/auth/login` · `/api/auth/signup` ·
`/api/auth/verify` · `/api/auth/resend` · `/api/invites/{token}` ·
`/api/invites/accept` · UI 정적 파일

**이 목록에 없는 주소는 전부 401** `NOT_LOGGED_IN`. 주소를 새로 만들면
자동으로 잠긴다.

## 1. 가입 · 로그인

### `POST /api/auth/signup`
회사를 만들며 첫 사람이 admin 으로 가입한다.

```json
{"company": "에이스", "name": "홍길동",
 "email": "hong@ace.co.kr", "password": "..."}
→ {"company_id": "C0001", "company": "에이스", "domain": "ace.co.kr",
   "email": "hong@ace.co.kr", "name": "홍길동", "role": "admin"}
```

| 오류 | 상태 | 언제 |
|---|---|---|
| `PUBLIC_EMAIL` | 400 | gmail · naver 같은 공용 메일 |
| `INVALID_EMAIL` | 400 | 메일 모양이 아님 |
| `WEAK_PASSWORD` | 400 | 길이·종류 미달, 아이디와 비슷함 |
| `NAME_REQUIRED` · `COMPANY_REQUIRED` | 400 | 빈 칸 |
| `USER_EXISTS` | 409 | 이미 가입된 메일 |
| `COMPANY_EXISTS` | 409 | 그 도메인 회사가 이미 있다 → 초대받아야 한다 |

### `POST /api/auth/login`

```json
{"email": "hong@ace.co.kr", "password": "..."}
→ {"token": "...", "user": {"name": "홍길동", "email": "...",
     "role": "admin", "company": "에이스", "companyId": "C0001",
     "domain": "ace.co.kr", "verified": true,
     "company_status": "ACTIVE", "user_id": "hong@ace.co.kr"}}
```

| 오류 | 상태 |
|---|---|
| `LOGIN_FAILED` | 401 |
| `ACCOUNT_LOCKED` | 423 (연속 실패) |
| `ACCOUNT_PENDING` · `ACCOUNT_DISABLED` · `COMPANY_SUSPENDED` | 403 |

없는 메일과 틀린 비밀번호는 응답 시간이 같다.

### `POST /api/auth/verify` · `POST /api/auth/resend`

메일을 보내지 않는다. 가입할 때 이미 인증된 상태로 들어간다.

```json
{"token": "..."} → {"verified": true, "note": "메일 발송은 구현하지 않았습니다. ..."}
{"email": "..."} → {"sent": false,   "note": "..."}
```

### `GET /api/auth/me` → `user` 와 같은 모양
### `POST /api/auth/logout` → `{"ok": true}`
### `POST /api/auth/password`

```json
{"old_password": "...", "new_password": "..."}
→ {"ok": true, "next": "다시 로그인하세요."}
```

바꾸면 쓰던 토큰이 그 자리에서 끊긴다. `LOGIN_FAILED` 401 / `WEAK_PASSWORD`
400 / `SAME_PASSWORD` 400.

## 2. 초대 (메일 없이)

### `POST /api/company/invites` — admin
```json
{"email": "kim@ace.co.kr", "role": "reviewer"}
→ {"token": "<43자>", "email": "...", "role": "reviewer",
   "company_id": "C0001", "company": "에이스", "expires_at": "...",
   "note": "메일을 보내지 않습니다. 이 링크를 직접 전달하세요."}
```
`DOMAIN_MISMATCH` 400 (다른 도메인) / `USER_EXISTS` 409 / `INVALID_ROLE` 400.

### `GET /api/invites/{token}` — 로그인 없이
```json
→ {"email": "kim@ace.co.kr", "company": "에이스", "role": "reviewer",
   "expires_at": "..."}
```
`INVITE_NOT_FOUND` 404 / `INVITE_EXPIRED` 400 / `INVITE_USED` 409 /
`INVITE_REVOKED` 400.

### `POST /api/invites/accept` — 로그인 없이
```json
{"token": "...", "name": "김검토", "password": "..."}
→ {"email": "...", "role": "reviewer", "company_id": "C0001"}
```

### `GET /api/company/invites` — admin
```json
→ {"items": [...], "invites": [...]}     각 줄은 위 invites 와 같은 모양
```

### `POST /api/company/invites/{token}/resend` — admin

화면의 "다시 보내기". **메일을 안 보내므로 기한만 다시 7일로 늘린다.**
토큰은 바꾸지 않는다 — 이미 링크를 전달했을 수 있다.

```json
→ {"ok": true, "token": "...", "expires_at": "...", "sent": false,
   "invite_url": "http://서버/?invite=<토큰>",
   "note": "메일을 보내지 않습니다. 기한만 다시 7일로 늘렸습니다."}
```

### `DELETE /api/company/invites/{token}` — admin

## 3. 멤버

### `GET /api/company/members` — 누구나 (자기 회사)

**팀 화면은 이 주소 하나만 부른다** (`app.js` 의 `loadTeam`). 그래서 초대
목록과 기록도 같이 들어 있다.

```json
→ {"members": [{"name": "...", "email": "...", "role": "admin",
                "status": "active", "verified": true, "active": true,
                "last_login_at": "..."}],
   "invites": [{"token": "...", "id": "...", "email": "...",
                "role": "reviewer", "status": "pending",
                "expires_at": "...",
                "invite_url": "http://서버/?invite=<토큰>"}],
   "audit": [],
   "company": "에이스", "domain": "ace.co.kr"}
```

`status` 는 `active` · `unverified` · `disabled` 셋 중 하나다.
`invites` 는 admin 에게만 채워진다 (대기 중인 것만).

`invite_url` 은 받는 사람이 열 전체 주소다. 화면은 아직 이걸 안 그린다 —
초대 행에 "링크 복사" 버튼 한 줄이 들어가면 바로 쓰인다 (ISSUES.md F-1).

### `PATCH /api/company/members/{email}` — admin

화면이 쓰는 자리다. 둘 중 **하나만** 보낸다.

```json
{"role": "member"}   → {"ok": true, "email": "...", "role": "member"}
{"active": false}    → {"ok": true, "email": "...", "active": false}
```

`active: false` 면 계정을 `DISABLED` 로 두고 쓰던 토큰을 끊는다. 지우지 않는다.

| 오류 | 상태 | 언제 |
|---|---|---|
| `LAST_ADMIN` | 400 | 마지막 admin 강등·중지 |
| `MEMBER_NOT_FOUND` | 404 | 그 회사 멤버가 아님 |
| `INVALID_ROLE` | 400 | 역할 이름이 틀림 |
| `NOTHING_TO_CHANGE` | 400 | 본문이 빔 |

`POST /api/company/members/{email}` `{role}` 도 같은 일을 한다 (먼저 만든 자리).

### `DELETE /api/company/members/{email}` — admin
내보내면 쓰던 토큰이 끊긴다. `LAST_ADMIN` 400.

## 4. 서비스 운영자 (super)

super 가 아니면 403 `NOT_ALLOWED`. admin 도 못 본다.

### `GET /api/admin/companies`
```json
→ {"items": [{"id": "C0001", "company_id": "C0001", "name": "에이스",
              "domain": "ace.co.kr", "admin": "hong@ace.co.kr",
              "members": 2, "createdAt": "2026-10-11",
              "created_at": "...", "status": "active"}],
   "companies": [ 같은 것 ]}
```
`status` 는 `active` 또는 `suspended`.

### `PATCH /api/admin/companies/{company_id}`
```json
{"status": "suspended"} → 정지. 그 회사 세션이 전부 끊기고 로그인도 막힌다
{"status": "active"}    → 해제
```
대문자로 보내도 받는다. `pending` · `rejected` 는 400 `INVALID_STATUS` —
가입 승인 흐름은 아직 없다 (ISSUES.md G). `POST` 도 같은 일을 한다.

### `GET /api/admin/users`
```json
→ {"items": [{"name": "...", "email": "...", "company": "에이스",
              "role": "admin", "status": "active",
              "last_login_at": "..."}],
   "users": [ 같은 것 ]}
```
`status` 는 `active` · `unverified` · `disabled` · `blocked`.
`blocked` 는 아직 만들지 않아서 나오지 않는다.

### `GET /api/admin/audit?limit=200`

운영자가 고객 증적을 건드린 기록. 지금 정책에서는 `DENIED` 만 쌓인다.

### `PATCH /api/admin/users/{email}` — **501, 아직 없음**
```json
{"blocked": true}
→ 501  {"detail": "운영자의 사용자 차단은 아직 지원하지 않습니다. ..."}
```
`detail` 이 글자라 화면 toast 에 그대로 뜬다.

```json
→ {"items": [{"occurred_at": "...", "actor": "ops@isso.io", "role": "super",
              "action": "VIEW_ORIGINAL", "evidence_id": "E0002",
              "evidence_version": 1, "outcome": "DENIED",
              "text": "E0002 v1 원본 조회 · 거부", "at": "..."}],
   "events": [ 같은 것 ]}
```
`text` · `at` 은 화면이 읽는 칸이다 (`app.js` 의 audit-log).
`action` 은 `VIEW_ORIGINAL` · `DOWNLOAD_ORIGINAL` · `REVIEW`,
`outcome` 은 `STARTED` · `SUCCEEDED` · `FAILED` · `DENIED`.

### `GET /api/admin/settings`

화면이 응답을 그대로 설정으로 쓴다. 그래서 **평평하게** 준다.
```json
→ {"requireApproval": false, "blockedDomains": ["gmail.com", ...],
   "mail_enabled": false, "invite_days": 7, "session_hours": 8,
   "max_failed": 5, "note": "...", "settings": { 같은 것 }}
```

### `PUT /api/admin/settings` — **501, 아직 없음**
```json
→ 501  {"detail": "가입 정책 저장은 아직 지원하지 않습니다. ..."}
```

## 5. 증적 · 검토 (기존 주소, 로그인만 추가됨)

주소와 응답은 그대로다. 달라진 것만.

- 목록·상세·결과는 **자기 회사 것만** 나온다
- 남의 회사 번호를 넣으면 **404** `EVIDENCE_NOT_FOUND` (403 이 아니다 —
  403 은 "그 번호가 있다" 를 알려주는 셈이다)
- 업로드할 때 `uploaded_by` · `company_id` 가 세션에서 자동으로 적힌다
- `POST /api/review/{id}/approve · modify · reject`,
  `POST /api/review/evidence/{id}/approve` 는 admin · reviewer 만
- `actor` · `role` 을 본문에 넣으면 **422**. 세션에서만 온다
- `GET /api/results/{id}` 는 파일을 열기 전에 DB 에 한 번 묻는다

## 6. 서버 쪽 오류

| 오류 | 상태 | 언제 |
|---|---|---|
| `NOT_LOGGED_IN` | 401 | 토큰 없음 · 만료 · 가짜 |
| `COMPANY_MISSING` | 403 | 로그인은 됐는데 소속 회사가 없다 |
| `NOT_ALLOWED` | 403 | 역할이 모자람 |
| `DB_UNAVAILABLE` | 503 | MySQL 에 못 붙었다 |
| `AUDIT_UNAVAILABLE` | 503 | super 기록을 못 남겨서 통과시키지 않았다 |
