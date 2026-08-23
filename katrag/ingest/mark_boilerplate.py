"""ตรวจจับและ mark chunk ที่เป็น boilerplate (หัว/ท้ายกระดาษที่ซ้ำทุกหน้า).

ปัญหาที่แก้: เล่มหลักสูตรมีหัวกระดาษซ้ำทุกหน้า เช่น
    "มคอ. 2 วท.บ (วิทยาการข้อมูลและการวิเคราะห์เชิงธุรกิจ) สาขาวิชา... คณะเทคโนโลยีสารสนเทศ"
chunk เหล่านี้มีคำสำคัญของหลักสูตรครบ ทำให้ lexical retrieval ดึงขึ้นมาติดอันดับ
แต่ไม่มีสาระที่ตอบคำถามได้เลย → ทำให้ citation precision ต่ำ

วิธีตรวจจับ (deterministic ไม่ต้องใช้โมเดล):
    1. ทำ "ลายเซ็น" ของข้อความ = NFC + แทนเลขทุกตัวด้วย # + ย่อช่องว่าง + ตัด 200 อักขระ
       (แทนเลขเพราะเลขหน้าต่างกันแต่เนื้อหาเดียวกัน)
    2. ถ้าลายเซ็นเดียวกันปรากฏใน >= MIN_REPEAT หน้าของ *เอกสารเดียวกัน*
       และข้อความสั้นกว่า MAX_LEN → ถือเป็น boilerplate
    3. ข้อความยาวไม่ mark แม้จะซ้ำ (อาจเป็นตารางที่ซ้ำจริงและมีสาระ)

ผลเก็บในคอลัมน์ chunk.is_boilerplate (0/1) — retrieval จะข้าม chunk ที่ =1

Usage:
    python -m katrag.ingest.mark_boilerplate
    python -m katrag.ingest.mark_boilerplate --dry-run
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

# ลายเซ็นเดียวกันต้องซ้ำอย่างน้อยกี่หน้าในเล่มเดียวกัน
MIN_REPEAT = 5
# ข้อความยาวกว่านี้ไม่ถือเป็น boilerplate แม้จะซ้ำ (น่าจะเป็นเนื้อหาจริง)
MAX_LEN = 400
# ความยาวลายเซ็นที่ใช้เทียบ
SIG_LEN = 200

_DIGITS = re.compile(r"\d+")


def signature(text: str) -> str:
    """ลายเซ็นข้อความสำหรับจับ chunk ที่ซ้ำข้ามหน้า."""
    t = unicodedata.normalize("NFC", text or "")
    t = _DIGITS.sub("#", t)
    t = " ".join(t.split())
    return t[:SIG_LEN]


def _ensure_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunk)")}
    if "is_boilerplate" not in cols:
        conn.execute("ALTER TABLE chunk ADD COLUMN is_boilerplate INTEGER DEFAULT 0")
        conn.commit()


def mark(
    db_path: Path | str,
    *,
    min_repeat: int = MIN_REPEAT,
    max_len: int = MAX_LEN,
    dry_run: bool = False,
) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_column(conn)

    rows = conn.execute("SELECT chunk_id, document_id, text FROM chunk").fetchall()
    total = len(rows)

    # นับลายเซ็นต่อเอกสาร
    per_doc: dict[str, Counter[str]] = {}
    sigs: dict[int, tuple[str, str, int]] = {}
    for r in rows:
        text = r["text"] or ""
        s = signature(text)
        per_doc.setdefault(r["document_id"], Counter())[s] += 1
        sigs[r["chunk_id"]] = (r["document_id"], s, len(text))

    boiler_ids: list[int] = []
    for cid, (did, s, length) in sigs.items():
        if length > max_len:
            continue
        if per_doc[did][s] >= min_repeat:
            boiler_ids.append(cid)

    if not dry_run:
        conn.execute("UPDATE chunk SET is_boilerplate = 0")
        conn.executemany(
            "UPDATE chunk SET is_boilerplate = 1 WHERE chunk_id = ?",
            [(cid,) for cid in boiler_ids],
        )
        conn.commit()

    marked = conn.execute(
        "SELECT COUNT(*) FROM chunk WHERE is_boilerplate = 1"
    ).fetchone()[0]
    conn.close()

    return {
        "total_chunks": total,
        "detected": len(boiler_ids),
        "marked_in_db": marked,
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(description="Mark boilerplate chunks (repeated headers/footers)")
    ap.add_argument("--db", default=str(root / "artifacts" / "katrag.sqlite3"))
    ap.add_argument("--min-repeat", type=int, default=MIN_REPEAT)
    ap.add_argument("--max-len", type=int, default=MAX_LEN)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    stats = mark(
        Path(args.db),
        min_repeat=args.min_repeat,
        max_len=args.max_len,
        dry_run=args.dry_run,
    )
    print(f"chunk ทั้งหมด          : {stats['total_chunks']:,}")
    print(f"ตรวจพบ boilerplate    : {stats['detected']:,} "
          f"({stats['detected']/stats['total_chunks']*100:.1f}%)")
    print(f"mark ไว้ใน DB         : {stats['marked_in_db']:,}"
          + ("  (dry-run: ไม่ได้เขียน)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
