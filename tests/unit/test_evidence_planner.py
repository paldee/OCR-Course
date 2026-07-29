"""Unit tests for katrag.query.evidence_planner.

ทดสอบ R14.1–R14.12:
- DAG invariants (node ≤ 60, ≤ 10 per hop, ≤ max_hops)
- Provenance rejection + missing_provenance count
- Cycle rejection + cycle_rejected count
- Version filtering + version_filtered count
- Halter integration (nan_guard, gain_below_cost)
- Halt reasons: max_hops_reached, no_new_evidence, nan_guard, time_budget_exceeded
- Per-hop trace recording
"""

from __future__ import annotations

import time

import pytest

from katrag.common.halter import GainCostHalter
from katrag.common.types import (
    BBox,
    CurriculumVersion,
    HaltDecision,
    HaltReason,
    Provenance,
)
from katrag.config import EvidenceConfig
from katrag.query.evidence_planner import (
    EvidenceGraph,
    EvidenceNode,
    EvidencePlanner,
    HopTrace,
    PlanResult,
    RetrievedChunk,
)


# ── Fixtures / helpers ────────────────────────────────────────────────


def _make_version(program: str = "CS", year: int = 2566) -> CurriculumVersion:
    return CurriculumVersion(program=program, curriculum_year=year, edition_status="current")


def _make_provenance(doc_id: str = "doc1", page: int = 1) -> Provenance:
    return Provenance(
        document_id=doc_id,
        page=page,
        bbox=BBox(x0=0.0, y0=0.0, x1=100.0, y1=50.0),
        span=(0, 10),
        extraction_method="text_layer",
    )


def _make_node(
    node_id: str = "n1",
    chunk_id: str = "c1",
    text: str = "sample",
    provenance: Provenance | None = None,
    version: CurriculumVersion | None = None,
) -> EvidenceNode:
    return EvidenceNode(
        node_id=node_id,
        chunk_id=chunk_id,
        text=text,
        provenance=provenance or _make_provenance(),
        version=version or _make_version(),
        edges_to=[],
    )


def _make_config(
    max_hops: int = 3,
    max_nodes_per_request: int = 60,
    max_nodes_per_hop: int = 10,
    evidence_time_budget_seconds: float = 10.0,
) -> EvidenceConfig:
    return EvidenceConfig(
        max_hops=max_hops,
        max_nodes_per_request=max_nodes_per_request,
        max_nodes_per_hop=max_nodes_per_hop,
        evidence_time_budget_seconds=evidence_time_budget_seconds,
    )


def _make_chunks(
    count: int,
    prefix: str = "chunk",
    version: CurriculumVersion | None = None,
    provenance: Provenance | None = None,
) -> list[RetrievedChunk]:
    """สร้าง list ของ RetrievedChunk จำนวน count ชิ้น."""
    ver = version or _make_version()
    prov = provenance or _make_provenance()
    return [
        RetrievedChunk(
            chunk_id=f"{prefix}_{i}",
            text=f"text of {prefix}_{i}",
            provenance=prov,
            version=ver,
        )
        for i in range(count)
    ]


# ══════════════════════════════════════════════════════════════════════
# Tests: EvidenceGraph
# ══════════════════════════════════════════════════════════════════════


class TestEvidenceGraphNodeLimit:
    """R14.1: node ≤ 60 per request."""

    def test_respects_max_nodes(self) -> None:
        graph = EvidenceGraph(max_nodes=5, max_per_hop=10)
        for i in range(10):
            node = _make_node(node_id=f"n{i}", chunk_id=f"c{i}")
            graph.add_node(node)
        assert graph.node_count == 5

    def test_is_full_property(self) -> None:
        graph = EvidenceGraph(max_nodes=2, max_per_hop=10)
        assert not graph.is_full
        graph.add_node(_make_node(node_id="n0", chunk_id="c0"))
        assert not graph.is_full
        graph.add_node(_make_node(node_id="n1", chunk_id="c1"))
        assert graph.is_full


