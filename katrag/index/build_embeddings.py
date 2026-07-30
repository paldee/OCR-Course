"""Build chunk embeddings ด้วย Gemini API แล้วเก็บลง chunk_embedding table.

Usage: python -m katrag.index.build_embeddings [--limit N]
"""

from __future__ import annotations

import sqlite3
import struct
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from katrag.index.gemini_embedder import GeminiEmbedder


def build(db_path: Path, *, limit: int | None = None) -> dict[str, int]:
    """Build embeddings for all chunks that don't have one yet."""
    load_dotenv(db_path.parent.parent / ".env")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # ดึง chunks ที่ยังไม่มี embedding
    limit_sql = f"LIMIT {limit}" if limit else ""
    rows = conn.execute(f"""
        SELECT c.chunk_id, c.text
        FROM chunk c
        LEFT JOIN chunk_embedding ce ON ce.chunk_id = c.chunk_id
        WHERE ce.chunk_id IS NULL AND length(c.text) > 10
        ORDER BY c.chunk_id
        {limit_sql}
    """).fetchall()

    total = len(rows)
    if total == 0:
        print("All chunks already have embeddings!")
        conn.close()
        return {"embedded": 0, "skipped": 0, "total_chunks": 0}

    print(f"Chunks to embed: {total}")

    embedder = GeminiEmbedder()
    dim = embedder.dim
    print(f"Embedding dim: {dim}")

    embedded = 0
    skipped = 0
    batch_size = 20  # Gemini batch limit
    t0 = time.time()

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        texts = [row["text"][:2048] for row in batch]
        chunk_ids = [row["chunk_id"] for row in batch]

        try:
            vectors = embedder.encode(texts)
        except Exception as e:
            print(f"  ERROR at batch {i}: {e}")
            skipped += len(batch)
            continue

        for j, (chunk_id, vec) in enumerate(zip(chunk_ids, vectors)):
            # เก็บเป็น blob (float32 little-endian)
            vec_blob = vec.astype(np.float32).tobytes()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO chunk_embedding
                    (chunk_id, model_name, dim, vector, token_vectors, token_count, built_at)
                    VALUES (?, 'gemini-embedding-001', ?, ?, NULL, NULL, datetime('now'))
                """, (chunk_id, dim, vec_blob))
                embedded += 1
            except Exception as e:
                print(f"  DB error chunk {chunk_id}: {e}")
                skipped += 1

        if (i + batch_size) % 100 == 0 or i + batch_size >= total:
            conn.commit()
            elapsed = time.time() - t0
            rate = (i + len(batch)) / elapsed if elapsed > 0 else 0
            print(f"  Progress: {i + len(batch)}/{total} ({rate:.0f} chunks/s)")

    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s! Embedded: {embedded}, Skipped: {skipped}")
    return {"embedded": embedded, "skipped": skipped, "total_chunks": total}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    build(db, limit=args.limit)
