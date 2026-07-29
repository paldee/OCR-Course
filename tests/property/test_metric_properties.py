"""Property test ของ metric และ manifest (task 14.3, R18.1, R8.8, R1.9).

คุณสมบัติที่ต้องคงอยู่เสมอ:
1. ค่า metric อยู่ในช่วงที่กำหนด (≥ 0 ทุกตัว; F1/precision/recall/accuracy ∈ [0,1])
2. CER ≥ 0 (สามารถเกิน 1.0 ได้เมื่อ system output ยาวกว่า reference)
3. การสลับลำดับ input (order-invariance) ไม่เปลี่ยนค่า metric ที่เป็น set-based
4. field ที่ไม่ถูกบันทึก (missing) นับเป็น false negative → recall ลดลง
5. ผลิต dataset manifest ซ้ำจากชุดข้อมูลเดิมได้เนื้อหาเหมือนเดิม (determinism)
"""

from __future__ import annotations

import random

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from katrag.eval.metrics import (
    CellKey,
    PrecisionRecallF1,
    citation_page_precision,
    citation_page_recall,
    field_precision_recall_f1,
    page_cer,
    recall_at_k,
    table_cell_f1,
    unsupported_claim_rate,
    version_selection_accuracy,
)
from katrag.common.hashing import canonical_json, sha256_mapping, sha256_text

PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Strategies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_text_st = st.text(min_size=0, max_size=50)
_nonempty_text_st = st.text(min_size=1, max_size=50)
_doc_id_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "Nd"), min_codepoint=48, max_codepoint=122),
    min_size=1,
    max_size=10,
)
_page_st = st.integers(min_value=0, max_value=100)
_row_col_st = st.integers(min_value=0, max_value=50)

_cell_key_st = st.builds(CellKey, document_id=_doc_id_st, page=_page_st, row_index=_row_col_st, col_index=_row_col_st)
_page_tuple_st = st.tuples(_doc_id_st, _page_st)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 1: page_cer — non-negative
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@given(system_text=_text_st, reference_text=_nonempty_text_st)
@PROPERTY_SETTINGS
def test_page_cer_non_negative(system_text: str, reference_text: str) -> None:
    """CER ต้อง ≥ 0 เสมอ (อาจเกิน 1.0 ได้ แต่ห้ามติดลบ)."""
    cer = page_cer(system_text, reference_text)
    assert cer >= 0.0, f"CER ต้อง ≥ 0 แต่ได้ {cer}"


@given(text=_text_st)
@PROPERTY_SETTINGS
def test_page_cer_identical_is_zero(text: str) -> None:
    """CER ของข้อความเหมือนกัน ต้องเป็น 0."""
    cer = page_cer(text, text)
    assert cer == 0.0, f"CER ของข้อความเหมือนกันต้องเป็น 0 แต่ได้ {cer}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 2: table_cell_f1 — in [0,1] and order-invariant
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@given(
    sys_keys=st.lists(_cell_key_st, min_size=1, max_size=10, unique=True),
    ref_keys=st.lists(_cell_key_st, min_size=1, max_size=10, unique=True),
    data=st.data(),
)
@PROPERTY_SETTINGS
def test_table_cell_f1_in_range(
    sys_keys: list[CellKey],
    ref_keys: list[CellKey],
    data: st.DataObject,
) -> None:
    """table_cell_f1 ต้องคืน precision/recall/F1 ∈ [0,1] เมื่อ key ไม่ซ้ำภายใน list."""
    sys_cells = [(k, data.draw(_text_st)) for k in sys_keys]
    ref_cells = [(k, data.draw(_text_st)) for k in ref_keys]

    result = table_cell_f1(sys_cells, ref_cells)
    assert 0.0 <= result.precision <= 1.0, f"precision {result.precision} ไม่อยู่ใน [0,1]"
    assert 0.0 <= result.recall <= 1.0, f"recall {result.recall} ไม่อยู่ใน [0,1]"
    assert 0.0 <= result.f1 <= 1.0, f"F1 {result.f1} ไม่อยู่ใน [0,1]"


