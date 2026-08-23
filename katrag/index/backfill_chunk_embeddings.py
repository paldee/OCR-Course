"""เติม chunk_embedding ที่ขาด ให้เป็น bge-m3 (dim 1024) ทั้งฐาน.

ปัญหาที่แก้: ตาราง chunk_embedding มีเวกเตอร์สองมิติปนกัน (bge-m3 1024 กับ
Gemini 3072 จากการ build ต่างรอบ) ทำให้ dense index โหลดได้เพียงมิติเดียว
และ chunk อีกส่วนค้นไม่เจอเลย

สคริปต์นี้:
  1. หา chunk ที่ยังไม่มีเวกเตอร์ dim=1024
  2. encode ด้วย bge-m3 แล้ว upsert
  3. ลบเวกเตอร์ dim ที่ไม่ใช่ 1024 ทิ้ง (ถ้า --purge-other)

Usage:
    python -m katrag.index.backfill_chunk_embeddings
    python -m katrag.index.backfill_chunk_embeddings --purge-other
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np

from katrag.index import bge_encoder

TARGET_DIM = 1024
MODEL_NAME = "bge-m3"


def backfill(db_path: Path | str, *, batch_size: int = 32, purge_other: bool = False) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute(
        """
        SELECT c.chunk_id, c.text
        FROM chunk c
        WHERE NOT EXISTS (
            SELECT 1 FROM chunk_embedding e
            WHERE e.chunk_id = c.chunk_id AND e.dim = ?
        )
        ORDER BY c.chunk_id
        """,
        (TARGET_DIM,),
    ).fetchall()

    missing = len(rows)
    print(f"chunk ที่ยังไม่มี embedding dim={TARGET_DIM}: {missing}")

    encoded = 0
    if rows:
        for start in range(0, missing, batch_size):
            batch = rows[start : start + batch_size]
            texts = [(r["text"] or "").strip() or " " for r in batch]
            vecs = bge_encoder.encode(texts, batch_size=batch_size)
            for r, vec in zip(batch, vecs):
                conn.execute(
                    "INSERT OR REPLACE INTO chunk_embedding "
                    "(chunk_id, model_name, dim, vector, built_at) "
                    "VALUES (?, ?, ?, ?, datetime('now'))",
                    (
                        r["chunk_id"],
                        MODEL_NAME,
                        TARGET_DIM,
                        vec.astype(np.float32).tobytes(),
                    ),
                )
            encoded += len(batch)
            conn.commit()
            done = min(start + batch_size, missing)
            print(f"  encoded {done}/{missing}", end="\r", flush=True)
        print()

    purged = 0
    if purge_other:
        cur = conn.execute("DELETE FROM chunk_embedding WHERE dim != ?", (TARGET_DIM,))
        purged = cur.rowcount or 0
        conn.commit()
        print(f"ลบเวกเตอร์มิติอื่นทิ้ง: {purged} แถว")

    stats = {
        r["dim"]: r["n"]
        for r in conn.execute(
            "SELECT dim, COUNT(*) n FROM chunk_embedding GROUP BY dim"
        ).fetchall()
    }
    total_chunk = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
    conn.close()

    print(f"chunk ทั้งหมด: {total_chunk}")
    for dim, n in sorted(stats.items()):
        print(f"  dim={dim}: {n}")
    return {"missing": missing, "encoded": encoded, "purged": purged}


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(description="Backfill chunk embeddings to bge-m3")
    ap.add_argument("--db", default=str(root / "artifacts" / "katrag.sqlite3"))
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument(
        "--purge-other",
        action="store_true",
        help="ลบเวกเตอร์มิติอื่น (เช่น Gemini 3072) ทิ้งหลังเติมครบ",
    )
    args = ap.parse_args()
    backfill(Path(args.db), batch_size=args.batch_size, purge_other=args.purge_other)


if __name__ == "__main__":
    main()
