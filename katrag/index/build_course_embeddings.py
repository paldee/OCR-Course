"""สร้าง embedding ระดับรายวิชา (ชื่อไทย+อังกฤษ) เพื่อค้นเชิงความหมาย.

เก็บใน table course_embedding(course_id, dim, vector)
Usage: python -m katrag.index.build_course_embeddings
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from katrag.index import bge_encoder


def build(db_path: Path | str) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS course_embedding (
            course_id INTEGER PRIMARY KEY REFERENCES course(course_id) ON DELETE CASCADE,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL
        )
    """)
    conn.commit()

    rows = conn.execute("SELECT course_id, name_th, name_en FROM course").fetchall()
    if not rows:
        conn.close()
        return 0

    # ข้อความ embed: ชื่อไทย + ชื่ออังกฤษ (ให้ bge-m3 เข้าใจทั้งสองภาษา)
    texts = []
    ids = []
    for r in rows:
        th = (r["name_th"] or "").strip()
        en = (r["name_en"] or "").strip()
        texts.append(f"{th} {en}".strip())
        ids.append(r["course_id"])

    print(f"Encoding {len(texts)} courses with bge-m3...")
    vectors = bge_encoder.encode(texts, batch_size=64)
    dim = vectors.shape[1]

    conn.execute("DELETE FROM course_embedding")
    for cid, vec in zip(ids, vectors):
        conn.execute(
            "INSERT OR REPLACE INTO course_embedding (course_id, dim, vector) VALUES (?, ?, ?)",
            (cid, dim, vec.astype(np.float32).tobytes()),
        )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM course_embedding").fetchone()[0]
    conn.close()
    return n


if __name__ == "__main__":
    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    n = build(db)
    print(f"Done! course_embedding rows: {n}")
