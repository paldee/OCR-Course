"""Evaluation metrics สำหรับ KatRAG-lite.

Metrics ทั้งหมดเป็น deterministic: input เดิมให้ผลเท่ากันทุกครั้ง

Metrics ที่ implement:
- page CER (R3.8): Character Error Rate จาก edit distance หลัง NFC
- table-cell F1 (R7.7): precision/recall/F1 ของเซลล์ตาราง
- field precision/recall/F1 และ field macro-F1 (R8.8): ต่อ field exact match
- Recall@k (R13.9): สัดส่วน gold evidence chunks ใน top-k, k = 5, 10, 20
- citation page precision/recall (R17.7): เทียบ cited pages vs expected pages
- unsupported-claim rate (R17.7): สัดส่วน claims ที่ไม่มี citation รองรับ
- version-selection accuracy (R10.9): % ที่เลือก version ถูกต้อง
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from katrag.common.normalize import normalize_nfc, normalize_for_compare


# ─── Levenshtein edit distance (DP implementation) ───────────────────────


def _levenshtein_distance(s: str, t: str) -> int:
    """Compute minimum edit distance (insertion, deletion, substitution) between *s* and *t*.

    Uses standard DP with O(min(len(s), len(t))) space.
    """
    # Ensure s is the shorter string for space efficiency
    if len(s) > len(t):
        s, t = t, s

    m = len(s)
    n = len(t)

    # previous and current row of distances
    prev = list(range(m + 1))
    curr = [0] * (m + 1)

    for j in range(1, n + 1):
        curr[0] = j
        for i in range(1, m + 1):
            if s[i - 1] == t[j - 1]:
                curr[i] = prev[i - 1]
            else:
                curr[i] = 1 + min(prev[i - 1], prev[i], curr[i - 1])
        prev, curr = curr, prev

    return prev[m]


# ─── Page CER (R3.8) ────────────────────────────────────────────────────


def page_cer(system_text: str, reference_text: str) -> float:
    """Compute Character Error Rate for a single page.

    CER = edit_distance(NFC(system), NFC(reference)) / len(NFC(reference))

    Both texts are NFC-normalized before comparison (R3.8).
    Returns 0.0 when reference is empty (no characters to compare).
    CER can exceed 1.0 if system output is much longer than reference.
    """
    sys_norm = normalize_nfc(system_text)
    ref_norm = normalize_nfc(reference_text)

    if not ref_norm:
        return 0.0

    distance = _levenshtein_distance(sys_norm, ref_norm)
    return distance / len(ref_norm)


def mean_page_cer(
    pairs: Sequence[tuple[str, str]],
) -> float:
    """Compute average page CER across multiple (system_text, reference_text) pairs.

    Returns 0.0 when pairs is empty.
    Threshold: ≤ 0.05 (R3.8).
    """
    if not pairs:
        return 0.0
    total = sum(page_cer(sys_text, ref_text) for sys_text, ref_text in pairs)
    return total / len(pairs)


# ─── Table-cell F1 (R7.7) ───────────────────────────────────────────────


@dataclass(frozen=True)
class CellKey:
    """Unique identifier for a table cell: (document_id, page, row_index, col_index)."""

    document_id: str
    page: int
    row_index: int
    col_index: int


@dataclass(frozen=True)
class PrecisionRecallF1:
    """Precision, recall and F1 score container."""

    precision: float
    recall: float
    f1: float


def _compute_prf(tp: int, fp: int, fn: int) -> PrecisionRecallF1:
    """Compute precision, recall, F1 from true positives, false positives, false negatives."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return PrecisionRecallF1(precision=precision, recall=recall, f1=f1)


def table_cell_f1(
    system_cells: Sequence[tuple[CellKey, str]],
    reference_cells: Sequence[tuple[CellKey, str]],
) -> PrecisionRecallF1:
    """Compute table-cell F1 (R7.7).

    A cell matches when (document_id, page, row_index, col_index) are equal
    AND normalized text matches exactly (after squeeze whitespace + combining mark order).

    Args:
        system_cells: sequence of (CellKey, text) from system output
        reference_cells: sequence of (CellKey, text) from gold set

    Returns:
        PrecisionRecallF1 with precision, recall, F1.
        Threshold: F1 ≥ 0.90.
    """
    # Build reference lookup: key -> normalized text
    ref_map: dict[CellKey, str] = {
        key: normalize_for_compare(text) for key, text in reference_cells
    }

    # Count true positives (system cell matches reference cell by key AND text)
    tp = 0
    for key, text in system_cells:
        sys_norm = normalize_for_compare(text)
        if key in ref_map and ref_map[key] == sys_norm:
            tp += 1

    fp = len(system_cells) - tp
    fn = len(reference_cells) - tp

    return _compute_prf(tp, fp, fn)


