"""Evidence Planner — สร้าง evidence graph แบบ DAG ด้วย multi-hop expansion.

ออกแบบตาม R14.1-R14.12:
- Evidence graph เป็น DAG ที่ node ≤ 60 ต่อคำขอ (R14.1)
- ≤ 10 node ต่อ hop (R14.2)
- hop ≤ max_hops (R14.3)
- Node ที่ขาด provenance → ปฏิเสธ + บันทึก missing_provenance (R14.4)
- เรียก GainCostHalter หลังทุก hop (R14.5)
- Edge ที่ทำให้เกิด cycle → ปฏิเสธ + บันทึก cycle_rejected (R14.6)
- Halt เมื่อ hop เพิ่ม 0 node ใหม่ (R14.7)
- Halt เมื่อ halter คืน nan_guard (R14.8)
- Halt เมื่อเกิน time budget (R14.9)
- กรอง node นอกชุดเวอร์ชัน + บันทึก version_filtered (R14.10)
- Halt เมื่อถึง max_hops (R14.11)
- บันทึก per-hop trace (R14.12)
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from katrag.common.halter import GainCostHalter
from katrag.common.types import (
    CurriculumVersion,
    HaltDecision,
    HaltReason,
    HaltVerdict,
    Provenance,
)
from katrag.config import EvidenceConfig


# ── Data types ────────────────────────────────────────────────────────


@dataclass(slots=True)
class EvidenceNode:
    """โหนดในกราฟหลักฐาน — แต่ละ node ต้องมี provenance ครบ."""

    node_id: str
    chunk_id: str
    text: str
    provenance: Provenance
    version: CurriculumVersion
    edges_to: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HopTrace:
    """รายละเอียดของแต่ละ hop สำหรับ query_trace (R14.12)."""

    hop_number: int
    search_query: str
    nodes_added: int
    gain: float
    cost: float
    decision: str
    reason: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class PlanResult:
    """ผลลัพธ์สุดท้ายจาก EvidencePlanner.plan()."""

    graph: EvidenceGraph
    halt_reason: HaltReason | None
    hop_traces: tuple[HopTrace, ...]
    version_filtered_count: int
    missing_provenance_count: int
    cycle_rejected_count: int


# ── Retriever chunk protocol ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """ผลลัพธ์จาก retriever — ผู้เรียกต้องแปลงให้เป็นรูปแบบนี้."""

    chunk_id: str
    text: str
    provenance: Provenance | None
    version: CurriculumVersion | None


# Type alias สำหรับ retriever callable
RetrieverFn = Callable[[str, frozenset[CurriculumVersion]], Sequence[RetrievedChunk]]


# ── Evidence Graph (DAG) ──────────────────────────────────────────────


class EvidenceGraph:
    """Evidence graph ที่บังคับ DAG invariant, provenance, และ node limit.

    - node ≤ max_nodes_per_request (R14.1)
    - ≤ max_nodes_per_hop node ต่อ hop (R14.2)
    - ปฏิเสธ node ที่ขาด provenance (R14.4)
    - ปฏิเสธ edge ที่ทำให้เกิด cycle (R14.6)
    - กรอง node นอกชุดเวอร์ชัน (R14.10)
    """

    __slots__ = (
        "_nodes",
        "_max_nodes",
        "_max_per_hop",
        "_allowed_versions",
        "_missing_provenance",
        "_cycle_rejected",
        "_version_filtered",
    )

    def __init__(
        self,
        max_nodes: int = 60,
        max_per_hop: int = 10,
        allowed_versions: frozenset[CurriculumVersion] | None = None,
    ) -> None:
        self._nodes: dict[str, EvidenceNode] = {}
        self._max_nodes = max_nodes
        self._max_per_hop = max_per_hop
        self._allowed_versions = allowed_versions
        self._missing_provenance: int = 0
        self._cycle_rejected: int = 0
        self._version_filtered: int = 0

    # ── properties ────────────────────────────────────────────────────

    @property
    def nodes(self) -> dict[str, EvidenceNode]:
        return self._nodes

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def missing_provenance(self) -> int:
        return self._missing_provenance

    @property
    def cycle_rejected(self) -> int:
        return self._cycle_rejected

    @property
    def version_filtered(self) -> int:
        return self._version_filtered

    @property
    def is_full(self) -> bool:
        return len(self._nodes) >= self._max_nodes

    # ── node operations ───────────────────────────────────────────────

    def add_node(self, node: EvidenceNode) -> bool:
        """เพิ่ม node — คืน True ถ้าสำเร็จ, False ถ้าถูกปฏิเสธ.

        ปฏิเสธเมื่อ:
        1. provenance ไม่ครบ (R14.4)
        2. node อยู่นอกชุดเวอร์ชันที่อนุญาต (R14.10)
        3. graph เต็มแล้ว (R14.1)
        4. node ซ้ำ (node_id already exists)
        """
        # ตรวจ provenance (R14.4)
        if node.provenance is None or not node.provenance.is_complete():
            self._missing_provenance += 1
            return False

        # ตรวจ version filter (R14.10)
        if self._allowed_versions is not None and node.version not in self._allowed_versions:
            self._version_filtered += 1
            return False

        # ตรวจ node limit (R14.1)
        if self.is_full:
            return False

        # ตรวจซ้ำ
        if node.node_id in self._nodes:
            return False

        self._nodes[node.node_id] = node
        return True

    def add_edge(self, from_id: str, to_id: str) -> bool:
        """เพิ่ม edge — คืน True ถ้าสำเร็จ.

        ปฏิเสธเมื่อ:
        1. node ต้นทางหรือปลายทางไม่อยู่ใน graph
        2. edge จะทำให้เกิด cycle (R14.6)
        """
        if from_id not in self._nodes or to_id not in self._nodes:
            return False

        # ตรวจว่า edge นี้จะทำให้เกิด cycle ไหม
        if self._would_create_cycle(from_id, to_id):
            self._cycle_rejected += 1
            return False

        self._nodes[from_id].edges_to.append(to_id)
        return True

    def _would_create_cycle(self, from_id: str, to_id: str) -> bool:
        """ตรวจว่าถ้าเพิ่ม edge from_id → to_id จะเกิด cycle หรือไม่.

        Cycle เกิดเมื่อ to_id สามารถเดินทางไปถึง from_id ได้ (ทำให้เป็น back-edge)
        """
        # Self-loop
        if from_id == to_id:
            return True

        # BFS/DFS จาก to_id ดูว่าเข้าถึง from_id ได้ไหม
        visited: set[str] = set()
        stack = [to_id]
        while stack:
            current = stack.pop()
            if current == from_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            node = self._nodes.get(current)
            if node:
                stack.extend(node.edges_to)
        return False

    def contains_chunk(self, chunk_id: str) -> bool:
        """ตรวจว่า chunk_id นี้อยู่ใน graph แล้วหรือยัง."""
        return any(n.chunk_id == chunk_id for n in self._nodes.values())


# ── Evidence Planner ──────────────────────────────────────────────────


class EvidencePlanner:
    """Orchestrator สำหรับ multi-hop evidence expansion.

    ทุก hop:
    1. เรียก retriever เพื่อค้น chunk ที่เกี่ยวข้อง
    2. กรอง version + provenance + node limit
    3. เพิ่ม node เข้า graph
    4. เรียก halter ตัดสินว่าจะทำ hop ถัดไปหรือไม่
    5. บันทึก hop trace (R14.12)
    """

    __slots__ = ("_config", "_halter")

    def __init__(self, config: EvidenceConfig, halter: GainCostHalter | None = None) -> None:
        self._config = config
        self._halter = halter or GainCostHalter(tau=1.0, l_min=1, oscillation_patience=2)

    def plan(
        self,
        question: str,
        versions: frozenset[CurriculumVersion],
        retriever: RetrieverFn,
    ) -> PlanResult:
        """รัน multi-hop expansion และคืน PlanResult.

        Args:
            question: คำถามจากผู้ใช้
            versions: ชุดเวอร์ชันที่อนุญาต
            retriever: callable(query, version_filter) -> list[RetrievedChunk]

        Returns:
            PlanResult พร้อม graph, halt_reason, traces, และสถิติ
        """
        graph = EvidenceGraph(
            max_nodes=self._config.max_nodes_per_request,
            max_per_hop=self._config.max_nodes_per_hop,
            allowed_versions=versions,
        )

        self._halter.reset()
        hop_traces: list[HopTrace] = []
        halt_reason: HaltReason | None = None
        plan_start = time.monotonic()

        for hop_number in range(1, self._config.max_hops + 1):
            hop_start = time.monotonic()

            # ตรวจ time budget ก่อนเริ่ม hop (R14.9)
            elapsed_total = time.monotonic() - plan_start
            if elapsed_total >= self._config.evidence_time_budget_seconds:
                halt_reason = HaltReason.TIME_BUDGET_EXCEEDED
                # บันทึก trace สำหรับ hop ที่ไม่ได้ทำ
                hop_traces.append(HopTrace(
                    hop_number=hop_number,
                    search_query=question,
                    nodes_added=0,
                    gain=0.0,
                    cost=0.0,
                    decision=HaltDecision.HALT,
                    reason=HaltReason.TIME_BUDGET_EXCEEDED,
                    elapsed_seconds=time.monotonic() - hop_start,
                ))
                break

            # สร้าง search query (hop แรกใช้คำถามเดิม, hop ถัดไปอาจขยาย)
            search_query = self._build_query(question, hop_number, graph)

            # เรียก retriever
            chunks = retriever(search_query, versions)

            # เพิ่ม node เข้า graph (จำกัด per hop)
            nodes_added = 0
            for chunk in chunks:
                if nodes_added >= self._config.max_nodes_per_hop:
                    break
                if graph.is_full:
                    break

                # ข้าม chunk ที่อยู่ใน graph แล้ว
                if graph.contains_chunk(chunk.chunk_id):
                    continue

                node = EvidenceNode(
                    node_id=_generate_node_id(),
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    provenance=chunk.provenance,  # type: ignore[arg-type]
                    version=chunk.version,  # type: ignore[arg-type]
                    edges_to=[],
                )

                if graph.add_node(node):
                    nodes_added += 1

            hop_elapsed = time.monotonic() - hop_start

            # ตรวจ no_new_evidence (R14.7)
            if nodes_added == 0:
                halt_reason = HaltReason.NO_NEW_EVIDENCE
                hop_traces.append(HopTrace(
                    hop_number=hop_number,
                    search_query=search_query,
                    nodes_added=0,
                    gain=0.0,
                    cost=0.0,
                    decision=HaltDecision.HALT,
                    reason=HaltReason.NO_NEW_EVIDENCE,
                    elapsed_seconds=hop_elapsed,
                ))
                break

            # คำนวณ gain = new evidence coverage
            gain = nodes_added / self._config.max_nodes_per_request

            # เรียก halter (R14.5)
            verdict = self._halter.observe(
                score=graph.node_count / self._config.max_nodes_per_request,
                elapsed_s=hop_elapsed,
                budget_s=self._config.evidence_time_budget_seconds,
            )

            # ตรวจ time budget หลัง hop (R14.9)
            elapsed_total = time.monotonic() - plan_start
            if elapsed_total >= self._config.evidence_time_budget_seconds:
                halt_reason = HaltReason.TIME_BUDGET_EXCEEDED
                hop_traces.append(HopTrace(
                    hop_number=hop_number,
                    search_query=search_query,
                    nodes_added=nodes_added,
                    gain=verdict.gain,
                    cost=verdict.cost,
                    decision=HaltDecision.HALT,
                    reason=HaltReason.TIME_BUDGET_EXCEEDED,
                    elapsed_seconds=hop_elapsed,
                ))
                break

            # ตัดสินจาก halter
            if verdict.should_halt:
                # nan_guard จาก halter (R14.8)
                halt_reason = verdict.reason
                hop_traces.append(HopTrace(
                    hop_number=hop_number,
                    search_query=search_query,
                    nodes_added=nodes_added,
                    gain=verdict.gain,
                    cost=verdict.cost,
                    decision=HaltDecision.HALT,
                    reason=verdict.reason.value if verdict.reason else "unknown",
                    elapsed_seconds=hop_elapsed,
                ))
                break

            # บันทึก trace สำหรับ hop ที่ทำสำเร็จ (R14.12)
            hop_traces.append(HopTrace(
                hop_number=hop_number,
                search_query=search_query,
                nodes_added=nodes_added,
                gain=verdict.gain,
                cost=verdict.cost,
                decision=HaltDecision.CONTINUE,
                reason="",
                elapsed_seconds=hop_elapsed,
            ))

            # ตรวจว่าถึง max_hops หรือยัง (R14.11)
            if hop_number >= self._config.max_hops:
                halt_reason = HaltReason.MAX_HOPS_REACHED
                break

            # ตรวจว่า graph เต็มแล้วหรือยัง
            if graph.is_full:
                halt_reason = HaltReason.MAX_HOPS_REACHED
                break

        # ถ้าออก loop โดยไม่มี halt_reason (ครบ max_hops)
        if halt_reason is None:
            halt_reason = HaltReason.MAX_HOPS_REACHED

        return PlanResult(
            graph=graph,
            halt_reason=halt_reason,
            hop_traces=tuple(hop_traces),
            version_filtered_count=graph.version_filtered,
            missing_provenance_count=graph.missing_provenance,
            cycle_rejected_count=graph.cycle_rejected,
        )

    def _build_query(self, question: str, hop_number: int, graph: EvidenceGraph) -> str:
        """สร้าง search query สำหรับ hop — hop แรกใช้คำถามเดิม."""
        # Multi-hop expansion: hop แรกใช้คำถามตรง ๆ
        # hop ถัดไป อาจขยายจากข้อความใน graph (simplified: ใช้คำถามเดิม)
        return question


# ── Helpers ───────────────────────────────────────────────────────────


def _generate_node_id() -> str:
    """สร้าง unique node ID."""
    return f"ev-{uuid.uuid4().hex[:12]}"
