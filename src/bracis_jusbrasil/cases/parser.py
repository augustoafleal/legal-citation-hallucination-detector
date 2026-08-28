"""Parser estrutural da identidade principal dos acórdãos canônicos."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..normalization import normalize_case_number, normalize_tribunal


@dataclass(frozen=True)
class ParsedCaseIdentity:
    """Identidade principal encontrada no bloco formal de um acórdão."""

    tribunal: str
    numero_raw: str
    numero_normalizado: str
    numero_family: str
    parse_source: str
    identity_pattern: str
    identity_block: str
    identity_block_start: int
    marker_source: str = "formal"


CNJ = re.compile(
    r"(?<!\d)\d{3,7}\s*-\s*\d{2}\s*[. ]\s*\d{4}\s*[. ]\s*"
    r"\d\s*[. ]\s*\d{2}\s*[. ]\s*\d{4}(?:\s*/\s*[A-Z]{2})?(?!\d)",
    re.I,
)
STF_NUMBER = re.compile(r"(?<!\d)\d{1,3}(?:\.\d{3})+(?!\d)")
STJ_TITLE = re.compile(
    r"\b(?P<class>(?:RECURSO|AGRAVO|AGINT|HABEAS|EMBARGOS|MANDADO|AÇÃO|"
    r"CONFLITO|CAUTELAR|QO|RECLAMAÇÃO|SUSPENSÃO|PETIÇÃO|EDCL)[^.;]{0,140}?)"
    r"\s+N[ºO.]\s*(?P<num>\d{1,3}(?:\.\d{3})*|\d+)\s*-\s*"
    r"(?P<uf>[A-Z]{2})\b",
    re.I,
)
TST_NUMBER = re.compile(
    r"(?P<raw>(?:TST\s*-\s*)?[A-Z][A-Z0-9]*(?:\s*-\s*[A-Z][A-Z0-9]*)*"
    r"\s*-\s*\d{1,7}\s*-\s*\d{2}\s*[. ]\s*\d{4}\s*[. ]\s*"
    r"\d\s*[. ]\s*\d{2}\s*[. ]\s*\d{4})",
    re.I,
)
STM_TITLE = re.compile(
    r"\b(?P<class>(?:APELA[ÇC][AÃ]O|AGRAVO|EMBARGOS|HABEAS CORPUS|CONFLITO|"
    r"RECURSO|A[ÇC][AÃ]O|CORREI[ÇC][AÃ]O|REPRESENTA[ÇC][AÃ]O|"
    r"RECLAMA[ÇC][AÃ]O)[^,]{0,140}?)\s+N[ºO.]\s*"
    r"(?P<num>\d{3,7}\s*-\s*\d{2}\s*[. ]\s*\d{4}\s*[. ]\s*"
    r"\d\s*[. ]\s*\d{2}\s*[. ]\s*\d{4})(?:\s*/\s*[A-Z]{2})?",
    re.I,
)

TSE_GAP = r"[\s\W_]*"
TSE_INSTITUTION = re.compile(
    r"TR(?:IBUNAL|E3UNAL)"
    + TSE_GAP
    + r"(?:SUPER(?:IOR|POR)" + TSE_GAP + r")?"
    + r"ELEITOR(?:A" + TSE_GAP + r"L)",
    re.I,
)
TSE_ACORDAO = re.compile(
    r"AC(?:Ó|O|€)" + TSE_GAP + r"RD(?:Ã|A)?" + TSE_GAP + r"O", re.I
)
TSE_RELATOR = re.compile(r"\b(?:RELATOR|REIATOR)\b", re.I)
TSE_TITULO = re.compile(
    r"\b(?:RECURSO|AGRAVO|EMBARGOS|AÇÃO|ACAO|PETIÇÃO|PETICAO|MANDADO|"
    r"HABEAS|LISTA|REPRESENTAÇÃO|REPRESENTACAO|RECLAMAÇÃO|RECLAMACAO)",
    re.I,
)
TSE_FALLBACK_LABEL = re.compile(
    r"\b(?:REspe|AgR-REspe|ED-AgR-AR|AgR-RO|RECURSO ESPECIAL ELEITORAL|"
    r"AGRAVO REGIMENTAL|EMBARGOS)\b",
    re.I,
)
TSE_LEGACY = re.compile(
    r"\bN\s*(?:[ºO‚.]|o)?\s*(?P<num>\d{1,3}(?:[.,]\d{3})+)\b", re.I
)
TSE_NUMBER_DEGRADED = re.compile(
    r"(?<!\d)(?P<seq>\d(?:[\s.,;:_€‚ƒ„…†‡‰]*\d){0,6})\s*[-–—]\s*"
    r"(?P<dv>\d(?:[\s.,;:_€‚ƒ„…†‡‰]*\d))\s*"
    r"[\s.,;:_€‚ƒ„…†‡‰]*(?P<year>\d(?:[\s.,;:_€‚ƒ„…†‡‰]*\d){3})\s*"
    r"[\s.,;:_€‚ƒ„…†‡‰]*(?P<segment>\d)\s*"
    r"[\s.,;:_€‚ƒ„…†‡‰]*(?P<trib>\d(?:[\s.,;:_€‚ƒ„…†‡‰]*\d))\s*"
    r"[\s.,;:_€‚ƒ„…†‡‰]*(?P<origin>\d(?:[\s.,;:_€‚ƒ„…†‡‰]*\d){3})(?!\d)",
    re.I,
)
TSE_CASE_NUMBER = re.compile(
    r"\d{1,7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}(?:/[A-Z]{2})?$", re.I
)


def _compact(text: str) -> str:
    return " ".join((text or "").split())


def _digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", value or "")


def _tse_cnj_value(match: re.Match[str]) -> str:
    return "".join(
        _digits(match.group(name))
        for name in ("seq", "dv", "year", "segment", "trib", "origin")
    )


def _record(
    tribunal: str,
    pattern: str,
    block: str,
    start: int,
    raw: str,
    family: str = "cnj",
    source: str = "formal_header",
    marker_source: str = "formal",
) -> ParsedCaseIdentity:
    return ParsedCaseIdentity(
        tribunal=tribunal,
        numero_raw=raw,
        numero_normalizado=normalize_case_number(raw),
        numero_family=family,
        parse_source=source,
        identity_pattern=pattern,
        identity_block=block,
        identity_block_start=start,
        marker_source=marker_source,
    )


def _parse_stf(text: str) -> ParsedCaseIdentity | None:
    s = _compact(text)
    relator = re.search(r"\bRELATORA?\b", s[:5000], re.I)
    if not relator:
        return None
    anchors = list(
        re.finditer(
            r"\b(?:PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA|SEXTA) TURMA\b|"
            r"\bPLEN[ÁA]RIO\b",
            s[: relator.start()],
            re.I,
        )
    )
    start = anchors[-1].start() if anchors else 0
    matches = list(STF_NUMBER.finditer(s, start, relator.start()))
    if len({normalize_case_number(m.group(0)) for m in matches}) != 1:
        return None
    match = matches[-1] if matches else None
    if match is None:
        return None
    return _record(
        "STF",
        "STF: órgão + classe + número + relator",
        s[start : min(len(s), relator.end() + 120)],
        start,
        match.group(0),
    )


def _parse_stj(text: str) -> ParsedCaseIdentity | None:
    s = _compact(text)
    matches = [
        m
        for m in STJ_TITLE.finditer(s[:2200])
        if re.search(r"\bRELATORA?\b", s[m.end() : m.end() + 260], re.I)
    ]
    if not matches:
        return None
    match = matches[0]
    relator = re.search(r"\bRELATORA?\b", s[match.end() : match.end() + 260], re.I)
    if relator is None:
        return None
    return _record(
        "STJ",
        "STJ: classe + Nº + número + UF + relator",
        s[match.start() : min(len(s), match.end() + relator.end() + 120)],
        match.start(),
        match.group("num"),
    )


def _parse_tse(text: str) -> ParsedCaseIdentity | None:
    s = _compact(text)
    institution = TSE_INSTITUTION.search(s[:5000])
    if institution is None:
        return None
    acordao = TSE_ACORDAO.search(
        s, institution.end(), min(len(s), institution.end() + 1500)
    )
    number_start = acordao.end() if acordao else institution.end()
    relator = TSE_RELATOR.search(
        s, number_start, min(len(s), number_start + 2200)
    )
    header_end = relator.start() if relator else min(len(s), number_start + 1800)
    marker_source = "formal"
    if not re.fullmatch(r"TRIBUNAL\s+SUPERIOR\s+ELEITORAL", institution.group(), re.I):
        marker_source = "ocr_tolerant"
    if acordao and not re.fullmatch(r"AC[ÓO]RD[ÃA]O", acordao.group(), re.I):
        marker_source = "ocr_tolerant"
    block = s[institution.start() : min(len(s), header_end + 100)]
    matches = list(TSE_NUMBER_DEGRADED.finditer(s, number_start, header_end))
    values = {_tse_cnj_value(match) for match in matches}
    if matches and len(values) != 1:
        return None
    if len(values) == 1 and matches:
        match = matches[0]
        source = (
            "formal_header"
            if TSE_CASE_NUMBER.fullmatch(match.group(0))
            else "normalized_header"
        )
        return _record(
            "TSE",
            "TSE: instituição + acórdão + classe + CNJ",
            block,
            institution.start(),
            match.group(0),
            source=source,
            marker_source=marker_source,
        )
    legacy = TSE_LEGACY.search(s, number_start, header_end)
    if legacy and TSE_TITULO.search(s[institution.end() : legacy.start()]):
        return _record(
            "TSE",
            "TSE: instituição + classe + número legado",
            block,
            institution.start(),
            legacy.group("num"),
            family="legacy",
            marker_source=marker_source,
        )
    for match in TSE_NUMBER_DEGRADED.finditer(s, header_end):
        context = s[max(header_end, match.start() - 350) : match.start()]
        if TSE_FALLBACK_LABEL.search(context):
            return _record(
                "TSE",
                "TSE: fallback intradocumento",
                block,
                institution.start(),
                match.group(0),
                source="intradocument_fallback",
                marker_source=marker_source,
            )
    return None


def _parse_tst(text: str) -> ParsedCaseIdentity | None:
    s = _compact(text)
    anchor = re.search(
        r"VISTOS,? RELATADOS E DISCUTIDOS ESTES AUTOS", s, re.I
    )
    if anchor is None:
        return None
    tail = s[anchor.end() : anchor.end() + 1300]
    end_marker = re.search(r",\s*EM QUE\b", tail, re.I)
    end = (
        anchor.end() + end_marker.start()
        if end_marker
        else min(len(s), anchor.end() + 1000)
    )
    matches = list(TST_NUMBER.finditer(s, anchor.end(), end))
    if len({normalize_case_number(m.group(0)) for m in matches}) != 1:
        return None
    match = matches[0] if matches else None
    if match is None:
        return None
    return _record(
        "TST",
        "TST: Vistos/autos + identificador TST/CNJ",
        s[anchor.start() : min(len(s), end + 120)],
        anchor.start(),
        match.group("raw"),
    )


def _parse_stm(text: str) -> ParsedCaseIdentity | None:
    s = _compact(text)
    relator = re.search(r"\bRELATORA?\b", s[:5500], re.I)
    limit = relator.start() if relator else 5500
    matches = list(STM_TITLE.finditer(s[:limit]))
    if not matches:
        return None
    match = matches[0]
    return _record(
        "STM",
        "STM: classe + Nº + CNJ + UF",
        s[match.start() : min(len(s), match.end() + 180)],
        match.start(),
        match.group("num"),
    )


_PARSERS = {
    "STF": _parse_stf,
    "STJ": _parse_stj,
    "TSE": _parse_tse,
    "TST": _parse_tst,
    "STM": _parse_stm,
}


def parse_case_identity(text: str, tribunal: str) -> ParsedCaseIdentity:
    """Extrai a identidade formal principal ou falha explicitamente."""
    normalized_tribunal = normalize_tribunal(tribunal)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Texto do acórdão vazio")

    parsed = _PARSERS[normalized_tribunal](text)
    if parsed is None:
        raise ValueError(
            f"Identidade principal não extraída para {normalized_tribunal}"
        )
    return parsed
