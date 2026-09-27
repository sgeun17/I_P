-- ================================================
-- chunk_schema.sql : 청크 저장용 표
--   schema.sql 을 먼저 돌린 뒤에 실행하세요.
--     mysql -u root -p evidence_db < chunk_schema.sql
-- ================================================

DROP TABLE IF EXISTS chunk;

CREATE TABLE chunk (
  chunk_id      VARCHAR(64)  NOT NULL,                 -- E0001_v1_c0000 (전체에서 유일)
  evidence_id   VARCHAR(20)  NOT NULL,                 -- 어떤 증적의 청크인지
  version       INT          NOT NULL,                 -- 증적 버전
  chunk_index   INT          NOT NULL,                 -- 문서 안에서의 순서 (0부터)
  file_type     VARCHAR(10)  NOT NULL,                 -- pdf, docx, xlsx, pptx, txt, csv, png, jpg
  source_file   VARCHAR(500) NOT NULL,                 -- 원래 파일 이름
  chunk_type    VARCHAR(10)  NOT NULL,                 -- text 또는 table
  page_start    INT          NULL,                     -- PDF=페이지, XLSX=시트, PPTX=슬라이드
  page_end      INT          NULL,                     --   DOCX·TXT·CSV·PNG·JPG 는 NULL
  heading       TEXT         NULL,                     -- 청크 바로 위의 가장 최근 제목
  text          TEXT         NOT NULL,                 -- 청크 본문 (800자 이하)
  block_orders  TEXT         NOT NULL,                 -- 원본 블록 번호 목록. JSON 배열 글자 "[1, 2]"
  source        VARCHAR(10)  NOT NULL,                 -- parser 또는 ocr
  created_at    DATETIME(3)  NOT NULL,                 -- 이 청크를 만든 시각 (한국 시간)

  PRIMARY KEY (chunk_id),
  UNIQUE KEY uk_evidence_version_index (evidence_id, version, chunk_index),
  KEY idx_evidence (evidence_id, version),
  KEY idx_source (source),

  -- 증적을 지우면 그 증적의 청크도 같이 지워집니다 (DB 와 청크가 어긋나지 않게)
  CONSTRAINT fk_chunk_evidence FOREIGN KEY (evidence_id)
    REFERENCES evidence (evidence_id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ※ 마스킹을 하지 않아서 text 칸에 개인정보가 그대로 들어갑니다.
--    DB 접근 권한과 백업 관리에 주의하세요.
