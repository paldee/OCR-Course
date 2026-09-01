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
    request_timeout_seconds: float
    max_question_chars: int


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
    page_quality: PageQualityConfig
    page_route: PageRouteConfig
    thai: ThaiConfig
    retrieval: RetrievalConfig
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
        request_timeout_seconds=_get(api_raw, "request_timeout_seconds", float, "api"),
        max_question_chars=_get(api_raw, "max_question_chars", int, "api"),
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
        page_quality=page_quality,
        page_route=page_route,
        thai=thai,
        retrieval=retrieval,
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
    if config.api.request_timeout_seconds <= 0:
        raise ConfigError(
            "request_timeout_seconds ต้องมากกว่า 0",
            section="api",
            key="request_timeout_seconds",
            value=config.api.request_timeout_seconds,
        )
    if config.api.max_question_chars < 1:
        raise ConfigError(
            "max_question_chars ต้องไม่น้อยกว่า 1",
            section="api",
            key="max_question_chars",
            value=config.api.max_question_chars,
        )

    # ── ค่าที่ต้องอยู่ในชุดค่าปิด ──
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
