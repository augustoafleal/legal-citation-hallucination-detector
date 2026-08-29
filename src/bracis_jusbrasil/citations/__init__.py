"""Detecção e parsing estrutural determinísticos de citações."""

from .detector import CitationCandidate, CitationDetector
from .parser import CitationParser, ParsedCitation

__all__ = ["CitationCandidate", "CitationDetector", "CitationParser", "ParsedCitation"]
