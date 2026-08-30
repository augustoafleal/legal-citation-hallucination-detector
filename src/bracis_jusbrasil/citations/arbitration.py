"""Arbitragem estrutural de sobreposições CNJ.

O módulo recebe candidatos e resultados já produzidos.  Ele não consulta o
corpus, não escolhe identidades e não altera objetos do detector: quando todos
os guards passam, cria uma nova representação cobrindo o intervalo literal
original; caso contrário preserva os candidatos de entrada.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping, Sequence

from .detector import CitationCandidate
from .parser import CitationParser, ParsedCitation
from .resolver import PrimaryIdentity, ResolutionResult


_ALLOWED_COMPANION_FAMILIES = frozenset(
    {"processo_ou_recurso_numerado", "jurisprudencia_tribunal_contextual"}
)
_NUMBER_TOKEN = re.compile(r"(?<!\w)\d(?:[\d.\-/\s]*\d)(?!\w)")
_CNJ_TRIBUNAL = {"5": "TST", "6": "TSE", "7": "STM"}


@dataclass(frozen=True)
class ArbitrationResult:
    """Saída imutável da arbitragem, incluindo a evidência de proveniência."""

    candidate: CitationCandidate
    parsed: ParsedCitation
    resolution: ResolutionResult
    source_candidates: tuple[CitationCandidate, ...]
    suppressed: tuple[CitationCandidate, ...] = ()
    reason: str = "preserved"

    def __post_init__(self) -> None:
        if not isinstance(self.source_candidates, tuple):
            raise TypeError("source_candidates deve ser tuple")
        if not isinstance(self.suppressed, tuple):
            raise TypeError("suppressed deve ser tuple")

    @property
    def survivor(self) -> CitationCandidate:
        """Alias semântico para a representação emitida."""
        return self.candidate

    @property
    def family(self) -> str:
        return self.candidate.family


def _lookup(value: Mapping[int, object] | Sequence[object], index: int) -> object | None:
    if isinstance(value, Mapping):
        return value.get(index)
    return value[index] if 0 <= index < len(value) else None


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _class_signature(value: str | None, tribunal: str | None = None) -> str | None:
    if not value:
        return None
    upper = value.upper()
    if (tribunal or "").upper() == "TST":
        match = re.search(
            r"(?:TST\s*-\s*)?(?P<prefix>[A-ZÀ-ÿ][A-Z0-9À-ÿ]*(?:\s*-\s*[A-ZÀ-ÿ][A-Z0-9À-ÿ]*)*)\s*-\s*(?=\d)",
            upper,
        )
        if match:
            blocks = re.findall(r"[A-ZÀ-ÿ][A-Z0-9À-ÿ]*", match.group("prefix"))
            if blocks:
                return f"TST:{blocks[-1]}"
        chain = re.findall(r"[A-ZÀ-ÿ][A-Z0-9À-ÿ]*", upper)
        if "-" in value and chain:
            return f"TST:{chain[-1]}"
        if re.fullmatch(r"[A-ZÀ-ÿ][A-Z0-9À-ÿ]*", value.strip(), re.I):
            return f"TST:{value.strip().upper()}"
    compact = re.sub(r"[^A-Z0-9À-ÿ]", "", upper)
    return compact or None


def _candidate_class(candidate: CitationCandidate, parsed: ParsedCitation) -> str | None:
    explicit = parsed.data.get("classe_raw")
    return _class_signature(explicit or candidate.text, parsed.tribunal)


def _candidate_tribunal(candidate: CitationCandidate, parsed: ParsedCitation, number: str | None) -> str | None:
    if parsed.tribunal:
        return parsed.tribunal
    match = re.search(r"\b(STF|STJ|TSE|TST|STM)\b", candidate.text, re.I)
    if match:
        return match.group(1).upper()
    if number and len(number) >= 7:
        return _CNJ_TRIBUNAL.get(number[-7])
    return None


def _primary_field(identity: object, name: str) -> object | None:
    if isinstance(identity, Mapping):
        return identity.get(name)
    return getattr(identity, name, None)


def _primary_number(identity: object, fallback: str | None) -> str | None:
    value = _primary_field(identity, "numero_normalizado")
    return str(value) if value else fallback


def _eligible_companion(
    primary: CitationCandidate,
    primary_parsed: ParsedCitation,
    companion: CitationCandidate,
    companion_parsed: ParsedCitation,
    identity: object,
) -> bool:
    if companion.family not in _ALLOWED_COMPANION_FAMILIES:
        return False
    if max(primary.start, companion.start) >= min(primary.end, companion.end):
        return False
    full = _primary_number(identity, primary_parsed.data.get("numero_normalizado"))
    if not full or len(full) < 14:
        return False
    partials = [_digits(token) for token in _NUMBER_TOKEN.findall(companion.text)]
    partials = [token for token in partials if len(token) >= 10 and full.startswith(token)]
    if not partials:
        return False
    primary_class = _primary_field(identity, "classe")
    companion_class = _candidate_class(companion, companion_parsed)
    if not primary_class or not companion_class:
        return False
    if str(primary_class) != companion_class:
        return False
    primary_tribunal = _primary_field(identity, "tribunal")
    companion_tribunal = _candidate_tribunal(companion, companion_parsed, max(partials, key=len))
    if primary_tribunal and companion_tribunal and str(primary_tribunal).upper() != companion_tribunal:
        return False
    return True


def structural_cnj_union_merge(
    text: str,
    candidates: Sequence[CitationCandidate],
    parsed: Mapping[int, ParsedCitation] | Sequence[ParsedCitation],
    resolutions: Mapping[int, ResolutionResult] | Sequence[ResolutionResult],
    *,
    primary_identities: Mapping[int, PrimaryIdentity | Mapping[str, object]] | None = None,
) -> tuple[ArbitrationResult, ...]:
    """Une uma única sobreposição CNJ + processo/contexto compatível.

    A função é deliberadamente abstencionista: resultados ambíguos, sem match,
    conflitos de classe/tribunal e múltiplos companheiros elegíveis ficam
    intactos.
    """
    if not isinstance(text, str):
        raise TypeError("text deve ser uma string")
    ordered = tuple(sorted(candidates, key=lambda item: (item.start, item.end, item.rule)))
    parser = CitationParser()
    parsed_by_position = {
        id(candidate): (_lookup(parsed, position) or parser.parse(candidate))
        for position, candidate in enumerate(candidates)
    }
    resolution_by_position = {
        id(candidate): _lookup(resolutions, position)
        for position, candidate in enumerate(candidates)
    }
    identities = primary_identities or MappingProxyType({})
    consumed: set[int] = set()
    output: list[ArbitrationResult] = []
    for candidate in ordered:
        if id(candidate) in consumed:
            continue
        current_parsed = parsed_by_position[id(candidate)]
        current_resolution = resolution_by_position[id(candidate)]
        if current_resolution is None:
            current_resolution = ResolutionResult("insufficient", None, (), None, "resolution_missing", None)
        if candidate.family != "processo_cnj" or current_resolution.status != "resolved":
            output.append(ArbitrationResult(candidate, current_parsed, current_resolution, (candidate,)))
            continue
        identity = identities.get(current_resolution.id_canonico) if current_resolution.id_canonico is not None else None
        if identity is None:
            output.append(ArbitrationResult(candidate, current_parsed, current_resolution, (candidate,)))
            continue
        eligible: list[tuple[CitationCandidate, ParsedCitation]] = []
        for other in ordered:
            if other is candidate or id(other) in consumed:
                continue
            other_parsed = parsed_by_position[id(other)]
            if _eligible_companion(candidate, current_parsed, other, other_parsed, identity):
                eligible.append((other, other_parsed))
        if len(eligible) != 1:
            reason = "preserved" if not eligible else "no_safe_arbitration"
            output.append(ArbitrationResult(candidate, current_parsed, current_resolution, (candidate,), reason=reason))
            continue
        companion, companion_parsed = eligible[0]
        start, end = min(candidate.start, companion.start), max(candidate.end, companion.end)
        merged = CitationCandidate(start, end, text[start:end], "structural_cnj_union_merge", "processo_cnj")
        merged_parsed = parser.parse(merged)
        consumed.update({id(candidate), id(companion)})
        output.append(
            ArbitrationResult(
                merged,
                merged_parsed,
                current_resolution,
                (candidate, companion),
                (candidate, companion),
                "structural_cnj_union_merge",
            )
        )
    # Add suppressed companions only through the merged result and retain all
    # non-consumed candidates.  Source ordering remains deterministic.
    merged_sources = {id(source) for item in output if item.reason == "structural_cnj_union_merge" for source in item.source_candidates}
    final = [item for item in output if id(item.candidate) not in merged_sources or item.reason == "structural_cnj_union_merge"]
    return tuple(sorted(final, key=lambda item: (item.candidate.start, item.candidate.end, item.candidate.rule)))


class StructuralCNJArbitrator:
    """Objeto reutilizável para arbitragem com metadados do resolver."""

    def __init__(self, primary_identities: Mapping[int, PrimaryIdentity | Mapping[str, object]]) -> None:
        self._primary_identities = MappingProxyType(dict(primary_identities))

    @property
    def primary_identities(self) -> Mapping[int, PrimaryIdentity | Mapping[str, object]]:
        return self._primary_identities

    def arbitrate(
        self,
        text: str,
        candidates: Sequence[CitationCandidate],
        parsed: Mapping[int, ParsedCitation] | Sequence[ParsedCitation],
        resolutions: Mapping[int, ResolutionResult] | Sequence[ResolutionResult],
    ) -> tuple[ArbitrationResult, ...]:
        return structural_cnj_union_merge(
            text, candidates, parsed, resolutions, primary_identities=self._primary_identities
        )
