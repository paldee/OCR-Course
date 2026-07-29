"""Property test ของหน่วยความจำและ resume ของ Ingestion_Manager (tasks 8.1, R6.1, R6.3, R6.8).

สองสมบัติที่ต้องพิสูจน์

1. จำนวน `PageSlot` ที่ถืออยู่พร้อมกัน ≤ เพดานที่ตั้งไว้ ตลอดลำดับการยืม/คืนแบบสุ่ม
   ความยาวใดก็ตาม (R6.1) — ทดสอบที่ระดับ `PageBufferPool` ตรง ๆ เพราะเป็นจุดที่บังคับ
   invariant นี้จริง ไม่ใช่ที่ `IngestionManager` ซึ่งเป็นเพียงผู้ใช้ pool
2. หลังการขัดจังหวะทุกรูปแบบ (หยุดกลางเอกสาร, restart กระบวนการ) การรันใหม่ต้องไม่
   ประมวลผลหน้าที่มีสถานะ `page_complete` แล้วซ้ำ และจำนวนการเรียก Text_Extractor
   สำหรับหน้าเหล่านั้นต้องเป็นศูนย์ (R6.8) — วัดทางอ้อมด้วยตัวนับการเรียกจริง
   เพราะ Ocr_Cascade ยังไม่ทำงานในเฟสนี้ (R6.8 พูดถึง "จำนวนการเรียก Ocr_Cascade" แต่
   หลักการเดียวกันใช้ตรวจ Text_Extractor ได้ เพราะทั้งคู่เป็น "งานหนักที่ resume ต้องข้าม")
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from katrag.common.scratch import PageBufferPool
from katrag.config import load_config
from katrag.ingest.manager import IngestionManager
from katrag.store.integrity import initialize
from katrag.store.provenance_store import ProvenanceStore

# ── Property: page slot ที่ถืออยู่พร้อมกัน ≤ เพดาน (R6.1) ──────────────

acquire_release_ops = st.lists(st.sampled_from(["acquire", "release"]), min_size=0, max_size=200)


@given(st.integers(min_value=1, max_value=4), acquire_release_ops)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_page_slot_never_exceeds_ceiling(max_slots: int, ops: list[str]) -> None:
    """ยืม/คืน slot ตามลำดับสุ่ม — จำนวนที่ถืออยู่พร้อมกันต้องไม่เกิน `max_slots` เสมอ (R6.1)."""
    pool = PageBufferPool.for_pages(max_slots)
    held: list[object] = []
    for op in ops:
        if op == "acquire":
            if pool.in_use_count >= max_slots:
                continue  # เทียบเท่า context manager ที่ยังไม่คืน — ผู้เรียกต้องรอ ไม่ยืมเพิ่ม
            cm = pool.page_slot()
            slot = cm.__enter__()
            held.append((cm, slot))
        elif op == "release" and held:
            cm, _slot = held.pop()
            cm.__exit__(None, None, None)
        assert pool.in_use_count <= max_slots
        assert pool.peak_in_use <= max_slots
    for cm, _slot in held:
        cm.__exit__(None, None, None)
    assert pool.in_use_count == 0


@given(st.integers(min_value=1, max_value=3))
@settings(max_examples=20, deadline=None)
def test_page_slot_raises_when_ceiling_violated_by_caller(max_slots: int) -> None:
    """ถ้าผู้เรียกยืมเกินเพดานโดยไม่ผ่าน pool (bug ของผู้เรียก) ต้อง raise ไม่ใช่ยอมเงียบ ๆ."""
    pool = PageBufferPool.for_pages(max_slots)
    # เก็บ context manager object ไว้ด้วย ไม่ใช่แค่ slot ที่คืนมา — มิฉะนั้น generator ของ
    # `@contextmanager` จะถูก garbage-collect ทันทีและรัน `finally` (release) ก่อนเวลา
    held = [(cm, cm.__enter__()) for cm in (pool.page_slot() for _ in range(max_slots))]
    try:
        raised = False
        blocked_cm = pool.page_slot()
        try:
            blocked_cm.__enter__()
        except RuntimeError:
            raised = True
        assert raised
    finally:
        for cm, _slot in held:
            cm.__exit__(None, None, None)


# ── Property: resume ไม่ประมวลผลหน้า page_complete ซ้ำ (R6.8) ──────────


def _make_probe_corpus(tmp_dir: Path, source_pdf: Path) -> Path:
    root = tmp_dir / "corpus"
    (root / "Bachelors_Degree").mkdir(parents=True, exist_ok=True)
    dest = root / "Bachelors_Degree" / "AIT2566_current.pdf"
    if not dest.exists():
        shutil.copy(source_pdf, dest)
    return root


def test_resume_does_not_reextract_completed_pages(tmp_path: Path) -> None:
    """หน้าที่ `page_complete` แล้วต้องไม่ถูกเรียก Text_Extractor ซ้ำเมื่อรันใหม่ (R6.8)."""
    cfg = load_config()
    source_pdf = cfg.dataset_root / "Bachelors_Degree" / "AIT2566_current.pdf"
    corpus_root = _make_probe_corpus(tmp_path, source_pdf)
    # `KatragConfig.resolve()` ทำ `project_root / relative` — เมื่อ `relative` เป็น absolute
    # path (tmp_path มักอยู่คนละไดรฟ์จาก project_root บน Windows) ผลลัพธ์จะเป็น absolute
    # path นั้นตรง ๆ (พฤติกรรมมาตรฐานของ `pathlib`) จึงส่ง absolute path ตรงได้โดยไม่ต้อง
    # ทำ relative_to ซึ่ง raise เมื่อสองพาธไม่ได้อยู่ใต้กันบนไดรฟ์เดียวกัน
    probe_cfg = replace(
        cfg,
        dataset=replace(
            cfg.dataset, root=str(corpus_root),
            expected_document_count=1, expected_page_total=346,
        ),
    )
    db_path = tmp_path / "resume.sqlite3"
    initialize(db_path)

    with ProvenanceStore(db_path) as store:
        manager = IngestionManager(probe_cfg, store)
        first = manager.run(resume=True)
        assert first.status == "success"
        assert first.pages_completed == 346

        extract_calls = {"count": 0}
        original_extract = manager._extractor.extract_page

        def counting_extract(*args: object, **kwargs: object):
            extract_calls["count"] += 1
            return original_extract(*args, **kwargs)

        manager._extractor.extract_page = counting_extract  # type: ignore[method-assign]
        second = manager.run(resume=True)

        assert second.status == "success"
        assert second.pages_completed == 346
        assert extract_calls["count"] == 0, "resume ต้องไม่เรียก Text_Extractor ของหน้าที่ complete แล้วเลย"


def test_resume_after_simulated_interruption_completes_remaining_pages(tmp_path: Path) -> None:
    """จำลองการขัดจังหวะกลางเอกสาร (สร้าง manager ใหม่ระหว่างทาง) — resume ต้องไปต่อจากจุดที่ค้างและได้ผลรวมถูกต้อง."""
    cfg = load_config()
    source_pdf = cfg.dataset_root / "Bachelors_Degree" / "AIT2566_current.pdf"
    corpus_root = _make_probe_corpus(tmp_path, source_pdf)
    probe_cfg = replace(
        cfg,
        dataset=replace(
            cfg.dataset, root=str(corpus_root),
            expected_document_count=1, expected_page_total=346,
        ),
    )
    db_path = tmp_path / "interrupt.sqlite3"
    initialize(db_path)

    with ProvenanceStore(db_path) as store:
        manager_a = IngestionManager(probe_cfg, store)
        # ลงทะเบียนเอกสารก่อน แล้วประมวลผลแค่ 10 หน้าแรกเพื่อจำลอง "ถูกขัดจังหวะ"
        from katrag.ingest.registry import DocumentRegistry

        registry = DocumentRegistry(probe_cfg, store)
        reg_result = registry.scan(corpus_root)
        registry.register(reg_result)
        doc_row = store.documents()[0]
        pages_done = 0
        for _result in manager_a.process_document(
            document_id=doc_row["document_id"],
            relative_path=doc_row["relative_path"],
            page_count=int(doc_row["page_count"]),
            corpus_root=corpus_root,
            resume=True,
        ):
            pages_done += 1
            if pages_done == 10:
                break  # จำลองขัดจังหวะ — generator ยังไม่ถูก drain จนหมด

        assert store.completed_page_count() == 10

        # "restart" ด้วย manager ใหม่ (จำลอง process ใหม่) แล้วรันต่อ
        manager_b = IngestionManager(probe_cfg, store)
        outcome = manager_b.run(resume=True)
        assert outcome.status == "success"
        assert outcome.pages_completed == 346
        assert store.completed_page_count() == 346
