"""Ingestion_Manager — per-page pull pipeline แบบ streaming + resume (design §3.5, §4.2).

หน้าที่ของชั้นนี้คือ**ประกอบ**ทุกขั้นที่ทำเสร็จแล้ว (Text_Extractor -> Thai_Glyph_Reorderer
-> Line_Assembler -> Page_Quality_Gate -> Ocr_Page_Router) เข้าเป็น pipeline ต่อหนึ่งหน้า
แล้วเขียนผลลง Provenance_Store แบบ atomic ต่อหน้า ไม่ใช่ผู้คำนวณตรรกะใหม่

ข้อบังคับที่ชั้นนี้รักษา (R6, R2.3, R4.9, R1.4, R1.5, R1.9)

1. **ไม่มี list ของหน้า** — ช่วงหน้ามาจาก `document.page_count` ที่บันทึกไว้แล้วเท่านั้น
   (R6.4) วนด้วย `range()` ตรง ๆ ไม่สร้าง list ของ page object ทั้งเอกสาร
2. **page image ที่ถืออยู่พร้อมกันไม่เกินเพดาน** — ยืม `PageSlot` จาก `PageBufferPool`
   ก่อนประมวลผลทุกหน้าและคืนใน `finally` เสมอ (R6.1, R6.2) แม้ Phase นี้ยังไม่ต้อง
   rasterize ภาพจริง (Ocr_Cascade ยังไม่ทำงานจนกว่าจะถึงงานที่ 9) ก็ยังคงรูปแบบการยืม/คืน
   slot ตาม pseudocode ของ design §3.5 ไว้ เพื่อให้ invariant ≤ 2 slot ถูกทดสอบได้จริง
3. **หนึ่ง transaction ต่อหน้า** — `page`, `page_metrics`, review issue ของหน้านั้น และ
   `mark_page_complete` (statement สุดท้าย) อยู่ใน transaction เดียว (R6.7)
4. **resume ข้ามหน้าที่ complete แล้ว** — ตรวจ `is_page_complete` ก่อนเรียก
   Text_Extractor ของหน้านั้นทุกครั้ง (R6.8)
5. **เอกสารที่ sha256 ซ้ำกันประมวลผลเนื้อหาเพียงครั้งเดียว** — เฉพาะ canonical document
   (ตามที่ `Document_Registry` ตัดสินไว้แล้ว) เดินผ่าน Text_Extractor/Thai_Glyph_Reorderer/
   Line_Assembler/Page_Quality_Gate.score() จริง ส่วนเอกสารซ้ำ (`canonical_document_id !=
   document_id`) ใช้ค่าตัวชี้วัดที่คำนวณไว้แล้วจาก canonical โดยตรง **ไม่เรียก
   Text_Extractor ซ้ำ** (R1.4, R1.5)
6. **โควตา OCR candidate นับข้ามทั้งเอกสาร canonical และเอกสารซ้ำ** — R4.5 บังคับว่าจำนวน
   หน้าที่ "ทำเครื่องหมายเป็น OCR candidate" ทั้ง dataset ต้องไม่เกิน 979 หน้า/3,689 หน้า
   ซึ่งนับทุกหน้าที่มี document_id ของตัวเอง (รวมหน้าของเอกสารซ้ำด้วย) ดังนั้น
   `PageQualityGate.mark()` ต้อง**ถูกเรียกอีกครั้งสำหรับหน้าของเอกสารซ้ำ**โดยใช้
   `PageMetrics` เดิมจาก canonical (ไม่คำนวณ score ใหม่) แต่ใช้ `candidates_so_far`
   ที่นับจากทั้งฐานข้อมูล ณ ขณะนั้น — มิฉะนั้นหน้าของเอกสารซ้ำจะได้ candidacy ที่ไม่ผ่าน
   การตรวจโควตา ทำให้ยอดรวมทั้ง dataset เกิน 979 ได้ (พบจริงตอนรันทั้งคลัง: ได้ 980
   ก่อนแก้ เพราะตัวนับเดิมนับเฉพาะหน้า canonical)
7. **resident memory เกิน 6 GB → บันทึกหน้าปัจจุบันให้เสร็จก่อนแล้วจึงหยุดทั้ง run** โดยคง
   ผลที่ commit ไปแล้วทั้งหมดไว้ (R6.6) — ไม่ replicate duplicate ต่อเมื่อหยุดกลางทาง
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

from katrag.common.hashing import sha256_text
from katrag.common.memory import MemoryMonitor
from katrag.common.normalize import normalize_text
from katrag.common.scratch import PageBufferPool
from katrag.common.types import PageResult
from katrag.config import KatragConfig
from katrag.errors import PageUnreadableError, ReviewIssue
from katrag.ingest.line_assembler import LineAssembler
from katrag.ingest.manifest import write_manifest
from katrag.ingest.page_router import OcrPageRouter
from katrag.ingest.quality_gate import DeclaredCharset, DomainLexicon, PageMetrics, PageQualityGate
from katrag.ingest.registry import DocumentRegistry
from katrag.ingest.text_extractor import TextExtractor
from katrag.ingest.thai_reorder import ThaiGlyphReorderer
from katrag.store.provenance_store import PageMetricsRow, PageRow, ProvenanceStore

#: compute path ทั้งสามค่า — ใช้เป็นเทมเพลตของ `pages_by_compute_path` ให้มีครบทุกคีย์เสมอ
_COMPUTE_PATHS: tuple[str, ...] = ("fast", "standard", "deep")

#: คัดลอกตัวชี้วัดที่คำนวณไว้แล้วของ canonical document ไปยัง document ซ้ำ — **ไม่รวม**
#: is_ocr_candidate/candidate_reason เพราะต้องตัดสินใหม่ด้วย candidates_so_far ของหน้านั้น
#: จริง (ดูข้อ 6 ใน docstring ของโมดูล) compute_path/route_reason_code คัดลอกได้ตรง ๆ
#: เพราะ `OcrPageRouter.route()` เป็น pure function ของ metrics เท่านั้น ไม่มีสถานะ
_CANONICAL_PAGES_SQL = """
SELECT p.page_number, p.width_pt, p.height_pt, p.char_count, p.image_count,
       p.page_text, p.extraction_method, p.page_sha256,
       m.extracted_char_count, m.out_of_charset_ratio, m.image_area_ratio,
       m.domain_lexicon_match_count, m.page_quality_score,
       m.compute_path, m.route_reason_code, m.weights_json
  FROM page p JOIN page_metrics m ON m.page_id = p.page_id
 WHERE p.document_id = ? AND p.status = 'page_complete'
 ORDER BY p.page_number
