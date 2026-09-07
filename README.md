# Jingjok-Thorius

Curriculum QA system — offline RAG for KMITL Information Technology course documents.

## Overview

Jingjok-Thorius is an offline, provenance-first Retrieval-Augmented Generation (RAG) system
designed to answer questions about KMITL's Information Technology curriculum from 14 PDF
documents. The system runs entirely on a single machine without any network access —
enforced at the socket level via a net guard module.

Key properties:
- **Offline-only**: No outbound network connections allowed (enforced by `katrag.common.net_guard`)
- **Provenance-first**: Every piece of extracted data is traced back to its source (document, page, bbox)
- **Version-aware**: Supports multiple curriculum versions simultaneously
- **Reproducible**: Evaluation metrics produce identical results on repeated runs

## Setup Instructions

### Prerequisites

- Python 3.11 (required — `>=3.11, <3.12`)
- Windows 10/11 (primary target)
- Tesseract OCR 5 (for OCR pipeline)
- CUDA-capable GPU (optional — for Typhoon-OCR stage 2)

### Installation

```bash
# Clone and install base dependencies
cd project
pip install -e .

# Install optional extras as needed:
pip install -e ".[dev]"     # pytest + hypothesis
pip install -e ".[ocr]"    # Tesseract + Typhoon OCR
pip install -e ".[index]"  # ONNX dense embeddings
pip install -e ".[serve]"  # FastAPI + uvicorn
```

### Configuration

All configuration lives in `config/`:
- `katrag.toml` — main settings (OCR, retrieval, evidence, evaluation thresholds)
- `value_sets.toml` — closed value sets for validation
- `engines.toml` — OCR engine definitions and weight files
- `domain_lexicon.toml` — Thai/English domain terms for quality scoring

### Data

Place the 14 curriculum PDF files in `data/pdfs/` (or the path configured in `katrag.toml`
under `[dataset].root`).

## Usage

All commands are available via `python -m katrag.cli` or the `katrag` console script:

```bash
# Check engine readiness
katrag preflight

# Ingest all documents (streaming, resumable)
katrag ingest
katrag ingest --fresh    # discard previous progress

# Build search indices
katrag index

# Run evaluation harness (needs teacher ground truth)
katrag evaluate

# Check extracted data for internal consistency (CHK1-CHK7, no ground truth needed)
katrag consistency
katrag consistency --fail-on-error   # exit 1 on error-level findings (for CI)

# Start API server (binds to 127.0.0.1:8000)
katrag serve

# Run end-to-end demo
katrag demo
katrag demo --verbose
```

### API Endpoints

When running `katrag serve`:
- `POST /ask` — Submit a question, receive answer with citations
- `GET /documents` — List all documents with versions
- `GET /pages/{citation_id}` — Get page content by citation ID
- `GET /traces/{request_id}` — Get query trace for debugging

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI (__main__.py)                         │
│  preflight │ ingest │ index │ evaluate │ serve │ demo           │
└──────┬─────┴────┬───┴───┬───┴────┬─────┴───┬───┴───┬───────────┘
       │          │       │        │         │       │
       ▼          ▼       ▼        ▼         ▼       ▼
  ┌─────────┐ ┌───────┐ ┌─────┐ ┌──────┐ ┌─────┐ ┌──────┐
  │Preflight│ │Ingest │ │Index│ │ Eval │ │ API │ │ Demo │
  └────┬────┘ └───┬───┘ └──┬──┘ └──┬───┘ └──┬──┘ └──┬───┘
       │          │        │       │        │       │
       ▼          ▼        ▼       ▼        ▼       ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                    Provenance Store (SQLite)                    │
  │  19 tables + 1 FTS5 virtual table — schema.sql                │
  └───────────────────────────────────────────────────────────────┘
       │
       ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                     Common Layer                                │
  │  net_guard │ memory │ hashing │ normalize │ scratch │ types    │
  └───────────────────────────────────────────────────────────────┘
```

### Module Organization

```
katrag/
├── cli/           # CLI commands and demo
├── common/        # Shared utilities (net_guard, hashing, memory monitor, normalize)
├── ingest/        # Scan, text extraction, Tesseract OCR, chunking, course/prereq population
│   └── ocr/       # OCR preflight (engine + weight verification)
├── store/         # SQLite provenance store + schema + integrity checks
├── index/         # Lexical (FTS5) + dense (BGE-M3) retrieval indices
├── query/         # pipeline.py orchestrator + structured query + hybrid/semantic retrieval + Typhoon LLM
├── eval/          # OCR CER/field eval, QA eval, gold set builder, report generators
├── api/           # FastAPI REST endpoints
├── config.py      # Frozen configuration loader
└── errors.py      # Error taxonomy
```

### Pipeline Flow

1. **Preflight** — Verify OCR engine availability and weights
2. **Ingest** — Extract text page-by-page (streaming, resumable, memory-bounded); Tesseract for
   image-only pages; then chunk and populate `course` / `prerequisite` tables
3. **Index** — Build FTS5 lexical index + BGE-M3 dense embeddings
4. **Query** — `pipeline.py`: resolve program → scope question → structured query →
   hybrid retrieve (RRF + adaptive cutoff) → build context → Typhoon LLM → resolve citations
5. **Evaluate** — OCR CER vs text layer, field accuracy vs teacher GT, QA + citation metrics
6. **Consistency** — CHK1-CHK7 cross-field rules that verify the extracted data agrees with
   itself (credits printed in two places must match, prerequisites must precede the course
   that requires them, no duplicate course in one term, …). Needs no answer key, so it covers
   all 13 curriculum versions and doubles as a regression guard for ingest changes

> Subsystems that were designed and built but never wired into the live path (OCR cascade,
> multi-hop evidence planner, citation validator, table extractor, and the `katgpt-rs` ports)
> have been removed from the codebase. Their design, line counts, and measured results are
> recorded in [`docs/removed_subsystems.md`](docs/removed_subsystems.md).
