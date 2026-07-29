# ER Diagram — KatRAG-lite Database Schema

## Table Relationships

```
┌───────────────────────┐       ┌────────────────────────┐
│  curriculum_version   │       │       document         │
├───────────────────────┤       ├────────────────────────┤
│ PK version_id         │◄──┐   │ PK document_id         │
│    program            │   │   │    relative_path       │
│    curriculum_year    │   │   │    sha256              │
│    edition_status     │   │   │    size_bytes          │
└───────────────────────┘   │   │    page_count          │
                            │   │    degree_level        │
                            ├───│ FK version_id          │
                            │   │ FK canonical_document_id│──┐ (self-ref)
                            │   └────────┬───────────────┘  │
                            │            │                   │
                            │            │◄──────────────────┘
                            │            │
                            │            │ 1:N
                            │            ▼
                            │   ┌────────────────────────┐
                            │   │         page           │
                            │   ├────────────────────────┤
                            │   │ PK page_id             │
                            │   │ FK document_id         │
                            │   │    page_number         │
                            │   │    width_pt, height_pt │
                            │   │    char_count          │
                            │   │    image_count         │
                            │   │    page_text           │
                            │   │    extraction_method   │
                            │   │    page_sha256         │
                            │   │    status              │
                            │   └───┬──────────┬─────────┘
                            │       │          │
                            │       │ 1:1      │ 1:N
                            │       ▼          ▼
                            │   ┌──────────┐  ┌──────────────┐
                            │   │page_metric│  │   region     │
                            │   ├──────────┤  ├──────────────┤
                            │   │PK page_id │  │PK region_id  │
                            │   │ ext_char  │  │FK page_id    │
                            │   │ oor_ratio │  │ x0,y0,x1,y1 │
                            │   │ img_ratio │  │ crop_sha256  │
                            │   │ quality   │  │ status       │
                            │   │ is_ocr    │  └──────┬───────┘
                            │   │ compute   │         │ 1:N
                            │   └──────────┘         ▼
                            │              ┌─────────────────────┐
                            │              │  ocr_stage_result   │
                            │              ├─────────────────────┤
                            │              │PK stage_result_id   │
                            │              │FK region_id         │
                            │              │   stage_index       │
                            │              │   engine            │
                            │              │   text              │
                            │              │   quality_score     │
                            │              │   halt_decision     │
                            │              └─────────────────────┘
                            │
  ┌─────────────────────┐   │
  │     provenance      │   │
  ├─────────────────────┤   │
  │ PK provenance_id    │   │
  │ FK document_id      │   │
  │    page_number      │   │
  │    x0,y0,x1,y1     │   │
  │    span_start/end   │   │
  │    extraction_method│   │
  │    provenance_source│   │
  └──┬──────────────────┘   │
     │                      │
     │ referenced by:       │
     │                      │
     ├──────────────────────┼───────────────────────────────┐
     │                      │                               │
     ▼                      ▼                               ▼
┌──────────────┐    ┌───────────────┐              ┌──────────────┐
│    course    │    │   plan_slot   │              │     rule     │
├──────────────┤    ├───────────────┤              ├──────────────┤
│PK course_id  │    │PK slot_id     │              │PK rule_id    │
│FK version_id │◄───│FK version_id  │──────────────│FK version_id │
│FK provenance │    │FK course_id   │              │FK provenance │
│   code       │    │FK provenance  │              │   rule_kind  │
│   name_th/en │    │   year        │              │   attribute  │
│   credits_*  │    │   semester    │              │   comparator │
│   category   │    │   plan_variant│              │   value_*    │
│   type       │    │FK cell_id     │              └──────────────┘
│   prereq_json│    └───────────────┘
└──────┬───────┘              │
       │                      │
       │ 1:N                  │ N:1
       ▼                      ▼
┌────────────────────┐  ┌──────────────┐
│course_field_prov   │  │  table_cell  │
├────────────────────┤  ├──────────────┤
│PK id               │  │PK cell_id    │
│FK course_id        │  │FK page_id    │
│FK provenance_id    │  │FK provenance │
│   field_name       │  │  table_index │
│   value_status     │  │  row/col_idx │
│   raw_text         │  │  row/col_span│
└────────────────────┘  │  text        │
                        │  plan_year   │
                        │  plan_semester│
                        └──────────────┘


┌───────────────────────┐       ┌────────────────────────┐
│        chunk          │       │    chunk_embedding     │
├───────────────────────┤       ├────────────────────────┤
│ PK chunk_id           │◄──────│ PK chunk_id (FK)       │
│ FK document_id        │       │    model_name          │
│ FK version_id         │───┐   │    dim                 │
│ FK provenance_id      │   │   │    vector (BLOB)       │
│    page_number        │   │   │    token_vectors       │
│    heading            │   │   └────────────────────────┘
│    text               │   │
│    token_count        │   │
│    content_sha256     │   │   ┌────────────────────────┐
└───────────────────────┘   │   │    chunk_fts (FTS5)    │
                            │   ├────────────────────────┤
  ┌─────────────────────┐   │   │    text                │
  │  document_relation  │   │   │    heading             │
  ├─────────────────────┤   │   │  (content=chunk)       │
  │PK relation_id       │   │   └────────────────────────┘
  │FK from_document_id  │   │
  │FK to_document_id    │   │
  │   relation_type     │   │
  └─────────────────────┘   │
                            │
  ┌─────────────────────┐   │   ┌────────────────────────┐
  │   review_issue      │   │   │      gold_set          │
  ├─────────────────────┤   │   ├────────────────────────┤
  │PK issue_id          │   │   │PK gold_id              │
  │FK document_id       │   │   │FK document_id          │
  │   issue_type        │   └───│FK version_id           │
  │   page_number       │       │   item_kind            │
  │   detail_json       │       │   question_level       │
  └─────────────────────┘       │   payload_json         │
                                │   expected_json        │
  ┌─────────────────────┐       └────────────────────────┘
  │   error_record      │
  ├─────────────────────┤       ┌────────────────────────┐
  │PK error_id          │       │     query_trace        │
  │FK document_id       │       ├────────────────────────┤
  │   scope             │       │PK request_id           │
  │   error_kind        │       │   question_text        │
  │   message           │       │   question_level       │
  └─────────────────────┘       │   route_selected       │
                                │   version_set_json     │
                                │   retrieved_json       │
                                │   hops_json            │
                                │   halt_reason          │
                                │   total_elapsed_ms     │
                                └────────────────────────┘
```