class TestEvidenceGraphProvenance:
    """R14.4: Node without provenance → reject + record missing_provenance."""

    def test_rejects_none_provenance(self) -> None:
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        node = EvidenceNode(
            node_id="n1",
            chunk_id="c1",
            text="text",
            provenance=None,  # type: ignore[arg-type]
            version=_make_version(),
            edges_to=[],
        )
        assert graph.add_node(node) is False
        assert graph.missing_provenance == 1
        assert graph.node_count == 0

    def test_rejects_incomplete_provenance(self) -> None:
        """Provenance with invalid bbox → is_complete() = False."""
        bad_prov = Provenance(
            document_id="doc1",
            page=1,
            bbox=BBox(x0=0.0, y0=0.0, x1=0.0, y1=0.0),  # invalid bbox
            span=(0, 5),
            extraction_method="text_layer",
        )
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        node = _make_node(node_id="n1", provenance=bad_prov)
        assert graph.add_node(node) is False
        assert graph.missing_provenance == 1

    def test_accepts_valid_provenance(self) -> None:
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        node = _make_node(node_id="n1")
        assert graph.add_node(node) is True
        assert graph.missing_provenance == 0
        assert graph.node_count == 1


class TestEvidenceGraphCycleDetection:
    """R14.6: Edge that creates cycle → reject + record cycle_rejected."""

    def test_rejects_self_loop(self) -> None:
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        graph.add_node(_make_node(node_id="a", chunk_id="ca"))
        assert graph.add_edge("a", "a") is False
        assert graph.cycle_rejected == 1

    def test_rejects_direct_cycle(self) -> None:
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        graph.add_node(_make_node(node_id="a", chunk_id="ca"))
        graph.add_node(_make_node(node_id="b", chunk_id="cb"))
        assert graph.add_edge("a", "b") is True
        assert graph.add_edge("b", "a") is False
        assert graph.cycle_rejected == 1

    def test_rejects_indirect_cycle(self) -> None:
        """a→b→c, then c→a should be rejected."""
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        graph.add_node(_make_node(node_id="a", chunk_id="ca"))
        graph.add_node(_make_node(node_id="b", chunk_id="cb"))
        graph.add_node(_make_node(node_id="c", chunk_id="cc"))
        graph.add_edge("a", "b")
        graph.add_edge("b", "c")
        assert graph.add_edge("c", "a") is False
        assert graph.cycle_rejected == 1

    def test_allows_valid_dag_edge(self) -> None:
        """a→b, a→c, b→c should be valid (diamond DAG)."""
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        graph.add_node(_make_node(node_id="a", chunk_id="ca"))
        graph.add_node(_make_node(node_id="b", chunk_id="cb"))
        graph.add_node(_make_node(node_id="c", chunk_id="cc"))
        assert graph.add_edge("a", "b") is True
        assert graph.add_edge("a", "c") is True
        assert graph.add_edge("b", "c") is True
        assert graph.cycle_rejected == 0

    def test_rejects_edge_to_nonexistent_node(self) -> None:
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        graph.add_node(_make_node(node_id="a", chunk_id="ca"))
        assert graph.add_edge("a", "nonexistent") is False


class TestEvidenceGraphVersionFilter:
    """R14.10: Filter nodes outside version set + record version_filtered."""

    def test_rejects_node_outside_version_set(self) -> None:
        allowed = frozenset([_make_version("CS", 2566)])
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10, allowed_versions=allowed)
        wrong_ver = _make_version("IT", 2565)
        node = _make_node(node_id="n1", version=wrong_ver)
        assert graph.add_node(node) is False
        assert graph.version_filtered == 1

    def test_accepts_node_in_version_set(self) -> None:
        allowed = frozenset([_make_version("CS", 2566)])
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10, allowed_versions=allowed)
        node = _make_node(node_id="n1", version=_make_version("CS", 2566))
        assert graph.add_node(node) is True
        assert graph.version_filtered == 0

    def test_no_filter_when_versions_none(self) -> None:
        """When allowed_versions is None, all versions pass."""
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10, allowed_versions=None)
        node = _make_node(node_id="n1", version=_make_version("ANYTHING", 2560))
        assert graph.add_node(node) is True


class TestEvidenceGraphDuplicateNode:
    def test_rejects_duplicate_node_id(self) -> None:
        graph = EvidenceGraph(max_nodes=60, max_per_hop=10)
        graph.add_node(_make_node(node_id="n1", chunk_id="c1"))
        assert graph.add_node(_make_node(node_id="n1", chunk_id="c2")) is False
        assert graph.node_count == 1


# ══════════════════════════════════════════════════════════════════════
# Tests: EvidencePlanner
# ══════════════════════════════════════════════════════════════════════