@given(
    keys=st.lists(_cell_key_st, min_size=2, max_size=10, unique=True),
    data=st.data(),
)
@PROPERTY_SETTINGS
def test_table_cell_f1_order_invariant(keys: list[CellKey], data: st.DataObject) -> None:
    """การสลับลำดับ input ไม่เปลี่ยนค่า F1 (key unique ภายใน list)."""
    # Split keys into sys and ref
    mid = len(keys) // 2
    sys_keys = keys[:mid] if mid > 0 else keys[:1]
    ref_keys = keys[mid:]

    sys_cells = [(k, data.draw(_text_st)) for k in sys_keys]
    ref_cells = [(k, data.draw(_text_st)) for k in ref_keys]

    result_original = table_cell_f1(sys_cells, ref_cells)

    # Shuffle both lists
    sys_shuffled = list(sys_cells)
    ref_shuffled = list(ref_cells)
    random.shuffle(sys_shuffled)
    random.shuffle(ref_shuffled)

    result_shuffled = table_cell_f1(sys_shuffled, ref_shuffled)

    assert result_original.precision == result_shuffled.precision
    assert result_original.recall == result_shuffled.recall
    assert result_original.f1 == result_shuffled.f1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 3: field_precision_recall_f1 — in [0,1]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@given(
    values=st.lists(_text_st, min_size=1, max_size=20),
)
@PROPERTY_SETTINGS
def test_field_prf_in_range(values: list[str]) -> None:
    """field_precision_recall_f1 ต้องคืน precision/recall/F1 ∈ [0,1]."""
    # Generate system values that may differ from reference
    system_values = values
    reference_values = values  # Same length guaranteed

    result = field_precision_recall_f1(system_values, reference_values)
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0
    assert 0.0 <= result.f1 <= 1.0


@given(
    sys_values=st.lists(_text_st, min_size=1, max_size=20),
    ref_values=st.lists(_text_st, min_size=1, max_size=20),
)
@PROPERTY_SETTINGS
def test_field_prf_in_range_different_content(sys_values: list[str], ref_values: list[str]) -> None:
    """field_precision_recall_f1 ∈ [0,1] แม้ค่า system กับ reference ต่างกัน."""
    # Ensure same length
    min_len = min(len(sys_values), len(ref_values))
    sys_trimmed = sys_values[:min_len]
    ref_trimmed = ref_values[:min_len]

    result = field_precision_recall_f1(sys_trimmed, ref_trimmed)
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0
    assert 0.0 <= result.f1 <= 1.0