## Summary of Relationships

| Parent Table          | Child Table            | Relationship | FK Column              |
|-----------------------|------------------------|:------------:|------------------------|
| curriculum_version    | document               | 1:N          | version_id             |
| curriculum_version    | course                 | 1:N          | version_id             |
| curriculum_version    | plan_slot              | 1:N          | version_id             |
| curriculum_version    | rule                   | 1:N          | version_id             |
| curriculum_version    | chunk                  | 1:N          | version_id             |
| curriculum_version    | gold_set               | 1:N          | version_id             |
| document              | document (self)        | 1:N          | canonical_document_id  |
| document              | page                   | 1:N          | document_id            |
| document              | chunk                  | 1:N          | document_id            |
| document              | document_relation      | 1:N          | from/to_document_id    |
| document              | review_issue           | 1:N          | document_id            |
| document              | error_record           | 1:N          | document_id            |
| document              | gold_set               | 1:N          | document_id            |
| page                  | page_metrics           | 1:1          | page_id                |
| page                  | region                 | 1:N          | page_id                |
| page                  | table_cell             | 1:N          | page_id                |
| page                  | provenance             | 1:N          | (document_id, page_number) |
| region                | ocr_stage_result       | 1:N          | region_id              |
| provenance            | course                 | 1:N          | provenance_id          |
| provenance            | course_field_provenance| 1:N          | provenance_id          |
| provenance            | plan_slot              | 1:N          | provenance_id          |
| provenance            | rule                   | 1:N          | provenance_id          |
| provenance            | chunk                  | 1:N          | provenance_id          |
| provenance            | table_cell             | 1:N          | provenance_id          |
| course                | course_field_provenance| 1:N          | course_id              |
| course                | plan_slot              | 1:N          | course_id              |
| chunk                 | chunk_embedding        | 1:1          | chunk_id               |
| chunk                 | chunk_fts (virtual)    | 1:1          | content_rowid          |
| table_cell            | plan_slot              | 1:N          | cell_id                |

## Table Count

- **19 physical tables** + **1 FTS5 virtual table** = 20 total
- Core data: curriculum_version, document, page, course, chunk
- Provenance chain: provenance → course/plan_slot/rule/chunk/table_cell
- OCR pipeline: region, ocr_stage_result
- Quality: page_metrics, review_issue, error_record
- Query path: query_trace, gold_set
- Indices: chunk_embedding, chunk_fts
