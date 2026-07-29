-- KatRAG-lite schema — 19 ตารางฐาน + 1 virtual table (design §5.2)
-- provenance-first: ทุกแถวข้อมูลหลักสูตรมี provenance_id NOT NULL (R9.2)
-- version-stamped: course / plan_slot / rule / chunk ผูก version_id NOT NULL (R10.1)
-- content-addressed: sha256 ทุกช่องเป็น hex ตัวพิมพ์เล็ก 64 อักขระ (R1.1, R9.6)

-- 1 --------------------------------------------------------------- curriculum_version
CREATE TABLE IF NOT EXISTS curriculum_version (
  version_id       INTEGER PRIMARY KEY,
  program          TEXT    NOT NULL CHECK (length(trim(program)) > 0),
  curriculum_year  INTEGER NOT NULL CHECK (curriculum_year BETWEEN 2500 AND 2699),
  edition_status   TEXT    NOT NULL CHECK (edition_status IN ('old','current')),
  version_sha256   TEXT    NOT NULL CHECK (length(version_sha256) = 64
                                      AND version_sha256 = lower(version_sha256)
                                      AND version_sha256 NOT GLOB '*[^0-9a-f]*'),
  UNIQUE (program, curriculum_year, edition_status)
);

-- 2 --------------------------------------------------------------- document
CREATE TABLE IF NOT EXISTS document (
  document_id           TEXT    PRIMARY KEY,
  relative_path         TEXT    NOT NULL UNIQUE,
  sha256                TEXT    NOT NULL CHECK (length(sha256) = 64
                                           AND sha256 = lower(sha256)
                                           AND sha256 NOT GLOB '*[^0-9a-f]*'),
  size_bytes            INTEGER NOT NULL CHECK (size_bytes >= 0),
  page_count            INTEGER NOT NULL CHECK (page_count >= 1),
  degree_level          TEXT    NOT NULL CHECK (degree_level IN ('bachelor','master','doctoral')),
  version_id            INTEGER NOT NULL REFERENCES curriculum_version(version_id),
  canonical_document_id TEXT    NOT NULL REFERENCES document(document_id),
  metadata_source_json  TEXT    NOT NULL,
  ingested_at           TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_document_sha256 ON document(sha256);
CREATE INDEX IF NOT EXISTS ix_document_version ON document(version_id);

-- 3 --------------------------------------------------------------- page
CREATE TABLE IF NOT EXISTS page (
  page_id           INTEGER PRIMARY KEY,
  document_id       TEXT    NOT NULL REFERENCES document(document_id),
  page_number       INTEGER NOT NULL CHECK (page_number >= 1),
  width_pt          REAL    NOT NULL CHECK (width_pt  > 0),
  height_pt         REAL    NOT NULL CHECK (height_pt > 0),
  char_count        INTEGER NOT NULL CHECK (char_count  >= 0),
  image_count       INTEGER NOT NULL CHECK (image_count >= 0),
  page_text         TEXT    NOT NULL,
  extraction_method TEXT    NOT NULL CHECK (length(trim(extraction_method)) > 0),
  page_sha256       TEXT    NOT NULL CHECK (length(page_sha256) = 64
                                       AND page_sha256 = lower(page_sha256)),
  status            TEXT    NOT NULL CHECK (status IN ('in_progress','page_complete')),
  completed_at      TEXT,
  UNIQUE (document_id, page_number)
);
CREATE INDEX IF NOT EXISTS ix_page_status ON page(document_id, status);

-- 4 --------------------------------------------------------------- page_metrics
CREATE TABLE IF NOT EXISTS page_metrics (
  page_id                    INTEGER PRIMARY KEY REFERENCES page(page_id) ON DELETE CASCADE,
  extracted_char_count       INTEGER NOT NULL CHECK (extracted_char_count >= 0),
  out_of_charset_ratio       REAL    NOT NULL CHECK (out_of_charset_ratio BETWEEN 0.0 AND 1.0),
  image_area_ratio           REAL    NOT NULL CHECK (image_area_ratio     BETWEEN 0.0 AND 1.0),
  domain_lexicon_match_count INTEGER NOT NULL CHECK (domain_lexicon_match_count >= 0),
  page_quality_score         REAL    NOT NULL CHECK (page_quality_score   BETWEEN 0.0 AND 1.0),
  is_ocr_candidate           INTEGER NOT NULL CHECK (is_ocr_candidate IN (0,1)),
  candidate_reason           TEXT,
  compute_path               TEXT    CHECK (compute_path IN ('fast','standard','deep')),
  route_reason_code          TEXT,
  weights_json               TEXT    NOT NULL
);

-- 5 --------------------------------------------------------------- provenance
CREATE TABLE IF NOT EXISTS provenance (
  provenance_id     INTEGER PRIMARY KEY,
  document_id       TEXT    NOT NULL,
  page_number       INTEGER NOT NULL CHECK (page_number >= 1),
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  span_start        INTEGER NOT NULL CHECK (span_start >= 0),
  span_end          INTEGER NOT NULL CHECK (span_end   >= span_start),
  extraction_method TEXT    NOT NULL CHECK (length(trim(extraction_method)) > 0),
  provenance_source TEXT    NOT NULL CHECK (provenance_source IN ('document_text','filename')),
  CHECK (x1 > x0 AND y1 > y0),
  FOREIGN KEY (document_id, page_number) REFERENCES page(document_id, page_number)
);
CREATE INDEX IF NOT EXISTS ix_provenance_page ON provenance(document_id, page_number);

-- bbox ต้องอยู่ในขอบเขตพิกัดของหน้า (R9.2) — ใช้ trigger เพราะต้องอ่านขนาดหน้า
CREATE TRIGGER IF NOT EXISTS trg_provenance_bbox_in_page BEFORE INSERT ON provenance
BEGIN
  SELECT CASE WHEN NOT EXISTS (
      SELECT 1 FROM page p
       WHERE p.document_id = NEW.document_id AND p.page_number = NEW.page_number
         AND NEW.x0 >= -0.5 AND NEW.y0 >= -0.5
         AND NEW.x1 <= p.width_pt + 0.5 AND NEW.y1 <= p.height_pt + 0.5)
    THEN RAISE(ABORT, 'provenance.bbox outside page bounds') END;
END;

-- 6 --------------------------------------------------------------- region
CREATE TABLE IF NOT EXISTS region (
  region_id                INTEGER PRIMARY KEY,
  page_id                  INTEGER NOT NULL REFERENCES page(page_id) ON DELETE CASCADE,
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  crop_sha256              TEXT    NOT NULL CHECK (length(crop_sha256) = 64),
  status                   TEXT    NOT NULL CHECK (status IN ('ok','ocr_failed')),
  selected_stage_result_id INTEGER,
  adjudication_json        TEXT    NOT NULL,
  CHECK (x1 > x0 AND y1 > y0)
);
CREATE INDEX IF NOT EXISTS ix_region_page ON region(page_id);

-- 7 --------------------------------------------------------------- ocr_stage_result
CREATE TABLE IF NOT EXISTS ocr_stage_result (
  stage_result_id       INTEGER PRIMARY KEY,
  region_id             INTEGER NOT NULL REFERENCES region(region_id) ON DELETE CASCADE,
  stage_index           INTEGER NOT NULL CHECK (stage_index IN (1,2)),
  engine                TEXT    NOT NULL CHECK (engine IN ('tesseract5','typhoon_ocr1_5_2b')),
  text                  TEXT    NOT NULL,
  quality_score         REAL    NOT NULL CHECK (quality_score BETWEEN 0.0 AND 1.0),
  confidence            REAL    NOT NULL CHECK (confidence    BETWEEN 0.0 AND 1.0),
  elapsed_ms            INTEGER NOT NULL CHECK (elapsed_ms >= 0),
  gain                  REAL,
  cost                  REAL,
  halt_decision         TEXT    CHECK (halt_decision IN ('halt','continue')),
  halt_reason           TEXT    CHECK (halt_reason IN ('oscillation','nan_guard','gain_below_cost')),
  preprocess_steps_json TEXT    NOT NULL,
  cache_hit             INTEGER NOT NULL CHECK (cache_hit IN (0,1)),
  is_selected           INTEGER NOT NULL CHECK (is_selected IN (0,1)),
  UNIQUE (region_id, stage_index, engine)
);

-- 8 --------------------------------------------------------------- table_cell
CREATE TABLE IF NOT EXISTS table_cell (
  cell_id       INTEGER PRIMARY KEY,
  page_id       INTEGER NOT NULL REFERENCES page(page_id) ON DELETE CASCADE,
  table_index   INTEGER NOT NULL CHECK (table_index >= 1),
  row_index     INTEGER NOT NULL CHECK (row_index   >= 1),
  col_index     INTEGER NOT NULL CHECK (col_index   >= 1),
  row_span      INTEGER NOT NULL DEFAULT 1 CHECK (row_span >= 1),
  col_span      INTEGER NOT NULL DEFAULT 1 CHECK (col_span >= 1),
  text          TEXT    NOT NULL,
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  plan_year     INTEGER CHECK (plan_year     BETWEEN 1 AND 8),
  plan_semester INTEGER CHECK (plan_semester BETWEEN 1 AND 3),
  provenance_id INTEGER NOT NULL REFERENCES provenance(provenance_id),
  UNIQUE (page_id, table_index, row_index, col_index)
);

-- 9 --------------------------------------------------------------- course
CREATE TABLE IF NOT EXISTS course (
  course_id              INTEGER PRIMARY KEY,
  version_id             INTEGER NOT NULL REFERENCES curriculum_version(version_id),
  code                   TEXT    NOT NULL CHECK (length(code) BETWEEN 1 AND 20),
  name_th                TEXT    NOT NULL CHECK (length(name_th) <= 255),
  name_en                TEXT    NOT NULL CHECK (length(name_en) <= 255),
  credits_total          INTEGER CHECK (credits_total      BETWEEN 0 AND 30),
  credits_lecture        INTEGER CHECK (credits_lecture    BETWEEN 0 AND 30),
  credits_lab            INTEGER CHECK (credits_lab        BETWEEN 0 AND 30),
  credits_self_study     INTEGER CHECK (credits_self_study BETWEEN 0 AND 30),
  credits_raw            TEXT    NOT NULL,
  year                   INTEGER CHECK (year     BETWEEN 1 AND 8),
  semester               INTEGER CHECK (semester BETWEEN 1 AND 3),
  category               TEXT,
  type                   TEXT,
  prerequisite_json      TEXT    NOT NULL,
  prerequisite_raw       TEXT    NOT NULL,
  flexible_year_semester INTEGER NOT NULL CHECK (flexible_year_semester IN (0,1)),
  note                   TEXT    NOT NULL CHECK (length(note) <= 500),
  provenance_id          INTEGER NOT NULL REFERENCES provenance(provenance_id),
  UNIQUE (version_id, code, year, semester)
);
CREATE INDEX IF NOT EXISTS ix_course_code ON course(code);
CREATE INDEX IF NOT EXISTS ix_course_version ON course(version_id);

-- 10 -------------------------------------------------------------- course_field_provenance
CREATE TABLE IF NOT EXISTS course_field_provenance (
  id            INTEGER PRIMARY KEY,
  course_id     INTEGER NOT NULL REFERENCES course(course_id) ON DELETE CASCADE,
  field_name    TEXT    NOT NULL CHECK (field_name IN
                  ('code','name_th','name_en','credits','year','semester',
                   'category','type','prerequisite','flexible_year_semester','note')),
  provenance_id INTEGER NOT NULL REFERENCES provenance(provenance_id),
  value_status  TEXT    NOT NULL CHECK (value_status IN ('resolved','empty')),
  raw_text      TEXT    NOT NULL,
  UNIQUE (course_id, field_name)
);

-- 11 -------------------------------------------------------------- plan_slot
CREATE TABLE IF NOT EXISTS plan_slot (
  slot_id       INTEGER PRIMARY KEY,
  version_id    INTEGER NOT NULL REFERENCES curriculum_version(version_id),
  course_id     INTEGER NOT NULL REFERENCES course(course_id) ON DELETE CASCADE,
  year          INTEGER NOT NULL CHECK (year     BETWEEN 1 AND 8),
  semester      INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 3),
  plan_variant  TEXT    NOT NULL DEFAULT 'default',
  cell_id       INTEGER REFERENCES table_cell(cell_id),
  provenance_id INTEGER NOT NULL REFERENCES provenance(provenance_id),
  UNIQUE (version_id, course_id, year, semester, plan_variant)
);
CREATE INDEX IF NOT EXISTS ix_plan_slot_slot ON plan_slot(version_id, year, semester);