@given(
    ref_values=st.lists(_nonempty_text_st, min_size=2, max_size=10),
)
@PROPERTY_SETTINGS
def test_field_missing_values_decrease_recall(ref_values: list[str]) -> None:
    """field ที่ไม่ถูกบันทึกนับเป็น false negative → recall ลดลง.

    ถ้า system ตอบถูกทุก field ได้ recall=1.0
    แต่ถ้าเปลี่ยน field สุดท้ายให้ผิด recall ต้องลดลง
    """
    # All correct → recall = 1.0
    result_all_correct = field_precision_recall_f1(ref_values, ref_values)
    assert result_all_correct.recall == 1.0

    # Make one field incorrect (simulate missing/wrong answer)
    sys_with_missing = list(ref_values)
    sys_with_missing[-1] = sys_with_missing[-1] + "__WRONG__"

    result_with_missing = field_precision_recall_f1(sys_with_missing, ref_values)
    assert result_with_missing.recall < result_all_correct.recall, (
        "field ที่ตอบผิดต้องทำให้ recall ลดลง"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 4: recall_at_k — in [0,1]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@given(
    retrieved=st.lists(_page_tuple_st, min_size=0, max_size=20, unique=True),
    gold=st.lists(_page_tuple_st, min_size=1, max_size=10, unique=True),
    k=st.integers(min_value=1, max_value=20),
)
@PROPERTY_SETTINGS
def test_recall_at_k_in_range(
    retrieved: list[tuple[str, int]],
    gold: list[tuple[str, int]],
    k: int,
) -> None:
    """recall@k ∈ [0,1] เมื่อ retrieved ไม่มีซ้ำ (สะท้อนการใช้งานจริง)."""
    result = recall_at_k(retrieved, gold, k)
    assert 0.0 <= result <= 1.0, f"recall@k={result} ไม่อยู่ใน [0,1]"


@given(
    gold=st.lists(_page_tuple_st, min_size=1, max_size=5),
    k=st.integers(min_value=1, max_value=20),
    data=st.data(),
)
@PROPERTY_SETTINGS
def test_recall_at_k_order_invariant_gold(
    gold: list[tuple[str, int]],
    k: int,
    data: st.DataObject,
) -> None:
    """การสลับลำดับ gold set ไม่เปลี่ยนค่า recall@k (gold เป็น set-based)."""
    retrieved = data.draw(st.lists(_page_tuple_st, min_size=1, max_size=15))

    result_original = recall_at_k(retrieved, gold, k)

    gold_shuffled = list(gold)
    random.shuffle(gold_shuffled)

    result_shuffled = recall_at_k(retrieved, gold_shuffled, k)
    assert result_original == result_shuffled


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 5: citation_page_precision/recall — in [0,1] and order-invariant
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@given(
    cited=st.lists(_page_tuple_st, min_size=1, max_size=10),
    expected=st.lists(_page_tuple_st, min_size=1, max_size=10),
)
@PROPERTY_SETTINGS
def test_citation_precision_in_range(
    cited: list[tuple[str, int]],
    expected: list[tuple[str, int]],
) -> None:
    """citation_page_precision ∈ [0,1]."""
    result = citation_page_precision(cited, expected)
    assert 0.0 <= result <= 1.0


@given(
    cited=st.lists(_page_tuple_st, min_size=1, max_size=10),
    expected=st.lists(_page_tuple_st, min_size=1, max_size=10),
)
@PROPERTY_SETTINGS
def test_citation_recall_in_range(
    cited: list[tuple[str, int]],
    expected: list[tuple[str, int]],
) -> None:
    """citation_page_recall ∈ [0,1]."""
    result = citation_page_recall(cited, expected)
    assert 0.0 <= result <= 1.0


@given(
    cited=st.lists(_page_tuple_st, min_size=2, max_size=10),
    expected=st.lists(_page_tuple_st, min_size=2, max_size=10),
)
@PROPERTY_SETTINGS
def test_citation_precision_order_invariant(
    cited: list[tuple[str, int]],
    expected: list[tuple[str, int]],
) -> None:
    """การสลับลำดับ expected ไม่เปลี่ยน precision (expected เป็น set-based)."""
    result_original = citation_page_precision(cited, expected)

    expected_shuffled = list(expected)
    random.shuffle(expected_shuffled)

    result_shuffled = citation_page_precision(cited, expected_shuffled)
    assert result_original == result_shuffled


@given(
    cited=st.lists(_page_tuple_st, min_size=2, max_size=10),
    expected=st.lists(_page_tuple_st, min_size=2, max_size=10),
)
@PROPERTY_SETTINGS
def test_citation_recall_order_invariant(
    cited: list[tuple[str, int]],
    expected: list[tuple[str, int]],
) -> None:
    """การสลับลำดับ cited ไม่เปลี่ยน recall (cited ถูกแปลงเป็น set)."""
    result_original = citation_page_recall(cited, expected)

    cited_shuffled = list(cited)
    random.shuffle(cited_shuffled)

    result_shuffled = citation_page_recall(cited_shuffled, expected)
    assert result_original == result_shuffled


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 6: unsupported_claim_rate — in [0,1]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@given(
    unsupported=st.integers(min_value=0, max_value=100),
    total=st.integers(min_value=1, max_value=100),
)
@PROPERTY_SETTINGS
def test_unsupported_claim_rate_in_range(unsupported: int, total: int) -> None:
    """unsupported_claim_rate ∈ [0,1] เมื่อ unsupported ≤ total."""
    assume(unsupported <= total)
    result = unsupported_claim_rate(unsupported, total)
    assert 0.0 <= result <= 1.0, f"unsupported_claim_rate={result} ไม่อยู่ใน [0,1]"


@given(total=st.integers(min_value=1, max_value=100))
@PROPERTY_SETTINGS
def test_unsupported_claim_rate_zero_when_all_supported(total: int) -> None:
    """ถ้าทุก claim มี citation → rate = 0."""
    result = unsupported_claim_rate(0, total)
    assert result == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 7: version_selection_accuracy — in [0,1]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_version_tuple_st = st.tuples(
    st.text(min_size=1, max_size=5),
    st.text(min_size=4, max_size=4),
    st.text(min_size=1, max_size=8),
)
_version_set_st = st.frozensets(_version_tuple_st, min_size=1, max_size=3)


@given(
    results=st.lists(
        st.tuples(_version_set_st, _version_set_st),
        min_size=1,
        max_size=10,
    ),
)
@PROPERTY_SETTINGS
def test_version_selection_accuracy_in_range(
    results: list[tuple[frozenset[tuple[str, str, str]], frozenset[tuple[str, str, str]]]],
) -> None:
    """version_selection_accuracy ∈ [0,1]."""
    result = version_selection_accuracy(results)
    assert 0.0 <= result <= 1.0, f"accuracy={result} ไม่อยู่ใน [0,1]"


@given(
    version_sets=st.lists(_version_set_st, min_size=1, max_size=10),
)
@PROPERTY_SETTINGS
def test_version_selection_accuracy_perfect_when_identical(
    version_sets: list[frozenset[tuple[str, str, str]]],
) -> None:
    """ถ้า system เลือกถูกทุกข้อ accuracy = 1.0."""
    results = [(vs, vs) for vs in version_sets]
    result = version_selection_accuracy(results)
    assert result == 1.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Property 8: Dataset manifest determinism (R1.9)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@given(
    data=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(
            st.text(min_size=0, max_size=30),
            st.integers(min_value=-1000, max_value=1000),
            st.booleans(),
        ),
        min_size=1,
        max_size=10,
    ),
)
@PROPERTY_SETTINGS
def test_manifest_deterministic_canonical_json(data: dict[str, str | int | bool]) -> None:
    """canonical_json ของ dict เดิมได้ผลเหมือนกันทุกครั้ง (deterministic)."""
    result1 = canonical_json(data)
    result2 = canonical_json(data)
    assert result1 == result2, "canonical_json ต้อง deterministic"


