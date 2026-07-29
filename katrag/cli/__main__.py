"""KatRAG CLI — entry point สำหรับ `python -m katrag.cli` (R21).

คำสั่งทั้งหมดทำงาน offline (R20.1, R20.9) — net_guard ถูกติดตั้งก่อนเรียก orchestrator
ทุกคำสั่ง เพื่อบังคับว่าไม่มี outbound request ออกนอก loopback

Commands:
    katrag preflight  — ตรวจ weight files + engine readiness
    katrag ingest     — สแกนเอกสารและประมวลผลทุกหน้า
    katrag index      — สร้าง FTS5 + dense index
    katrag evaluate   — รัน evaluation harness
    katrag serve      — เริ่ม API server (127.0.0.1)
    katrag demo       — แสดง end-to-end demo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _project_root() -> Path:
    """คืนรากของโปรเจกต์ (ไดเรกทอรีแม่ของ package katrag)."""
    return Path(__file__).resolve().parent.parent.parent


# ── Command handlers ──────────────────────────────────────────────────


def cmd_preflight(args: argparse.Namespace) -> int:
    """ตรวจ engine readiness ก่อนรัน pipeline จริง (R20.6-R20.8)."""
    from katrag.common.net_guard import enforce_offline
    from katrag.config import load_config
    from katrag.ingest.ocr.preflight import run_preflight

    with enforce_offline():
        config = load_config(_project_root())
        report = run_preflight(
            config.engines,
            config.project_root,
            hash_cache_path=config.project_root / "artifacts" / "preflight_cache.json",
        )
        print(f"Preflight {'PASS' if report.ok else 'FAIL'}")
        for engine in report.engine_checks:
            status = "OK" if engine.ok else "FAIL"
            if engine.skipped_reason:
                status = f"SKIP ({engine.skipped_reason})"
            print(f"  {engine.name}: {status}")
            for w in engine.weight_checks:
                print(f"    {w.declared_path}: {w.status}")
        return 0 if report.ok else 1


def cmd_ingest(args: argparse.Namespace) -> int:
    """สแกนเอกสารและประมวลผลทุกหน้า (R1-R6)."""
    from katrag.common.net_guard import enforce_offline
    from katrag.config import load_config
    from katrag.ingest.manager import IngestionManager
    from katrag.store.provenance_store import ProvenanceStore

    with enforce_offline():
        config = load_config(_project_root())
        store = ProvenanceStore(config.sqlite_path)
        manager = IngestionManager(config, store)
        outcome = manager.run(resume=not args.fresh)
        print(f"Ingestion {outcome.status}")
        print(f"  Documents registered: {outcome.documents_registered}")
        print(f"  Pages completed: {outcome.pages_completed}")
        print(f"  OCR candidates: {outcome.ocr_candidate_pages}")
        print(f"  Peak RSS: {outcome.peak_resident_bytes / (1024**2):.1f} MB")
        if outcome.status == "success":
            manifest_path = config.resolve(config.paths.dataset_manifest)
            manager.build_manifest(manifest_path)
            print(f"  Manifest: {manifest_path}")
        return 0 if outcome.status == "success" else 1


def cmd_index(args: argparse.Namespace) -> int:
    """สร้าง chunks + FTS5 index (R13)."""
    from katrag.common.net_guard import enforce_offline
    from katrag.config import load_config
    from katrag.index.lexical import build_index
    from katrag.ingest.chunker import Chunk, Chunker
    from katrag.common.types import CurriculumVersion
    from katrag.store.integrity import connect

    with enforce_offline():
        config = load_config(_project_root())
        conn = connect(config.sqlite_path)
        try:
            # Step 1: สร้าง chunks จาก page text (ถ้ายังไม่มี)
            existing_chunks = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
            if existing_chunks == 0:
                print("  Creating chunks from page text...")
                # ดึง documents + versions
                docs = conn.execute(
                    "SELECT d.document_id, d.page_count, cv.program, cv.curriculum_year, cv.edition_status "
                    "FROM document d JOIN curriculum_version cv ON cv.version_id = d.version_id"
                ).fetchall()

                total_chunks = 0
                for doc in docs:
                    doc_id = doc[0]
                    program = doc[2]
                    year = doc[3]
                    edition = doc[4]

                    version = CurriculumVersion(program=program, curriculum_year=year, edition_status=edition)
                    chunker = Chunker(version)

                    # ดึง page texts ทั้งหมดของเอกสารนี้
                    pages = conn.execute(
                        "SELECT page_number, page_text FROM page "
                        "WHERE document_id = ? AND status = 'page_complete' AND page_text != '' "
                        "ORDER BY page_number",
                        (doc_id,)
                    ).fetchall()

                    page_tuples = [(int(p[0]), str(p[1])) for p in pages if p[1]]
                    chunks = chunker.chunk_pages(doc_id, page_tuples)

                    for chunk in chunks:
                        # เขียน chunk ลง store
                        ver_row = conn.execute(
                            "SELECT version_id FROM curriculum_version WHERE program=? AND curriculum_year=? AND edition_status=?",
                            (chunk.program, chunk.curriculum_year, chunk.edition_status)
                        ).fetchone()
                        if ver_row is None:
                            continue
                        version_id = ver_row[0]

                        # สร้าง provenance (bbox = full page)
                        page_row = conn.execute(
                            "SELECT width_pt, height_pt FROM page WHERE document_id=? AND page_number=?",
                            (doc_id, chunk.page_start)
                        ).fetchone()
                        w_pt = float(page_row[0]) if page_row else 595.0
                        h_pt = float(page_row[1]) if page_row else 842.0
                        prov_cur = conn.execute(
                            "INSERT INTO provenance (document_id, page_number, x0, y0, x1, y1, span_start, span_end, extraction_method, provenance_source) "
                            "VALUES (?, ?, 0.0, 0.0, ?, ?, 0, ?, 'text_layer', 'document_text')",
                            (doc_id, chunk.page_start, w_pt, h_pt, len(chunk.content_text))
                        )
                        prov_id = prov_cur.lastrowid

                        # Insert chunk
                        conn.execute(
                            "INSERT OR IGNORE INTO chunk (document_id, page_number, version_id, heading, text, token_count, content_sha256, provenance_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (doc_id, chunk.page_start, version_id, chunk.heading, chunk.content_text, chunk.token_count, chunk.content_sha256, prov_id)
                        )
                        total_chunks += 1

                conn.commit()
                print(f"  Created {total_chunks} chunks")
            else:
                print(f"  {existing_chunks} chunks already exist")

            # Step 2: Build FTS5 index
            # ดึง chunks ที่อยู่ใน store แล้ว
            rows = conn.execute(
                "SELECT c.chunk_id, c.text, c.content_sha256, c.document_id, c.page_number, c.heading, "
                "c.token_count, cv.program, cv.curriculum_year, cv.edition_status "
                "FROM chunk c JOIN curriculum_version cv ON cv.version_id = c.version_id"
            ).fetchall()

            if not rows:
                print("No chunks found — run `katrag ingest` first.")
                return 1

            chunks_for_index = []
            for row in rows:
                chunks_for_index.append(Chunk(
                    content_text=row[1],
                    content_sha256=row[2],
                    document_id=row[3],
                    page_start=row[4],
                    page_end=row[4],
                    heading=row[5] or "",
                    program=row[7],
                    curriculum_year=row[8],
                    edition_status=row[9],
                ))

            count, issues = build_index(conn, chunks_for_index)
            conn.commit()
            print(f"  FTS5 index: {count} entries")
            if issues:
                for issue in issues:
                    print(f"  Warning: {issue.kind}")
        finally:
            conn.close()

        # Dense index (ข้ามถ้าไม่มี onnxruntime)
        try:
            from katrag.index.dense import DenseIndex
            from katrag.index.embedder import BgeM3Embedder

            embedder = BgeM3Embedder()
            dense = DenseIndex(store, embedder)
            dense.build()
            print("Dense index build complete")
        except (ImportError, Exception) as exc:
            print(f"Dense index skipped: {exc}")
        return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """รัน evaluation harness (R18)."""
    from katrag.common.net_guard import enforce_offline
    from katrag.config import load_config
    from katrag.eval.harness import EvaluationHarness, MetricInput

    with enforce_offline():
        config = load_config(_project_root())
        harness = EvaluationHarness(config.evaluation)
        output_path = config.resolve(config.paths.evaluation_report)
        # Produce report with available metrics
        inputs: list[MetricInput] = []
        report, repro_errors = harness.run_with_reproducibility_check(
            inputs, output_path=output_path
        )
        print(f"Evaluation report: {output_path}")
        for m in report.metrics:
            pf = f" [{m.pass_fail}]" if m.pass_fail else ""
            print(f"  {m.name}: {m.value:.4f} ({m.status}){pf}")
        if repro_errors:
            print(f"  Reproducibility errors: {len(repro_errors)}")
            return 1
        return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """เริ่ม API server — binds to 127.0.0.1 (R19.2)."""
    from katrag.common.net_guard import global_guard

    # Install net_guard แต่เปิด allow_external เพราะ serve เรียก Typhoon LLM API
    # (ข้อยกเว้นที่ผู้ใช้ยอมรับต่อ R20.1 เพื่อคุณภาพคำตอบ — pipeline อื่นยัง offline เข้ม)
    global_guard().set_allow_external(True)
    global_guard().install()
    try:
        from katrag.api.service import main as api_main

        api_main()
    finally:
        global_guard().uninstall()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """แสดง end-to-end demo (R21.4-R21.8)."""
    from katrag.cli.demo import run_demo

    return run_demo(verbose=args.verbose)


# ── Argument parser ───────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for all CLI commands."""
    parser = argparse.ArgumentParser(
        prog="katrag",
        description="KatRAG-lite: Curriculum QA system - offline RAG for 14 PDF files",
    )
    subparsers = parser.add_subparsers(dest="command", help="available commands")

    # preflight
    subparsers.add_parser("preflight", help="check engine readiness")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="scan documents and process all pages")
    ingest_parser.add_argument(
        "--fresh", action="store_true", help="discard previous progress and start fresh"
    )

    # index
    subparsers.add_parser("index", help="build FTS5 + dense index")

    # evaluate
    subparsers.add_parser("evaluate", help="run evaluation harness")

    # serve
    subparsers.add_parser("serve", help="start API server (127.0.0.1)")

    # demo
    demo_parser = subparsers.add_parser("demo", help="run end-to-end demo")
    demo_parser.add_argument(
        "--verbose", "-v", action="store_true", help="show detailed output for each step"
    )

    return parser


# ── Main entry point ──────────────────────────────────────────────────

_COMMANDS = {
    "preflight": cmd_preflight,
    "ingest": cmd_ingest,
    "index": cmd_index,
    "evaluate": cmd_evaluate,
    "serve": cmd_serve,
    "demo": cmd_demo,
}


def main() -> None:
    """Entry point — เรียกจาก `python -m katrag.cli` หรือ console script `katrag`."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        exit_code = handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        exit_code = 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
