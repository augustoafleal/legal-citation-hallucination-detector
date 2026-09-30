"""Resolução canônica conservadora de citações já parseadas."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
import sqlite3
from types import MappingProxyType
from typing import Literal, Mapping

from ..cases import CaseIndex
from ..cases.parser import parse_case_identity
from ..normalization import normalize_tribunal
from .legal_catalog import LegalCanonicalCatalog
from .parser import ParsedCitation


ResolutionStatus = Literal["resolved", "no_match", "ambiguous", "insufficient"]
_SUMULA_NUMBER = re.compile(r"S[ÚU]MULA(?:\s+VINCULANTE)?\s+(?P<number>\d+)", re.IGNORECASE)
_CNJ_PRIMARY = re.compile(
    r"^\d{1,7}\d{2}\d{4}\d\d{2}\d{4}$"
)
# Os segmentos 1/2 aparecem tanto em STF quanto em STJ no corpus; somente os
# segmentos inequívocos são usados como guard estrutural.
_CNJ_SEGMENT_TRIBUNAL = {"5": "TST", "6": "TSE", "7": "STM"}


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


@dataclass(frozen=True)
class PrimaryIdentity:
    """Metadados formais mínimos usados pelo resolver/arbitrador."""

    id_canonico: int
    tribunal: str
    numero_raw: str
    numero_normalizado: str
    classe: str | None


class CitationResolver:
    """Resolver conservador V11 para jurisprudência, súmulas e dispositivos."""

    def __init__(
        self,
        *,
        case_index: CaseIndex,
        connection: sqlite3.Connection,
        legal_catalog: LegalCanonicalCatalog | None = None,
    ) -> None:
        self._case_index = case_index
        self._legal_catalog = legal_catalog or LegalCanonicalCatalog.from_database(connection)
        self._primary_identities = MappingProxyType(self._build_primary_identities(connection))
        sumula_ids, sumula_ids_by_number = self._build_sumula_mappings(connection)
        self._sumula_ids = MappingProxyType(sumula_ids)
        self._sumula_ids_by_number = MappingProxyType(sumula_ids_by_number)

    @property
    def primary_identities(self) -> Mapping[int, PrimaryIdentity]:
        """Metadados somente-leitura para a arbitragem estrutural."""
        return self._primary_identities

    @property
    def legal_catalog(self) -> LegalCanonicalCatalog:
        """Catálogo do SQLite recebido usado pela estratégia artigo+diploma."""
        return self._legal_catalog

    @staticmethod
    def _class_signature(value: str | None, tribunal: str | None = None) -> str | None:
        if not value:
            return None
        # Normalização deliberadamente textual: caixa, pontuação e espaços.
        compact = re.sub(r"[^A-Z0-9À-ÿ]", "", value.upper())
        if not compact:
            return None
        normalized_tribunal = (tribunal or "").upper()
        if normalized_tribunal == "TST":
            # Nos identificadores TST a classe é o último bloco alfabético
            # imediatamente anterior ao número (RR, AIRR, AgARR etc.).
            match = re.search(
                r"(?:TST\s*-\s*)?(?P<prefix>[A-ZÀ-ÿ][A-Z0-9À-ÿ]*(?:\s*-\s*[A-ZÀ-ÿ][A-Z0-9À-ÿ]*)*)"
                r"\s*-\s*(?=\d)",
                value.upper(),
            )
            if match:
                blocks = re.findall(r"[A-ZÀ-ÿ][A-Z0-9À-ÿ]*", match.group("prefix"))
                if blocks:
                    return f"TST:{blocks[-1]}"
            chain = re.findall(r"[A-ZÀ-ÿ][A-Z0-9À-ÿ]*", value.upper())
            if "-" in value and chain:
                return f"TST:{chain[-1]}"
            # A parsed citation may contain only the terminal class token.
            if re.fullmatch(r"[A-ZÀ-ÿ][A-Z0-9À-ÿ]*", value.strip(), re.I):
                return f"TST:{compact}"
        # Para os demais tribunais, retenha apenas a expressão de classe que
        # precede o marcador formal do número. Não há tabela de sinônimos:
        # ``REsp`` e ``Recurso Especial`` permanecem valores distintos.
        before_number = re.split(r"\bN\s*[ºO.]?\b", value.upper(), maxsplit=1)[0]
        phrases = re.findall(
            r"(?:RECURSO\s+ESPECIAL\s+ELEITORAL|RECURSO\s+ESPECIAL|"
            r"RECURSO\s+EXTRAORDIN[ÁA]RIO|HABEAS\s+CORPUS|"
            r"RECLAMA[ÇC][AÃ]O|APELA[ÇC][AÃ]O|AGRAVO\s+INTERNO|"
            r"AGRAVO\s+REGIMENTAL|MANDADO\s+DE\s+SEGURAN[ÇC]A)",
            before_number,
            re.I,
        )
        if phrases:
            return re.sub(r"[^A-Z0-9À-ÿ]", "", phrases[-1].upper())
        tokens = re.findall(r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO)\b", before_number, re.I)
        return re.sub(r"[^A-Z0-9À-ÿ]", "", tokens[-1].upper()) if tokens else compact

    @classmethod
    def _build_primary_identities(cls, connection: sqlite3.Connection) -> dict[int, PrimaryIdentity]:
        identities: dict[int, PrimaryIdentity] = {}
        rows = connection.execute(
            "SELECT id, tribunal, texto FROM documentos WHERE natureza = 'acordao' ORDER BY id"
        ).fetchall()
        for row in rows:
            try:
                tribunal = normalize_tribunal(row["tribunal"])
                parsed = parse_case_identity(row["texto"], tribunal)
            except (KeyError, TypeError, ValueError):
                continue
            identities[int(row["id"])] = PrimaryIdentity(
                id_canonico=int(row["id"]),
                tribunal=tribunal,
                numero_raw=parsed.numero_raw,
                numero_normalizado=parsed.numero_normalizado,
                classe=cls._class_signature(parsed.identity_block, tribunal),
            )
        return identities

    @staticmethod
    def _build_sumula_mappings(
        connection: sqlite3.Connection,
    ) -> tuple[dict[tuple[str, str], tuple[int, ...]], dict[str, tuple[int, ...]]]:
        grouped_by_tribunal: dict[tuple[str, str], list[int]] = defaultdict(list)
        grouped_by_number: dict[str, list[int]] = defaultdict(list)
        rows = connection.execute(
            "SELECT id, tribunal, texto FROM documentos WHERE natureza = 'sumula' ORDER BY id"
        ).fetchall()
        for row in rows:
            tribunal = row["tribunal"]
            match = _SUMULA_NUMBER.search(row["texto"])
            if match is None:
                continue
            number = match.group("number")
            id_canonico = int(row["id"])
            grouped_by_number[number].append(id_canonico)
            if isinstance(tribunal, str):
                grouped_by_tribunal[(normalize_tribunal(tribunal), number)].append(id_canonico)
        return (
            {key: tuple(sorted(ids)) for key, ids in grouped_by_tribunal.items()},
            {number: tuple(sorted(ids)) for number, ids in grouped_by_number.items()},
        )

    def resolve(self, parsed_citation: ParsedCitation) -> ResolutionResult:
        """Resolve famílias cobertas pelas estratégias seguras da V3."""
        if not isinstance(parsed_citation, ParsedCitation):
            raise TypeError("parsed_citation deve ser ParsedCitation")
        if parsed_citation.family == "processo_ou_recurso_numerado":
            return self._resolve_numbered_case(parsed_citation)
        if parsed_citation.family == "processo_cnj":
            return self._resolve_cnj(parsed_citation)
        if parsed_citation.family == "sumula_numerada":
            return self._resolve_sumula(parsed_citation)
        if parsed_citation.tipo == "lei" or parsed_citation.family.startswith("lei_"):
            return self._resolve_legal(parsed_citation)
        return self._unsupported_family(parsed_citation)

    def _resolve_legal(self, parsed: ParsedCitation) -> ResolutionResult:
        identity = parsed.legal
        if identity is None:
            return ResolutionResult(
                "insufficient", None, (), "legal_identity_abstention",
                "legal_identity_missing", "dispositivo",
            )
        if identity.has_critical_ocr:
            return ResolutionResult(
                "insufficient", None, (), "legal_identity_abstention",
                "legal_identity_critical_ocr", "dispositivo",
            )
        if identity.has_ambiguous_identity:
            return ResolutionResult(
                "insufficient", None, (), "legal_identity_abstention",
                "legal_identity_ambiguous", "dispositivo",
            )
        if (
            not identity.has_complete_identity
            or not identity.article_normalized
            or not identity.diploma_normalized
        ):
            return ResolutionResult(
                "insufficient", None, (), "legal_identity_abstention",
                "legal_identity_incomplete", "dispositivo",
            )

        candidate_ids = self._legal_catalog.lookup(
            identity.article_normalized, identity.diploma_normalized
        )
        if len(candidate_ids) == 1:
            return ResolutionResult(
                "resolved", candidate_ids[0], candidate_ids,
                "legal_article_diploma_catalog", "legal_article_diploma_unique",
                "dispositivo",
            )
        if len(candidate_ids) > 1:
            return ResolutionResult(
                "ambiguous", None, candidate_ids,
                "legal_article_diploma_catalog", "legal_catalog_key_ambiguous",
                "dispositivo",
            )
        if self._legal_catalog.is_covered_namespace(identity.diploma_normalized):
            return ResolutionResult(
                "no_match", None, (), "legal_article_diploma_covered_no_match",
                "legal_article_diploma_not_found", "dispositivo",
            )
        return ResolutionResult(
            "insufficient", None, (), "legal_identity_abstention",
            "legal_diploma_namespace_uncovered", "dispositivo",
        )

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

        cited_class = self._class_signature(parsed.data.get("classe_raw"), parsed.tribunal)
        matching = self._matching_primary_class_ids(candidate_ids, cited_class)
        if len(matching) == 1:
            return ResolutionResult(
                "resolved",
                matching[0],
                matching,
                "case_number_primary_class",
                "case_number_primary_class_unique",
                "acordao",
            )
        return ResolutionResult("ambiguous", None, candidate_ids, "case_number_exact", "case_has_multiple_canonical_ids", "acordao")

    def _resolve_cnj(self, parsed: ParsedCitation) -> ResolutionResult:
        number = parsed.data.get("numero_normalizado")
        if not number:
            return ResolutionResult("insufficient", None, (), None, "cnj_number_missing", "acordao")
        case = self._case_index.lookup_by_number(number)
        if case is None:
            return ResolutionResult("no_match", None, (), "cnj_primary_identity", "cnj_primary_not_found", "acordao")

        # Somente o número formal primário CNJ pode ser candidato. Isto evita
        # transformar números internos/destacados no corpo em identidade.
        candidate_ids = tuple(
            id_canonico
            for id_canonico in case.canonical_ids
            if self._is_cnj_primary(id_canonico, number)
        )
        if not candidate_ids:
            return ResolutionResult("no_match", None, (), "cnj_primary_identity", "cnj_primary_not_found", "acordao")

        cited_tribunal = parsed.tribunal or self._cnj_tribunal(number)
        if cited_tribunal:
            compatible = tuple(
                id_canonico
                for id_canonico in candidate_ids
                if (identity := self._primary_identities.get(id_canonico)) is None
                or identity.tribunal == cited_tribunal
            )
            if not compatible:
                return ResolutionResult("insufficient", None, (), "cnj_primary_identity", "cnj_tribunal_conflict", "acordao")
            candidate_ids = compatible

        cited_class = self._class_signature(parsed.data.get("classe_raw"), cited_tribunal)
        if cited_class:
            known = tuple(
                id_canonico
                for id_canonico in candidate_ids
                if (identity := self._primary_identities.get(id_canonico)) is not None and identity.classe is not None
            )
            matching = self._matching_primary_class_ids(candidate_ids, cited_class)
            if known and not matching:
                return ResolutionResult("insufficient", None, (), "cnj_primary_identity", "cnj_primary_class_conflict", "acordao")
            if matching:
                candidate_ids = matching

        if len(candidate_ids) == 1:
            return ResolutionResult("resolved", candidate_ids[0], candidate_ids, "cnj_primary_identity", "cnj_primary_unique", "acordao")
        return ResolutionResult("ambiguous", None, candidate_ids, "cnj_primary_identity", "cnj_primary_ambiguous", "acordao")

    def _matching_primary_class_ids(
        self, candidate_ids: tuple[int, ...], cited_class: str | None
    ) -> tuple[int, ...]:
        """Retorna IDs primários cujo guard de classe coincide com a citação."""
        if not cited_class:
            return ()
        return tuple(
            id_canonico
            for id_canonico in candidate_ids
            if (identity := self._primary_identities.get(id_canonico)) is not None
            and identity.classe is not None
            and identity.classe == cited_class
        )

    def _is_cnj_primary(self, id_canonico: int, number: str) -> bool:
        identity = self._primary_identities.get(id_canonico)
        if identity is not None:
            return bool(_CNJ_PRIMARY.fullmatch(identity.numero_normalizado)) and identity.numero_normalizado == number
        # Synthetic CaseIndex instances used by clients/tests may not have DB
        # metadata; retain a structural fallback without selecting an ID.
        return bool(_CNJ_PRIMARY.fullmatch(number))

    @staticmethod
    def _cnj_tribunal(number: str) -> str | None:
        if len(number) < 7:
            return None
        return _CNJ_SEGMENT_TRIBUNAL.get(number[-7])

    def _resolve_sumula(self, parsed: ParsedCitation) -> ResolutionResult:
        number = parsed.data.get("sumula_numero")
        if not number:
            return ResolutionResult("insufficient", None, (), None, "sumula_number_missing", "sumula")

        # Preserve the V1 path when the citation supplies a trusted tribunal.
        # Without one, the promoted V2 strategy deliberately resolves by number
        # alone and determines uniqueness from the complete sumula corpus.
        if parsed.tribunal and parsed.tribunal_source == "explicit":
            strategy = "sumula_number_tribunal"
            candidate_ids = self._sumula_ids.get((parsed.tribunal, number), ())
            not_found_reason = "sumula_not_found"
            ambiguous_reason = "sumula_has_multiple_canonical_ids"
        else:
            strategy = "sumula_number_only"
            candidate_ids = self._sumula_ids_by_number.get(number, ())
            not_found_reason = "sumula_number_not_found"
            ambiguous_reason = "sumula_number_ambiguous"

        if not candidate_ids:
            return ResolutionResult("no_match", None, (), strategy, not_found_reason, "sumula")
        if len(candidate_ids) == 1:
            return ResolutionResult("resolved", candidate_ids[0], candidate_ids, strategy, "sumula_unique", "sumula")
        return ResolutionResult("ambiguous", None, candidate_ids, strategy, ambiguous_reason, "sumula")

    @staticmethod
    def _unsupported_family(parsed: ParsedCitation) -> ResolutionResult:
        # Mantém o reason histórico para consumidores que persistem essa
        # enumeração; a estratégia CNJ acima é a única promoção da V3.
        return ResolutionResult("insufficient", None, (), None, "family_not_supported_in_v1", None)
