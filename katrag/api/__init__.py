"""KatRAG API package — FastAPI service (R19.1-R19.9)."""

from katrag.api.schemas import (
    AskRequest,
    AskResponse,
    CitationItem,
    DocumentItem,
    DocumentsResponse,
    PageResponse,
    TraceResponse,
)
from katrag.api.service import app, create_app

__all__ = [
    "AskRequest",
    "AskResponse",
    "CitationItem",
    "DocumentItem",
    "DocumentsResponse",
    "PageResponse",
    "TraceResponse",
    "app",
    "create_app",
]
