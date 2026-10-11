-- =========================================================
-- seed_demo_admin.sql : 시험용 관리자 계정을 DB 에 바로 넣는다
--   mysql -u root -p evidence_db < auth\seed_demo_admin.sql
--
--   아이디   operator@isms-p.test
--   비밀번호 demo1234
--   역할     admin (회사: 증적있소 테스트 / isms-p.test)
--
-- 가입 화면으로는 못 만든다 — demo1234 는 비밀번호 규칙(2종 10자 이상)에
-- 안 맞는다. 그래서 해시를 미리 계산해 바로 넣는다.
--
-- 여러 번 돌려도 안전하다. 비밀번호가 바뀌어도 다시 돌리면 되돌아온다.
--
-- ★ 시험용이다. 실제 운영에 쓰면 안 된다.
--   누구나 아는 비밀번호라 이 계정이 살아 있으면 로그인 통제가 없는 것과 같다.
--   끝나면 지운다:  DELETE FROM app_user WHERE email = 'operator@isms-p.test';
-- =========================================================

-- 한글이 ??? 로 깨지지 않게. mysql 로 파일을 읽을 때 꼭 필요하다.
-- COLLATE 까지 표와 맞춰야 아래 domain 비교에서 1267 오류가 안 난다.
SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 회사: 이미 있으면 그걸 쓰고, 없으면 다음 번호로 만든다
SET @dom = 'isms-p.test';
SET @cid = (SELECT company_id FROM company WHERE domain = @dom LIMIT 1);
SET @cid = IFNULL(@cid, CONCAT('C', LPAD(
    IFNULL((SELECT MAX(CAST(SUBSTRING(company_id, 2) AS UNSIGNED)) FROM company), 0) + 1,
    4, '0')));

INSERT IGNORE INTO company (company_id, name, domain, status, created_at, created_by)
VALUES (@cid, '증적있소 테스트', @dom, 'ACTIVE', NOW(3), 'operator@isms-p.test');

-- 관리자. password_hash 는 demo1234 를 PBKDF2-HMAC-SHA256 60만 회로 돌린 값이다.
INSERT INTO app_user
  (user_id, display_name, password_hash, role, is_admin, is_super, status,
   failed_count, locked_until, created_at, password_changed_at, approved_at,
   last_login_at, email, company_id, verified)
VALUES
  ('operator@isms-p.test', '시험 관리자',
   'pbkdf2_sha256$600000$VmqTtu8pjWiE6xgFIG9VzQ$oUCNrdtfU856kOwxM4uIXBO7cklwwou_xx2bk2qWkwM',
   'admin', 1, 0, 'ACTIVE', 0, NULL, NOW(3), NOW(3), NOW(3), NULL,
   'operator@isms-p.test', @cid, 1)
ON DUPLICATE KEY UPDATE
  password_hash = VALUES(password_hash),
  role          = 'admin',
  is_admin      = 1,
  status        = 'ACTIVE',
  verified      = 1,
  failed_count  = 0,
  locked_until  = NULL,
  company_id    = @cid;

-- 혹시 잠겨 있던 세션은 정리한다
UPDATE user_session SET revoked_at = NOW(3)
 WHERE user_id = 'operator@isms-p.test' AND revoked_at IS NULL;

-- ── 확인 ─────────────────────────────────────────────────
SELECT u.email, u.display_name, u.role, u.status, u.verified,
       c.company_id, c.name AS company, c.domain
  FROM app_user u JOIN company c ON c.company_id = u.company_id
 WHERE u.email = 'operator@isms-p.test';
