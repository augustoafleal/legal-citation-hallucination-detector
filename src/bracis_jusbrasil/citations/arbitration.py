"""Arbitragem estrutural de sobreposições CNJ e legais.

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
_H4_HEADER_LAYOUT = re.compile(
    r"\b(?:poder judici[aá]rio|supremo tribunal|tribunal superior|"
    r"cabe[cç]alho|identifica[cç][aã]o)\b",
    re.IGNORECASE,
)
_H4_PARTY_TERMS = re.compile(
    r"\b(?:recorrente|recorrido|agravante|agravado|impetrante|paciente|"
    r"autor|r[ée]u|reclamante|reclamada)\b",
    re.IGNORECASE,
)
_H4_HISTORY_TERMS = re.compile(
    r"\b(?:origem|autos? de origem|hist[oó]rico|andamento|tr[âa]mite|"
    r"distribu[ií]d[oa]|remetid[oa]|instaura[cç][aã]o|j[aá] sentenciad[oa])\b",
    re.IGNORECASE,
)
_H4_PROCESS_NEAR = re.compile(
    r"\b(?:processos?|autos?|feitos?|inquéritos?|"
    r"a(?:ç|c)[aã](?:o|oes|ões)?\s+pen(?:al|ais)|recursos?)\s*"
    r"(?:n[ºo°.]?|número)?\s*",
    re.IGNORECASE,
)
_H4_PRECEDENT_ARGUMENT = re.compile(
    r"\b(?:precedente|jurisprud[êe]ncia|ratio decidendi|tese|conforme|"
    r"confira-se|como\s+j[aá]\s+(?:se\s+)?(?:decidiu|reconheceu)|"
    r"entendimento|orienta[cç][aã]o|firmou|aplica-se|invoca-se|corrobora)\b",
    re.IGNORECASE,
)
_H4_CITATIONAL_VERB = re.compile(
    r"\b(?:cita|citado|citada|invoca|invocado|conforme|confira-se|"
    r"reconheceu|decidiu|firmou|aplicou|transcreve)\b",
    re.IGNORECASE,
)
_G4_PROCESS_CLASS = re.compile(
    r"\b(?:apela[cç][aã]o|apela[cç][õo]es|recursos?|agravos?|"
    r"habeas\s+corpus|mandado\s+de\s+seguran[cç]a|reclama[cç][aã]o|"
    r"embargos?)\b",
    re.IGNORECASE,
)
_G4_PUBLICATION_OR_AUTHOR = re.compile(
    r"\b(?:dje|di[aá]rio\s+de\s+justi[cç]a|relator(?:a)?|rel\.|ementa)\b",
    re.IGNORECASE,
)
_G4_FOUNDATION = re.compile(
    r"\b(?:no\s+mesmo\s+sentido|conforme|entendimento|"
    r"orienta[cç][aã]o|jurisprud[êe]ncia|precedentes?)\b",
    re.IGNORECASE,
)
_G4_QUOTE = re.compile(r'["“”]')
_H4_WINDOW = 120
_G4_WINDOW = 1600


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


def is_cnj_preserved_by_jurisprudential_guard(
    text: str, candidate: CitationCandidate
) -> bool:
    """Preserve a CNJ embedded in a bounded jurisprudential citation block."""
    local = text[
        max(0, candidate.start - _G4_WINDOW):min(len(text), candidate.end + _G4_WINDOW)
    ]
    return bool(
        len(_G4_PROCESS_CLASS.findall(local)) >= 2
        and _G4_PUBLICATION_OR_AUTHOR.search(local)
        and _G4_QUOTE.search(local)
        and _G4_FOUNDATION.search(local)
    )


def _h4_context(text: str, candidate: CitationCandidate, parsed: ParsedCitation) -> tuple[str, str] | None:
    raw_number = parsed.data.get("numero_raw")
    if not isinstance(raw_number, str):
        return None
    raw_position = candidate.text.find(raw_number)
    if raw_position < 0:
        return None
    raw_start = candidate.start + raw_position
    raw_end = raw_start + len(raw_number)
    context_start = max(0, candidate.start - _H4_WINDOW)
    context_end = min(len(text), candidate.end + _H4_WINDOW)
    return text[context_start:raw_start], text[raw_end:context_end]


def _overlaps(left: CitationCandidate, right: CitationCandidate) -> bool:
    return max(left.start, right.start) < min(left.end, right.end)


def _has_overlapping_candidate(
    candidate: CitationCandidate, candidates: Sequence[CitationCandidate]
) -> bool:
    for other in candidates:
        if other is candidate or not _overlaps(candidate, other):
            continue
        return True
    return False


def is_non_precedential_cnj_reference(
    text: str,
    candidate: CitationCandidate,
    parsed: ParsedCitation,
    resolution: ResolutionResult,
    candidates: Sequence[CitationCandidate],
) -> bool:
    """Return whether the guarded H-CNJ-4 policy can safely suppress a CNJ."""
    if candidate.rule != "cnj" or candidate.family != "processo_cnj":
        return False
    if resolution.status != "no_match":
        return False
    if is_cnj_preserved_by_jurisprudential_guard(text, candidate):
        return False
    if _has_overlapping_candidate(candidate, candidates):
        return False
    context = _h4_context(text, candidate, parsed)
    if context is None:
        return False
    before, after = context
    nearby = f"{before} {after}"
    process_identifier = bool(_H4_PROCESS_NEAR.search(before[-100:]))
    own_or_origin_layout = bool(
        _H4_HEADER_LAYOUT.search(before)
        or _H4_PARTY_TERMS.search(nearby)
        or _H4_HISTORY_TERMS.search(nearby)
    )
    return bool(
        process_identifier
        and own_or_origin_layout
        and not _H4_PRECEDENT_ARGUMENT.search(nearby)
        and not _H4_CITATIONAL_VERB.search(nearby)
    )


def filter_non_precedential_cnj_references(
    text: str,
    candidates: Sequence[CitationCandidate],
    parsed: Mapping[int, ParsedCitation] | Sequence[ParsedCitation],
    resolutions: Mapping[int, ResolutionResult] | Sequence[ResolutionResult],
) -> tuple[CitationCandidate, ...]:
    """Apply the V10 CNJ-only filter after parsing/resolution and before merge."""
    parser = CitationParser()
    parsed_by_candidate = {
        id(candidate): (_lookup(parsed, position) or parser.parse(candidate))
        for position, candidate in enumerate(candidates)
    }
    resolutions_by_candidate = {
        id(candidate): (
            _lookup(resolutions, position)
            or ResolutionResult("insufficient", None, (), None, "resolution_missing", None)
        )
        for position, candidate in enumerate(candidates)
    }
    return tuple(
        candidate
        for candidate in candidates
        if not is_non_precedential_cnj_reference(
            text,
            candidate,
            parsed_by_candidate[id(candidate)],
            resolutions_by_candidate[id(candidate)],
            candidates,
        )
    )


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
    filtered_candidates = filter_non_precedential_cnj_references(text, candidates, parsed, resolutions)
    ordered = tuple(sorted(filtered_candidates, key=lambda item: (item.start, item.end, item.rule)))
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


def _legal_priority(output: ArbitrationResult) -> tuple[int, int]:
    identity = output.parsed.legal
    if identity is None:
        return (0, output.candidate.end - output.candidate.start)
    has_complement = any(
        value is not None
        for value in (
            identity.paragraph_normalized,
            identity.inciso_normalized,
            identity.alinea_normalized,
        )
    )
    if identity.has_complete_identity and has_complement:
        rank = 5
    elif identity.has_complete_identity:
        rank = 4
    elif identity.article_normalized and has_complement:
        rank = 3
    elif identity.article_normalized:
        rank = 2
    else:
        rank = 1
    return rank, output.candidate.end - output.candidate.start


def _safe_legal_supersession(
    survivor: ArbitrationResult, partial: ArbitrationResult
) -> bool:
    if not survivor.family.startswith("lei_") or not partial.family.startswith("lei_"):
        return False
    survivor_identity = survivor.parsed.legal
    partial_identity = partial.parsed.legal
    if survivor_identity is None or partial_identity is None:
        return False
    if not survivor_identity.article_normalized or not partial_identity.article_normalized:
        return False
    if survivor_identity.article_normalized != partial_identity.article_normalized:
        return False
    if (
        survivor_identity.diploma_normalized
        and partial_identity.diploma_normalized
        and survivor_identity.diploma_normalized != partial_identity.diploma_normalized
    ):
        return False
    contains = (
        survivor.candidate.start <= partial.candidate.start
        and survivor.candidate.end >= partial.candidate.end
    )
    return contains and _legal_priority(survivor) > _legal_priority(partial)


def legal_maximal_span_arbitration(
    outputs: Sequence[ArbitrationResult],
) -> tuple[ArbitrationResult, ...]:
    """Suprime apenas spans legais inferiores contidos na mesma identidade."""
    ordered = tuple(sorted(outputs, key=lambda item: (item.candidate.start, item.candidate.end, item.candidate.rule)))
    suppressed_ids: set[int] = set()
    replacements: dict[int, ArbitrationResult] = {}
    legal = [item for item in ordered if item.family.startswith("lei_")]
    for survivor in sorted(legal, key=_legal_priority, reverse=True):
        if id(survivor) in suppressed_ids:
            continue
        suppressed = [
            item
            for item in legal
            if item is not survivor
            and id(item) not in suppressed_ids
            and _safe_legal_supersession(survivor, item)
        ]
        if not suppressed:
            continue
        suppressed_ids.update(id(item) for item in suppressed)
        sources = tuple(
            dict.fromkeys(
                (*survivor.source_candidates, *(item.candidate for item in suppressed))
            )
        )
        replacements[id(survivor)] = ArbitrationResult(
            candidate=survivor.candidate,
            parsed=survivor.parsed,
            resolution=survivor.resolution,
            source_candidates=sources,
            suppressed=tuple((*survivor.suppressed, *(item.candidate for item in suppressed))),
            reason="legal_maximal_span",
        )
    final = [
        replacements.get(id(item), item)
        for item in ordered
        if id(item) not in suppressed_ids
    ]
    return tuple(sorted(final, key=lambda item: (item.candidate.start, item.candidate.end, item.candidate.rule)))


def arbitrate_citations(
    text: str,
    candidates: Sequence[CitationCandidate],
    parsed: Mapping[int, ParsedCitation] | Sequence[ParsedCitation],
    resolutions: Mapping[int, ResolutionResult] | Sequence[ResolutionResult],
    *,
    primary_identities: Mapping[int, PrimaryIdentity | Mapping[str, object]] | None = None,
) -> tuple[ArbitrationResult, ...]:
    """Compõe a arbitragem CNJ V10 inalterada com a arbitragem legal V11."""
    v10_outputs = structural_cnj_union_merge(
        text,
        candidates,
        parsed,
        resolutions,
        primary_identities=primary_identities,
    )
    return legal_maximal_span_arbitration(v10_outputs)
