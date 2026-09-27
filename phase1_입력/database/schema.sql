-- =========================================================
-- ISMS-P 증적 도구 : 테이블 2개 만들기
-- 실행 방법(터미널):  mysql -u root -p < schema.sql
-- 주의: 실행하면 기존 테이블과 데이터가 지워지고 새로 만들어집니다.
--
-- evidence          : 지금 쓰는 증적 (증적 하나당 한 줄, 항상 최신본)
-- evidence_history  : 교체되기 전의 옛날 버전들 (교체될 때마다 한 줄씩 쌓임)
--
-- 증적을 수정해서 다시 올리면
--   1) evidence 의 현재 줄을 evidence_history 로 복사해두고
--   2) evidence 의 그 줄을 새 파일 정보로 덮어씀 (version +1)
-- =========================================================

CREATE DATABASE IF NOT EXISTS evidence_db
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE evidence_db;

DROP TABLE IF EXISTS evidence_history;
DROP TABLE IF EXISTS evidence;

-- ── 1) 현재 증적 ──────────────────────────────
CREATE TABLE evidence (
  evidence_id   VARCHAR(20)  NOT NULL,                    -- 증적 번호 (000001), 교체해도 안 바뀜
  version       INT          NOT NULL DEFAULT 1,          -- 몇 번째 파일인지 (교체할 때마다 +1)
  file_name     VARCHAR(500) NOT NULL,                    -- 사용자가 올린 원래 파일명
  file_type     VARCHAR(10)  NOT NULL,                    -- pdf, docx, xlsx, pptx, png, jpg
  file_size     BIGINT       NOT NULL,                    -- 파일 크기 (바이트)
  file_hash     CHAR(64)     NOT NULL,                    -- 파일 지문 sha256 (64글자)
  uploaded_at   DATETIME(3)  NOT NULL,                    -- 이 버전을 올린 시각 (한국 시간)
  status        VARCHAR(20)  NOT NULL DEFAULT 'UPLOADED', -- 처리 상태
  error_code    VARCHAR(40)  NULL,                        -- 실패 시 에러 코드
  error_message TEXT         NULL,                        -- 실패 시 에러 설명
  PRIMARY KEY (evidence_id),
  KEY idx_file_hash (file_hash),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 2) 교체된 옛날 버전 보관함 ─────────────────
CREATE TABLE evidence_history (
  history_id    BIGINT       NOT NULL AUTO_INCREMENT,     -- 보관함 안에서의 순번
  evidence_id   VARCHAR(20)  NOT NULL,                    -- 어떤 증적의 옛날 버전인지
  version       INT          NOT NULL,
  file_name     VARCHAR(500) NOT NULL,
  file_type     VARCHAR(10)  NOT NULL,
  file_size     BIGINT       NOT NULL,
  file_hash     CHAR(64)     NOT NULL,
  uploaded_at   DATETIME(3)  NOT NULL,                    -- 그 버전을 올렸던 시각
  status        VARCHAR(20)  NOT NULL,                    -- 교체되기 직전 상태
  error_code    VARCHAR(40)  NULL,
  error_message TEXT         NULL,
  PRIMARY KEY (history_id),
  UNIQUE KEY uk_evidence_version (evidence_id, version),  -- 같은 증적의 같은 버전은 한 번만
  KEY idx_hist_file_hash (file_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
