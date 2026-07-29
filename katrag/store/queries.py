"""Predefined SQL สำหรับตอบคำถาม L1/L2 ด้วย statement เดียว (design §5.5, R9.4, R16.2).

ข้อบังคับที่ทุก statement ในไฟล์นี้ต้องรักษา

- **parameterized เท่านั้น** ไม่มีการต่อสตริงจาก input ผู้ใช้ (กัน SQL injection)
- **กรองเวอร์ชันใน SQL** — ตัวกรอง `version_id` อยู่ใน WHERE ก่อน scoring
  ไม่ใช่กรองหลังได้ผลลัพธ์ (R10.5)
- **คืน provenance มาด้วย** ทุก statement ที่คืนค่า field ต้องคืนหน้าและ bbox
  เพื่อให้ประกอบ citation ได้ทันที (R9.7)
- ผูก `version_id` เป็นรายการด้วย `json_each` เพื่อรับชุดเวอร์ชันหลายค่า
  โดยไม่ต้องสร้าง placeholder แบบพลวัต
"""

from __future__ import annotations

from typing import Final

# ── L1: ค่าเดียวของวิชาเดียว ─────────────────────────────────────────

Q_L1_COURSE_CREDITS: Final = """
SELECT c.code, c.name_th, c.name_en,
       c.credits_total, c.credits_lecture, c.credits_lab, c.credits_self_study,
       c.credits_raw,
       v.program, v.curriculum_year, v.edition_status,
       p.document_id, p.page_number, p.x0, p.y0, p.x1, p.y1
  FROM course c
  JOIN curriculum_version v ON v.version_id = c.version_id
  JOIN provenance p ON p.provenance_id = c.provenance_id
 WHERE c.code = :course_code
   AND c.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY c.year, c.semester, c.course_id
"""

Q_L1_COURSE_FIELD: Final = """
SELECT c.code, f.field_name, f.value_status, f.raw_text,
       v.program, v.curriculum_year, v.edition_status,
       p.document_id, p.page_number, p.x0, p.y0, p.x1, p.y1
  FROM course_field_provenance f
  JOIN course c ON c.course_id = f.course_id
  JOIN curriculum_version v ON v.version_id = c.version_id
  JOIN provenance p ON p.provenance_id = f.provenance_id
 WHERE c.code = :course_code
   AND f.field_name = :field_name
   AND c.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY c.course_id
"""

Q_L1_COURSE_BY_NAME: Final = """
SELECT c.code, c.name_th, c.name_en, c.credits_raw, c.year, c.semester,
       v.program, v.curriculum_year, v.edition_status,
       p.document_id, p.page_number, p.x0, p.y0, p.x1, p.y1
  FROM course c
  JOIN curriculum_version v ON v.version_id = c.version_id
  JOIN provenance p ON p.provenance_id = c.provenance_id
 WHERE (c.name_th = :name OR c.name_en = :name)
   AND c.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY c.code
"""

# ── L2: กรอง / รวม / prerequisite ───────────────────────────────────

Q_L2_COURSES_IN_PLAN_SLOT: Final = """
SELECT c.code, c.name_th, c.name_en, c.credits_total, c.credits_raw,
       c.category, c.type, s.plan_variant,
       v.program, v.curriculum_year, v.edition_status,
       p.document_id, p.page_number, p.x0, p.y0, p.x1, p.y1
  FROM plan_slot s
  JOIN course c ON c.course_id = s.course_id
  JOIN curriculum_version v ON v.version_id = s.version_id
  JOIN provenance p ON p.provenance_id = s.provenance_id
 WHERE s.year = :year
   AND s.semester = :semester
   AND s.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY c.code, s.plan_variant
"""

Q_L2_CREDIT_TOTALS_BY_CATEGORY: Final = """
SELECT c.category,
       COUNT(*) AS course_count,
       COALESCE(SUM(c.credits_total), 0) AS credits_sum,
       v.program, v.curriculum_year, v.edition_status
  FROM course c
  JOIN curriculum_version v ON v.version_id = c.version_id
 WHERE c.version_id IN (SELECT value FROM json_each(:version_ids))
   AND c.credits_total IS NOT NULL
 GROUP BY c.category, v.version_id
 ORDER BY c.category
"""

Q_L2_CREDIT_TOTAL_BY_PLAN_SLOT: Final = """
SELECT s.year, s.semester, s.plan_variant,
       COUNT(*) AS course_count,
       COALESCE(SUM(c.credits_total), 0) AS credits_sum
  FROM plan_slot s
  JOIN course c ON c.course_id = s.course_id
 WHERE s.version_id IN (SELECT value FROM json_each(:version_ids))
 GROUP BY s.year, s.semester, s.plan_variant
 ORDER BY s.year, s.semester, s.plan_variant
"""

Q_L2_COURSES_BY_TYPE: Final = """
SELECT c.code, c.name_th, c.credits_total, c.year, c.semester, c.category, c.type,
       p.document_id, p.page_number, p.x0, p.y0, p.x1, p.y1
  FROM course c
  JOIN provenance p ON p.provenance_id = c.provenance_id
 WHERE c.type = :course_type
   AND c.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY c.year, c.semester, c.code
"""

Q_L2_PREREQUISITE_OF_COURSE: Final = """
SELECT c.code, c.prerequisite_raw, c.prerequisite_json,
       v.program, v.curriculum_year, v.edition_status,
       p.document_id, p.page_number, p.x0, p.y0, p.x1, p.y1
  FROM course c
  JOIN curriculum_version v ON v.version_id = c.version_id
  JOIN course_field_provenance f
       ON f.course_id = c.course_id AND f.field_name = 'prerequisite'
  JOIN provenance p ON p.provenance_id = f.provenance_id
 WHERE c.code = :course_code
   AND c.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY c.course_id
"""

