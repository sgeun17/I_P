-- =========================================================
-- ISMS-P 증적 도구 : Phase 2 결과 · 검토 이력 · 제외 목록
--
-- 실행:  mysql -u root -p < phase2_schema.sql
--   schema.sql · chunk_schema.sql 을 먼저 실행한 뒤에 돌린다.
--   기존 세 표(evidence, evidence_history, chunk)는 건드리지 않는다.
--
-- ⚠ 이 파일은 CREATE TABLE IF NOT EXISTS 다. 여러 번 돌려도 데이터가 안 지워진다.
--   표를 완전히 새로 만들려면 phase2_schema_reset.sql 을 쓴다.
-- =========================================================

USE evidence_db;

-- ── 1) Phase 2 판정 결과 ──────────────────────────────
CREATE TABLE IF NOT EXISTS phase2_result (
  result_id        BIGINT       NOT NULL AUTO_INCREMENT,
  evidence_id      VARCHAR(20)  NOT NULL,
  evidence_version INT          NOT NULL,
  control_id       VARCHAR(20)  NOT NULL,
  control_name     VARCHAR(200) NOT NULL,

  overall_result   VARCHAR(20)  NOT NULL,
  review_required  TINYINT(1)   NOT NULL DEFAULT 0,

  -- 이 판정 하나의 검토 상태. evidence.status 와 별개다.
  -- 한 증적이 통제항목 여러 개에 걸리면 항목마다 따로 검토·승인해야 한다.
  --   NOT_REQUIRED  검토가 필요 없다
  --   PENDING       검토 대기
  --   APPROVED      승인됨
  --   REVALIDATING  사람이 고쳐서 다시 판정해야 한다
  --   REJECTED      반려됨
  review_state     VARCHAR(16)  NOT NULL DEFAULT 'NOT_REQUIRED',

  provisional        TINYINT(1)   NOT NULL DEFAULT 0,
  provisional_reason VARCHAR(300) NULL,

  -- ── 승인 전에 반드시 같이 보여야 하는 것들 ──
  phase1_review_required TINYINT(1)   NOT NULL DEFAULT 0,
  phase1_review_reasons  VARCHAR(200) NULL,
  error_codes            VARCHAR(200) NULL,
  freshness_status       VARCHAR(30)  NULL,
  format_status          VARCHAR(30)  NULL,

  citation_count       INT NOT NULL DEFAULT 0,
  citation_with_source INT NOT NULL DEFAULT 0,

  schema_version    VARCHAR(40) NOT NULL,
  rule_version      VARCHAR(40) NOT NULL,
  checklist_version VARCHAR(80) NULL,
  checklist_approved TINYINT(1) NOT NULL DEFAULT 0,

  payload       JSON        NOT NULL,          -- 원본 출력. 사람이 고쳐도 안 바뀐다
  created_at    DATETIME(3) NOT NULL,
  superseded_at DATETIME(3) NULL,

  -- 같은 증적·항목에서 '현재 유효한 줄' 은 하나뿐이어야 한다.
  -- 생성 컬럼으로 만들어 UNIQUE 를 건다 (NULL 은 UNIQUE 에 안 걸려서 그냥은 못 막는다).
  current_key VARCHAR(45) GENERATED ALWAYS AS
    (IF(superseded_at IS NULL, CONCAT(evidence_id, '#', control_id), NULL)) STORED,

  PRIMARY KEY (result_id),
  UNIQUE KEY uk_current (current_key),
  KEY idx_current (control_id, superseded_at),
  KEY idx_evidence (evidence_id, superseded_at),
  KEY idx_overall (overall_result, superseded_at),
  KEY idx_state (review_state, superseded_at),
  CONSTRAINT fk_p2r_evidence FOREIGN KEY (evidence_id)
    REFERENCES evidence (evidence_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 2) 검토 이력 ──────────────────────────────────────
-- 사람이 고친 값은 payload 를 덮지 않고 여기에만 쌓인다.
-- 지금 보여줄 값은 payload + 이 이력을 순서대로 적용해서 만든다 (effective).
CREATE TABLE IF NOT EXISTS review_history (
  history_id   BIGINT       NOT NULL AUTO_INCREMENT,
  result_id    BIGINT       NOT NULL,
  seq          INT          NOT NULL,

  action       VARCHAR(10)  NOT NULL,          -- APPROVE / MODIFY / REJECT
  actor        VARCHAR(100) NOT NULL,
  actor_role   VARCHAR(20)  NULL,
  acted_at     DATETIME(3)  NOT NULL,

  target       VARCHAR(80)  NULL,              -- overall_result / 2.5.1-Q03 / 2.5.1-Q03.reason_codes
  value_before TEXT         NULL,              -- 저장 시점에 DB 에서 읽은 값. 호출자가 못 정한다
  value_after  TEXT         NULL,
  reason       TEXT         NULL,

  state_before VARCHAR(16)  NOT NULL,          -- 이 판정의 review_state
  state_after  VARCHAR(16)  NOT NULL,
  status_before VARCHAR(20) NOT NULL,          -- 증적 전체의 status
  status_after  VARCHAR(20) NOT NULL,

  PRIMARY KEY (history_id),
  UNIQUE KEY uk_result_seq (result_id, seq),
  KEY idx_result (result_id, seq),
  KEY idx_actor (actor, acted_at),
  CONSTRAINT fk_rh_result FOREIGN KEY (result_id)
    REFERENCES phase2_result (result_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 3) Phase 2 를 돌리지 못한 대상 ─────────────────────
-- control_id 가 NULL 이면 증적 전체 (NO_MATCH·E502),
-- 값이 있으면 그 통제항목만 (NO_CHECKLIST).
CREATE TABLE IF NOT EXISTS phase2_excluded (
  excluded_id  BIGINT       NOT NULL AUTO_INCREMENT,
  evidence_id  VARCHAR(20)  NOT NULL,
  control_id   VARCHAR(20)  NOT NULL DEFAULT '',   -- '' = 증적 전체
  reason_code  VARCHAR(40)  NOT NULL,
  reason       VARCHAR(300) NULL,
  noted_at     DATETIME(3)  NOT NULL,
  PRIMARY KEY (excluded_id),
  UNIQUE KEY uk_evidence_control (evidence_id, control_id),
  KEY idx_reason (reason_code),
  CONSTRAINT fk_p2x_evidence FOREIGN KEY (evidence_id)
    REFERENCES evidence (evidence_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 이 표가 없으면 올린 증적 33건 중 25건만 화면에 보이고 8건이 사라진다.
