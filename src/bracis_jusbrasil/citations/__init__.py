"""Detecção e parsing estrutural determinísticos de citações."""

from .detector import CitationCandidate, CitationDetector
from .parser import CitationParser, ParsedCitation
from .resolver import CitationResolver, ResolutionResult

__all__ = [
    "CitationCandidate",
    "CitationDetector",
    "CitationParser",
    "CitationResolver",
    "ParsedCitation",
    "ResolutionResult",
]
