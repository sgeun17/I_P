-- =========================================================
-- company_schema.sql : 회사 · 멤버 · 초대
--   mysql -u root -p evidence_db < company_schema.sql
--
-- IS_SO_UI 의 모델에 맞춘다 — 회사 = 이메일 도메인, 같은 도메인만 초대.
-- user_schema.sql 을 먼저 돌린 뒤에 실행한다.
--
-- 이미 있는 칸·표는 건너뛴다. 여러 번 돌려도 안전하다.
-- =========================================================

-- ── 1) 회사 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS company (
  company_id  VARCHAR(50)  NOT NULL,              -- C0001
  name        VARCHAR(200) NOT NULL,              -- 가입할 때 받은 회사 이름
  domain      VARCHAR(100) NOT NULL,              -- acme.co.kr — 이게 격리 단위다
  status      VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE / SUSPENDED
  created_at  DATETIME(3)  NOT NULL,
  created_by  VARCHAR(190) NULL,
  PRIMARY KEY (company_id),
  UNIQUE KEY uk_company_domain (domain)           -- 한 도메인에 회사 하나
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 2) 초대 ──────────────────────────────────────────────
-- 메일을 보내지 않는다. 화면이 초대 링크를 띄우고 사람이 직접 전달한다.
CREATE TABLE IF NOT EXISTS invite (
  token       CHAR(43)     NOT NULL,              -- secrets.token_urlsafe(32)
  company_id  VARCHAR(50)  NOT NULL,
  email       VARCHAR(190) NOT NULL,
  role        VARCHAR(20)  NOT NULL,              -- admin / reviewer / member
  invited_by  VARCHAR(190) NOT NULL,
  created_at  DATETIME(3)  NOT NULL,
  expires_at  DATETIME(3)  NOT NULL,              -- 7일
  accepted_at DATETIME(3)  NULL,
  revoked_at  DATETIME(3)  NULL,
  PRIMARY KEY (token),
  KEY idx_invite_company (company_id, created_at),
  KEY idx_invite_email (email),
  CONSTRAINT fk_invite_company FOREIGN KEY (company_id)
    REFERENCES company (company_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 3) app_user 에 칸 추가 ───────────────────────────────
-- MySQL 은 ADD COLUMN IF NOT EXISTS 가 없어서 information_schema 를 먼저 본다.
DROP PROCEDURE IF EXISTS add_col;
DELIMITER //
CREATE PROCEDURE add_col(IN t VARCHAR(64), IN c VARCHAR(64), IN spec TEXT)
BEGIN
  IF (SELECT COUNT(*) FROM information_schema.COLUMNS
       WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = t AND COLUMN_NAME = c) = 0 THEN
    SET @s = CONCAT('ALTER TABLE ', t, ' ADD COLUMN ', c, ' ', spec);
    PREPARE p FROM @s; EXECUTE p; DEALLOCATE PREPARE p;
  END IF;
END //
DELIMITER ;

CALL add_col('app_user', 'email',      'VARCHAR(190) NULL');
CALL add_col('app_user', 'company_id', 'VARCHAR(50) NULL');
CALL add_col('app_user', 'verified',   'TINYINT(1) NOT NULL DEFAULT 0');
CALL add_col('app_user', 'is_super',   'TINYINT(1) NOT NULL DEFAULT 0');
-- company_id = 누가 볼 수 있나(격리), uploaded_by = 누가 올렸나(책임추적성)
CALL add_col('evidence',         'company_id',  'VARCHAR(50) NULL');
CALL add_col('evidence_history', 'company_id',  'VARCHAR(50) NULL');
CALL add_col('evidence',         'uploaded_by', 'VARCHAR(190) NULL');
CALL add_col('evidence_history', 'uploaded_by', 'VARCHAR(190) NULL');

DROP PROCEDURE IF EXISTS add_col;

-- 인덱스도 같은 방식으로
SET @sql = IF((SELECT COUNT(*) FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'app_user'
                  AND INDEX_NAME = 'uk_user_email') = 0,
  'ALTER TABLE app_user ADD UNIQUE KEY uk_user_email (email)',
  'SELECT "uk_user_email 이미 있음" AS note');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql = IF((SELECT COUNT(*) FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'evidence'
                  AND INDEX_NAME = 'idx_evidence_company') = 0,
  'ALTER TABLE evidence ADD KEY idx_evidence_company (company_id)',
  'SELECT "idx_evidence_company 이미 있음" AS note');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ── 확인 ─────────────────────────────────────────────────
SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND ((TABLE_NAME = 'app_user'  AND COLUMN_NAME IN ('email','company_id','verified','is_super'))
     OR (TABLE_NAME IN ('evidence','evidence_history')
         AND COLUMN_NAME IN ('company_id','uploaded_by')))
 ORDER BY TABLE_NAME, COLUMN_NAME;
