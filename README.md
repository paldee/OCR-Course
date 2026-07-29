# KatRAG-lite

Curriculum QA system — offline RAG for KMITL Information Technology course documents.

## Overview

KatRAG-lite is an offline, provenance-first Retrieval-Augmented Generation (RAG) system
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

# Run evaluation harness
katrag evaluate

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
  │  net_guard │ halter │ memory │ hashing │ normalize │ types    │
  └───────────────────────────────────────────────────────────────┘
```

### Module Organization

```
katrag/
├── cli/           # CLI commands and demo
├── common/        # Shared utilities (net_guard, hashing, memory monitor)
├── ingest/        # Document scanning, text extraction, OCR cascade
│   ├── fields/    # Structured field extraction (credits, prerequisites)
│   └── ocr/       # Multi-stage OCR with halter
├── store/         # SQLite provenance store + schema
├── index/         # Lexical (FTS5) + dense (ONNX) retrieval indices
├── query/         # Question routing, evidence planning, answer generation
├── eval/          # Evaluation harness + gold set management
├── api/           # FastAPI REST endpoints
├── config.py      # Frozen configuration loader
└── errors.py      # Error taxonomy
```

### Pipeline Flow

1. **Preflight** — Verify OCR weights and engine availability
2. **Ingest** — Extract text page-by-page (streaming, resumable, memory-bounded)
3. **Index** — Build FTS5 lexical index + ONNX dense embeddings
4. **Query** — Route questions → retrieve → plan evidence → generate answer → validate citations
5. **Evaluate** — Compute metrics against gold set, check reproducibility
