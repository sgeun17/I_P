-- =========================================================
-- user_schema.sql : 회원가입·로그인용 표 3개
--   mysql -u root -p evidence_db < user_schema.sql
--
-- CREATE TABLE IF NOT EXISTS 다. 여러 번 돌려도 데이터가 안 지워진다.
-- DROP 이 없으므로 기존 evidence·chunk·phase2_* 표는 건드리지 않는다.
-- =========================================================

-- ── 1) 사용자 ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_user (
  user_id       VARCHAR(50)  NOT NULL,                     -- 로그인 아이디
  display_name  VARCHAR(100) NOT NULL,                     -- review_history.actor 에 들어갈 이름
  password_hash VARCHAR(255) NOT NULL,                     -- pbkdf2_sha256$반복수$소금$해시
  role          VARCHAR(20)  NOT NULL,                     -- REVIEWER / APPROVER
  is_admin      TINYINT(1)   NOT NULL DEFAULT 0,           -- 가입 승인·계정 관리 권한
  status        VARCHAR(20)  NOT NULL DEFAULT 'PENDING',   -- PENDING / ACTIVE / DISABLED
  failed_count  INT          NOT NULL DEFAULT 0,           -- 연속 로그인 실패 횟수
  locked_until  DATETIME(3)  NULL,                         -- 이 시각까지 잠김
  created_at    DATETIME(3)  NOT NULL,
  approved_at   DATETIME(3)  NULL,
  approved_by   VARCHAR(50)  NULL,
  password_changed_at DATETIME(3) NOT NULL,
  last_login_at DATETIME(3)  NULL,
  PRIMARY KEY (user_id),
  KEY idx_user_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 2) 로그인 세션 ───────────────────────────────────────
-- session_id 는 토큰 원본이 아니라 토큰의 sha256 이다.
-- DB 를 읽어도 남의 세션을 그대로 쓸 수 없게 한다.
CREATE TABLE IF NOT EXISTS user_session (
  session_id  CHAR(64)    NOT NULL,
  user_id     VARCHAR(50) NOT NULL,
  created_at  DATETIME(3) NOT NULL,
  expires_at  DATETIME(3) NOT NULL,
  revoked_at  DATETIME(3) NULL,
  PRIMARY KEY (session_id),
  KEY idx_session_user (user_id, expires_at),
  CONSTRAINT fk_session_user FOREIGN KEY (user_id)
    REFERENCES app_user (user_id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 3) 계정 활동 기록 ────────────────────────────────────
-- 없는 아이디로 들어온 로그인 실패도 남겨야 해서 FOREIGN KEY 를 걸지 않는다.
CREATE TABLE IF NOT EXISTS user_login_log (
  log_id   BIGINT       NOT NULL AUTO_INCREMENT,
  user_id  VARCHAR(50)  NOT NULL,
  event    VARCHAR(20)  NOT NULL,   -- SIGNUP/ACTIVATE/DISABLE/LOGIN_OK/LOGIN_FAIL/LOCKED/LOGOUT/PW_CHANGE
  detail   VARCHAR(200) NULL,
  at       DATETIME(3)  NOT NULL,
  PRIMARY KEY (log_id),
  KEY idx_log_user (user_id, at),
  KEY idx_log_event (event, at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
