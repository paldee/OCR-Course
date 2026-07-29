"""Provenance_Store — API เดียวสำหรับเขียน/อ่านข้อมูลหลักสูตร (design §4.17, §5.3).

กฎที่ชั้นนี้บังคับ (นอกเหนือจาก CHECK/FK ใน schema)

1. **provenance closure** — ทุกแถวในตารางข้อมูลหลักสูตรต้องมี provenance ที่ครบ
   ทุกฟิลด์ และทุก course ต้องมี provenance ครบทั้ง 11 field; การละเมิดทำให้
   `ROLLBACK` ทั้ง transaction โดยไม่มี partial commit (R9.3)
2. **version stamping** — chunk / course / plan_slot / rule ต้องมี version ครบ
   สามค่า มิฉะนั้นปฏิเสธการเขียนแถวนั้น (R10.2)
3. **atomic page unit** — `UPDATE page SET status='page_complete'` เป็น statement
   สุดท้ายของ transaction ต่อหน้า (R6.7)
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from katrag.common.hashing import canonical_json, is_sha256_hex
from katrag.common.types import BBox, CurriculumVersion, Provenance
from katrag.errors import (
    ProvenanceViolationError,
    ReviewIssue,
    StoreAccessError,
    VersionStampMissingError,
)
from katrag.store import integrity

#: ตารางที่ต้องผ่านการตรวจ provenance closure ก่อน commit (design §5.3)
CURRICULUM_TABLES: tuple[str, ...] = (
    "course",
    "course_field_provenance",
    "plan_slot",
    "rule",
    "chunk",
    "table_cell",
)

#: 11 field ของ course ที่ requirements บังคับให้มี provenance ครบ (R8.1, R8.7)
COURSE_FIELDS: tuple[str, ...] = (
    "code",
    "name_th",
    "name_en",
    "credits",
    "year",
    "semester",
    "category",
    "type",
    "prerequisite",
    "flexible_year_semester",
    "note",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class DocumentRow:
    document_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    page_count: int
    degree_level: str
    version_id: int
    canonical_document_id: str
    metadata_source: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PageRow:
    document_id: str
    page_number: int
    width_pt: float
    height_pt: float
    char_count: int
    image_count: int
    page_text: str
    extraction_method: str
    page_sha256: str


@dataclass(frozen=True, slots=True)
class PageMetricsRow:
    extracted_char_count: int
    out_of_charset_ratio: float
    image_area_ratio: float
    domain_lexicon_match_count: int
    page_quality_score: float
    is_ocr_candidate: bool
    candidate_reason: str | None
    compute_path: str | None
    route_reason_code: str | None
    weights: Mapping[str, float]


class ProvenanceStore:
    """SQLite store ไฟล์เดียวของโปรเจกต์ (R9.1)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._conn = integrity.connect(self._path)
        integrity.apply_schema(self._conn)
        self._in_transaction = False

    # ── lifecycle ────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ProvenanceStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def check_integrity(self) -> None:
        integrity.integrity_check(self._conn)

    def table_counts(self) -> dict[str, int]:
        return integrity.table_counts(self._conn)

    # ── transaction ──────────────────────────────────────────────────

    @contextmanager
    def transaction(self, *, enforce_closure: bool = True) -> Iterator[sqlite3.Connection]:
        """transaction ที่ตรวจ provenance closure ก่อน commit.

        เมื่อการตรวจไม่ผ่าน จะ `ROLLBACK` ทั้งก้อนและโยน
        `ProvenanceViolationError` — ไม่มีแถวใดของ transaction นั้นค้างอยู่ (R9.3)
        """
        if self._in_transaction:
            raise StoreAccessError("ไม่รองรับ transaction ซ้อนกัน")
        self._in_transaction = True
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            if enforce_closure:
                self._assert_provenance_closure()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False

    def _assert_provenance_closure(self) -> None:
        for table in CURRICULUM_TABLES:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM {table} t
                 LEFT JOIN provenance p ON p.provenance_id = t.provenance_id
                 WHERE p.provenance_id IS NULL
                    OR p.extraction_method IS NULL
                    OR trim(p.extraction_method) = ''
                    OR NOT (p.x1 > p.x0 AND p.y1 > p.y0)
                """
            ).fetchone()
            if row is not None and int(row["n"]) != 0:
                raise ProvenanceViolationError(
                    table=table, field_name="provenance_id", provenance_attribute="closure"
                )

        row = self._conn.execute(
            """
            SELECT c.course_id AS course_id, COUNT(f.field_name) AS n
              FROM course c
              LEFT JOIN course_field_provenance f ON f.course_id = c.course_id
             GROUP BY c.course_id
            HAVING COUNT(f.field_name) <> ?
             LIMIT 1
            """,
            (len(COURSE_FIELDS),),
        ).fetchone()
        if row is not None:
            raise ProvenanceViolationError(
                table="course_field_provenance",
                field_name=f"course_id={row['course_id']}",
                provenance_attribute=f"ต้องมีครบ {len(COURSE_FIELDS)} field แต่พบ {row['n']}",
            )

    # ── curriculum version ───────────────────────────────────────────

    def upsert_version(self, version: CurriculumVersion, version_sha256: str) -> int:
        """ลงทะเบียนเวอร์ชันหลักสูตร (idempotent) และคืน version_id."""
        if not is_sha256_hex(version_sha256):
            raise StoreAccessError(
                "version_sha256 ต้องเป็น hex ตัวพิมพ์เล็ก 64 อักขระ", value=version_sha256
            )
        self._require_full_version(version)
        conn = self._conn
        conn.execute(
            """
            INSERT INTO curriculum_version (program, curriculum_year, edition_status, version_sha256)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (program, curriculum_year, edition_status) DO NOTHING
            """,
            (version.program, version.curriculum_year, version.edition_status, version_sha256),
        )
        row = conn.execute(
            """
            SELECT version_id FROM curriculum_version
             WHERE program = ? AND curriculum_year = ? AND edition_status = ?
            """,
            (version.program, version.curriculum_year, version.edition_status),
        ).fetchone()
        if row is None:  # pragma: no cover - เป็นไปได้เฉพาะเมื่อ DB เสีย
            raise StoreAccessError("ลงทะเบียน curriculum version ไม่สำเร็จ")
        return int(row["version_id"])

    def version_by_id(self, version_id: int) -> CurriculumVersion | None:
        row = self._conn.execute(
            "SELECT program, curriculum_year, edition_status FROM curriculum_version WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        return CurriculumVersion(
            program=row["program"],
            curriculum_year=int(row["curriculum_year"]),
            edition_status=row["edition_status"],
        )

    @staticmethod
    def _require_full_version(version: CurriculumVersion) -> None:
        missing: list[str] = []
        if not version.program:
            missing.append("program")
        if not version.curriculum_year:
            missing.append("curriculum_year")
        if not version.edition_status:
            missing.append("edition_status")
        if missing:
            raise VersionStampMissingError("curriculum_version", tuple(missing))

    # ── document ─────────────────────────────────────────────────────

    def insert_document(self, doc: DocumentRow) -> None:
        if not is_sha256_hex(doc.sha256):
            raise StoreAccessError("document.sha256 ไม่ถูกรูปแบบ", value=doc.sha256)
        self._conn.execute(
            """
            INSERT INTO document (document_id, relative_path, sha256, size_bytes, page_count,
                                  degree_level, version_id, canonical_document_id,
                                  metadata_source_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.document_id,
                doc.relative_path,
                doc.sha256,
                doc.size_bytes,
                doc.page_count,
                doc.degree_level,
                doc.version_id,
                doc.canonical_document_id,
                canonical_json(dict(doc.metadata_source)),
                _now(),
            ),
        )

    def documents(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute("SELECT * FROM document ORDER BY relative_path").fetchall()
        )

    def document_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM document").fetchone()
        return int(row["n"]) if row else 0

    def total_page_count(self) -> int:
        row = self._conn.execute("SELECT COALESCE(SUM(page_count),0) AS n FROM document").fetchone()
        return int(row["n"]) if row else 0

    def insert_document_relation(
        self, from_document_id: str, to_document_id: str, relation_type: str, note: str
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO document_relation (from_document_id, to_document_id, relation_type, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (from_document_id, to_document_id, relation_type) DO NOTHING
            """,
            (from_document_id, to_document_id, relation_type, note),
        )

    # ── page ─────────────────────────────────────────────────────────

    def upsert_page(self, page: PageRow, *, status: str = "in_progress") -> int:
        """สร้าง/อัปเดตแถวหน้า แล้วคืน page_id (สถานะยังไม่ complete)."""
        if not is_sha256_hex(page.page_sha256):
            raise StoreAccessError("page.page_sha256 ไม่ถูกรูปแบบ", value=page.page_sha256)
        self._conn.execute(
            """
            INSERT INTO page (document_id, page_number, width_pt, height_pt, char_count,
                              image_count, page_text, extraction_method, page_sha256, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (document_id, page_number) DO UPDATE SET
                width_pt = excluded.width_pt,
                height_pt = excluded.height_pt,
                char_count = excluded.char_count,
                image_count = excluded.image_count,
                page_text = excluded.page_text,
                extraction_method = excluded.extraction_method,
                page_sha256 = excluded.page_sha256,
                status = excluded.status,
                completed_at = NULL
            """,
            (
                page.document_id,
                page.page_number,
                page.width_pt,
                page.height_pt,
                page.char_count,
                page.image_count,
                page.page_text,
                page.extraction_method,
                page.page_sha256,
                status,
            ),
        )
        return self.page_id(page.document_id, page.page_number)

    def page_id(self, document_id: str, page_number: int) -> int:
        row = self._conn.execute(
            "SELECT page_id FROM page WHERE document_id = ? AND page_number = ?",
            (document_id, page_number),
        ).fetchone()
        if row is None:
            raise StoreAccessError(
                "ไม่พบแถวหน้าที่ระบุ", document_id=document_id, page_number=page_number
            )
        return int(row["page_id"])

    def insert_page_metrics(self, page_id: int, metrics: PageMetricsRow) -> None:
        self._conn.execute(
            """
            INSERT INTO page_metrics (page_id, extracted_char_count, out_of_charset_ratio,
                                      image_area_ratio, domain_lexicon_match_count,
                                      page_quality_score, is_ocr_candidate, candidate_reason,
                                      compute_path, route_reason_code, weights_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (page_id) DO UPDATE SET
                extracted_char_count = excluded.extracted_char_count,
                out_of_charset_ratio = excluded.out_of_charset_ratio,
                image_area_ratio = excluded.image_area_ratio,
                domain_lexicon_match_count = excluded.domain_lexicon_match_count,
                page_quality_score = excluded.page_quality_score,
                is_ocr_candidate = excluded.is_ocr_candidate,
                candidate_reason = excluded.candidate_reason,
                compute_path = excluded.compute_path,
                route_reason_code = excluded.route_reason_code,
                weights_json = excluded.weights_json
            """,
            (
                page_id,
                metrics.extracted_char_count,
                metrics.out_of_charset_ratio,
                metrics.image_area_ratio,
                metrics.domain_lexicon_match_count,
                metrics.page_quality_score,
                1 if metrics.is_ocr_candidate else 0,
                metrics.candidate_reason,
                metrics.compute_path,
                metrics.route_reason_code,
                canonical_json(dict(metrics.weights)),
            ),
        )

    def is_page_complete(self, document_id: str, page_number: int) -> bool:
        """ใช้สำหรับ resume — หน้าที่ complete แล้วต้องไม่ถูกประมวลผลซ้ำ (R6.8)."""
        row = self._conn.execute(
            """
            SELECT 1 FROM page
             WHERE document_id = ? AND page_number = ? AND status = 'page_complete'
            """,
            (document_id, page_number),
        ).fetchone()
        return row is not None

    def completed_page_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM page WHERE status = 'page_complete'"
        ).fetchone()
        return int(row["n"]) if row else 0

    def mark_page_complete(self, document_id: str, page_number: int) -> None:
        """statement สุดท้ายของ transaction ต่อหน้า (R6.7).

        ต้องเรียกภายใน `transaction()` เท่านั้น เพื่อให้ทั้งหน้าเป็นหน่วย atomic
        """
        if not self._in_transaction:
            raise StoreAccessError(
                "mark_page_complete ต้องถูกเรียกภายใน transaction เพื่อความ atomic",
                document_id=document_id,
                page_number=page_number,
            )
        cursor = self._conn.execute(
            """
            UPDATE page SET status = 'page_complete', completed_at = ?
             WHERE document_id = ? AND page_number = ?
            """,
            (_now(), document_id, page_number),
        )
        if cursor.rowcount != 1:
            raise StoreAccessError(
                "ไม่พบแถวหน้าที่จะทำเครื่องหมาย page_complete",
                document_id=document_id,
                page_number=page_number,
            )

    # ── provenance ───────────────────────────────────────────────────

    def insert_provenance(
        self, provenance: Provenance, *, source: str = "document_text"
    ) -> int:
        """เขียน provenance หนึ่งแถว แล้วคืน provenance_id.

        Raises:
            ProvenanceViolationError: เมื่อฟิลด์ไม่ครบ — ผู้เรียกต้องปล่อยให้
                transaction ถูก rollback ไม่ใช่บันทึกค่าบางส่วน (R9.3)
        """
        missing = provenance.missing_attributes()
        if missing:
            raise ProvenanceViolationError(
                table="provenance", field_name="provenance", provenance_attribute=missing[0]
            )
        try:
            cursor = self._conn.execute(
                """
                INSERT INTO provenance (document_id, page_number, x0, y0, x1, y1,
                                        span_start, span_end, extraction_method, provenance_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provenance.document_id,
                    provenance.page,
                    provenance.bbox.x0,
                    provenance.bbox.y0,
                    provenance.bbox.x1,
                    provenance.bbox.y1,
                    provenance.span[0],
                    provenance.span[1],
                    provenance.extraction_method,
                    source,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ProvenanceViolationError(
                table="provenance", field_name="bbox/page", provenance_attribute=str(exc)
            ) from exc
        return int(cursor.lastrowid)

    def provenance_of(self, provenance_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM provenance WHERE provenance_id = ?", (provenance_id,)
        ).fetchone()

    # ── chunk ────────────────────────────────────────────────────────

    def insert_chunk(
        self,
        *,
        document_id: str,
        page_number: int,
        version_id: int | None,
        heading: str,
        text: str,
        token_count: int,
        content_sha256: str,
        provenance_id: int,
    ) -> int:
        """เขียน chunk พร้อมตรวจว่ามี version stamp (R10.1, R10.2)."""
        if version_id is None:
            raise VersionStampMissingError("chunk.version_id", ("version_id",))
        version = self.version_by_id(version_id)
        if version is None:
            raise VersionStampMissingError(
                "chunk.version_id", ("program", "curriculum_year", "edition_status")
            )
        if not is_sha256_hex(content_sha256):
            raise StoreAccessError("chunk.content_sha256 ไม่ถูกรูปแบบ", value=content_sha256)
        cursor = self._conn.execute(
            """
            INSERT INTO chunk (document_id, page_number, version_id, heading, text,
                               token_count, content_sha256, provenance_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                page_number,
                version_id,
                heading,
                text,
                token_count,
                content_sha256,
                provenance_id,
            ),
        )
        return int(cursor.lastrowid)

    # ── review issue / error record ──────────────────────────────────

    def record_review_issue(
        self,
        issue: ReviewIssue,
        *,
        subject_ref: str | None = None,
        expected: Any = None,
        actual: Any = None,
    ) -> int:
        """บันทึกรายการที่ต้องให้มนุษย์ตรวจ — เรียกได้ทั้งใน/นอก transaction."""
        cursor = self._conn.execute(
            """
            INSERT INTO review_issue (issue_type, document_id, page_number, subject_ref,
                                      expected_json, actual_json, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue.kind,
                issue.document_id,
                issue.page,
                subject_ref,
                None if expected is None else canonical_json(expected),
                None if actual is None else canonical_json(actual),
                canonical_json(dict(issue.detail)),
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def record_error(
        self,
        *,
        scope: str,
        error_kind: str,
        message: str,
        document_id: str | None = None,
        page_number: int | None = None,
        bbox: BBox | None = None,
    ) -> int:
        """บันทึก error record (R2.4, R2.5, R5.6) โดยไม่ยุติการประมวลผลทั้งชุด."""
        cursor = self._conn.execute(
            """
            INSERT INTO error_record (scope, error_kind, document_id, page_number,
                                      x0, y0, x1, y1, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope,
                error_kind,
                document_id,
                page_number,
                bbox.x0 if bbox else None,
                bbox.y0 if bbox else None,
                bbox.x1 if bbox else None,
                bbox.y1 if bbox else None,
                message,
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def review_issues(self, issue_type: str | None = None) -> list[sqlite3.Row]:
        if issue_type is None:
            return list(
                self._conn.execute("SELECT * FROM review_issue ORDER BY issue_id").fetchall()
            )
        return list(
            self._conn.execute(
                "SELECT * FROM review_issue WHERE issue_type = ? ORDER BY issue_id",
                (issue_type,),
            ).fetchall()
        )

    def error_records(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM error_record ORDER BY error_id").fetchall())

    # ── generic read helper ──────────────────────────────────────────

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        """รัน SELECT ที่ parameterized เท่านั้น (ไม่ต่อสตริงจาก input ผู้ใช้)."""
        return list(self._conn.execute(sql, params).fetchall())

    @staticmethod
    def json_loads(value: str | None) -> Any:
        return None if value is None else json.loads(value)
