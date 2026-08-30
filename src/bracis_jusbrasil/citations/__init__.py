"""Detecção e parsing estrutural determinísticos de citações."""

from .detector import CitationCandidate, CitationDetector
from .parser import CitationParser, ParsedCitation
from .resolver import CitationResolver, PrimaryIdentity, ResolutionResult
from .arbitration import ArbitrationResult, StructuralCNJArbitrator, structural_cnj_union_merge

__all__ = [
    "CitationCandidate",
    "CitationDetector",
    "CitationParser",
    "CitationResolver",
    "PrimaryIdentity",
    "ParsedCitation",
    "ResolutionResult",
    "ArbitrationResult",
    "StructuralCNJArbitrator",
    "structural_cnj_union_merge",
]