# ─── Field precision/recall/F1 (R8.8) ──────────────────────────────────


def field_precision_recall_f1(
    system_values: Sequence[str],
    reference_values: Sequence[str],
) -> PrecisionRecallF1:
    """Compute precision/recall/F1 for a single field based on exact match.

    Each element in system_values and reference_values is a field value string.
    The sequences are positionally aligned (same index = same record).
    A match means exact string equality after NFC normalization and whitespace squeeze.

    Args:
        system_values: predicted field values (positionally aligned with reference)
        reference_values: ground truth field values (positionally aligned with system)

    Returns:
        PrecisionRecallF1 for this field.
    """
    if len(system_values) != len(reference_values):
        raise ValueError(
            f"system_values ({len(system_values)}) and reference_values "
            f"({len(reference_values)}) must have same length"
        )

    tp = 0
    for sys_val, ref_val in zip(system_values, reference_values):
        if normalize_for_compare(sys_val) == normalize_for_compare(ref_val):
            tp += 1

    # For positionally aligned comparison:
    # precision = correct / total_predicted = tp / len(system_values)
    # recall = correct / total_reference = tp / len(reference_values)
    # Since lengths are equal, precision == recall == accuracy
    total = len(system_values)
    if total == 0:
        return PrecisionRecallF1(precision=0.0, recall=0.0, f1=0.0)

    precision = tp / total
    recall = tp / total
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return PrecisionRecallF1(precision=precision, recall=recall, f1=f1)


def field_macro_f1(
    per_field_results: Sequence[PrecisionRecallF1],
) -> float:
    """Compute field macro-F1: average F1 across all fields (R8.8).

    Args:
        per_field_results: F1 results for each of the 11 fields.

    Returns:
        Average F1 across all fields. Threshold: ≥ 0.91.
    """
    if not per_field_results:
        return 0.0
    return sum(r.f1 for r in per_field_results) / len(per_field_results)


# ─── Recall@k (R13.9) ───────────────────────────────────────────────────


def recall_at_k(
    retrieved_chunks: Sequence[tuple[str, int]],
    gold_evidence_pages: Sequence[tuple[str, int]],
    k: int,
) -> float:
    """Compute Recall@k for a single query (R13.9).

    A chunk is a hit when its (document_id, page) is in the gold evidence set.

    Args:
        retrieved_chunks: ordered sequence of (document_id, page) from retrieval,
                         ordered by rank (index 0 = rank 1).
        gold_evidence_pages: set of (document_id, page) that are correct evidence.
        k: cutoff rank.

    Returns:
        Proportion of gold evidence pages found in top-k results.
        Value in [0.0, 1.0].
    """
    if not gold_evidence_pages:
        return 0.0

    gold_set = set(gold_evidence_pages)
    top_k = retrieved_chunks[:k]
    hits = sum(1 for chunk in top_k if chunk in gold_set)
    return hits / len(gold_set)


def mean_recall_at_k(
    queries: Sequence[tuple[Sequence[tuple[str, int]], Sequence[tuple[str, int]]]],
    k: int,
) -> float:
    """Compute mean Recall@k across multiple queries.

    Args:
        queries: sequence of (retrieved_chunks, gold_evidence_pages) per query.
        k: cutoff rank (5, 10, or 20).

    Returns:
        Average Recall@k. Threshold at k=10: ≥ 0.90.
    """
    if not queries:
        return 0.0
    total = sum(
        recall_at_k(retrieved, gold, k) for retrieved, gold in queries
    )
    return total / len(queries)


# ─── Citation page precision/recall (R17.7) ─────────────────────────────