class TestPlannerMaxHops:
    """R14.3, R14.11: hop ≤ max_hops, halt reason max_hops_reached."""

    def test_stops_at_max_hops(self) -> None:
        config = _make_config(max_hops=2, max_nodes_per_request=60)
        planner = EvidencePlanner(config)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            return _make_chunks(3, prefix=f"hop{call_count}")

        result = planner.plan("test question", frozenset([_make_version()]), retriever)
        assert result.halt_reason == HaltReason.MAX_HOPS_REACHED
        assert call_count == 2
        assert len(result.hop_traces) >= 2


class TestPlannerNodesPerHop:
    """R14.2: ≤ 10 nodes per hop."""

    def test_limits_nodes_per_hop(self) -> None:
        config = _make_config(max_hops=1, max_nodes_per_hop=3)
        planner = EvidencePlanner(config)

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            return _make_chunks(10, prefix="many")

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        # Should add at most 3 nodes in first hop
        assert result.graph.node_count <= 3


class TestPlannerNoNewEvidence:
    """R14.7: Halt reason no_new_evidence when hop adds 0 new nodes."""

    def test_halts_on_empty_retrieval(self) -> None:
        config = _make_config(max_hops=5)
        planner = EvidencePlanner(config)

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            return []

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        assert result.halt_reason == HaltReason.NO_NEW_EVIDENCE
        assert len(result.hop_traces) == 1
        assert result.hop_traces[0].nodes_added == 0

    def test_halts_when_all_duplicates(self) -> None:
        """If retriever returns same chunks every hop, second hop adds 0."""
        config = _make_config(max_hops=5)
        planner = EvidencePlanner(config)
        fixed_chunks = _make_chunks(3, prefix="same")

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            return fixed_chunks

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        # First hop adds 3, second hop adds 0 → halt
        assert result.halt_reason == HaltReason.NO_NEW_EVIDENCE
        assert result.graph.node_count == 3


class TestPlannerNanGuard:
    """R14.8: Halt reason nan_guard from halter."""

    def test_halts_on_nan_from_halter(self) -> None:
        """Use a custom halter subclass to inject NaN behavior."""
        from katrag.common.types import HaltVerdict

        class NanHalter(GainCostHalter):
            """Halter that always returns nan_guard on observe."""

            def observe(self, score: float, elapsed_s: float, budget_s: float) -> HaltVerdict:
                # Feed NaN to trigger nan_guard
                return super().observe(float("nan"), elapsed_s, budget_s)

        config = _make_config(max_hops=5, evidence_time_budget_seconds=100.0)
        halter = NanHalter(tau=1.0, l_min=1, oscillation_patience=2)
        planner = EvidencePlanner(config, halter=halter)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            return _make_chunks(2, prefix=f"nan{call_count}")

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        assert result.halt_reason == HaltReason.NAN_GUARD


class TestPlannerTimeBudget:
    """R14.9: Halt reason time_budget_exceeded."""

    def test_halts_when_time_exceeded(self) -> None:
        config = _make_config(max_hops=5, evidence_time_budget_seconds=0.001)
        planner = EvidencePlanner(config)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            # Simulate slow retrieval
            time.sleep(0.01)
            return _make_chunks(2, prefix=f"slow{call_count}")

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        assert result.halt_reason == HaltReason.TIME_BUDGET_EXCEEDED


class TestPlannerVersionFiltering:
    """R14.10: Filter nodes outside version set + record version_filtered."""

    def test_counts_version_filtered(self) -> None:
        config = _make_config(max_hops=1)
        planner = EvidencePlanner(config)
        allowed_ver = _make_version("CS", 2566)
        wrong_ver = _make_version("IT", 2565)

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(
                    chunk_id="c_ok",
                    text="ok",
                    provenance=_make_provenance(),
                    version=allowed_ver,
                ),
                RetrievedChunk(
                    chunk_id="c_bad",
                    text="bad",
                    provenance=_make_provenance(),
                    version=wrong_ver,
                ),
            ]

        result = planner.plan("q", frozenset([allowed_ver]), retriever)
        assert result.version_filtered_count == 1
        assert result.graph.node_count == 1