"""

#: โควตา OCR candidate ของทั้ง dataset (R4.5) — นับทุก document รวมเอกสารซ้ำด้วย
_TOTAL_CANDIDATE_COUNT_SQL = "SELECT COUNT(*) AS n FROM page_metrics WHERE is_ocr_candidate = 1"

_COMPUTE_PATH_COUNT_SQL = (
    "SELECT compute_path, COUNT(*) AS n FROM page_metrics"
    " WHERE compute_path IS NOT NULL GROUP BY compute_path"
)


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    """ผลของการรัน `IngestionManager.run()` หนึ่งครั้ง (design §4.2)."""

    status: Literal["success", "failed", "halted"]
    documents_registered: int
    pages_completed: int
    ocr_candidate_pages: int
    ocr_invoked_pages: int
    pages_by_compute_path: Mapping[str, int]
    peak_resident_bytes: int
    review_issue_ids: tuple[int, ...]


class IngestionManager:
    """ประสาน Text_Extractor -> Thai_Glyph_Reorderer -> Line_Assembler -> Page_Quality_Gate
    -> Ocr_Page_Router ต่อหนึ่งหน้า แล้วเขียนผลลง Provenance_Store แบบ streaming/resume."""

    def __init__(self, config: KatragConfig, store: ProvenanceStore) -> None:
        self._config = config
        self._store = store
        self._extractor = TextExtractor()
        self._reorderer = ThaiGlyphReorderer(config.thai)
        self._assembler = LineAssembler(config.thai)
        self._gate = PageQualityGate(
            config.page_quality,
            DomainLexicon.from_config(config.domain_lexicon),
            DeclaredCharset.from_config(config.domain_lexicon),
        )
        self._router = OcrPageRouter(config.page_route)
        self._workspace = PageBufferPool.for_pages(config.memory.max_resident_page_images)
        self._halted = False

    # ── public API (design §4.2) ─────────────────────────────────────

    def run(self, corpus_root: Path | None = None, *, resume: bool = True) -> IngestionOutcome:
        """สแกน/ลงทะเบียนเอกสาร (ครั้งแรกเท่านั้น) แล้ว pull ประมวลผลทุกหน้าแบบ streaming.

        ถ้าขอบเขต dataset ไม่ตรง 14 เอกสาร/3,689 หน้า จบงานด้วยสถานะ `failed` ทันที
        โดยไม่ประมวลผลหน้าใดเลย (R1.3) — เอกสารที่ลงทะเบียนได้แล้วยังคงอยู่ในฐานข้อมูล
        """
        root = corpus_root or self._config.dataset_root

        if self._store.document_count() == 0:
            registry = DocumentRegistry(self._config, self._store)
            result = registry.scan(root)
            registry.register(result)

        # ตรวจขอบเขตทุกครั้งที่ run() ถูกเรียก ไม่ใช่เฉพาะครั้งแรกที่ลงทะเบียน — มิฉะนั้น
        # การเรียกซ้ำ (resume=True) หลัง scope ผิดพลาดครั้งแรกจะเดินหน้าประมวลผลหน้าต่อไป
        # อย่างเงียบ ๆ ทั้งที่ยังไม่ครบ 14 เอกสาร/3,689 หน้าตามที่ R1.3 บังคับ (R1.2, R1.3)
        expected_docs = self._config.dataset.expected_document_count
        expected_pages = self._config.dataset.expected_page_total
        documents = self._store.documents()
        actual_pages = sum(int(row["page_count"]) for row in documents)
        if len(documents) != expected_docs or actual_pages != expected_pages:
            return IngestionOutcome(
                status="failed",
                documents_registered=len(documents),
                pages_completed=self._store.completed_page_count(),
                ocr_candidate_pages=0,
                ocr_invoked_pages=0,
                pages_by_compute_path={path: 0 for path in _COMPUTE_PATHS},
                peak_resident_bytes=0,
                review_issue_ids=(),
            )
        canonical_docs = [row for row in documents if row["canonical_document_id"] == row["document_id"]]
        duplicate_docs = [row for row in documents if row["canonical_document_id"] != row["document_id"]]

        monitor = MemoryMonitor.create(
            baseline_page_index=self._config.memory.rss_baseline_page_index,
            drift_tolerance=self._config.memory.rss_drift_tolerance,
            limit_bytes=self._config.memory.limit_bytes,
        )
        review_issue_ids: list[int] = []
        halted = False

        for doc_row in canonical_docs:
            if halted:
                break
            for _result in self.process_document(
                document_id=doc_row["document_id"],
                relative_path=doc_row["relative_path"],
                page_count=int(doc_row["page_count"]),
                corpus_root=root,
                resume=resume,
                monitor=monitor,
                review_issue_ids=review_issue_ids,
            ):
                pass  # side effects เขียนลง store ระหว่างวน — ผลลัพธ์ต่อหน้าไม่ต้องใช้ที่นี่
            if self._halted:
                halted = True

        if not halted:
            self._replicate_duplicate_pages(duplicate_docs)

        summary = self._summarize()
        status: Literal["success", "failed", "halted"] = "halted" if halted else "success"
        return IngestionOutcome(
            status=status,
            documents_registered=len(documents),
            pages_completed=summary["pages_completed"],
            ocr_candidate_pages=summary["ocr_candidate_pages"],
            ocr_invoked_pages=0,  # Ocr_Cascade ยังไม่ทำงาน (งานที่ 9)
            pages_by_compute_path=summary["pages_by_compute_path"],
            peak_resident_bytes=monitor.peak_bytes,
            review_issue_ids=tuple(review_issue_ids),
        )

    def process_document(
        self,
        *,
        document_id: str,
        relative_path: str,
        page_count: int,
        corpus_root: Path,
        resume: bool = True,
        monitor: MemoryMonitor | None = None,
        review_issue_ids: list[int] | None = None,
    ) -> Iterator[PageResult]:
        """ไล่หน้า 1..page_count ของเอกสารหนึ่ง แบบ pull/streaming (R6.4).

        หยุดทันทีที่หน้าใดทำให้ resident memory เกิน 6 GB (R6.6) โดย **หน้านั้นถูกบันทึก
        เสร็จแล้ว** ก่อนที่ generator นี้จะหยุด (จึงต้อง `yield` ผลของหน้านั้นก่อนหยุด)
        สถานะการหยุดอ่านได้จาก `self._halted` หลัง generator ถูก drain จนหมด
        """
        self._halted = False
        own_monitor = monitor is None
        if own_monitor:
            monitor = MemoryMonitor.create(
                baseline_page_index=self._config.memory.rss_baseline_page_index,
                drift_tolerance=self._config.memory.rss_drift_tolerance,
                limit_bytes=self._config.memory.limit_bytes,
            )
        issue_sink = review_issue_ids if review_issue_ids is not None else []

        pdf = self._extractor.open_document(corpus_root / relative_path)
        try:
            for page_number in range(1, page_count + 1):
                if resume and self._store.is_page_complete(document_id, page_number):
                    continue
                with self._workspace.page_slot():
                    result, issue_ids = self._process_page(pdf, document_id, page_number)
                issue_sink.extend(issue_ids)
                yield result

                observation = monitor.observe_page()
                if observation.limit_exceeded:
                    issue_sink.append(
                        self._store.record_review_issue(
                            ReviewIssue(
                                kind="memory_limit_exceeded",
                                document_id=document_id,
                                page=page_number,
                                detail={
                                    "resident_bytes": observation.resident_bytes,
                                    "limit_bytes": self._config.memory.limit_bytes,
                                },
                            )
                        )
                    )
                    self._halted = True
                    return
        finally:
            pdf.close()

    def process_page(self, pdf: Any, document_id: str, page_number: int) -> PageResult:
        """ประมวลผลหนึ่งหน้าโดยไม่ผ่านการวน document ทั้งไฟล์ — ใช้เรียกตรง/ทดสอบ."""
        with self._workspace.page_slot():
            result, _issue_ids = self._process_page(pdf, document_id, page_number)
        return result

    def build_manifest(self, out_path: Path) -> Path:
        """ผลิต dataset manifest จากข้อมูลใน store (R1.9)."""
        return write_manifest(self._store, out_path)

    # ── internals: per-page pipeline ────────────────────────────────

    def _process_page(
        self, pdf: Any, document_id: str, page_number: int
    ) -> tuple[PageResult, list[int]]:
        issue_ids: list[int] = []

        try:
            extraction = self._extractor.extract_page(pdf, document_id, page_number)
        except PageUnreadableError as exc:
            self._store.record_error(
                scope="page",
                error_kind="page_unreadable",
                document_id=document_id,
                page_number=page_number,
                message=str(exc),
            )
            result = PageResult(
                document_id=document_id,
                page=page_number,
                status="page_error",
                text="",
                lines=(),
                char_count=0,
                image_count=0,
                quality_score=0.0,
                compute_path=None,
                extraction_method="text_layer",
                ocr_invoked=False,
            )
            return result, issue_ids

        reordered = self._reorderer.reorder(extraction.char_set)
        assembled = self._assembler.assemble(reordered)

        # ใช้ raw get_text() จาก PyMuPDF โดยตรง — สะอาดกว่า reorderer/assembler
        # (ยืนยันจากการวินิจฉัยจริง: text layer ของ PDF ชุดนี้สะอาดอยู่แล้ว
        #  reorderer ทำพังแทน เช่น "หลักสูตร" กลายเป็น "หลกัสูตร")
        # แต่ต้องผ่าน normalize_text ก่อน เพื่อแปลง PUA (U+F70x) ของฟอนต์ไทยเก่า
        # กลับเป็นสระ/วรรณยุกต์จริง มิฉะนั้น "หน่วยกิต" จะถูกเก็บเป็น "หนวยกิต"
        if extraction.plain_text:
            page_text = normalize_text(extraction.plain_text).strip()
        else:
            page_text = assembled.text

        metrics = self._gate.score(extraction.char_set, page_text)
        candidates_so_far = self._total_candidate_count()
        decision = self._gate.mark(metrics, extraction.char_set.image_count, candidates_so_far)
        route = self._router.route(metrics)

        with self._store.transaction(enforce_closure=False):
            page_id = self._store.upsert_page(
                PageRow(
                    document_id=document_id,
                    page_number=page_number,
                    width_pt=extraction.char_set.width_pt,
                    height_pt=extraction.char_set.height_pt,
                    char_count=metrics.extracted_char_count,
                    image_count=extraction.char_set.image_count,
                    page_text=page_text,
                    extraction_method="text_layer",
                    page_sha256=sha256_text(page_text),
                )
            )
            self._store.insert_page_metrics(
                page_id,
                PageMetricsRow(
                    extracted_char_count=metrics.extracted_char_count,
                    out_of_charset_ratio=metrics.out_of_charset_ratio,
                    image_area_ratio=metrics.image_area_ratio,
                    domain_lexicon_match_count=metrics.domain_lexicon_match_count,
                    page_quality_score=metrics.page_quality_score,
                    is_ocr_candidate=decision.is_ocr_candidate,
                    candidate_reason=decision.candidate_reason,
                    compute_path=route.compute_path.value,
                    route_reason_code=route.reason_code,
                    weights=metrics.weights,
                ),
            )
            for issue in (
                *extraction.review_issues,
                *reordered.review_issues,
                *assembled.review_issues,
                *decision.review_issues,
            ):
                issue_ids.append(self._store.record_review_issue(issue))
            self._store.mark_page_complete(document_id, page_number)

        result = PageResult(
            document_id=document_id,
            page=page_number,
            status="page_complete",
            text=page_text,
            lines=assembled.lines,
            char_count=metrics.extracted_char_count,
            image_count=extraction.char_set.image_count,
            quality_score=metrics.page_quality_score,
            compute_path=route.compute_path if decision.is_ocr_candidate else None,
            extraction_method="text_layer",
            ocr_invoked=False,  # Ocr_Cascade ยังไม่ทำงาน (งานที่ 9)
        )
        return result, issue_ids

    # ── internals: duplicate replication (R1.4, R1.5) ────────────────

    def _replicate_duplicate_pages(self, duplicate_docs: list[Any]) -> None:
        """นำตัวชี้วัดที่คำนวณไว้แล้วของ canonical document มาใช้กับเอกสารซ้ำ — ไม่เรียก
        Text_Extractor/Thai_Glyph_Reorderer/Line_Assembler/Page_Quality_Gate.score() ซ้ำ
        เพราะเนื้อหาถูกประมวลผลครั้งเดียวจาก canonical (R1.4, R1.5)

        แต่ **OCR candidacy ต้องตัดสินใหม่** ด้วย `PageQualityGate.mark()` โดยใช้
        `candidates_so_far` ที่นับจากทั้งฐานข้อมูล ณ ขณะนั้น (รวมหน้าที่ replicate ไปแล้ว)
        เพื่อให้โควตา 979 หน้าเป็นเพดานของทั้ง dataset จริง ไม่ใช่ของ canonical เท่านั้น
        (ดูเหตุผลเต็มใน docstring ของโมดูล ข้อ 6)
        """
        for row in duplicate_docs:
            target_id = row["document_id"]
            canonical_id = row["canonical_document_id"]
            for page_row in self._store.query(_CANONICAL_PAGES_SQL, (canonical_id,)):
                page_number = int(page_row["page_number"])
                if self._store.is_page_complete(target_id, page_number):
                    continue

                metrics = PageMetrics(
                    document_id=target_id,
                    page=page_number,
                    extracted_char_count=int(page_row["extracted_char_count"]),
                    out_of_charset_ratio=float(page_row["out_of_charset_ratio"]),
                    image_area_ratio=float(page_row["image_area_ratio"]),
                    domain_lexicon_match_count=int(page_row["domain_lexicon_match_count"]),
                    page_quality_score=float(page_row["page_quality_score"]),
                    weights=self._store.json_loads(page_row["weights_json"]) or {},
                )
                candidates_so_far = self._total_candidate_count()
                decision = self._gate.mark(metrics, int(page_row["image_count"]), candidates_so_far)

                with self._store.transaction(enforce_closure=False):
                    page_id = self._store.upsert_page(
                        PageRow(
                            document_id=target_id,
                            page_number=page_number,
                            width_pt=float(page_row["width_pt"]),
                            height_pt=float(page_row["height_pt"]),
                            char_count=int(page_row["char_count"]),
                            image_count=int(page_row["image_count"]),
                            page_text=page_row["page_text"],
                            extraction_method=page_row["extraction_method"],
                            page_sha256=page_row["page_sha256"],
                        )
                    )
                    self._store.insert_page_metrics(
                        page_id,
                        PageMetricsRow(
                            extracted_char_count=metrics.extracted_char_count,
                            out_of_charset_ratio=metrics.out_of_charset_ratio,
                            image_area_ratio=metrics.image_area_ratio,
                            domain_lexicon_match_count=metrics.domain_lexicon_match_count,
                            page_quality_score=metrics.page_quality_score,
                            is_ocr_candidate=decision.is_ocr_candidate,
                            candidate_reason=decision.candidate_reason,
                            compute_path=page_row["compute_path"],
                            route_reason_code=page_row["route_reason_code"],
                            weights=metrics.weights,
                        ),
                    )
                    for issue in decision.review_issues:
                        self._store.record_review_issue(issue)
                    self._store.mark_page_complete(target_id, page_number)

    # ── internals: bookkeeping ───────────────────────────────────────

    def _total_candidate_count(self) -> int:
        """จำนวน OCR candidate ที่ทำเครื่องหมายไปแล้วในฐานข้อมูล ณ ขณะนี้ (สะสมทั้ง dataset
        รวมหน้าของเอกสารซ้ำ) — ใช้เป็น `candidates_so_far` ของ `PageQualityGate.mark()`."""
        rows = self._store.query(_TOTAL_CANDIDATE_COUNT_SQL)
        return int(rows[0]["n"]) if rows else 0

    def _summarize(self) -> dict[str, object]:
        pages_completed = self._store.completed_page_count()
        rows = self._store.query(_TOTAL_CANDIDATE_COUNT_SQL)
        ocr_candidate_pages = int(rows[0]["n"]) if rows else 0
        path_rows = self._store.query(_COMPUTE_PATH_COUNT_SQL)
        pages_by_compute_path = {path: 0 for path in _COMPUTE_PATHS}
        for row in path_rows:
            pages_by_compute_path[row["compute_path"]] = int(row["n"])
        return {
            "pages_completed": pages_completed,
            "ocr_candidate_pages": ocr_candidate_pages,
            "pages_by_compute_path": pages_by_compute_path,
        }