def citation_page_precision(
    cited_pages: Sequence[tuple[str, int]],
    expected_pages: Sequence[tuple[str, int]],
) -> float:
    """Compute citation page precision for a single question (R17.7).

    Precision = |cited ∩ expected| / |cited|

    Args:
        cited_pages: (document_id, page) pairs cited in the answer.
        expected_pages: (document_id, page) pairs expected per Gold_Set.

    Returns:
        Precision in [0.0, 1.0]. Returns 0.0 if no citations.
        Threshold: ≥ 0.95.
    """
    if not cited_pages:
        return 0.0

    expected_set = set(expected_pages)
    hits = sum(1 for page in cited_pages if page in expected_set)
    return hits / len(cited_pages)


def citation_page_recall(
    cited_pages: Sequence[tuple[str, int]],
    expected_pages: Sequence[tuple[str, int]],
) -> float:
    """Compute citation page recall for a single question (R17.7).

    Recall = |cited ∩ expected| / |expected|

    Args:
        cited_pages: (document_id, page) pairs cited in the answer.
        expected_pages: (document_id, page) pairs expected per Gold_Set.

    Returns:
        Recall in [0.0, 1.0]. Returns 0.0 if no expected pages.
        Threshold: ≥ 0.91.
    """
    if not expected_pages:
        return 0.0

    expected_set = set(expected_pages)
    cited_set = set(cited_pages)
    hits = sum(1 for page in expected_set if page in cited_set)
    return hits / len(expected_set)


def mean_citation_page_metrics(
    questions: Sequence[tuple[Sequence[tuple[str, int]], Sequence[tuple[str, int]]]],
) -> tuple[float, float]:
    """Compute mean citation page precision and recall across all questions.

    Args:
        questions: sequence of (cited_pages, expected_pages) per question.

    Returns:
        (mean_precision, mean_recall) tuple.
    """
    if not questions:
        return 0.0, 0.0

    total_precision = sum(
        citation_page_precision(cited, expected) for cited, expected in questions
    )
    total_recall = sum(
        citation_page_recall(cited, expected) for cited, expected in questions
    )
    n = len(questions)
    return total_precision / n, total_recall / n


# ─── Unsupported-claim rate (R17.7) ─────────────────────────────────────


def unsupported_claim_rate(
    unsupported_claims: int,
    total_factual_claims: int,
) -> float:
    """Compute unsupported-claim rate for a single question or aggregated (R17.7).

    Rate = unsupported_claims / total_factual_claims

    Args:
        unsupported_claims: number of claim units marked as unsupported.
        total_factual_claims: total number of factual claim units.

    Returns:
        Rate in [0.0, 1.0]. Returns 0.0 if no factual claims.
        Threshold: < 0.05.
    """
    if total_factual_claims <= 0:
        return 0.0
    return unsupported_claims / total_factual_claims


def aggregate_unsupported_claim_rate(
    questions: Sequence[tuple[int, int]],
) -> float:
    """Compute aggregate unsupported-claim rate across all questions (R17.7).

    Per R17.7: total unsupported claims / total factual claims across all questions.

    Args:
        questions: sequence of (unsupported_claims, total_factual_claims) per question.

    Returns:
        Aggregate rate. Threshold: < 0.05.
    """
    total_unsupported = sum(u for u, _ in questions)
    total_factual = sum(t for _, t in questions)
    return unsupported_claim_rate(total_unsupported, total_factual)


# ─── Version-selection accuracy (R10.9) ─────────────────────────────────


def version_selection_accuracy(
    results: Sequence[tuple[frozenset[tuple[str, str, str]], frozenset[tuple[str, str, str]]]],
) -> float:
    """Compute version-selection accuracy (R10.9).

    For questions with known correct version, proportion of times system
    selects the right version set exactly.

    Each element is (system_versions, expected_versions) where each version is
    a frozenset of (program, curriculum_year, edition_status) tuples.

    A question is correct when system_versions == expected_versions (exact set match).

    Args:
        results: sequence of (system_version_set, expected_version_set) per question.

    Returns:
        Accuracy in [0.0, 1.0]. Threshold: ≥ 0.98.
    """
    if not results:
        return 0.0

    correct = sum(1 for sys_v, exp_v in results if sys_v == exp_v)
    return correct / len(results)