-- 12 -------------------------------------------------------------- rule
CREATE TABLE IF NOT EXISTS rule (
  rule_id       INTEGER PRIMARY KEY,
  version_id    INTEGER NOT NULL REFERENCES curriculum_version(version_id),
  rule_kind     TEXT    NOT NULL CHECK (rule_kind IN
                  ('graduation','honors','dismissal','probation','grading')),
  attribute     TEXT    NOT NULL CHECK (length(trim(attribute)) > 0),
  comparator    TEXT    NOT NULL CHECK (comparator IN ('>=','>','<=','<','=','in')),
  value_numeric REAL,
  value_text    TEXT,
  provenance_id INTEGER NOT NULL REFERENCES provenance(provenance_id),
  UNIQUE (version_id, rule_kind, attribute)
);

-- 13 -------------------------------------------------------------- chunk
CREATE TABLE IF NOT EXISTS chunk (
  chunk_id       INTEGER PRIMARY KEY,
  document_id    TEXT    NOT NULL REFERENCES document(document_id),
  page_number    INTEGER NOT NULL CHECK (page_number >= 1),
  version_id     INTEGER NOT NULL REFERENCES curriculum_version(version_id),
  heading        TEXT    NOT NULL,
  text           TEXT    NOT NULL,
  token_count    INTEGER NOT NULL CHECK (token_count > 0),
  content_sha256 TEXT    NOT NULL CHECK (length(content_sha256) = 64
                                    AND content_sha256 = lower(content_sha256)),
  provenance_id  INTEGER NOT NULL REFERENCES provenance(provenance_id),
  UNIQUE (content_sha256, version_id)
);
CREATE INDEX IF NOT EXISTS ix_chunk_version ON chunk(version_id);
CREATE INDEX IF NOT EXISTS ix_chunk_page ON chunk(document_id, page_number);

