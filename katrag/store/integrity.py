"""Connection factory และการตรวจ integrity ของ SQLite (R9.1, R9.8).

ข้อบังคับสองข้อที่ต้องทำทุกครั้งที่เปิด connection
1. `PRAGMA foreign_keys = ON` — SQLite ปิดไว้เป็นค่าตั้งต้น ถ้าไม่เปิด
   foreign key ทั้ง schema จะไม่ถูกบังคับเลย (R9.1)
2. `PRAGMA journal_mode = WAL` — ให้การอ่านไม่บล็อกการเขียน

ความล้มเหลวของการเปิด/เขียนไฟล์ หรือ integrity check ที่ไม่ผ่าน ต้องแยกชนิดได้
และต้องไม่ commit ข้อมูลบางส่วน (R9.8)
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

from katrag.errors import StoreAccessError

SCHEMA_FILE: Final = Path(__file__).with_name("schema.sql")

#: ตารางฐานทั้งหมดที่ schema ต้องมี (19 ตาราง) — ใช้ตรวจความครบถ้วน (R21.2)
BASE_TABLES: Final[tuple[str, ...]] = (
    "curriculum_version",
    "document",
    "page",
    "page_metrics",
    "provenance",
    "region",
    "ocr_stage_result",
    "table_cell",
    "course",
    "course_field_provenance",
    "plan_slot",
    "rule",
    "chunk",
    "chunk_embedding",
    "review_issue",
    "error_record",
    "document_relation",
    "query_trace",
    "gold_set",
)

#: virtual table ที่ต้องมี
VIRTUAL_TABLES: Final[tuple[str, ...]] = ("chunk_fts",)


def connect(db_path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """เปิด connection ที่บังคับ foreign key และตั้ง WAL.

    Raises:
        StoreAccessError: เมื่อเปิดไฟล์ไม่ได้ หรือตั้ง pragma ไม่สำเร็จ
    """
    path = Path(db_path)
    try:
        if read_only:
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, isolation_level=None)
    except sqlite3.Error as exc:
        raise StoreAccessError(
            "เปิดไฟล์ฐานข้อมูลไม่สำเร็จ", path=str(path), reason=str(exc)
        ) from exc

    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        if not foreign_keys_enabled(conn):
            raise StoreAccessError("บังคับ foreign key ไม่สำเร็จ", path=str(path))
    except sqlite3.Error as exc:
        conn.close()
        raise StoreAccessError(
            "ตั้งค่า connection ไม่สำเร็จ", path=str(path), reason=str(exc)
        ) from exc
    except StoreAccessError:
        conn.close()
        raise
    return conn


def foreign_keys_enabled(conn: sqlite3.Connection) -> bool:
    """ยืนยันว่า `PRAGMA foreign_keys` เปิดอยู่จริงบน connection นี้."""
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    return bool(row[0]) if row is not None else False


def apply_schema(conn: sqlite3.Connection, *, schema_file: Path | None = None) -> None:
    """สร้าง schema ทั้งหมดจากไฟล์ DDL (idempotent ด้วย IF NOT EXISTS).

    Raises:
        StoreAccessError: เมื่ออ่านไฟล์ DDL ไม่ได้ หรือ DDL ทำงานไม่สำเร็จ
    """
    path = schema_file or SCHEMA_FILE
    try:
        ddl = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StoreAccessError("อ่านไฟล์ schema ไม่สำเร็จ", path=str(path), reason=str(exc)) from exc
    try:
        conn.executescript(ddl)
    except sqlite3.Error as exc:
        raise StoreAccessError("สร้าง schema ไม่สำเร็จ", reason=str(exc)) from exc


def missing_objects(conn: sqlite3.Connection) -> tuple[str, ...]:
    """คืนชื่อตารางที่ schema ต้องมีแต่ยังไม่มีในฐานข้อมูล."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    present = {row["name"] for row in rows}
    expected = set(BASE_TABLES) | set(VIRTUAL_TABLES)
    return tuple(sorted(expected - present))


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """จำนวนแถวของทุกตารางฐาน — ใช้ในรายงานและการตรวจสภาพ."""
    counts: dict[str, int] = {}
    for table in BASE_TABLES:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        counts[table] = int(row["n"]) if row is not None else 0
    return counts


def integrity_check(conn: sqlite3.Connection) -> None:
    """รัน integrity check และ foreign key check.

    Raises:
        StoreAccessError: เมื่อ integrity ไม่ผ่าน หรือพบ foreign key ที่ค้าง
    """
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        raise StoreAccessError("รัน integrity_check ไม่สำเร็จ", reason=str(exc)) from exc
    results = [row[0] for row in rows]
    if results != ["ok"]:
        raise StoreAccessError("integrity check ไม่ผ่าน", findings=results[:10])

    try:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.Error as exc:
        raise StoreAccessError("รัน foreign_key_check ไม่สำเร็จ", reason=str(exc)) from exc
    if violations:
        raise StoreAccessError(
            "พบ foreign key ที่ไม่สมบูรณ์",
            violation_count=len(violations),
            first_table=violations[0][0],
        )

    missing = missing_objects(conn)
    if missing:
        raise StoreAccessError("schema ขาดตารางที่จำเป็น", missing=list(missing))


def initialize(db_path: str | Path) -> None:
    """สร้างไฟล์ฐานข้อมูลและ schema แล้วตรวจ integrity ทันที."""
    with closing(connect(db_path)) as conn:
        apply_schema(conn)
        integrity_check(conn)