Q_L2_RULES_OF_VERSION: Final = """
SELECT r.rule_kind, r.attribute, r.comparator, r.value_numeric, r.value_text,
       v.program, v.curriculum_year, v.edition_status,
       p.document_id, p.page_number, p.x0, p.y0, p.x1, p.y1
  FROM rule r
  JOIN curriculum_version v ON v.version_id = r.version_id
  JOIN provenance p ON p.provenance_id = r.provenance_id
 WHERE r.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY r.rule_kind, r.attribute
"""

# ── L3/L4: เปรียบเทียบข้ามเวอร์ชัน ──────────────────────────────────

Q_L4_VERSION_DIFF_COURSES: Final = """
SELECT c.code, c.name_th, c.credits_total, c.year, c.semester,
       v.version_id, v.program, v.curriculum_year, v.edition_status,
       p.document_id, p.page_number
  FROM course c
  JOIN curriculum_version v ON v.version_id = c.version_id
  JOIN provenance p ON p.provenance_id = c.provenance_id
 WHERE c.version_id IN (:version_a, :version_b)
 ORDER BY c.code, v.version_id
"""

Q_L4_VERSION_DIFF_RULES: Final = """
SELECT v.version_id, v.program, v.curriculum_year, v.edition_status,
       r.rule_kind, r.attribute, r.comparator, r.value_numeric, r.value_text,
       p.document_id, p.page_number
  FROM rule r
  JOIN curriculum_version v ON v.version_id = r.version_id
  JOIN provenance p ON p.provenance_id = r.provenance_id
 WHERE r.version_id IN (:version_a, :version_b)
 ORDER BY r.rule_kind, r.attribute, v.version_id
"""

# ── provenance / citation ───────────────────────────────────────────

Q_PROVENANCE_OF_CITATION: Final = """
SELECT ch.chunk_id, ch.content_sha256, ch.heading, ch.version_id,
       d.document_id, d.relative_path, p.page_number,
       p.x0, p.y0, p.x1, p.y1
  FROM chunk ch
  JOIN provenance p ON p.provenance_id = ch.provenance_id
  JOIN document d ON d.document_id = ch.document_id
 WHERE ch.content_sha256 = :chunk_sha256
"""

Q_DOCUMENTS_WITH_VERSION: Final = """
SELECT d.document_id, d.relative_path, d.page_count, d.degree_level,
       v.program, v.curriculum_year, v.edition_status
  FROM document d
  JOIN curriculum_version v ON v.version_id = d.version_id
 ORDER BY d.relative_path
 LIMIT :limit
"""

Q_PAGE_TEXT: Final = """
SELECT document_id, page_number, width_pt, height_pt, page_text, extraction_method, status
  FROM page
 WHERE document_id = :document_id AND page_number = :page_number
"""

# ── retrieval (lexical) ─────────────────────────────────────────────

Q_RETRIEVE_LEXICAL: Final = """
SELECT ch.chunk_id, ch.version_id, ch.document_id, ch.page_number, ch.heading,
       bm25(chunk_fts) AS score
  FROM chunk_fts
  JOIN chunk ch ON ch.chunk_id = chunk_fts.rowid
 WHERE chunk_fts MATCH :match_query
   AND ch.version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY score, ch.chunk_id
 LIMIT :top_k
"""

Q_CHUNKS_OF_VERSIONS: Final = """
SELECT chunk_id, version_id, document_id, page_number, heading, text, content_sha256
  FROM chunk
 WHERE version_id IN (SELECT value FROM json_each(:version_ids))
 ORDER BY chunk_id
"""

#: แมประดับคำถามกับ statement ที่ใช้ตอบ — ใช้ตรวจว่า L1/L2 ตอบได้ทุกข้อ (R9.4)
L1_QUERIES: Final[dict[str, str]] = {
    "course_credits": Q_L1_COURSE_CREDITS,
    "course_field": Q_L1_COURSE_FIELD,
    "course_by_name": Q_L1_COURSE_BY_NAME,
}

L2_QUERIES: Final[dict[str, str]] = {
    "courses_in_plan_slot": Q_L2_COURSES_IN_PLAN_SLOT,
    "credit_totals_by_category": Q_L2_CREDIT_TOTALS_BY_CATEGORY,
    "credit_total_by_plan_slot": Q_L2_CREDIT_TOTAL_BY_PLAN_SLOT,
    "courses_by_type": Q_L2_COURSES_BY_TYPE,
    "prerequisite_of_course": Q_L2_PREREQUISITE_OF_COURSE,
    "rules_of_version": Q_L2_RULES_OF_VERSION,
}

ALL_QUERIES: Final[dict[str, str]] = {
    **{f"l1.{k}": v for k, v in L1_QUERIES.items()},
    **{f"l2.{k}": v for k, v in L2_QUERIES.items()},
    "l4.version_diff_courses": Q_L4_VERSION_DIFF_COURSES,
    "l4.version_diff_rules": Q_L4_VERSION_DIFF_RULES,
    "provenance.of_citation": Q_PROVENANCE_OF_CITATION,
    "document.list": Q_DOCUMENTS_WITH_VERSION,
    "page.text": Q_PAGE_TEXT,
    "retrieve.lexical": Q_RETRIEVE_LEXICAL,
    "retrieve.chunks_of_versions": Q_CHUNKS_OF_VERSIONS,
}


def version_ids_param(version_ids: "frozenset[int] | set[int] | tuple[int, ...]") -> str:
    """แปลงชุด version_id เป็น JSON array สำหรับ `json_each` (เรียงค่าเพื่อ determinism)."""
    return "[" + ",".join(str(int(v)) for v in sorted(version_ids)) + "]"
