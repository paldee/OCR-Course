"""Property test ของ retrieval pipeline (task 16.3).

คุณสมบัติที่ทดสอบ:
1. Determinism: คำถามเดียวกันบนดัชนีเดิมได้ลำดับผลลัพธ์เดิมทุกครั้ง
2. Version isolation: เมื่อตั้ง version_filter แล้ว ไม่มี chunk นอก version set
   ปรากฏในผลลัพธ์

หมายเหตุ: property ของ `apply_phrase_boost` และ `rerank_maxsim` ถูกถอดออกพร้อมกับ
โมดูลต้นทาง (อยู่หลัง feature flag ที่ปิดตลอด ไม่ได้ต่อสายเข้าเส้นทางจริง)
ดู `docs/removed_subsystems.md` หัวข้อ "katgpt-rs ports"

**Validates: Requirements 13.3, 10.5**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from katrag.common.types import CurriculumVersion
from katrag.index.dense import DenseHit
from katrag.index.lexical import LexicalHit
from katrag.query.hybrid_retriever import retrieve

PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)

# ══════════════════════════════════════════════════════════════════════
# Strategies
# ══════════════════════════════════════════════════════════════════════

# ── chunk id: content_sha256 hex strings ──────────────────────────────

_chunk_id_st = st.text(
    alphabet="0123456789abcdef",
    min_size=8,
    max_size=64,
)

# ── query text: non-empty, at most 500 chars (within valid limit) ─────

_query_st = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda s: s.strip())

# ── LexicalHit / DenseHit factory from chunk_ids ──────────────────────

_VALID_VERSION = CurriculumVersion(
    program="IT", curriculum_year=2565, edition_status="current"
)


def _make_lexical_hits(
    chunk_ids: list[str],
    version: CurriculumVersion = _VALID_VERSION,
) -> list[LexicalHit]:
    """สร้าง LexicalHit list จาก chunk_ids."""
    return [
        LexicalHit(
            chunk_id=cid,
            score=1.0 / (i + 1),
            heading=f"heading_{cid[:8]}",
            text_snippet=f"snippet_{cid[:8]}",
            program=version.program,
            curriculum_year=version.curriculum_year,
            edition_status=version.edition_status,
        )
        for i, cid in enumerate(chunk_ids)
    ]


def _make_dense_hits(
    chunk_ids: list[str],
    version: CurriculumVersion = _VALID_VERSION,
) -> list[DenseHit]:
    """สร้าง DenseHit list จาก chunk_ids."""
    return [
        DenseHit(
            chunk_id=cid,
            score=1.0 / (i + 1),
            heading=f"heading_{cid[:8]}",
            text_snippet=f"snippet_{cid[:8]}",
            program=version.program,
            curriculum_year=version.curriculum_year,
            edition_status=version.edition_status,
        )
        for i, cid in enumerate(chunk_ids)
    ]


# ══════════════════════════════════════════════════════════════════════
# Property 1: Determinism — same query + same index → same ordered results
# ══════════════════════════════════════════════════════════════════════


@given(
    query=_query_st,
    chunk_ids=st.lists(_chunk_id_st, min_size=1, max_size=20, unique=True),
)
@PROPERTY_SETTINGS
def test_retrieve_determinism(query: str, chunk_ids: list[str]) -> None:
    """คำถามเดียวกันบนดัชนีเดิมได้ลำดับผลลัพธ์เดิมทุกครั้ง.

    เรียก retrieve สองครั้งด้วย query เดียวกัน mock searcher เดียวกัน
    ต้องได้ผลลัพธ์เหมือนกันทั้งลำดับและค่า score.

    **Validates: Requirement 13.3**
    """
    lexical_hits = _make_lexical_hits(chunk_ids)
    dense_hits = _make_dense_hits(chunk_ids)

    def lexical_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[LexicalHit]:
        return lexical_hits

    def dense_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[DenseHit]:
        return dense_hits

    response_1 = retrieve(
        query,
        lexical_searcher=lexical_searcher,
        dense_searcher=dense_searcher,
    )
    response_2 = retrieve(
        query,
        lexical_searcher=lexical_searcher,
        dense_searcher=dense_searcher,
    )

    # ผลลัพธ์ต้องเหมือนกันทุกประการ (ลำดับ + คะแนน)
    assert len(response_1.results) == len(response_2.results), (
        f"result count differs: {len(response_1.results)} vs {len(response_2.results)}"
    )
    for i, (r1, r2) in enumerate(zip(response_1.results, response_2.results)):
        assert r1.chunk_id == r2.chunk_id, (
            f"chunk_id differs at position {i}: {r1.chunk_id} vs {r2.chunk_id}"
        )
        assert r1.fused_score == r2.fused_score, (
            f"fused_score differs at position {i}: {r1.fused_score} vs {r2.fused_score}"
        )


# ══════════════════════════════════════════════════════════════════════
# Property 4: Version isolation — zero chunks outside version set
# ══════════════════════════════════════════════════════════════════════


@given(
    query=_query_st,
    chunk_ids=st.lists(_chunk_id_st, min_size=1, max_size=15, unique=True),
)
@PROPERTY_SETTINGS
def test_version_isolation(query: str, chunk_ids: list[str]) -> None:
    """เมื่อตั้ง version_filter ไม่มี chunk นอก version set ในผลลัพธ์.

    จำนวน chunk นอกชุดเวอร์ชันที่ส่งต่อเท่ากับศูนย์.

    Mock searchers จะคืนเฉพาะ chunk ที่ตรงกับ version_filter
    (จำลองพฤติกรรมจริงของ index ที่กรอง version ก่อน scoring)

    **Validates: Requirement 10.5**
    """
    target_version = CurriculumVersion(
        program="IT", curriculum_year=2565, edition_status="current"
    )
    other_version = CurriculumVersion(
        program="CE", curriculum_year=2560, edition_status="old"
    )

    # แบ่ง chunk_ids: ครึ่งแรกเป็น target version, ครึ่งหลังเป็น other version
    mid = max(1, len(chunk_ids) // 2)
    target_ids = chunk_ids[:mid]
    other_ids = chunk_ids[mid:]

    # Mock searcher: เมื่อมี version_filter จะคืนเฉพาะ chunk ที่ตรง version
    def lexical_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[LexicalHit]:
        if version_filter is not None:
            # คืนเฉพาะ chunk ที่ตรง version (จำลอง FTS5 WHERE clause)
            return _make_lexical_hits(target_ids, version=target_version)
        # ไม่กรอง → คืนทุก chunk
        return _make_lexical_hits(target_ids, target_version) + _make_lexical_hits(
            other_ids, other_version
        )

    def dense_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[DenseHit]:
        if version_filter is not None:
            return _make_dense_hits(target_ids, version=target_version)
        return _make_dense_hits(target_ids, target_version) + _make_dense_hits(
            other_ids, other_version
        )

    response = retrieve(
        query,
        lexical_searcher=lexical_searcher,
        dense_searcher=dense_searcher,
        version_filter=target_version,
    )

    # ตรวจว่าทุก chunk_id ในผลลัพธ์อยู่ใน target_ids (อยู่ใน version set)
    result_ids = {r.chunk_id for r in response.results}
    target_id_set = set(target_ids)
    outside_version = result_ids - target_id_set

    assert len(outside_version) == 0, (
        f"พบ {len(outside_version)} chunk นอก version set: {outside_version}\n"
        f"target_ids: {target_id_set}\n"
        f"result_ids: {result_ids}"
    )