class TestPlannerMissingProvenance:
    """R14.4: Node without provenance → reject + record missing_provenance."""

    def test_counts_missing_provenance(self) -> None:
        config = _make_config(max_hops=1)
        planner = EvidencePlanner(config)

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(
                    chunk_id="c_ok",
                    text="ok",
                    provenance=_make_provenance(),
                    version=_make_version(),
                ),
                RetrievedChunk(
                    chunk_id="c_no_prov",
                    text="no prov",
                    provenance=None,
                    version=_make_version(),
                ),
            ]

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        assert result.missing_provenance_count == 1
        assert result.graph.node_count == 1


class TestPlannerHopTrace:
    """R14.12: Record per-hop trace detail."""

    def test_trace_contains_required_fields(self) -> None:
        config = _make_config(max_hops=2)
        planner = EvidencePlanner(config)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            return _make_chunks(2, prefix=f"trace{call_count}")

        result = planner.plan("my question", frozenset([_make_version()]), retriever)

        assert len(result.hop_traces) >= 1
        trace = result.hop_traces[0]
        assert trace.hop_number == 1
        assert trace.search_query == "my question"
        assert trace.nodes_added == 2
        assert isinstance(trace.gain, float)
        assert isinstance(trace.cost, float)
        assert trace.decision in (HaltDecision.CONTINUE, HaltDecision.HALT)
        assert isinstance(trace.elapsed_seconds, float)
        assert trace.elapsed_seconds >= 0.0

    def test_trace_records_all_hops(self) -> None:
        config = _make_config(max_hops=3)
        planner = EvidencePlanner(config)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            return _make_chunks(2, prefix=f"h{call_count}")

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        # max_hops = 3, should have traces for all executed hops
        hop_numbers = [t.hop_number for t in result.hop_traces]
        assert hop_numbers == sorted(hop_numbers)
        assert hop_numbers[0] == 1


class TestPlannerHalterIntegration:
    """R14.5: Call GainCostHalter after every hop."""

    def test_halter_observe_called_per_hop(self) -> None:
        config = _make_config(max_hops=3, evidence_time_budget_seconds=100.0)
        halter = GainCostHalter(tau=1.0, l_min=1, oscillation_patience=2)
        planner = EvidencePlanner(config, halter=halter)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            return _make_chunks(2, prefix=f"obs{call_count}")

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        # halter.iterations_done should reflect number of hops where observe was called
        assert halter.iterations_done >= 1


class TestPlannerDAGProperty:
    """R14.1: Evidence graph is a DAG (directed acyclic graph)."""

    def test_result_graph_is_dag(self) -> None:
        """After plan(), the graph should not contain any cycles."""
        config = _make_config(max_hops=3)
        planner = EvidencePlanner(config)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            return _make_chunks(5, prefix=f"dag{call_count}")

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        # Verify DAG property: no node can reach itself
        graph = result.graph
        for node_id, node in graph.nodes.items():
            visited: set[str] = set()
            stack = list(node.edges_to)
            while stack:
                current = stack.pop()
                assert current != node_id, f"Cycle detected: {node_id} can reach itself"
                if current not in visited:
                    visited.add(current)
                    next_node = graph.nodes.get(current)
                    if next_node:
                        stack.extend(next_node.edges_to)


class TestPlannerNodeCount:
    """R14.1: node ≤ 60 per request (integration)."""

    def test_total_nodes_within_limit(self) -> None:
        config = _make_config(max_hops=5, max_nodes_per_request=10, max_nodes_per_hop=5)
        planner = EvidencePlanner(config)

        call_count = 0

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            nonlocal call_count
            call_count += 1
            # Return more than allowed
            return _make_chunks(20, prefix=f"over{call_count}")

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        assert result.graph.node_count <= 10


class TestPlanResult:
    """Test PlanResult structure."""

    def test_plan_result_fields(self) -> None:
        config = _make_config(max_hops=1)
        planner = EvidencePlanner(config)

        def retriever(query: str, versions: frozenset[CurriculumVersion]) -> list[RetrievedChunk]:
            return _make_chunks(2)

        result = planner.plan("q", frozenset([_make_version()]), retriever)
        assert isinstance(result, PlanResult)
        assert isinstance(result.graph, EvidenceGraph)
        assert isinstance(result.halt_reason, HaltReason)
        assert isinstance(result.hop_traces, tuple)
        assert isinstance(result.version_filtered_count, int)
        assert isinstance(result.missing_provenance_count, int)
        assert isinstance(result.cycle_rejected_count, int)
