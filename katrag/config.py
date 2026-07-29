"""KatragConfig — โหลดและตรวจไฟล์ตั้งค่าทั้งสี่ไฟล์ครั้งเดียวต่อ process.

ทุกค่าที่ requirements ระบุว่า "อ่านจากไฟล์ตั้งค่า" ต้องมาจากที่นี่เท่านั้น
ห้ามฮาร์ดโค้ดค่าเหล่านั้นในโค้ดส่วนอื่น

Validation บังคับช่วงค่าที่ requirements กำหนดไว้อย่างชัดเจน:
- max_hops 1-5 (R14.3)
- rerank_depth 20-40 (R13.6)
- phrase_boost_multiplier 1.00-3.00 (R13.5)
- answer_time_budget_seconds 10-180 (R17.1)
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from katrag.errors import ConfigError

CONFIG_DIR_NAME = "config"
KATRAG_TOML = "katrag.toml"
VALUE_SETS_TOML = "value_sets.toml"
ENGINES_TOML = "engines.toml"
DOMAIN_LEXICON_TOML = "domain_lexicon.toml"


# ── section dataclasses ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HaltConfig:
    tau: float
    l_min: int
    oscillation_patience: int


@dataclass(frozen=True, slots=True)
class TyphoonConfig:
    """ค่าตั้งค่าของ stage 2 (Typhoon-OCR-1.5-2B) — GPU-gated (R5.1.1, R5.1.3, R20.7)."""

    model_id: str
    max_new_tokens: int
    repetition_penalty: float
    no_repeat_ngram_size: int
    image_max_dimension_px: int
    known_institution_name: str
    require_cuda: bool


@dataclass(frozen=True, slots=True)
class StageTimeoutConfig:
    """Per-engine hard wall-clock timeout ต่อ region (R5.6 revised)."""

    tesseract5: float
    typhoon_ocr1_5_2b: float

    def for_engine(self, engine_name: str) -> float:
        """คืน timeout สำหรับ engine ที่ระบุ — raise KeyError ถ้าไม่รู้จัก."""
        mapping = {
            "tesseract5": self.tesseract5,
            "typhoon_ocr1_5_2b": self.typhoon_ocr1_5_2b,
        }
        if engine_name not in mapping:
            raise KeyError(f"ไม่รู้จัก engine '{engine_name}' ใน stage_timeout config")
        return mapping[engine_name]


@dataclass(frozen=True, slots=True)
class EscalationConfig:
    """Budget/circuit-breaker สำหรับ selective escalation ไป stage 2."""

    max_typhoon_seconds_per_run: float
    max_consecutive_typhoon_failures: int
    min_stage1_quality_for_skip: float


@dataclass(frozen=True, slots=True)
class OcrConfig:
    max_stages_per_region: int
    per_page_time_budget_seconds: float
    crop_cache_max_entries_per_document: int
    stage_order: tuple[str, ...]
    adjudicate_iou_threshold: float
    confidence_tie_epsilon: float
    stage_timeout: StageTimeoutConfig
    escalation: EscalationConfig
    typhoon: TyphoonConfig

    def timeout_for(self, engine_name: str) -> float:
        """Shortcut — คืน hard timeout สำหรับ engine ที่ระบุ."""
        return self.stage_timeout.for_engine(engine_name)


@dataclass(frozen=True, slots=True)
class PreprocessConfig:
    skew_degrees_threshold: float
    min_dpi: int
    contrast_score_threshold: float


@dataclass(frozen=True, slots=True)
class PageQualityConfig:
    weight_extracted_char_count: float
    weight_out_of_charset_ratio: float
    weight_image_area_ratio: float
    weight_domain_lexicon_match_count: float
    low_text_char_threshold: int
    ocr_candidate_budget_pages: int
    char_count_reference: int
    lexicon_match_reference: int


@dataclass(frozen=True, slots=True)
class PageRouteConfig:
    fast_max_image_area_ratio: float
    deep_min_image_area_ratio: float


@dataclass(frozen=True, slots=True)
class ThaiConfig:
    zero_width_max_points: float
    baseline_tolerance_ratio: float
    horizontal_window_ratio: float
    line_baseline_tolerance_ratio: float


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    lexical_top_k: int
    dense_top_k: int
    fusion_output_max: int
    fusion_lexical_weight: float
    fusion_dense_weight: float
    fusion_rrf_k: int
    dense_p95_latency_budget_seconds: float
    phrase_boost_multiplier: float
    rerank_depth: int
    maxsim_enabled: bool
    maxsim_status: str


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    max_hops: int
    max_nodes_per_request: int
    max_nodes_per_hop: int
    evidence_time_budget_seconds: float


@dataclass(frozen=True, slots=True)
class AnswerConfig:
    answer_time_budget_seconds: float
    max_evidence_units: int
    model_path: str
    request_timeout_seconds: float
    max_versions_per_request: int


@dataclass(frozen=True, slots=True)
class QuestionRouterConfig:
    max_question_chars: int
    api_max_question_chars: int
    retriever_max_question_chars: int
    min_confidence: float
    classification_budget_ms: int
    structured_path_budget_ms: int
    max_route_escalations: int


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    limit_bytes: int
    max_resident_page_images: int
    rss_drift_tolerance: float
    rss_baseline_page_index: int


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    min_samples_for_measured: int
    page_cer_threshold: float
    table_cell_f1_threshold: float
    field_macro_f1_threshold: float
    recall_at_10_threshold: float
    citation_precision_threshold: float
    citation_recall_threshold: float
    unsupported_claim_rate_threshold: float
    version_selection_accuracy_threshold: float


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    root: str
    expected_document_count: int
    expected_page_total: int


@dataclass(frozen=True, slots=True)
class PathsConfig:
    sqlite: str
    dataset_manifest: str
    evaluation_report: str
    gt_normalized_dir: str
    teacher_gt_dir: str
    gold_set_dir: str


@dataclass(frozen=True, slots=True)
class ApiConfig:
    host: str
    port: int
    max_documents_per_response: int


@dataclass(frozen=True, slots=True)
class ValueSets:
    """ชุดค่าปิดจาก value_sets.toml — ใช้ตรวจค่าก่อนเขียนลง store."""

    course_category: frozenset[str]
    course_type: frozenset[str]
    extraction_method: frozenset[str]
    provenance_source: frozenset[str]
    edition_status: frozenset[str]
    degree_level: frozenset[str]
    compute_path: frozenset[str]
    question_level: frozenset[str]
    metric_status: frozenset[str]
    reference_source: frozenset[str]
    page_status: frozenset[str]
    review_issue_kind: frozenset[str]
    halt_reason: frozenset[str]
    category_synonym: Mapping[str, str]

    def canonical_category(self, raw: str) -> str:
        """map ชื่อหมวดที่เป็นคำพ้องให้เป็นค่า canonical (R11.9)."""
        return self.category_synonym.get(raw, raw)


@dataclass(frozen=True, slots=True)
class KatragConfig:
    """ค่าตั้งค่าทั้งหมดของ process (frozen)."""

    project_root: Path
    halt: HaltConfig
    ocr: OcrConfig
    preprocess: PreprocessConfig
    page_quality: PageQualityConfig
    page_route: PageRouteConfig
    thai: ThaiConfig
    retrieval: RetrievalConfig
    evidence: EvidenceConfig
    answer: AnswerConfig
    question_router: QuestionRouterConfig
    memory: MemoryConfig
    evaluation: EvaluationConfig
    dataset: DatasetConfig
    paths: PathsConfig
    api: ApiConfig
    value_sets: ValueSets
    engines: Mapping[str, Any]
    domain_lexicon: Mapping[str, Any]

    # ── path helpers ──────────────────────────────────────────────────

    def resolve(self, relative: str) -> Path:
        """คืน absolute path เทียบจาก project root."""
        return (self.project_root / relative).resolve()

    @property
    def dataset_root(self) -> Path:
        return self.resolve(self.dataset.root)

    @property
    def sqlite_path(self) -> Path:
        return self.resolve(self.paths.sqlite)


# ── loader helpers ────────────────────────────────────────────────────


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError("ไม่พบไฟล์ตั้งค่า", path=str(path))
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - รูปแบบไฟล์เสีย
        raise ConfigError("อ่านไฟล์ตั้งค่าไม่สำเร็จ", path=str(path), reason=str(exc)) from exc


def _section(data: Mapping[str, Any], name: str, path: Path) -> Mapping[str, Any]:
    node: Any = data
    for part in name.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise ConfigError("ไฟล์ตั้งค่าขาดหัวข้อที่จำเป็น", section=name, path=str(path))
        node = node[part]
    if not isinstance(node, Mapping):
        raise ConfigError("หัวข้อในไฟล์ตั้งค่าต้องเป็นตาราง", section=name, path=str(path))
    return node


def _get(section: Mapping[str, Any], key: str, kind: type, section_name: str) -> Any:
    if key not in section:
        raise ConfigError("ค่าตั้งค่าที่จำเป็นขาดไป", section=section_name, key=key)
    value = section[key]
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if kind is bool:
        if not isinstance(value, bool):
            raise ConfigError("ชนิดค่าตั้งค่าไม่ถูกต้อง", section=section_name, key=key, expected="bool")
        return value
    if not isinstance(value, kind) or isinstance(value, bool) is not (kind is bool):
        raise ConfigError(
            "ชนิดค่าตั้งค่าไม่ถูกต้อง",
            section=section_name,
            key=key,
            expected=kind.__name__,
            actual=type(value).__name__,
        )
    return value


def _require_range(
    value: float,
    *,
    section: str,
    key: str,
    minimum: float,
    maximum: float,
) -> None:
    if not minimum <= value <= maximum:
        raise ConfigError(
            "ค่าตั้งค่าอยู่นอกช่วงที่ข้อกำหนดอนุญาต",
            section=section,
            key=key,
            value=value,
            allowed_min=minimum,
            allowed_max=maximum,
        )


def _str_tuple(section: Mapping[str, Any], key: str, section_name: str) -> tuple[str, ...]:
    raw = section.get(key)
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigError("ค่าตั้งค่าต้องเป็นรายการของสตริง", section=section_name, key=key)
    return tuple(raw)


def _str_frozenset(data: Mapping[str, Any], key: str, path: Path) -> frozenset[str]:
    raw = data.get(key)
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigError("ชุดค่าปิดต้องเป็นรายการของสตริง", key=key, path=str(path))
    if not raw:
        raise ConfigError("ชุดค่าปิดต้องไม่ว่าง", key=key, path=str(path))
    return frozenset(raw)


# ── public loader ─────────────────────────────────────────────────────


def load_config(project_root: str | Path | None = None) -> KatragConfig:
    """โหลดไฟล์ตั้งค่าทั้งสี่ไฟล์และตรวจช่วงค่า.

    Args:
        project_root: รากของโปรเจกต์ (ค่าเริ่มต้นคือไดเรกทอรีแม่ของ package นี้)

    Raises:
        ConfigError: เมื่อไฟล์ขาด, หัวข้อขาด, ชนิดผิด หรือค่าอยู่นอกช่วงที่กำหนด
    """
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parent.parent
    root = root.resolve()
    config_dir = root / CONFIG_DIR_NAME

    katrag_path = config_dir / KATRAG_TOML
    value_sets_path = config_dir / VALUE_SETS_TOML
    engines_path = config_dir / ENGINES_TOML
    lexicon_path = config_dir / DOMAIN_LEXICON_TOML

    data = _read_toml(katrag_path)
    value_sets_data = _read_toml(value_sets_path)
    engines_data = _read_toml(engines_path)
    lexicon_data = _read_toml(lexicon_path)

    halt_raw = _section(data, "halt", katrag_path)
    halt = HaltConfig(
        tau=_get(halt_raw, "tau", float, "halt"),
        l_min=_get(halt_raw, "l_min", int, "halt"),
        oscillation_patience=_get(halt_raw, "oscillation_patience", int, "halt"),
    )

    ocr_raw = _section(data, "ocr", katrag_path)
    typhoon_raw = _section(data, "ocr.typhoon", katrag_path)
    typhoon = TyphoonConfig(
        model_id=_get(typhoon_raw, "model_id", str, "ocr.typhoon"),
        max_new_tokens=_get(typhoon_raw, "max_new_tokens", int, "ocr.typhoon"),
        repetition_penalty=_get(typhoon_raw, "repetition_penalty", float, "ocr.typhoon"),
        no_repeat_ngram_size=_get(typhoon_raw, "no_repeat_ngram_size", int, "ocr.typhoon"),
        image_max_dimension_px=_get(typhoon_raw, "image_max_dimension_px", int, "ocr.typhoon"),
        known_institution_name=_get(typhoon_raw, "known_institution_name", str, "ocr.typhoon"),
        require_cuda=_get(typhoon_raw, "require_cuda", bool, "ocr.typhoon"),
    )

    stage_timeout_raw = _section(data, "ocr.stage_timeout", katrag_path)
    stage_timeout = StageTimeoutConfig(
        tesseract5=_get(stage_timeout_raw, "tesseract5", float, "ocr.stage_timeout"),
        typhoon_ocr1_5_2b=_get(stage_timeout_raw, "typhoon_ocr1_5_2b", float, "ocr.stage_timeout"),
    )

    escalation_raw = _section(data, "ocr.escalation", katrag_path)
    escalation = EscalationConfig(
        max_typhoon_seconds_per_run=_get(
            escalation_raw, "max_typhoon_seconds_per_run", float, "ocr.escalation"
        ),
        max_consecutive_typhoon_failures=_get(
            escalation_raw, "max_consecutive_typhoon_failures", int, "ocr.escalation"
        ),
        min_stage1_quality_for_skip=_get(
            escalation_raw, "min_stage1_quality_for_skip", float, "ocr.escalation"
        ),
    )

    ocr = OcrConfig(
        max_stages_per_region=_get(ocr_raw, "max_stages_per_region", int, "ocr"),
        per_page_time_budget_seconds=_get(ocr_raw, "per_page_time_budget_seconds", float, "ocr"),
        crop_cache_max_entries_per_document=_get(
            ocr_raw, "crop_cache_max_entries_per_document", int, "ocr"
        ),
        stage_order=_str_tuple(ocr_raw, "stage_order", "ocr"),
        adjudicate_iou_threshold=_get(ocr_raw, "adjudicate_iou_threshold", float, "ocr"),
        confidence_tie_epsilon=_get(ocr_raw, "confidence_tie_epsilon", float, "ocr"),
        stage_timeout=stage_timeout,
        escalation=escalation,
        typhoon=typhoon,
    )

    preprocess_raw = _section(data, "preprocess", katrag_path)
    preprocess = PreprocessConfig(
        skew_degrees_threshold=_get(preprocess_raw, "skew_degrees_threshold", float, "preprocess"),
        min_dpi=_get(preprocess_raw, "min_dpi", int, "preprocess"),
        contrast_score_threshold=_get(preprocess_raw, "contrast_score_threshold", float, "preprocess"),
    )

    pq_raw = _section(data, "page_quality", katrag_path)
    page_quality = PageQualityConfig(
        weight_extracted_char_count=_get(pq_raw, "weight_extracted_char_count", float, "page_quality"),
        weight_out_of_charset_ratio=_get(pq_raw, "weight_out_of_charset_ratio", float, "page_quality"),
        weight_image_area_ratio=_get(pq_raw, "weight_image_area_ratio", float, "page_quality"),
        weight_domain_lexicon_match_count=_get(
            pq_raw, "weight_domain_lexicon_match_count", float, "page_quality"
        ),
        low_text_char_threshold=_get(pq_raw, "low_text_char_threshold", int, "page_quality"),
        ocr_candidate_budget_pages=_get(pq_raw, "ocr_candidate_budget_pages", int, "page_quality"),
        char_count_reference=_get(pq_raw, "char_count_reference", int, "page_quality"),
        lexicon_match_reference=_get(pq_raw, "lexicon_match_reference", int, "page_quality"),
    )

    route_raw = _section(data, "route.page", katrag_path)
    page_route = PageRouteConfig(
        fast_max_image_area_ratio=_get(route_raw, "fast_max_image_area_ratio", float, "route.page"),
        deep_min_image_area_ratio=_get(route_raw, "deep_min_image_area_ratio", float, "route.page"),
    )

    thai_raw = _section(data, "thai", katrag_path)
    thai = ThaiConfig(
        zero_width_max_points=_get(thai_raw, "zero_width_max_points", float, "thai"),
        baseline_tolerance_ratio=_get(thai_raw, "baseline_tolerance_ratio", float, "thai"),
        horizontal_window_ratio=_get(thai_raw, "horizontal_window_ratio", float, "thai"),
        line_baseline_tolerance_ratio=_get(thai_raw, "line_baseline_tolerance_ratio", float, "thai"),
    )

    retrieval_raw = _section(data, "retrieval", katrag_path)
    retrieval = RetrievalConfig(
        lexical_top_k=_get(retrieval_raw, "lexical_top_k", int, "retrieval"),
        dense_top_k=_get(retrieval_raw, "dense_top_k", int, "retrieval"),
        fusion_output_max=_get(retrieval_raw, "fusion_output_max", int, "retrieval"),
        fusion_lexical_weight=_get(retrieval_raw, "fusion_lexical_weight", float, "retrieval"),
        fusion_dense_weight=_get(retrieval_raw, "fusion_dense_weight", float, "retrieval"),
        fusion_rrf_k=_get(retrieval_raw, "fusion_rrf_k", int, "retrieval"),
        dense_p95_latency_budget_seconds=_get(
            retrieval_raw, "dense_p95_latency_budget_seconds", float, "retrieval"
        ),
        phrase_boost_multiplier=_get(retrieval_raw, "phrase_boost_multiplier", float, "retrieval"),
        rerank_depth=_get(retrieval_raw, "rerank_depth", int, "retrieval"),
        maxsim_enabled=_get(retrieval_raw, "maxsim_enabled", bool, "retrieval"),
        maxsim_status=_get(retrieval_raw, "maxsim_status", str, "retrieval"),
    )

    evidence_raw = _section(data, "evidence", katrag_path)
    evidence = EvidenceConfig(
        max_hops=_get(evidence_raw, "max_hops", int, "evidence"),
        max_nodes_per_request=_get(evidence_raw, "max_nodes_per_request", int, "evidence"),
        max_nodes_per_hop=_get(evidence_raw, "max_nodes_per_hop", int, "evidence"),
        evidence_time_budget_seconds=_get(
            evidence_raw, "evidence_time_budget_seconds", float, "evidence"
        ),
    )

    answer_raw = _section(data, "answer", katrag_path)
    answer = AnswerConfig(
        answer_time_budget_seconds=_get(answer_raw, "answer_time_budget_seconds", float, "answer"),
        max_evidence_units=_get(answer_raw, "max_evidence_units", int, "answer"),
        model_path=_get(answer_raw, "model_path", str, "answer"),
        request_timeout_seconds=_get(answer_raw, "request_timeout_seconds", float, "answer"),
        max_versions_per_request=_get(answer_raw, "max_versions_per_request", int, "answer"),
    )

    router_raw = _section(data, "router.question", katrag_path)
    question_router = QuestionRouterConfig(
        max_question_chars=_get(router_raw, "max_question_chars", int, "router.question"),
        api_max_question_chars=_get(router_raw, "api_max_question_chars", int, "router.question"),
        retriever_max_question_chars=_get(
            router_raw, "retriever_max_question_chars", int, "router.question"
        ),
        min_confidence=_get(router_raw, "min_confidence", float, "router.question"),
        classification_budget_ms=_get(router_raw, "classification_budget_ms", int, "router.question"),
        structured_path_budget_ms=_get(router_raw, "structured_path_budget_ms", int, "router.question"),
        max_route_escalations=_get(router_raw, "max_route_escalations", int, "router.question"),
    )

    memory_raw = _section(data, "memory", katrag_path)
    memory = MemoryConfig(
        limit_bytes=_get(memory_raw, "limit_bytes", int, "memory"),
        max_resident_page_images=_get(memory_raw, "max_resident_page_images", int, "memory"),
        rss_drift_tolerance=_get(memory_raw, "rss_drift_tolerance", float, "memory"),
        rss_baseline_page_index=_get(memory_raw, "rss_baseline_page_index", int, "memory"),
    )

    eval_raw = _section(data, "evaluation", katrag_path)
    evaluation = EvaluationConfig(
        min_samples_for_measured=_get(eval_raw, "min_samples_for_measured", int, "evaluation"),
        page_cer_threshold=_get(eval_raw, "page_cer_threshold", float, "evaluation"),
        table_cell_f1_threshold=_get(eval_raw, "table_cell_f1_threshold", float, "evaluation"),
        field_macro_f1_threshold=_get(eval_raw, "field_macro_f1_threshold", float, "evaluation"),
        recall_at_10_threshold=_get(eval_raw, "recall_at_10_threshold", float, "evaluation"),
        citation_precision_threshold=_get(eval_raw, "citation_precision_threshold", float, "evaluation"),
        citation_recall_threshold=_get(eval_raw, "citation_recall_threshold", float, "evaluation"),
        unsupported_claim_rate_threshold=_get(
            eval_raw, "unsupported_claim_rate_threshold", float, "evaluation"
        ),
        version_selection_accuracy_threshold=_get(
            eval_raw, "version_selection_accuracy_threshold", float, "evaluation"
        ),
    )

    dataset_raw = _section(data, "dataset", katrag_path)
    dataset = DatasetConfig(
        root=_get(dataset_raw, "root", str, "dataset"),
        expected_document_count=_get(dataset_raw, "expected_document_count", int, "dataset"),
        expected_page_total=_get(dataset_raw, "expected_page_total", int, "dataset"),
    )

    paths_raw = _section(data, "paths", katrag_path)
    paths = PathsConfig(
        sqlite=_get(paths_raw, "sqlite", str, "paths"),
        dataset_manifest=_get(paths_raw, "dataset_manifest", str, "paths"),
        evaluation_report=_get(paths_raw, "evaluation_report", str, "paths"),
        gt_normalized_dir=_get(paths_raw, "gt_normalized_dir", str, "paths"),
        teacher_gt_dir=_get(paths_raw, "teacher_gt_dir", str, "paths"),
        gold_set_dir=_get(paths_raw, "gold_set_dir", str, "paths"),
    )

    api_raw = _section(data, "api", katrag_path)
    api = ApiConfig(
        host=_get(api_raw, "host", str, "api"),
        port=_get(api_raw, "port", int, "api"),
        max_documents_per_response=_get(api_raw, "max_documents_per_response", int, "api"),
    )

    synonym_raw = value_sets_data.get("category_synonym", {})
    if not isinstance(synonym_raw, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in synonym_raw.items()
    ):
        raise ConfigError("category_synonym ต้องเป็นตารางของสตริง", path=str(value_sets_path))

    value_sets = ValueSets(
        course_category=_str_frozenset(value_sets_data, "course_category", value_sets_path),
        course_type=_str_frozenset(value_sets_data, "course_type", value_sets_path),
        extraction_method=_str_frozenset(value_sets_data, "extraction_method", value_sets_path),
        provenance_source=_str_frozenset(value_sets_data, "provenance_source", value_sets_path),
        edition_status=_str_frozenset(value_sets_data, "edition_status", value_sets_path),
        degree_level=_str_frozenset(value_sets_data, "degree_level", value_sets_path),
        compute_path=_str_frozenset(value_sets_data, "compute_path", value_sets_path),
        question_level=_str_frozenset(value_sets_data, "question_level", value_sets_path),
        metric_status=_str_frozenset(value_sets_data, "metric_status", value_sets_path),
        reference_source=_str_frozenset(value_sets_data, "reference_source", value_sets_path),
        page_status=_str_frozenset(value_sets_data, "page_status", value_sets_path),
        review_issue_kind=_str_frozenset(value_sets_data, "review_issue_kind", value_sets_path),
        halt_reason=_str_frozenset(value_sets_data, "halt_reason", value_sets_path),
        category_synonym=dict(synonym_raw),
    )

    config = KatragConfig(
        project_root=root,
        halt=halt,
        ocr=ocr,
        preprocess=preprocess,
        page_quality=page_quality,
        page_route=page_route,
        thai=thai,
        retrieval=retrieval,
        evidence=evidence,
        answer=answer,
        question_router=question_router,
        memory=memory,
        evaluation=evaluation,
        dataset=dataset,
        paths=paths,
        api=api,
        value_sets=value_sets,
        engines=engines_data,
        domain_lexicon=lexicon_data,
    )
    _validate(config)
    return config


def _validate(config: KatragConfig) -> None:
    """ตรวจช่วงค่าที่ requirements กำหนดไว้อย่างชัดเจน."""
    # ── ช่วงค่าที่ requirements ระบุเป็นตัวเลขตรง ๆ ──
    _require_range(config.evidence.max_hops, section="evidence", key="max_hops", minimum=1, maximum=5)
    _require_range(
        config.retrieval.rerank_depth, section="retrieval", key="rerank_depth", minimum=20, maximum=40
    )
    _require_range(
        config.retrieval.phrase_boost_multiplier,
        section="retrieval",
        key="phrase_boost_multiplier",
        minimum=1.00,
        maximum=3.00,
    )
    _require_range(
        config.answer.answer_time_budget_seconds,
        section="answer",
        key="answer_time_budget_seconds",
        minimum=10,
        maximum=180,
    )

    # ── halter: patience และ l_min ต้องมีความหมาย ──
    if config.halt.l_min < 1:
        raise ConfigError("l_min ต้องไม่น้อยกว่า 1", section="halt", key="l_min", value=config.halt.l_min)
    if config.halt.oscillation_patience < 1:
        raise ConfigError(
            "oscillation_patience ต้องไม่น้อยกว่า 1",
            section="halt",
            key="oscillation_patience",
            value=config.halt.oscillation_patience,
        )
    if config.halt.tau <= 0:
        raise ConfigError("tau ต้องมากกว่า 0", section="halt", key="tau", value=config.halt.tau)

    # ── OCR ──
    if config.ocr.max_stages_per_region != len(config.ocr.stage_order):
        raise ConfigError(
            "max_stages_per_region ต้องเท่ากับจำนวน stage ใน stage_order",
            section="ocr",
            max_stages_per_region=config.ocr.max_stages_per_region,
            stage_order=list(config.ocr.stage_order),
        )
    _require_range(
        config.ocr.adjudicate_iou_threshold,
        section="ocr",
        key="adjudicate_iou_threshold",
        minimum=0.0,
        maximum=1.0,
    )

    # ── OCR: per-engine timeout ต้องเป็นบวก ──
    if config.ocr.stage_timeout.tesseract5 <= 0:
        raise ConfigError(
            "stage_timeout.tesseract5 ต้องมากกว่า 0",
            section="ocr.stage_timeout",
            key="tesseract5",
            value=config.ocr.stage_timeout.tesseract5,
        )
    if config.ocr.stage_timeout.typhoon_ocr1_5_2b <= 0:
        raise ConfigError(
            "stage_timeout.typhoon_ocr1_5_2b ต้องมากกว่า 0",
            section="ocr.stage_timeout",
            key="typhoon_ocr1_5_2b",
            value=config.ocr.stage_timeout.typhoon_ocr1_5_2b,
        )

    # ── OCR: escalation budget/circuit-breaker ──
    if config.ocr.escalation.max_typhoon_seconds_per_run <= 0:
        raise ConfigError(
            "max_typhoon_seconds_per_run ต้องมากกว่า 0",
            section="ocr.escalation",
            key="max_typhoon_seconds_per_run",
        )
    if config.ocr.escalation.max_consecutive_typhoon_failures < 1:
        raise ConfigError(
            "max_consecutive_typhoon_failures ต้องไม่น้อยกว่า 1",
            section="ocr.escalation",
            key="max_consecutive_typhoon_failures",
        )
    _require_range(
        config.ocr.escalation.min_stage1_quality_for_skip,
        section="ocr.escalation",
        key="min_stage1_quality_for_skip",
        minimum=0.0,
        maximum=1.0,
    )

    # ── page quality: น้ำหนักต้องรวมได้ 1.0 เพื่อให้คะแนนอยู่ในช่วง 0-1 ──
    weight_sum = (
        config.page_quality.weight_extracted_char_count
        + config.page_quality.weight_out_of_charset_ratio
        + config.page_quality.weight_image_area_ratio
        + config.page_quality.weight_domain_lexicon_match_count
    )
    if abs(weight_sum - 1.0) > 1e-9:
        raise ConfigError(
            "น้ำหนักของ page_quality_score ต้องรวมได้ 1.0",
            section="page_quality",
            weight_sum=weight_sum,
        )
    if config.page_quality.low_text_char_threshold < 1:
        raise ConfigError(
            "low_text_char_threshold ต้องไม่น้อยกว่า 1",
            section="page_quality",
            key="low_text_char_threshold",
        )
    if config.page_quality.ocr_candidate_budget_pages < 0:
        raise ConfigError(
            "ocr_candidate_budget_pages ต้องไม่ติดลบ",
            section="page_quality",
            key="ocr_candidate_budget_pages",
        )

    # ── page routing: fast/deep threshold ต้องไม่คร่อมกัน ──
    if config.page_route.fast_max_image_area_ratio >= config.page_route.deep_min_image_area_ratio:
        raise ConfigError(
            "fast_max_image_area_ratio ต้องน้อยกว่า deep_min_image_area_ratio",
            section="route.page",
            fast=config.page_route.fast_max_image_area_ratio,
            deep=config.page_route.deep_min_image_area_ratio,
        )

    # ── retrieval ──
    if config.retrieval.fusion_output_max > min(
        config.retrieval.lexical_top_k + config.retrieval.dense_top_k, 50
    ):
        raise ConfigError(
            "fusion_output_max ต้องไม่เกิน 50 รายการตามข้อกำหนด",
            section="retrieval",
            key="fusion_output_max",
            value=config.retrieval.fusion_output_max,
        )
    if config.retrieval.rerank_depth > config.retrieval.fusion_output_max:
        raise ConfigError(
            "rerank_depth ต้องไม่เกิน fusion_output_max",
            section="retrieval",
            rerank_depth=config.retrieval.rerank_depth,
            fusion_output_max=config.retrieval.fusion_output_max,
        )
    if not config.retrieval.maxsim_enabled and config.retrieval.maxsim_status != "pending_ablation":
        raise ConfigError(
            "เมื่อ maxsim ปิด สถานะต้องเป็น pending_ablation ตามข้อกำหนด",
            section="retrieval",
            key="maxsim_status",
            value=config.retrieval.maxsim_status,
        )

    # ── evidence ──
    if config.evidence.max_nodes_per_hop > config.evidence.max_nodes_per_request:
        raise ConfigError(
            "max_nodes_per_hop ต้องไม่เกิน max_nodes_per_request",
            section="evidence",
        )
    if config.evidence.evidence_time_budget_seconds <= 0:
        raise ConfigError(
            "evidence_time_budget_seconds ต้องมากกว่า 0",
            section="evidence",
            key="evidence_time_budget_seconds",
        )

    # ── question router: ขอบเขตความยาวคำถามสามชั้นต้องเรียงถูก ──
    if not (
        config.question_router.max_question_chars
        <= config.question_router.retriever_max_question_chars
        <= config.question_router.api_max_question_chars
    ):
        raise ConfigError(
            "ขอบเขตความยาวคำถามต้องเรียงจาก router <= retriever <= api",
            section="router.question",
            router=config.question_router.max_question_chars,
            retriever=config.question_router.retriever_max_question_chars,
            api=config.question_router.api_max_question_chars,
        )
    _require_range(
        config.question_router.min_confidence,
        section="router.question",
        key="min_confidence",
        minimum=0.0,
        maximum=1.0,
    )

    # ── answer: งบสร้างคำตอบต้องไม่เกินเพดานของคำขอ ──
    if config.answer.answer_time_budget_seconds > config.answer.request_timeout_seconds:
        raise ConfigError(
            "answer_time_budget_seconds ต้องไม่เกิน request_timeout_seconds",
            section="answer",
            answer_budget=config.answer.answer_time_budget_seconds,
            request_timeout=config.answer.request_timeout_seconds,
        )
    if config.answer.max_versions_per_request < 1:
        raise ConfigError(
            "max_versions_per_request ต้องไม่น้อยกว่า 1",
            section="answer",
            key="max_versions_per_request",
        )

    # ── memory ──
    if config.memory.max_resident_page_images < 1:
        raise ConfigError(
            "max_resident_page_images ต้องไม่น้อยกว่า 1",
            section="memory",
            key="max_resident_page_images",
        )
    if config.memory.limit_bytes <= 0:
        raise ConfigError("limit_bytes ต้องมากกว่า 0", section="memory", key="limit_bytes")
    _require_range(
        config.memory.rss_drift_tolerance,
        section="memory",
        key="rss_drift_tolerance",
        minimum=0.0,
        maximum=1.0,
    )

    # ── evaluation ──
    if config.evaluation.min_samples_for_measured < 1:
        raise ConfigError(
            "min_samples_for_measured ต้องไม่น้อยกว่า 1",
            section="evaluation",
            key="min_samples_for_measured",
        )
    for key, value in (
        ("page_cer_threshold", config.evaluation.page_cer_threshold),
        ("table_cell_f1_threshold", config.evaluation.table_cell_f1_threshold),
        ("field_macro_f1_threshold", config.evaluation.field_macro_f1_threshold),
        ("recall_at_10_threshold", config.evaluation.recall_at_10_threshold),
        ("citation_precision_threshold", config.evaluation.citation_precision_threshold),
        ("citation_recall_threshold", config.evaluation.citation_recall_threshold),
        ("unsupported_claim_rate_threshold", config.evaluation.unsupported_claim_rate_threshold),
        (
            "version_selection_accuracy_threshold",
            config.evaluation.version_selection_accuracy_threshold,
        ),
    ):
        _require_range(value, section="evaluation", key=key, minimum=0.0, maximum=1.0)

    # ── dataset (measured fact ของชุดข้อมูลนี้) ──
    if config.dataset.expected_document_count < 1 or config.dataset.expected_page_total < 1:
        raise ConfigError(
            "ขอบเขต dataset ต้องเป็นจำนวนเต็มบวก",
            section="dataset",
            expected_document_count=config.dataset.expected_document_count,
            expected_page_total=config.dataset.expected_page_total,
        )

    # ── api: loopback เท่านั้นเป็นค่าตั้งต้น (R19.2) ──
    if config.api.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigError(
            "ค่าตั้งต้นของ api.host ต้องเป็น loopback address",
            section="api",
            key="host",
            value=config.api.host,
        )
    if config.api.max_documents_per_response > 500:
        raise ConfigError(
            "max_documents_per_response ต้องไม่เกิน 500 ตามข้อกำหนด",
            section="api",
            key="max_documents_per_response",
            value=config.api.max_documents_per_response,
        )

    # ── ค่าที่ต้องอยู่ในชุดค่าปิด ──
    for stage in config.ocr.stage_order:
        if not stage:
            raise ConfigError("ชื่อ stage ใน stage_order ต้องไม่ว่าง", section="ocr")
    unknown_synonym_targets = {
        target
        for target in config.value_sets.category_synonym.values()
        if target not in config.value_sets.course_category
    }
    if unknown_synonym_targets:
        raise ConfigError(
            "ปลายทางของ category_synonym ต้องอยู่ในชุด course_category",
            unknown=sorted(unknown_synonym_targets),
        )
