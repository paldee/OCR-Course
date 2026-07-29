"""Property tests ของ evidence graph และ routing (task 17.4).

คุณสมบัติที่ทดสอบ:
1. DAG invariant: กราฟไม่มี cycle หลังลำดับ add_node/add_edge ใด ๆ (R14.1, R14.6)
2. Node/hop bounds: node_count ≤ max_nodes_per_request AND nodes_added ≤ max_nodes_per_hop (R14.1, R14.3)
3. Version isolation: ทุก node สังกัดชุดเวอร์ชันของคำขอ (R14.10)
4. Classification monotonicity: เพิ่ม L4 signal → ระดับไม่ลดลง (R16.1)

Requirements: 14.1, 14.3, 14.10, 14.11, 16.1
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from katrag.common.halter import GainCostHalter
from katrag.common.types import BBox, CurriculumVersion, HaltReason, Provenance
from katrag.config import EvidenceConfig, QuestionRouterConfig
from katrag.query.evidence_planner import (
    EvidenceGraph,
    EvidenceNode,
    EvidencePlanner,
    PlanResult,
    RetrievedChunk,
)
from katrag.query.question_router import ClassificationResult, classify_question

PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)


# ── strategies ────────────────────────────────────────────────────────

_PROGRAMS = ("IT", "CS", "SE", "DS")
_YEARS = (2560, 2563, 2565, 2567)


def _version_st() -> st.SearchStrategy[CurriculumVersion]:
    """สร้าง CurriculumVersion แบบสุ่ม."""
    return st.builds(
        CurriculumVersion,
        program=st.sampled_from(_PROGRAMS),
        curriculum_year=st.sampled_from(_YEARS),
        edition_status=st.sampled_from(("old", "current")),
    )


def _bbox_st() -> st.SearchStrategy[BBox]:
    """สร้าง valid BBox."""
    return st.builds(
        BBox,
        x0=st.floats(min_value=0.0, max_value=400.0),
        y0=st.floats(min_value=0.0, max_value=700.0),
        x1=st.floats(min_value=401.0, max_value=600.0),
        y1=st.floats(min_value=701.0, max_value=900.0),
    )


def _provenance_st() -> st.SearchStrategy[Provenance]:
    """สร้าง complete Provenance."""
    return st.builds(
        Provenance,
        document_id=st.text(min_size=1, max_size=10, alphabet="abcdef0123456789"),
        page=st.integers(min_value=1, max_value=100),
        bbox=_bbox_st(),
        span=st.tuples(
            st.integers(min_value=0, max_value=500),
            st.integers(min_value=501, max_value=1000),
        ),
        extraction_method=st.sampled_from(
            ("text_layer", "ocr_tesseract", "ocr_typhoon", "ocr_adjudicated")
        ),
    )


def _node_st(
    allowed_versions: frozenset[CurriculumVersion] | None = None,
) -> st.SearchStrategy[EvidenceNode]:
    """สร้าง EvidenceNode ที่มี provenance ครบและอยู่ในชุดเวอร์ชัน."""
    version_strategy = (
        st.sampled_from(sorted(allowed_versions, key=lambda v: v.key()))
        if allowed_versions
        else _version_st()
    )
    return st.builds(
        EvidenceNode,
        node_id=st.from_regex(r"ev-[a-f0-9]{12}", fullmatch=True),
        chunk_id=st.text(min_size=5, max_size=15, alphabet="abcdef0123456789"),
        text=st.text(min_size=1, max_size=50),
        provenance=_provenance_st(),
        version=version_strategy,
        edges_to=st.just([]),
    )


def _evidence_config_st() -> st.SearchStrategy[EvidenceConfig]:
    """สร้าง EvidenceConfig ที่มีค่าอยู่ในช่วงที่สมเหตุสมผล."""
    return st.builds(
        EvidenceConfig,
        max_hops=st.integers(min_value=1, max_value=5),
        max_nodes_per_request=st.integers(min_value=5, max_value=60),
        max_nodes_per_hop=st.integers(min_value=2, max_value=10),
        evidence_time_budget_seconds=st.floats(min_value=5.0, max_value=60.0),
    )


# ── Property 1: DAG invariant ────────────────────────────────────────


@given(
    max_nodes=st.integers(min_value=5, max_value=30),
    node_count=st.integers(min_value=3, max_value=15),
    edge_attempts=st.lists(
        st.tuples(st.integers(min_value=0, max_value=14), st.integers(min_value=0, max_value=14)),
        min_size=1,
        max_size=30,
    ),
    data=st.data(),
)
@PROPERTY_SETTINGS
def test_dag_invariant_no_cycle_after_random_operations(
    max_nodes: int,
    node_count: int,
    edge_attempts: list[tuple[int, int]],
    data: st.DataObject,
) -> None:
    """กราฟไม่มี cycle หลังลำดับ add_node/add_edge แบบสุ่ม (R14.6).

    ทดสอบโดย:
    1. สุ่มเพิ่ม node จำนวน node_count ตัว
    2. สุ่มพยายามเพิ่ม edge หลายรอบ
    3. ตรวจว่าไม่มี node ใดเดินถึงตัวเองได้ (no self-reachability)
    """
    versions = frozenset({
        CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current"),
    })
    graph = EvidenceGraph(max_nodes=max_nodes, max_per_hop=10, allowed_versions=versions)

    # เพิ่ม node
    actual_count = min(node_count, max_nodes)
    node_ids: list[str] = []
    for i in range(actual_count):
        node = data.draw(_node_st(allowed_versions=versions))
        # ทำให้ node_id ไม่ซ้ำกัน
        unique_node = EvidenceNode(
            node_id=f"ev-{uuid.uuid4().hex[:12]}",
            chunk_id=f"chunk-{i}-{uuid.uuid4().hex[:6]}",
            text=node.text,
            provenance=node.provenance,
            version=node.version,
            edges_to=[],
        )
        if graph.add_node(unique_node):
            node_ids.append(unique_node.node_id)

    assume(len(node_ids) >= 2)

    # สุ่มเพิ่ม edge
    for from_idx, to_idx in edge_attempts:
        if from_idx < len(node_ids) and to_idx < len(node_ids):
            graph.add_edge(node_ids[from_idx], node_ids[to_idx])

    # ตรวจ DAG: ไม่มี node ใดเดินถึงตัวเอง
    for node_id in node_ids:
        visited: set[str] = set()
        stack = list(graph.nodes[node_id].edges_to)
        reachable_self = False
        while stack:
            current = stack.pop()
            if current == node_id:
                reachable_self = True
                break
            if current in visited:
                continue
            visited.add(current)
            if current in graph.nodes:
                stack.extend(graph.nodes[current].edges_to)
        assert not reachable_self, (
            f"พบ cycle: node {node_id} สามารถเดินถึงตัวเองได้"
        )


# ── Property 2: Node/hop bounds ──────────────────────────────────────


@given(
    config=_evidence_config_st(),
    num_chunks_per_hop=st.integers(min_value=1, max_value=20),
    data=st.data(),
)
@PROPERTY_SETTINGS
def test_node_and_hop_bounds_after_plan(
    config: EvidenceConfig,
    num_chunks_per_hop: int,
    data: st.DataObject,
) -> None:
    """หลัง plan(): node_count ≤ max_nodes_per_request AND แต่ละ hop nodes_added ≤ max_nodes_per_hop (R14.1, R14.3).

    ทดสอบโดยสร้าง fake retriever ที่คืน chunk จำนวนมาก
    แล้วตรวจว่า planner บังคับเพดานได้ถูกต้อง
    """
    versions = frozenset({
        CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current"),
    })

    # สร้าง chunk pool ขนาดใหญ่
    chunk_pool: list[RetrievedChunk] = []
    for i in range(config.max_nodes_per_request + 20):
        prov = data.draw(_provenance_st())
        chunk_pool.append(RetrievedChunk(
            chunk_id=f"chunk-{i}-{uuid.uuid4().hex[:6]}",
            text=f"content {i}",
            provenance=prov,
            version=CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current"),
        ))

    call_count = [0]

    def fake_retriever(
        query: str, version_filter: frozenset[CurriculumVersion]
    ) -> Sequence[RetrievedChunk]:
        """คืน chunk ที่ยังไม่ซ้ำ ทีละ num_chunks_per_hop ชิ้น."""
        start = call_count[0] * num_chunks_per_hop
        end = start + num_chunks_per_hop
        call_count[0] += 1
        return chunk_pool[start:end]

    halter = GainCostHalter(tau=0.001, l_min=1, oscillation_patience=10)
    planner = EvidencePlanner(config=config, halter=halter)
    result: PlanResult = planner.plan(
        question="วิชาอะไรบ้างในปี 2565",
        versions=versions,
        retriever=fake_retriever,
    )

    # Property: node_count ≤ max_nodes_per_request
    assert result.graph.node_count <= config.max_nodes_per_request, (
        f"node_count={result.graph.node_count} เกิน max_nodes_per_request={config.max_nodes_per_request}"
    )

    # Property: แต่ละ hop nodes_added ≤ max_nodes_per_hop
    for trace in result.hop_traces:
        assert trace.nodes_added <= config.max_nodes_per_hop, (
            f"hop {trace.hop_number}: nodes_added={trace.nodes_added} "
            f"เกิน max_nodes_per_hop={config.max_nodes_per_hop}"
        )


# ── Property 3: Version isolation ────────────────────────────────────


@given(
    config=_evidence_config_st(),
    data=st.data(),
)
@PROPERTY_SETTINGS
def test_version_isolation_all_nodes_in_allowed_set(
    config: EvidenceConfig,
    data: st.DataObject,
) -> None:
    """ทุก node ใน graph สุดท้ายต้องสังกัดชุดเวอร์ชันที่อนุญาต (R14.10).

    ทดสอบโดย retriever คืน chunk ที่มีทั้ง version ที่อนุญาตและไม่อนุญาต
    แล้วตรวจว่า node ใน graph สุดท้ายมีเฉพาะ version ที่อนุญาตเท่านั้น
    """
    allowed_versions = frozenset({
        CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current"),
        CurriculumVersion(program="CS", curriculum_year=2563, edition_status="current"),
    })

    # Versions ที่ไม่อนุญาต
    disallowed_versions = [
        CurriculumVersion(program="SE", curriculum_year=2560, edition_status="old"),
        CurriculumVersion(program="DS", curriculum_year=2567, edition_status="current"),
    ]

    # สร้าง chunk pool ผสมทั้ง allowed และ disallowed
    chunk_pool: list[RetrievedChunk] = []
    all_versions = list(allowed_versions) + disallowed_versions
    for i in range(config.max_nodes_per_request + 10):
        prov = data.draw(_provenance_st())
        version = all_versions[i % len(all_versions)]
        chunk_pool.append(RetrievedChunk(
            chunk_id=f"chunk-{i}-{uuid.uuid4().hex[:6]}",
            text=f"content version isolation {i}",
            provenance=prov,
            version=version,
        ))

    call_count = [0]

    def mixed_retriever(
        query: str, version_filter: frozenset[CurriculumVersion]
    ) -> Sequence[RetrievedChunk]:
        start = call_count[0] * config.max_nodes_per_hop
        end = start + config.max_nodes_per_hop + 5  # คืนเกินเพื่อทดสอบการกรอง
        call_count[0] += 1
        return chunk_pool[start:end]

    halter = GainCostHalter(tau=0.001, l_min=1, oscillation_patience=10)
    planner = EvidencePlanner(config=config, halter=halter)
    result: PlanResult = planner.plan(
        question="เปรียบเทียบหลักสูตร",
        versions=allowed_versions,
        retriever=mixed_retriever,
    )

    # Property: ทุก node ต้องอยู่ในชุดเวอร์ชันที่อนุญาต
    for node_id, node in result.graph.nodes.items():
        assert node.version in allowed_versions, (
            f"node {node_id} มี version={node.version} ซึ่งไม่อยู่ใน allowed_versions"
        )

    # ยืนยันว่ามี node ที่ถูกกรองออกจริง (version_filtered > 0)
    # (ถ้า retriever คืนแต่ allowed version อาจ = 0 ได้ แต่ส่วนใหญ่ > 0)
    # ไม่ assert เพราะอาจเกิดกรณีที่ chunk pool หมดก่อน


# ── Property 4: Classification monotonicity ──────────────────────────


# ลำดับระดับ: L1 < L2 < L3 < L4
_LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}

# Keyword ที่เป็นสัญญาณ L4 (comparison)
_L4_SIGNAL_KEYWORDS = [
    "เปรียบเทียบ", "ต่างกัน", "เหมือนกัน", "แตกต่าง",
]

# Default QuestionRouterConfig สำหรับทดสอบ
_TEST_ROUTER_CONFIG = QuestionRouterConfig(
    max_question_chars=500,
    api_max_question_chars=500,
    retriever_max_question_chars=500,
    min_confidence=0.50,
    classification_budget_ms=200,
    structured_path_budget_ms=1000,
    max_route_escalations=1,
)


@given(
    base_question=st.sampled_from([
        "วิชา 06016401 มีกี่หน่วยกิต",
        "ปีที่ 1 ภาค 1 มีวิชาอะไรบ้าง",
        "รายวิชาทั้งหมดในหลักสูตร",
        "วิชาบังคับปีที่ 2",
    ]),
    l4_keyword=st.sampled_from(_L4_SIGNAL_KEYWORDS),
    version_refs=st.sampled_from([
        "2560 กับ 2565",
        "หลักสูตร 2563 และ 2567",
    ]),
)
@PROPERTY_SETTINGS
def test_classification_monotonicity_adding_signal_does_not_decrease_level(
    base_question: str,
    l4_keyword: str,
    version_refs: str,
) -> None:
    """เพิ่ม L4 signal keywords ลงในคำถาม L2 → ระดับต้องไม่ลดลง (R16.1).

    Specifically: คำถาม + comparison keyword + version references → L3 หรือ L4
    """
    # จำแนกคำถามฐาน
    base_result = classify_question(base_question, _TEST_ROUTER_CONFIG)
    base_level_order = _LEVEL_ORDER[base_result.level]

    # เพิ่ม L4 signal (comparison keyword + multiple version refs)
    enhanced_question = f"{base_question} {l4_keyword} {version_refs}"
    enhanced_result = classify_question(enhanced_question, _TEST_ROUTER_CONFIG)
    enhanced_level_order = _LEVEL_ORDER[enhanced_result.level]

    # Property: ระดับต้องไม่ลดลงเมื่อเพิ่ม signal
    assert enhanced_level_order >= base_level_order, (
        f"ระดับลดลง: base='{base_question}' → {base_result.level}, "
        f"enhanced='{enhanced_question}' → {enhanced_result.level}"
    )

    # เพิ่มเติม: เมื่อมี comparison keyword + version refs → ต้องเป็น L3 หรือ L4
    assert enhanced_level_order >= 3, (
        f"คำถามที่มี comparison keyword + version refs ควรเป็น L3/L4 "
        f"แต่ได้ {enhanced_result.level}: '{enhanced_question}'"
    )
