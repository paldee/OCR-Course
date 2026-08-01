"""Semantic course search — ค้นรายวิชาด้วยความหมาย (bge-m3 embedding).

แก้ข้อจำกัดของ keyword: "คณิต" จะได้ "ความน่าจะเป็นและสถิติ" ด้วย
เพราะ embedding เข้าใจว่าเกี่ยวข้องกัน แม้ชื่อไม่มีคำว่าคณิต
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class CourseHit:
    code: str
    name_th: str
    name_en: str
    credits_raw: str
    program: str
    curriculum_year: int
    score: float
    year: int | None = None
    semester: int | None = None
    version_id: int = 0

    @property
    def plan_label(self) -> str:
        """ป้ายชั้นปี/ภาค เช่น 'ปีที่ 2 ภาคการศึกษาที่ 1'."""
        if self.year and self.semester:
            return f"ปีที่ {self.year} ภาคการศึกษาที่ {self.semester}"
        if self.year:
            return f"ปีที่ {self.year}"
        return "ไม่ระบุชั้นปี (วิชาเลือก/วิชาในหมวดเลือก)"


class CourseSemanticIndex:
    """โหลด course embeddings ลง memory + ค้น cosine similarity."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._vecs: np.ndarray | None = None
        self._meta: list[dict] = []

    # ── loading ──────────────────────────────────────────────────────

    def load(self) -> int:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT ce.course_id, ce.vector, c.code, c.name_th, c.name_en, c.credits_raw,
                   c.year, c.semester, c.version_id,
                   cv.program, cv.curriculum_year, cv.edition_status
            FROM course_embedding ce
            JOIN course c ON c.course_id = ce.course_id
            JOIN curriculum_version cv ON cv.version_id = c.version_id
        """).fetchall()
        conn.close()
        if not rows:
            return 0
        vecs = []
        self._meta = []
        for r in rows:
            v = np.frombuffer(r["vector"], dtype=np.float32)
            vecs.append(v)
            self._meta.append({
                "code": r["code"], "name_th": r["name_th"], "name_en": r["name_en"],
                "credits_raw": r["credits_raw"], "version_id": r["version_id"],
                "program": r["program"], "curriculum_year": r["curriculum_year"],
                "edition_status": r["edition_status"],
                "year": r["year"], "semester": r["semester"],
            })
        self._vecs = np.vstack(vecs)
        return len(self._meta)

    @property
    def size(self) -> int:
        return 0 if self._vecs is None else int(self._vecs.shape[0])

    # ── search ───────────────────────────────────────────────────────

    def search(
        self,
        topic: str,
        *,
        version_ids: list[int] | None = None,
        top_k: int = 30,
        min_score: float = 0.45,
        rel_margin: float | None = None,
    ) -> list[CourseHit]:
        """ค้นวิชาที่เกี่ยวกับ topic เชิงความหมาย.

        Args:
            version_ids: จำกัดเฉพาะเวอร์ชันหลักสูตรที่ระบุ (None = ทุกเวอร์ชัน)
            min_score: cosine ขั้นต่ำแบบสัมบูรณ์
            rel_margin: ตัดหางแบบสัมพัทธ์ — เก็บเฉพาะที่ score >= best - rel_margin
                        ช่วยกัน noise เมื่อคำถามชัดเจน (best สูง) โดยไม่ตัดทิ้ง
                        เมื่อคำถามกว้าง (best ต่ำ)
        """
        if self._vecs is None or self._vecs.shape[0] == 0:
            return []
        from katrag.index import bge_encoder
        qv = bge_encoder.encode_one(topic)
        scores = self._vecs @ qv  # cosine (vectors normalized แล้ว)

        allowed = set(version_ids) if version_ids else None
        order = np.argsort(scores)[::-1]

        # หา best score ภายใน scope ก่อน เพื่อคำนวณ threshold สัมพัทธ์
        best = 0.0
        for i in order:
            if allowed is not None and self._meta[i]["version_id"] not in allowed:
                continue
            best = float(scores[i])
            break
        floor = min_score
        if rel_margin is not None:
            floor = max(min_score, best - rel_margin)

        hits: list[CourseHit] = []
        for i in order:
            s = float(scores[i])
            if s < floor:
                break
            m = self._meta[i]
            if allowed is not None and m["version_id"] not in allowed:
                continue
            hits.append(CourseHit(
                code=m["code"], name_th=m["name_th"], name_en=m["name_en"],
                credits_raw=m["credits_raw"], program=m["program"],
                curriculum_year=m["curriculum_year"], score=s,
                year=m["year"], semester=m["semester"], version_id=m["version_id"],
            ))
            if len(hits) >= top_k:
                break
        return hits

    def search_grouped(
        self,
        topic: str,
        *,
        version_ids: list[int] | None = None,
        top_k: int = 25,
        min_score: float = 0.5,
        rel_margin: float | None = None,
    ) -> dict[str, list[CourseHit]]:
        """ค้นแล้วจัดกลุ่มตามหลักสูตร (สำหรับกรณีไม่ระบุ program)."""
        hits = self.search(
            topic, version_ids=version_ids, top_k=top_k,
            min_score=min_score, rel_margin=rel_margin,
        )
        grouped: dict[str, list[CourseHit]] = {}
        for h in hits:
            grouped.setdefault(f"{h.program} {h.curriculum_year}", []).append(h)
        return grouped


# ══════════════════════════════════════════════════════════════════════
# Version scoping — เลือกเวอร์ชันเดียวต่อหลักสูตร (กันผลซ้ำข้ามเวอร์ชัน)
# ══════════════════════════════════════════════════════════════════════


def current_version_ids(
    conn: sqlite3.Connection, program: str | None = None
) -> tuple[list[int], dict[int, str]]:
    """คืน version_id ของหลักสูตรที่ใช้อยู่ (current) — หลักสูตรละ 1 เวอร์ชัน.

    บาง program มีหลาย current (เช่น IT 2565/2566/2568) เลือกตัวที่มีรายวิชามากสุด
    เพื่อไม่ให้ผลค้นซ้ำกันหลายเวอร์ชัน
    """
    sql = (
        "SELECT cv.version_id, cv.program, cv.curriculum_year, "
        "       COUNT(c.course_id) AS n_course "
        "FROM curriculum_version cv "
        "LEFT JOIN course c ON c.version_id = cv.version_id "
        "WHERE cv.edition_status = 'current' "
    )
    params: list = []
    if program:
        sql += "AND cv.program = ? "
        params.append(program)
    sql += "GROUP BY cv.version_id ORDER BY n_course DESC, cv.curriculum_year DESC"

    rows = conn.execute(sql, params).fetchall()
    # BIT = ชื่อเดิมของ AIT (เลิกรับแล้ว) — ไม่นับเมื่อไม่ได้ถามถึงโดยตรง
    legacy = {"BIT"} if not program else set()
    best: dict[str, tuple[int, int]] = {}  # program → (version_id, curriculum_year)
    for r in rows:
        prog = r[1] if not isinstance(r, sqlite3.Row) else r["program"]
        vid = r[0] if not isinstance(r, sqlite3.Row) else r["version_id"]
        cyear = r[2] if not isinstance(r, sqlite3.Row) else r["curriculum_year"]
        n = r[3] if not isinstance(r, sqlite3.Row) else r["n_course"]
        if n == 0 or prog in legacy:
            continue
        if prog not in best:
            best[prog] = (vid, cyear)
    vids = [v for v, _ in best.values()]
    labels = {v: f"{p} {y}" for p, (v, y) in best.items()}
    return vids, labels
