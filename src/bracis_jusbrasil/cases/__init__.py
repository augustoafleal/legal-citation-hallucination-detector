"""Domínio dos feitos conhecidos no banco de referência."""

from .index import Case, CaseIndex, CaseIndexStats, CaseRecord, build_case_index

__all__ = [
    "Case",
    "CaseIndex",
    "CaseIndexStats",
    "CaseRecord",
    "build_case_index",
]
