"""One-command demo — แสดง end-to-end functionality ของ KatRAG-lite (R21.4-R21.8).

ลำดับขั้นตอน:
  1. Preflight — ตรวจ engine readiness (skip ถ้า weight ไม่พร้อม)
  2. Ingest — สแกนและประมวลผลเอกสาร (ข้ามถ้า ingest เสร็จแล้ว)
  3. Sample questions — ส่งคำถามตัวอย่างเข้า pipeline
  4. Show results — แสดงคำตอบพร้อม citations
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


_SAMPLE_QUESTIONS = [
    "หลักสูตร IT มีกี่หน่วยกิตรวม?",
    "วิชาบังคับก่อนของ Data Structures คืออะไร?",
    "เกณฑ์การสำเร็จการศึกษาระดับปริญญาตรี IT มีอะไรบ้าง?",
]


def _project_root() -> Path:
    """คืนรากของโปรเจกต์."""
    return Path(__file__).resolve().parent.parent.parent


def _banner(text: str) -> None:
    """แสดง banner แบ่งขั้นตอน."""
    sep = "─" * 60
    print(f"\n{sep}\n  {text}\n{sep}")


def run_demo(*, verbose: bool = False) -> int:
    """Run the full demo pipeline.

    Returns:
        exit code (0 = success, 1 = partial failure)
    """
    from katrag.common.net_guard import enforce_offline, assert_no_external_egress

    with enforce_offline() as guard:
        start = time.perf_counter()

        # ── Step 1: Preflight ─────────────────────────────────────────
        _banner("Step 1/4: Preflight check")
        preflight_ok = _run_preflight(verbose)
        if preflight_ok:
            print("  ✓ Preflight passed")
        else:
            print("  ⚠ Preflight had warnings (continuing anyway)")

        # ── Step 2: Ingest (if needed) ────────────────────────────────
        _banner("Step 2/4: Ingestion")
        ingest_ok = _run_ingest(verbose)
        if ingest_ok:
            print("  ✓ Ingestion complete")
        else:
            print("  ⚠ Ingestion incomplete (continuing with available data)")

        # ── Step 3: Sample questions ──────────────────────────────────
        _banner("Step 3/4: Sample questions")
        results = _run_questions(verbose)

        # ── Step 4: Show results ──────────────────────────────────────
        _banner("Step 4/4: Results")
        for i, (q, answer) in enumerate(results, 1):
            print(f"\n  Q{i}: {q}")
            print(f"  A{i}: {answer}")

        # ── Summary ───────────────────────────────────────────────────
        elapsed = time.perf_counter() - start
        print(f"\n{'─' * 60}")
        print(f"  Demo complete in {elapsed:.2f}s")
        print(f"  Network guard: {guard.stats.blocked_attempts} blocked, "
              f"{guard.stats.allowed_connections} allowed (loopback)")

        # Final assertion — no external egress
        try:
            assert_no_external_egress()
            print("  ✓ Offline invariant: no external network calls")
        except Exception as exc:
            print(f"  ✗ Offline violation: {exc}", file=sys.stderr)
            return 1

    return 0


def _run_preflight(verbose: bool) -> bool:
    """Run preflight check; return True if OK."""
    try:
        from katrag.config import load_config
        from katrag.ingest.ocr.preflight import run_preflight

        config = load_config(_project_root())
        report = run_preflight(
            config.engines,
            config.project_root,
            hash_cache_path=config.project_root / "artifacts" / "preflight_cache.json",
        )
        if verbose:
            for engine in report.engine_checks:
                status = "OK" if engine.ok else "FAIL"
                if engine.skipped_reason:
                    status = f"SKIP ({engine.skipped_reason})"
                print(f"    {engine.name}: {status}")
        return report.ok
    except Exception as exc:
        if verbose:
            print(f"    Preflight error: {exc}")
        return False


def _run_ingest(verbose: bool) -> bool:
    """Run ingestion if store is empty; return True if data available."""
    try:
        from katrag.config import load_config
        from katrag.store.provenance_store import ProvenanceStore

        config = load_config(_project_root())
        store = ProvenanceStore(config.sqlite_path)

        # Check if already ingested
        if store.completed_page_count() > 0:
            count = store.completed_page_count()
            print(f"    Already ingested ({count} pages). Skipping.")
            return True

        # Run ingestion
        from katrag.ingest.manager import IngestionManager

        manager = IngestionManager(config, store)
        outcome = manager.run(resume=True)
        if verbose:
            print(f"    Status: {outcome.status}")
            print(f"    Pages: {outcome.pages_completed}")
        return outcome.status == "success"
    except Exception as exc:
        if verbose:
            print(f"    Ingest error: {exc}")
        return False


def _run_questions(verbose: bool) -> list[tuple[str, str]]:
    """Send sample questions through the pipeline; return (question, answer) pairs."""
    results: list[tuple[str, str]] = []
    for q in _SAMPLE_QUESTIONS:
        answer = _ask_question(q, verbose)
        results.append((q, answer))
    return results


def _ask_question(question: str, verbose: bool) -> str:
    """Ask a single question through the query pipeline."""
    try:
        from katrag.config import load_config
        from katrag.store.provenance_store import ProvenanceStore
        from katrag.query.hybrid_retriever import HybridRetriever

        config = load_config(_project_root())
        store = ProvenanceStore(config.sqlite_path)

        # Try retrieval
        retriever = HybridRetriever(config.retrieval, store)
        hits = retriever.search(question)

        if hits:
            top_text = hits[0].text[:200] if hasattr(hits[0], "text") else str(hits[0])
            answer = f"[retrieved {len(hits)} chunks] top: {top_text}..."
        else:
            answer = "[no results — pipeline not fully connected]"

        return answer
    except Exception as exc:
        if verbose:
            print(f"    Query error for '{question[:30]}...': {exc}")
        return f"[error: {type(exc).__name__}]"
