"""Resolução canônica conservadora de citações já parseadas."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
import sqlite3
from types import MappingProxyType
from typing import Literal

from ..cases import CaseIndex
from ..normalization import normalize_tribunal
from .parser import ParsedCitation


ResolutionStatus = Literal["resolved", "no_match", "ambiguous", "insufficient"]
_SUMULA_NUMBER = re.compile(r"S[ÚU]MULA(?:\s+VINCULANTE)?\s+(?P<number>\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ResolutionResult:
    """Resultado auditável e neutro da resolução de uma citação."""

    status: ResolutionStatus
    id_canonico: int | None
    candidate_ids: tuple[int, ...]
    strategy: str | None
    reason: str
    record_type: str | None

    def __post_init__(self) -> None:
        if self.status not in {"resolved", "no_match", "ambiguous", "insufficient"}:
            raise ValueError(f"status de resolução inválido: {self.status!r}")
        if not isinstance(self.candidate_ids, tuple):
            raise TypeError("candidate_ids deve ser tuple")
        if self.status == "resolved":
            if self.id_canonico is None or self.candidate_ids != (self.id_canonico,):
                raise ValueError("resolved exige exatamente o ID canônico candidato")
        elif self.status == "no_match":
            if self.id_canonico is not None or self.candidate_ids:
                raise ValueError("no_match exige ID nulo e nenhum candidato")
        elif self.status == "ambiguous":
            if self.id_canonico is not None or len(self.candidate_ids) < 2:
                raise ValueError("ambiguous exige mais de um candidato e ID nulo")
        elif self.id_canonico is not None:
            raise ValueError("insufficient exige ID nulo")


class CitationResolver:
    """Resolver V1 parcial: processo numerado e súmula com tribunal explícito."""

    def __init__(self, *, case_index: CaseIndex, connection: sqlite3.Connection) -> None:
        self._case_index = case_index
        self._sumula_ids = MappingProxyType(self._build_sumula_mapping(connection))

    @staticmethod
    def _build_sumula_mapping(connection: sqlite3.Connection) -> dict[tuple[str, str], tuple[int, ...]]:
        grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
        rows = connection.execute(
            "SELECT id, tribunal, texto FROM documentos WHERE natureza = 'sumula' ORDER BY id"
        ).fetchall()
        for row in rows:
            tribunal = row["tribunal"]
            match = _SUMULA_NUMBER.search(row["texto"])
            if not isinstance(tribunal, str) or match is None:
                continue
            grouped[(normalize_tribunal(tribunal), match.group("number"))].append(int(row["id"]))
        return {key: tuple(sorted(ids)) for key, ids in grouped.items()}

    def resolve(self, parsed_citation: ParsedCitation) -> ResolutionResult:
        """Resolve somente famílias cobertas pelas estratégias seguras da V1."""
        if not isinstance(parsed_citation, ParsedCitation):
            raise TypeError("parsed_citation deve ser ParsedCitation")
        if parsed_citation.family == "processo_ou_recurso_numerado":
            return self._resolve_numbered_case(parsed_citation)
        if parsed_citation.family == "sumula_numerada":
            return self._resolve_sumula(parsed_citation)
        return self._unsupported_family(parsed_citation)

    def _resolve_numbered_case(self, parsed: ParsedCitation) -> ResolutionResult:
        number = parsed.data.get("numero_normalizado")
        if not number:
            return ResolutionResult("insufficient", None, (), None, "case_number_missing", "acordao")
        case = self._case_index.lookup_by_number(number)
        if case is None:
            return ResolutionResult("no_match", None, (), "case_number_exact", "case_number_not_found", "acordao")
        candidate_ids = case.canonical_ids
        if len(candidate_ids) == 1:
            return ResolutionResult("resolved", candidate_ids[0], candidate_ids, "case_number_exact", "case_number_unique", "acordao")
        return ResolutionResult("ambiguous", None, candidate_ids, "case_number_exact", "case_has_multiple_canonical_ids", "acordao")

    def _resolve_sumula(self, parsed: ParsedCitation) -> ResolutionResult:
        number = parsed.data.get("sumula_numero")
        if not number or not parsed.tribunal or parsed.tribunal_source != "explicit":
            return ResolutionResult("insufficient", None, (), None, "sumula_number_or_tribunal_missing", "sumula")
        candidate_ids = self._sumula_ids.get((parsed.tribunal, number), ())
        if not candidate_ids:
            return ResolutionResult("no_match", None, (), "sumula_number_tribunal", "sumula_not_found", "sumula")
        if len(candidate_ids) == 1:
            return ResolutionResult("resolved", candidate_ids[0], candidate_ids, "sumula_number_tribunal", "sumula_unique", "sumula")
        return ResolutionResult("ambiguous", None, candidate_ids, "sumula_number_tribunal", "sumula_has_multiple_canonical_ids", "sumula")

    @staticmethod
    def _unsupported_family(parsed: ParsedCitation) -> ResolutionResult:
        if parsed.family == "processo_cnj":
            return ResolutionResult("insufficient", None, (), None, "cnj_resolution_disabled", "acordao")
        if parsed.family.startswith("lei_"):
            return ResolutionResult("insufficient", None, (), None, "legal_identity_not_verifiable", "dispositivo")
        return ResolutionResult("insufficient", None, (), None, "family_not_supported_in_v1", None)
