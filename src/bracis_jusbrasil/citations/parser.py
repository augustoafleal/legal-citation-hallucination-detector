"""Parsing estrutural e determinístico de citações já delimitadas."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping

from .detector import CitationCandidate


_FAMILY_TYPES = {
    "processo_cnj": "jurisprudencia",
    "processo_ou_recurso_numerado": "jurisprudencia",
    "sumula_numerada": "jurisprudencia",
    "jurisprudencia_tribunal_contextual": "jurisprudencia",
    "jurisprudencia_referencia_geral": "jurisprudencia",
    "lei_dispositivo_com_diploma": "lei",
    "lei_dispositivo_sem_diploma": "lei",
    "lei_referencia_geral": "lei",
}

_CNJ_PATTERN = re.compile(
    r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b"
)
_TRIBUNAL_PATTERN = re.compile(
    r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|"
    r"Superior Tribunal de Justiça|Tribunal Superior Eleitoral|"
    r"Tribunal Superior do Trabalho|Superior Tribunal Militar)\b",
    re.IGNORECASE,
)
_TRIBUNAL_NORMALIZATION = {
    "STF": "STF",
    "SUPREMO TRIBUNAL FEDERAL": "STF",
    "STJ": "STJ",
    "SUPERIOR TRIBUNAL DE JUSTIÇA": "STJ",
    "TSE": "TSE",
    "TRIBUNAL SUPERIOR ELEITORAL": "TSE",
    "TST": "TST",
    "TRIBUNAL SUPERIOR DO TRABALHO": "TST",
    "STM": "STM",
    "SUPERIOR TRIBUNAL MILITAR": "STM",
}
_PROCESS_CLASS_PATTERN = re.compile(
    r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|"
    r"AIRR|AP|RO|Agravo|Recurso Especial|Recurso Extraordinário|"
    r"Habeas Corpus|Reclamação)\b",
    re.IGNORECASE,
)
_NUMBERED_CASE_PATTERN = re.compile(
    r"(?P<classe>(?:(?:AgInt|AgRg|EDcl|ED)\s+(?:no|n[oº.]+)\s+)?"
    r"(?:AREsp|REsp|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|"
    r"Recurso\s+(?:Especial|Extraordin[aá]rio)|Agravo(?:\s+Regimental)?|"
    r"Habeas\s+Corpus|Reclamaç[aã]o))\s*(?:n[ºo.]?\s*)?"
    r"(?P<number>\d[\d.\-/ ]{2,}\d)(?:\s*(?P<uf>[-/]\s*[A-Z]{2}))?",
    re.IGNORECASE,
)
_RECURSO_CLASS_PATTERN = re.compile(r"(?:AREsp|REsp|Rec[.]?\s*Esp[.]?|Recurso\s+Especial)", re.IGNORECASE)
_MARKER_NUMBER = {
    False: re.compile(
        r"(?<![A-Za-z])n(?:o|[º°.]|o)?[ \t\u00a0]*"
        r"(?P<number>\d(?:[\d.\-/ \t\u00a0]*\d)?)",
        re.IGNORECASE,
    ),
    True: re.compile(
        r"(?<![A-Za-z])n(?:o|[º°.]|o)?\s*"
        r"(?P<number>\d(?:[\d.\-/ \n]*\d)?)",
        re.IGNORECASE,
    ),
}
_UF_CODES = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
_UF_TAIL = re.compile(
    r"^\s*(?P<raw>(?:[-/]\s*|\(\s*|[ \t\u00a0]+)"
    r"(?P<uf>" + "|".join(_UF_CODES) + r")(?:\s*\))?)\b"
)
_OCR_TOKEN = re.compile(r"\d(?:[\d.\-/\s]*[OIlS])(?:[\d.\-/\sOIlS]*\d)", re.IGNORECASE)
_OCR_TRANSLATION = str.maketrans({"O": "0", "o": "0", "I": "1", "i": "1", "l": "1", "S": "5", "s": "5"})
_SUMULA_PATTERN = re.compile(r"\bS[ÚU]MULA(?:\s+VINCULANTE)?\s*(?:N[ºO.]?\s*)?(?P<number>\d+)", re.IGNORECASE)
_ARTICLE_PATTERN = re.compile(r"\b(?:art(?:igo)?s?[.]?\s*)(?P<number>\d+(?:[.]\d+)*(?:[ºo]|-[A-Za-z])?)", re.IGNORECASE)
_PARAGRAPH_PATTERN = re.compile(r"(?P<raw>§{1,2}\s*(?P<number>\d+(?:[ºo])?|único))", re.IGNORECASE)
_DIPLOMA_PATTERN = re.compile(
    r"\b(?:Constituiç[aã]o(?: Federal)?|C[oó]digo(?: Civil| Penal| de Processo Civil| de Processo Penal)?|"
    r"Lei(?: Complementar)?\s*(?:n[ºo.]?\s*)?\d+[./-]?[\d./-]*|CLT|CPC|CPP|CC|CDC|CPM)\b",
    re.IGNORECASE,
)
_LAW_NUMBER_PATTERN = re.compile(r"\bLei(?: Complementar)?\s*(?:n[ºo.]?\s*)?(?P<number>\d+[./-]?\d*)(?:/(?P<year>\d{4}))?", re.IGNORECASE)
_RELATOR_PATTERN = re.compile(r"\b(?:Rel\.?|Relator(?:a)?)\s*(?:Min\.?|Ministra|Ministro|Des\.?)?\s*(?P<name>[A-Z][A-Za-zÀ-ÿ .-]{2,})", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(?P<year>19\d{2}|20\d{2})\b")
_CNJ_CONTEXT_PREFIX = re.compile(
    r"(?P<tribunal>\b(?:STF|STJ|TSE|TST|STM)\b)"
    r"\s*[-:]\s*(?P<classe>[A-Za-zÀ-ÿ][A-Za-z0-9À-ÿ.-]*)\s*[-:]?\s*$",
    re.IGNORECASE,
)
_COMPOUND_PROCEDURAL_CHAIN = re.compile(
    r"(?<![A-Za-z0-9À-ÿ]-)(?<!\w)"
    r"(?P<chain>(?:TST-(?:ED|E|RR)(?:-(?:ED|E|RR)){0,3}|"
    r"(?:ED|E|RR)(?:-(?:ED|E|RR)){1,4}))"
    r"-(?P<number>\d{1,7}-\d{2}[.]\d{4}[.]\d[.]\d{2}[.]\d{4})(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedCitation:
    """Descrição textual auditável de uma ``CitationCandidate``."""

    family: str
    tipo: str
    tribunal_raw: str | None
    tribunal: str | None
    tribunal_source: str
    data: Mapping[str, str]
    provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        """Defende os payloads contra mutação e aliasing externos."""
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _tribunal(text: str) -> tuple[str | None, str | None, str]:
    match = _TRIBUNAL_PATTERN.search(text)
    if match is None:
        return None, None, "unknown"
    raw = match.group(0)
    return raw, _TRIBUNAL_NORMALIZATION[raw.upper()], "explicit"


def _marker_number(text: str, *, allow_newline: bool) -> tuple[str | None, int | None, int | None]:
    match = _MARKER_NUMBER[allow_newline].search(text)
    if match is None:
        return None, None, None
    if not allow_newline and re.match(r"[.\-]*[ \t\u00a0]*\n", text[match.end("number") :]):
        return None, None, None
    return match.group("number"), match.start("number"), match.end("number")


def _number_position(text: str, raw: str) -> tuple[int, int]:
    start = text.find(raw)
    return start, start + len(raw)


def _tail_uf(text: str, end: int | None) -> tuple[str | None, str | None]:
    if end is None:
        return None, None
    match = _UF_TAIL.match(text[end:])
    if match is None:
        return None, None
    return match.group("raw"), match.group("uf")


def _process_data(text: str) -> tuple[dict[str, str], dict[str, str]]:
    data: dict[str, str] = {}
    provenance: dict[str, str] = {}
    match = _NUMBERED_CASE_PATTERN.search(text)
    class_match = _PROCESS_CLASS_PATTERN.search(text)
    if match is not None:
        data["classe_raw"] = match.group("classe")
    elif class_match is not None:
        data["classe_raw"] = class_match.group(0)

    raw: str | None = match.group("number") if match else None
    if raw is not None:
        start, end = _number_position(text, raw)
        provenance["numero"] = "normalized"
    else:
        raw, start, end = _marker_number(text, allow_newline=False)
        if raw is None:
            raw, start, end = _marker_number(text, allow_newline=True)
        if raw is not None:
            provenance["numero"] = "structural_repair"

    if raw is not None:
        uf_raw, uf = _tail_uf(text, end)
        ocr_match = _OCR_TOKEN.search(text)
        if (
            ocr_match is not None
            and ocr_match.start() == start
            and re.search(r"[OIlS]", ocr_match.group(0), re.IGNORECASE)
        ):
            raw = ocr_match.group(0)
            start, end = ocr_match.start(), ocr_match.end()
            normalized = _digits(raw.translate(_OCR_TRANSLATION))
            provenance["numero"] = "ocr_repair"
        else:
            normalized = _digits(raw)
        data["numero_raw"] = raw
        data["numero_normalizado"] = normalized
        data["numero_family"] = "recurso_number" if _RECURSO_CLASS_PATTERN.search(text) else "case_number"

        if uf is not None:
            data["uf_raw"] = uf_raw
            data["uf"] = uf
            provenance["uf"] = "explicit"
    return data, provenance


def _compound_procedural_chain_data(
    text: str,
) -> tuple[dict[str, str], dict[str, str], str | None]:
    match = _COMPOUND_PROCEDURAL_CHAIN.fullmatch(text)
    if match is None:
        return {}, {}, None
    tokens = match.group("chain").upper().split("-")
    process_tokens = tokens[1:] if tokens[0] == "TST" else tokens
    if any(process_tokens.count(token) > 2 for token in set(process_tokens)):
        return {}, {}, None
    number = match.group("number")
    return (
        {
            "classe_raw": process_tokens[-1],
            "numero_raw": number,
            "numero_normalizado": _digits(number),
            "numero_family": "case_number",
        },
        {"numero": "compound_procedural_chain", "classe": "explicit"},
        "TST" if tokens[0] == "TST" else None,
    )


def _cnj_data(text: str) -> tuple[dict[str, str], dict[str, str]]:
    match = _CNJ_PATTERN.search(text)
    if match is None:
        return {}, {}
    raw = match.group(0)
    return {"numero_raw": raw, "numero_normalizado": _digits(raw), "numero_family": "cnj"}, {"numero": "normalized"}


def _cnj_context_data(
    candidate: CitationCandidate,
    context: str | None,
    data: dict[str, str],
    provenance: dict[str, str],
) -> tuple[str | None, str | None, str]:
    """Enrich a CNJ with a strictly adjacent ``tribunal-classe-CNJ`` prefix.

    This is intentionally opt-in so ``parse(candidate)`` retains the V1
    contract.  The prefix cannot cross a newline or sentence delimiter and is
    therefore not a broad context scan.
    """
    if not context:
        return None, None, "unknown"
    start = candidate.start
    if start < 0 or candidate.end > len(context) or context[start : candidate.end] != candidate.text:
        return None, None, "unknown"
    # Oracle/integration callers may pass a candidate span that already
    # contains its local prefix (with ``start == 0``).  Anchor at the CNJ
    # token inside that span while retaining the same strict locality guard.
    cnj_offset = candidate.text.find(data.get("numero_raw", ""))
    anchor = start + cnj_offset if cnj_offset >= 0 else start
    prefix = context[max(0, anchor - 100) : anchor]
    if "\n" in prefix or "\r" in prefix:
        prefix = prefix.rsplit("\n", 1)[-1].rsplit("\r", 1)[-1]
    match = _CNJ_CONTEXT_PREFIX.search(prefix)
    if match is None:
        return None, None, "unknown"
    tribunal = match.group("tribunal").upper()
    classe = match.group("classe").rstrip(".-")
    if not classe:
        return None, None, "unknown"
    data["classe_raw"] = classe
    provenance["classe"] = "contextual_structural"
    return match.group("tribunal"), tribunal, "explicit"


def _sumula_data(text: str) -> tuple[dict[str, str], dict[str, str]]:
    match = _SUMULA_PATTERN.search(text)
    if match is None:
        return {}, {}
    raw = match.group("number")
    return {"sumula_numero_raw": raw, "sumula_numero": _digits(raw)}, {"sumula_numero": "normalized"}


def _contextual_data(text: str) -> tuple[dict[str, str], dict[str, str]]:
    data: dict[str, str] = {}
    provenance: dict[str, str] = {}
    relator = _RELATOR_PATTERN.search(text)
    year = _YEAR_PATTERN.search(text)
    if relator is not None:
        data["relator_raw"] = relator.group(0)
        provenance["relator"] = "explicit"
    if year is not None:
        data["ano"] = year.group("year")
        provenance["ano"] = "explicit"
    return data, provenance


def _legal_data(text: str) -> tuple[dict[str, str], dict[str, str]]:
    data: dict[str, str] = {}
    provenance: dict[str, str] = {}
    article = _ARTICLE_PATTERN.search(text)
    paragraph = _PARAGRAPH_PATTERN.search(text)
    diploma = _DIPLOMA_PATTERN.search(text)
    law = _LAW_NUMBER_PATTERN.search(text)
    if article is not None:
        data["artigo"] = _digits(article.group("number").rstrip("ºo"))
        provenance["artigo"] = "normalized"
    if paragraph is not None:
        data["paragrafo"] = paragraph.group("number").rstrip("ºo")
        provenance["paragrafo"] = "explicit"
    if diploma is not None:
        raw = diploma.group(0)
        data["diploma_raw"] = raw
        data["diploma_normalizado"] = re.sub(r"\s+", " ", raw).upper()
        provenance["diploma"] = "normalized"
    if law is not None:
        raw = law.group("number")
        data["law_number"] = _digits(raw)
        provenance["law_number"] = "normalized"
        if law.group("year") is not None:
            data["law_year"] = law.group("year")
            provenance["law_year"] = "explicit"
    return data, provenance


class CitationParser:
    """Converte candidatos delimitados em estrutura textual por família."""

    def parse(self, candidate: CitationCandidate, *, context: str | None = None) -> ParsedCitation:
        """Retorna os campos explícitos de ``candidate`` sem consultar corpus."""
        if not isinstance(candidate, CitationCandidate):
            raise TypeError("candidate deve ser CitationCandidate")
        try:
            citation_type = _FAMILY_TYPES[candidate.family]
        except KeyError as exc:
            raise ValueError(f"família de citação não suportada: {candidate.family}") from exc

        tribunal_raw, tribunal, tribunal_source = _tribunal(candidate.text)
        if candidate.family == "processo_cnj":
            data, provenance = _cnj_data(candidate.text)
            if context is not None:
                contextual_raw, contextual_tribunal, contextual_source = _cnj_context_data(
                    candidate, context, data, provenance
                )
                if contextual_tribunal is not None:
                    tribunal_raw, tribunal, tribunal_source = (
                        contextual_raw,
                        contextual_tribunal,
                        contextual_source,
                    )
        elif candidate.family == "processo_ou_recurso_numerado":
            if candidate.rule == "compound_procedural_chain":
                data, provenance, compound_tribunal = _compound_procedural_chain_data(candidate.text)
                if compound_tribunal is None:
                    tribunal_raw, tribunal, tribunal_source = None, None, "unknown"
                else:
                    tribunal_raw, tribunal, tribunal_source = "TST", "TST", "explicit"
            else:
                data, provenance = _process_data(candidate.text)
        elif candidate.family == "sumula_numerada":
            data, provenance = _sumula_data(candidate.text)
        elif candidate.family == "jurisprudencia_tribunal_contextual":
            data, provenance = _contextual_data(candidate.text)
        elif candidate.family == "jurisprudencia_referencia_geral":
            data, provenance = {}, {}
        else:
            data, provenance = _legal_data(candidate.text)

        return ParsedCitation(
            family=candidate.family,
            tipo=citation_type,
            tribunal_raw=tribunal_raw,
            tribunal=tribunal,
            tribunal_source=tribunal_source,
            data=data,
            provenance=provenance,
        )