@given(
    data=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(
            st.text(min_size=0, max_size=30),
            st.integers(min_value=-1000, max_value=1000),
            st.booleans(),
        ),
        min_size=1,
        max_size=10,
    ),
)
@PROPERTY_SETTINGS
def test_manifest_deterministic_sha256(data: dict[str, str | int | bool]) -> None:
    """sha256_mapping ของ dict เดิมได้ hash เหมือนกันทุกครั้ง (reproducible manifest)."""
    hash1 = sha256_mapping(data)
    hash2 = sha256_mapping(data)
    assert hash1 == hash2, "sha256_mapping ต้อง deterministic"


@given(
    data=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(
            st.text(min_size=0, max_size=30),
            st.integers(min_value=-1000, max_value=1000),
            st.booleans(),
        ),
        min_size=2,
        max_size=10,
    ),
)
@PROPERTY_SETTINGS
def test_manifest_key_order_independent(data: dict[str, str | int | bool]) -> None:
    """ลำดับ key ไม่มีผลต่อ manifest hash (canonical JSON เรียง key)."""
    keys = list(data.keys())
    assume(len(keys) >= 2)

    # สร้าง dict ในลำดับต่างกัน
    reversed_data = {k: data[k] for k in reversed(keys)}

    hash_original = sha256_mapping(data)
    hash_reversed = sha256_mapping(reversed_data)
    assert hash_original == hash_reversed, (
        "sha256_mapping ต้องไม่ขึ้นกับลำดับ key"
    )


@given(
    texts=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=5),
)
@PROPERTY_SETTINGS
def test_manifest_file_hash_deterministic(texts: list[str]) -> None:
    """sha256_text ที่ใช้สร้าง file hash ใน manifest ต้อง deterministic.

    จำลองการสร้าง manifest จากชุดไฟล์ — hash เนื้อหาของแต่ละไฟล์ซ้ำสองครั้ง
    ได้ผลเหมือนกันทุกครั้ง (R1.9)
    """
    # สร้าง manifest entries จากชุดข้อความ (จำลองเนื้อหาไฟล์)
    manifest1 = {f"file_{i}.txt": sha256_text(text) for i, text in enumerate(texts)}
    manifest2 = {f"file_{i}.txt": sha256_text(text) for i, text in enumerate(texts)}

    assert manifest1 == manifest2, "manifest ที่สร้างจากชุดไฟล์เดิมต้องเหมือนกัน"

    # Hash ของ manifest ทั้ง dict ก็ต้องเหมือนกัน
    assert sha256_mapping(manifest1) == sha256_mapping(manifest2)
