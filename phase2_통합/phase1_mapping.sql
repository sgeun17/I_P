-- phase1_mapping.sql : Phase 1 매핑 결과를 DB 에 남긴다
--
-- 지금은 매핑 결과가 phase1_통합/results/{evidence_id}_v{n}.json 파일에만 있다.
-- 원본·청크·판정은 DB 에 있는데 매핑만 파일이라, 화면에서 "이 통제항목에 어떤
-- 증적이 후보로 걸렸나" 를 조회로 못 한다.
--
-- orchestrator 를 안 고쳐도 된다. phase2_runner 가 Phase 2 를 돌릴 때마다
-- phase1_mapping_store.save() 로 자동으로 채운다.
-- 이미 쌓인 옛 결과는 backfill() 로 한 번에 올린다.
--
--   mysql -u root -p evidence_db < phase1_mapping.sql

CREATE TABLE IF NOT EXISTS phase1_mapping (
  mapping_id       BIGINT       NOT NULL AUTO_INCREMENT,
  evidence_id      VARCHAR(20)  NOT NULL,
  evidence_version INT          NOT NULL,
  control_id       VARCHAR(20)  NOT NULL,
  control_name     VARCHAR(200) NOT NULL,
  -- PRIMARY 만 Phase 2 판정 대상이다. RELATED 는 후보로만 보여준다.
  relation         VARCHAR(20)  NOT NULL,
  similarity_score DECIMAL(5,4) NULL,
  llm_confidence   DECIMAL(5,4) NULL,
  reason           TEXT         NULL,
  citation_count   INT          NOT NULL DEFAULT 0,
  -- Phase 1 이 내린 전체 판단. 증적마다 같은 값이 통제항목 수만큼 반복된다.
  match_status     VARCHAR(20)  NULL,
  review_required  TINYINT(1)   NOT NULL DEFAULT 0,
  review_reasons   VARCHAR(200) NULL,
  trace_id         VARCHAR(64)  NULL,
  noted_at         DATETIME(3)  NOT NULL,
  PRIMARY KEY (mapping_id),
  UNIQUE KEY uk_mapping (evidence_id, evidence_version, control_id),
  KEY idx_control (control_id),
  KEY idx_relation (relation),
  CONSTRAINT fk_p1m_evidence FOREIGN KEY (evidence_id)
    REFERENCES evidence (evidence_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 매핑이 인용한 청크. 화면에서 "왜 이 통제항목에 걸렸나" 를 보여줄 때 쓴다.
CREATE TABLE IF NOT EXISTS phase1_mapping_citation (
  citation_id      BIGINT       NOT NULL AUTO_INCREMENT,
  mapping_id       BIGINT       NOT NULL,
  seq              INT          NOT NULL,
  chunk_id         VARCHAR(40)  NOT NULL,
  page             INT          NULL,
  -- 원문 그대로. 줄이거나 고치면 인용이 아니게 된다.
  quote            TEXT         NOT NULL,
  source_file      VARCHAR(255) NULL,
  PRIMARY KEY (citation_id),
  UNIQUE KEY uk_mapping_seq (mapping_id, seq),
  CONSTRAINT fk_p1mc_mapping FOREIGN KEY (mapping_id)
    REFERENCES phase1_mapping (mapping_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
