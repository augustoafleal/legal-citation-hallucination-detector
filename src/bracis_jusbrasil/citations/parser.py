"""Parsing estrutural e determinístico de citações já delimitadas."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping

from .detector import CitationCandidate
from .legal_catalog import COVERED_DIPLOMA_NAMESPACES, normalize_diploma_alias


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
_ARTICLE_PATTERN = re.compile(r"\b(?:art(?:igo)?s?[.]?\s*)(?P<number>\d+(?:[.]\d+)*(?:[ºo])?(?:-[A-Za-z])?)", re.IGNORECASE)
_PARAGRAPH_PATTERN = re.compile(
    r"(?P<raw>§{1,2}\s*(?P<number>\d+(?:[ºo])?(?:-[A-Za-z])?|único)|"
    r"parágrafo\s+(?P<word_number>\d+(?:[ºo])?(?:-[A-Za-z])?|único))",
    re.IGNORECASE,
)
_INCISO_PATTERN = re.compile(r",\s*(?:inciso\s+)?(?P<roman>[IVXLCDM]+)\b", re.IGNORECASE)
_ALINEA_EXPLICIT_PATTERN = re.compile(r"\balínea\s+['’\"]?(?P<letter>[A-Za-z])['’\"]?", re.IGNORECASE)
_ALINEA_QUOTED_PATTERN = re.compile(r",\s*['’\"](?P<letter>[A-Za-z])['’\"]", re.IGNORECASE)
_DIPLOMA_PATTERN = re.compile(
    r"\b(?:Constituiç[aã]o\s+(?:Federal|Fedcral|da\s+República(?:\s+Federativa\s+do\s+Brasil)?)|"
    r"C[oó]digo\s+(?:Civil|Eleitoral|Penal(?:\s+Militar)?|de\s+Processo\s+(?:Civil|Penal)|"
    r"de\s+Defesa\s+do\s+Consumidor)|Consolidação\s+das\s+Leis\s+do\s+Trabalho|"
    r"Lei(?:\s+Complementar)?\s*(?:n(?:[ºo]|[.])?\s*)?\d+(?:[.]\d+)*\s*/\s*\d{4}|"
    r"LC\s*(?:n(?:[ºo]|[.])?\s*)?\d+\s*/\s*\d{4}|CLT|CPC|CPP|CC|CDC|CPM|CF(?:/88)?|CE)\b",
    re.IGNORECASE,
)
_TRUNCATED_DIPLOMA_PATTERN = re.compile(r"\bC[oó]digo\s+de\s+De\b", re.IGNORECASE)
_RIGHT_LEGAL_LINK_PATTERN = re.compile(
    r"^\s*(?:,\s*(?:§{1,2}\s*(?:\d+(?:[ºo])?(?:-[A-Za-z])?|único)|"
    r"parágrafo\s+(?:\d+(?:[ºo])?(?:-[A-Za-z])?|único)|"
    r"(?:inciso\s+)?[IVXLCDM]+|alínea\s+['’\"]?[A-Za-z]['’\"]?|"
    r"item\s+\d+|['’\"]?[A-Za-z]['’\"]?))*\s*,?\s*(?:do|da|de)\s*$",
    re.IGNORECASE,
)
_LEFT_LEGAL_LINK_PATTERN = re.compile(r"^\s*,?\s*(?:em\s+seu\s+)?$", re.IGNORECASE)
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
    legal: ParsedLegalIdentity | None = None

    def __post_init__(self) -> None:
        """Defende os payloads contra mutação e aliasing externos."""
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True)
class ParsedLegalIdentity:
    """Identidade legal explícita, imutável e independente do Gold."""

    raw_span: tuple[int, int]
    raw_text: str
    article_raw: str | None
    article_normalized: str | None
    article_suffix: str | None
    paragraph_raw: str | None
    paragraph_normalized: str | None
    inciso_raw: str | None
    inciso_normalized: str | None
    alinea_raw: str | None
    alinea_normalized: str | None
    diploma_raw: str | None
    diploma_normalized: str | None
    diploma_alias_source: str
    has_complete_identity: bool
    has_ambiguous_identity: bool
    has_critical_ocr: bool
    parse_source: str
    parse_warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.raw_span, tuple) or len(self.raw_span) != 2:
            raise TypeError("raw_span deve ser uma tupla (start, end)")
        if not isinstance(self.parse_warnings, tuple):
            raise TypeError("parse_warnings deve ser tuple")


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


def _normalize_number_with_suffix(value: str) -> tuple[str, str | None]:
    cleaned = value.rstrip("ºo")
    base, separator, suffix = cleaned.partition("-")
    return _digits(base), suffix.upper() if separator and suffix else None


def _legal_identity(candidate: CitationCandidate) -> ParsedLegalIdentity:
    text = candidate.text
    article = _ARTICLE_PATTERN.search(text)
    paragraph = _PARAGRAPH_PATTERN.search(text)
    diploma = _DIPLOMA_PATTERN.search(text)
    truncated = _TRUNCATED_DIPLOMA_PATTERN.search(text)
    article_raw = article.group("number") if article else None
    article_normalized, article_suffix = (
        _normalize_number_with_suffix(article_raw) if article_raw else (None, None)
    )

    paragraph_raw = paragraph.group("raw") if paragraph else None
    paragraph_number = (paragraph.group("number") or paragraph.group("word_number")) if paragraph else None
    paragraph_normalized = None
    if paragraph_number:
        folded = paragraph_number.rstrip("ºo")
        if folded.lower() == "único":
            paragraph_normalized = "UNICO"
        else:
            base, separator, suffix = folded.partition("-")
            paragraph_normalized = base.rstrip("ºo") + (f"-{suffix.upper()}" if separator else "")

    complement_start = article.end() if article else 0
    complement_end = (
        diploma.start()
        if diploma and article and article.start() < diploma.start()
        else len(text)
    )
    complement = text[complement_start:complement_end]
    inciso = _INCISO_PATTERN.search(complement)
    alinea = _ALINEA_EXPLICIT_PATTERN.search(complement) or _ALINEA_QUOTED_PATTERN.search(complement)
    if alinea is None and inciso is not None:
        after_inciso = complement[inciso.end():]
        unquoted = re.search(r",\s*(?P<letter>[A-Ha-h])\b", after_inciso)
        if unquoted is not None:
            alinea = unquoted

    diploma_raw = diploma.group(0) if diploma else truncated.group(0) if truncated else None
    right_link = bool(
        article and diploma and article.start() < diploma.start()
        and _RIGHT_LEGAL_LINK_PATTERN.fullmatch(text[article.end():diploma.start()])
    )
    left_link = bool(
        article and diploma and diploma.start() < article.start()
        and _LEFT_LEGAL_LINK_PATTERN.fullmatch(text[diploma.end():article.start()])
    )
    strong_context = bool(
        candidate.family == "lei_dispositivo_com_diploma"
        and "\n\n" not in text
        and (right_link or left_link)
    )
    normalized = (
        normalize_diploma_alias(diploma_raw, strong_legal_context=strong_context)
        if diploma_raw
        else None
    )

    warnings: list[str] = []
    if article is None:
        warnings.append("ARTICLE_MISSING")
    if diploma_raw is None:
        warnings.append("DIPLOMA_MISSING")
    if normalized and normalized.warning:
        warnings.append(normalized.warning)
    if article_suffix:
        warnings.append("ARTICLE_SUFFIX_UNCOVERED")
    if "\n\n" in text:
        warnings.append("PARAGRAPH_BOUNDARY")

    diploma_normalized = normalized.normalized if normalized else None
    critical_ocr = normalized.has_critical_ocr if normalized else False
    ambiguous = bool(
        normalized
        and normalized.guarded
        and diploma_normalized is None
    )
    # Namespaces reconhecidos, mas sem proveniência coberta, são identidade
    # textual útil para diagnóstico, porém insuficiente para classificação.
    covered_or_catalogued = diploma_normalized in COVERED_DIPLOMA_NAMESPACES
    complete = bool(
        article_normalized
        and not article_suffix
        and diploma_normalized
        and covered_or_catalogued
        and not ambiguous
        and not critical_ocr
        and "PARAGRAPH_BOUNDARY" not in warnings
    )
    if candidate.rule == "lei_com_diploma_esquerda":
        parse_source = "bounded_left_diploma"
    elif article and diploma:
        parse_source = "right_diploma" if article.start() < diploma.start() else "bounded_left_diploma"
    elif article:
        parse_source = "article_only"
    else:
        parse_source = "vague_reference"

    return ParsedLegalIdentity(
        raw_span=(candidate.start, candidate.end),
        raw_text=text,
        article_raw=article_raw,
        article_normalized=article_normalized,
        article_suffix=article_suffix,
        paragraph_raw=paragraph_raw,
        paragraph_normalized=paragraph_normalized,
        inciso_raw=inciso.group(0).lstrip(",").strip() if inciso else None,
        inciso_normalized=inciso.group("roman").upper() if inciso else None,
        alinea_raw=alinea.group("letter") if alinea else None,
        alinea_normalized=alinea.group("letter").lower() if alinea else None,
        diploma_raw=diploma_raw,
        diploma_normalized=diploma_normalized,
        diploma_alias_source=normalized.alias_source if normalized else "unknown",
        has_complete_identity=complete,
        has_ambiguous_identity=ambiguous,
        has_critical_ocr=critical_ocr,
        parse_source=parse_source,
        parse_warnings=tuple(dict.fromkeys(warnings)),
    )


def _legal_data(identity: ParsedLegalIdentity) -> tuple[dict[str, str], dict[str, str]]:
    data: dict[str, str] = {}
    provenance: dict[str, str] = {}
    if identity.article_normalized is not None:
        data["artigo"] = identity.article_normalized
        provenance["artigo"] = "normalized"
    if identity.article_suffix is not None:
        data["artigo_sufixo"] = identity.article_suffix
    if identity.paragraph_normalized is not None:
        data["paragrafo"] = identity.paragraph_normalized
        provenance["paragrafo"] = "explicit"
    if identity.inciso_normalized is not None:
        data["inciso"] = identity.inciso_normalized
        provenance["inciso"] = "explicit"
    if identity.alinea_normalized is not None:
        data["alinea"] = identity.alinea_normalized
        provenance["alinea"] = "explicit"
    if identity.diploma_raw is not None:
        data["diploma_raw"] = identity.diploma_raw
    if identity.diploma_normalized is not None:
        data["diploma_normalizado"] = identity.diploma_normalized
        provenance["diploma"] = "normalized"
    law = _LAW_NUMBER_PATTERN.search(identity.raw_text)
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
            legal = _legal_identity(candidate)
            data, provenance = _legal_data(legal)

        if not candidate.family.startswith("lei_"):
            legal = None

        return ParsedCitation(
            family=candidate.family,
            tipo=citation_type,
            tribunal_raw=tribunal_raw,
            tribunal=tribunal,
            tribunal_source=tribunal_source,
            data=data,
            provenance=provenance,
            legal=legal,
        )
