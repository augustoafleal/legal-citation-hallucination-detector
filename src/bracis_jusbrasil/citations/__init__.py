"""Detecção e parsing estrutural determinísticos de citações."""

from .detector import CitationCandidate, CitationDetector
from .parser import CitationParser, ParsedCitation, ParsedLegalIdentity
from .resolver import CitationResolver, PrimaryIdentity, ResolutionResult
from .arbitration import (
    ArbitrationResult,
    StructuralCNJArbitrator,
    arbitrate_citations,
    legal_maximal_span_arbitration,
    structural_cnj_union_merge,
)
from .legal_catalog import (
    COVERED_DIPLOMA_NAMESPACES,
    DEFAULT_LEGAL_CATALOG,
    LegalCanonicalCatalog,
    LegalCatalogEntry,
)

__all__ = [
    "CitationCandidate",
    "CitationDetector",
    "CitationParser",
    "CitationResolver",
    "PrimaryIdentity",
    "ParsedCitation",
    "ParsedLegalIdentity",
    "ResolutionResult",
    "ArbitrationResult",
    "StructuralCNJArbitrator",
    "arbitrate_citations",
    "legal_maximal_span_arbitration",
    "structural_cnj_union_merge",
    "COVERED_DIPLOMA_NAMESPACES",
    "DEFAULT_LEGAL_CATALOG",
    "LegalCanonicalCatalog",
    "LegalCatalogEntry",
]