-- 14 -------------------------------------------------------------- chunk_embedding
CREATE TABLE IF NOT EXISTS chunk_embedding (
  chunk_id      INTEGER PRIMARY KEY REFERENCES chunk(chunk_id) ON DELETE CASCADE,
  model_name    TEXT    NOT NULL,
  dim           INTEGER NOT NULL CHECK (dim > 0),
  vector        BLOB    NOT NULL,
  token_vectors BLOB,
  token_count   INTEGER,
  built_at      TEXT    NOT NULL
);

-- 15 -------------------------------------------------------------- review_issue
CREATE TABLE IF NOT EXISTS review_issue (
  issue_id      INTEGER PRIMARY KEY,
  issue_type    TEXT    NOT NULL CHECK (issue_type IN (
                  'dataset_scope_mismatch','duplicate_content','metadata_unresolved',
                  'metadata_conflict','low_content_page','ocr_budget_exhausted',
                  'thai_reorder_unresolved','glyph_count_mismatch',
                  'table_context_unresolved','table_shape_mismatch',
                  'credits_parse_error','prerequisite_parse_error','field_unresolved',
                  'prerequisite_cycle','memory_limit_exceeded','index_build_incomplete',
                  'metric_sample_insufficient','gold_set_invalid_reference')),
  document_id   TEXT    REFERENCES document(document_id),
  page_number   INTEGER,
  subject_ref   TEXT,
  expected_json TEXT,
  actual_json   TEXT,
  detail_json   TEXT    NOT NULL,
  created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_issue_type ON review_issue(issue_type);

-- 16 -------------------------------------------------------------- error_record
CREATE TABLE IF NOT EXISTS error_record (
  error_id    INTEGER PRIMARY KEY,
  scope       TEXT    NOT NULL CHECK (scope IN ('document','page','region','engine','database')),
  error_kind  TEXT    NOT NULL,
  document_id TEXT    REFERENCES document(document_id),
  page_number INTEGER,
  x0 REAL,
  y0 REAL,
  x1 REAL,
  y1 REAL,
  message     TEXT    NOT NULL,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_error_record_scope ON error_record(scope);

-- 17 -------------------------------------------------------------- document_relation
CREATE TABLE IF NOT EXISTS document_relation (
  relation_id      INTEGER PRIMARY KEY,
  from_document_id TEXT NOT NULL REFERENCES document(document_id),
  to_document_id   TEXT NOT NULL REFERENCES document(document_id),
  relation_type    TEXT NOT NULL CHECK (relation_type IN
                     ('duplicate_content','supersedes','superseded_by','same_program')),
  note             TEXT NOT NULL,
  UNIQUE (from_document_id, to_document_id, relation_type)
);

-- 18 -------------------------------------------------------------- query_trace
CREATE TABLE IF NOT EXISTS query_trace (
  request_id               TEXT PRIMARY KEY,
  question_text            TEXT NOT NULL,
  normalized_question      TEXT NOT NULL,
  question_level           TEXT NOT NULL CHECK (question_level IN ('L1','L2','L3','L4')),
  router_confidence        REAL NOT NULL CHECK (router_confidence BETWEEN 0.0 AND 1.0),
  router_rule_id           TEXT NOT NULL,
  router_elapsed_ms        INTEGER NOT NULL CHECK (router_elapsed_ms >= 0),
  route_selected           TEXT NOT NULL,
  route_reason             TEXT,
  version_set_json         TEXT NOT NULL,
  version_source           TEXT NOT NULL,
  queries_json             TEXT NOT NULL,
  retrieved_json           TEXT NOT NULL,
  hops_json                TEXT NOT NULL,
  halt_reason              TEXT,
  evidence_nodes_json      TEXT NOT NULL,
  citation_issued_count    INTEGER NOT NULL CHECK (citation_issued_count    >= 0),
  citation_validated_count INTEGER NOT NULL CHECK (citation_validated_count >= 0),
  claim_removed_count      INTEGER NOT NULL CHECK (claim_removed_count      >= 0),
  unsupported_claim_count  INTEGER NOT NULL CHECK (unsupported_claim_count  >= 0),
  validator_status         TEXT NOT NULL CHECK (validator_status IN ('validated','unsupported','rejected')),
  answer_elapsed_ms        INTEGER NOT NULL CHECK (answer_elapsed_ms >= 0),
  total_elapsed_ms         INTEGER NOT NULL CHECK (total_elapsed_ms  >= 0),
  -- R4.10: เส้นทางคำถามต้องไม่เรียก OCR เลย บังคับด้วย CHECK ระดับ schema
  ocr_invocations          INTEGER NOT NULL CHECK (ocr_invocations          = 0),
  preprocessor_invocations INTEGER NOT NULL CHECK (preprocessor_invocations = 0),
  adjudicator_invocations  INTEGER NOT NULL CHECK (adjudicator_invocations  = 0),
  created_at               TEXT NOT NULL
);

-- 19 -------------------------------------------------------------- gold_set
CREATE TABLE IF NOT EXISTS gold_set (
  gold_id                 INTEGER PRIMARY KEY,
  item_kind               TEXT NOT NULL CHECK (item_kind IN ('page_text','table_cell','question')),
  document_id             TEXT REFERENCES document(document_id),
  page_number             INTEGER,
  version_id              INTEGER REFERENCES curriculum_version(version_id),
  question_level          TEXT CHECK (question_level IN ('L1','L2','L3','L4')),
  payload_json            TEXT NOT NULL,
  expected_json           TEXT NOT NULL,
  expected_citations_json  TEXT NOT NULL,
  author                  TEXT NOT NULL CHECK (length(trim(author)) > 0),
  created_date            TEXT NOT NULL,
  review_method           TEXT NOT NULL CHECK (length(trim(review_method)) > 0)
);
CREATE INDEX IF NOT EXISTS ix_gold_set_kind ON gold_set(item_kind);

-- 20 (virtual) ---------------------------------------------------- chunk_fts
-- remove_diacritics 0 บังคับไว้เพราะการตัดวรรณยุกต์ไทยจะทำให้คำเปลี่ยนความหมาย
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  text, heading,
  content='chunk', content_rowid='chunk_id',
  tokenize='unicode61 remove_diacritics 0'
);
