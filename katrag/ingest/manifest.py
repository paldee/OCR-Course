"""Dataset manifest writer (R1.9, R21.3).

ข้อบังคับ: ผลิตซ้ำจากชุดไฟล์เดิมต้องได้ **เนื้อหาเหมือนเดิมทุกไบต์** ดังนั้น
- ทุกค่าอ่านจาก Provenance_Store โดยตรง ไม่คำนวณใหม่จากไฟล์
- entry เรียงตาม relative path
- คีย์ใน JSON เรียงชื่อ
- **ไม่มี timestamp ในไฟล์** (timestamp ของการ ingest อยู่ในฐานข้อมูล ไม่ใช่ใน manifest)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from katrag.store.provenance_store import ProvenanceStore

_DOCUMENT_SQL = """
SELECT d.document_id, d.relative_path, d.sha256, d.size_bytes, d.page_count,
       d.degree_level, d.canonical_document_id, d.metadata_source_json,
       v.program, v.curriculum_year, v.edition_status
  FROM document d
  JOIN curriculum_version v ON v.version_id = d.version_id
 ORDER BY d.relative_path
"""

_ISSUE_SQL = """
SELECT issue_id, issue_type, document_id, page_number, subject_ref, detail_json
  FROM review_issue
 WHERE document_id IS NOT NULL
 ORDER BY issue_id
"""

_RELATION_SQL = """
SELECT from_document_id, to_document_id, relation_type, note
  FROM document_relation
 ORDER BY from_document_id, to_document_id, relation_type
"""


def build_manifest(store: ProvenanceStore) -> dict[str, Any]:
    """ประกอบ manifest จากข้อมูลใน store (ไม่แตะไฟล์ PDF)."""
    issues_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in store.query(_ISSUE_SQL):
        entry = {
            "issue_id": int(row["issue_id"]),
            "issue_type": row["issue_type"],
            "page_number": row["page_number"],
            "subject_ref": row["subject_ref"],
            "detail": store.json_loads(row["detail_json"]),
        }
        issues_by_document.setdefault(row["document_id"], []).append(entry)

    relations_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in store.query(_RELATION_SQL):
        relations_by_document.setdefault(row["from_document_id"], []).append(
            {
                "to_document_id": row["to_document_id"],
                "relation_type": row["relation_type"],
                "note": row["note"],
            }
        )

    documents: list[dict[str, Any]] = []
    for row in store.query(_DOCUMENT_SQL):
        document_id = row["document_id"]
        documents.append(
            {
                "document_id": document_id,
                "relative_path": row["relative_path"],
                "sha256": row["sha256"],
                "size_bytes": int(row["size_bytes"]),
                "page_count": int(row["page_count"]),
                "degree_level": row["degree_level"],
                "curriculum_version": {
                    "program": row["program"],
                    "curriculum_year": int(row["curriculum_year"]),
                    "edition_status": row["edition_status"],
                },
                "canonical_document_id": row["canonical_document_id"],
                "is_canonical": row["canonical_document_id"] == document_id,
                "metadata_source": store.json_loads(row["metadata_source_json"]),
                "relations": relations_by_document.get(document_id, []),
                "review_issues": issues_by_document.get(document_id, []),
            }
        )

    return {
        "dataset_root": "Information_Technology_Course",
        "document_count": len(documents),
        "page_total": sum(doc["page_count"] for doc in documents),
        "canonical_document_count": sum(1 for doc in documents if doc["is_canonical"]),
        "documents": documents,
    }


def write_manifest(store: ProvenanceStore, out_path: str | Path) -> Path:
    """เขียน manifest แบบ deterministic แล้วคืน path ที่เขียน."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        build_manifest(store), ensure_ascii=False, sort_keys=True, indent=2
    )
    path.write_text(payload + "\n", encoding="utf-8")
    return path
