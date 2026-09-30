"""Normalização legal e catálogo construído do SQLite de entrada."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
import sqlite3
from types import MappingProxyType
import unicodedata


# O parser importa este conjunto durante a inicialização. Ele contém somente
# namespaces reconhecidos, nunca IDs ou chaves extraídas do dataset público.
COVERED_DIPLOMA_NAMESPACES = frozenset(
    {"CE", "CPM", "CDC", "CF", "CLT", "CPP", "CC", "LC:64:1990", "CPC", "LEI:9504:1997", "LEI:13467:2017"}
)
GUARDED_DIPLOMA_ALIASES = frozenset({"CF", "CF/88", "CC", "CE"})


@dataclass(frozen=True)
class LegalCatalogEntry:
    """Chave artigo+diploma observada no banco recebido."""

    id_canonico: int
    normalized_article: str
    normalized_diploma: str


@dataclass(frozen=True)
class DiplomaNormalization:
    """Resultado auditável da normalização de um diploma capturado."""

    normalized: str | None
    alias_source: str
    guarded: bool = False
    has_critical_ocr: bool = False
    warning: str | None = None


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    upper = re.sub(r"\s+", " ", plain.upper()).strip()
    upper = re.sub(r"\s*/\s*", "/", upper)
    return re.sub(r"\bN\s*[º°.O]\s*", "N ", upper)


_SAFE_ALIASES = {
    "CF": ("Constituição Federal", "Constituição da República", "Constituição da República Federativa do Brasil"),
    "CLT": ("CLT", "Consolidação das Leis do Trabalho"),
    "CPC": ("CPC", "Código de Processo Civil", "Código de Processo Civil de 2015", "Lei nº 13.105/2015", "Lei n. 13.105/2015"),
    "CPP": ("CPP", "Código de Processo Penal"),
    "CPM": ("CPM", "Código Penal Militar"),
    "CC": ("Código Civil",),
    "CDC": ("CDC", "Código de Defesa do Consumidor"),
    "CE": ("Código Eleitoral", "Lei nº 4.737/1965", "Lei n. 4.737/1965"),
    "LC:64:1990": ("Lei Complementar nº 64/1990", "Lei Complementar n. 64/1990", "LC nº 64/1990", "LC 64/1990"),
}
_GUARDED_ALIASES = {
    _fold(alias): diploma
    for diploma, aliases in {"CF": ("CF", "CF/88"), "CC": ("CC",), "CE": ("CE",)}.items()
    for alias in aliases
}
_SAFE_ALIAS_INDEX = {
    _fold(alias): diploma for diploma, aliases in _SAFE_ALIASES.items() for alias in aliases
}
_LONG_SAFE_ALIASES = {
    folded: diploma for folded, diploma in _SAFE_ALIAS_INDEX.items()
    if len(folded) >= 16 and folded.replace(" ", "").isalpha()
}
_LAW_IDENTITY = re.compile(r"^LEI(?P<complementar> COMPLEMENTAR)?(?: N)? (?P<number>\d+(?:[.]\d+)*)/(?P<year>\d{4})$")
_ARTICLE_HEADER = re.compile(
    r"^\s*(?:ART(?:IGO)?)[.]?\s*(?P<number>\d+(?:[.]\d+)*(?:[ºªO])?(?:-[A-Z])?)\b",
    re.IGNORECASE,
)
_HEADER_STATUTE = re.compile(
    r"(?P<kind>DECRETO-?LEI|LEI\s+COMPLEMENTAR|LEI)\s*(?:N[ºO.]?\s*)?"
    r"(?P<number>\d+(?:[.]\d+)*).*?(?P<year>\d{4})",
    re.IGNORECASE,
)
_HEADER_LIMIT = 512


def _single_edit(left: str, right: str) -> bool:
    return len(left) == len(right) and sum(a != b for a, b in zip(left, right)) == 1


def normalize_diploma_alias(raw: str, *, strong_legal_context: bool) -> DiplomaNormalization:
    """Normalize aliases legislativos sem usar inventário de dispositivos."""
    folded = _fold(raw)
    if folded in _SAFE_ALIAS_INDEX:
        return DiplomaNormalization(_SAFE_ALIAS_INDEX[folded], "safe_alias")
    if folded in _GUARDED_ALIASES:
        if strong_legal_context:
            return DiplomaNormalization(_GUARDED_ALIASES[folded], "guarded_alias", guarded=True)
        return DiplomaNormalization(None, "guarded_alias", guarded=True, warning="GUARDED_ALIAS_CONTEXT_FAILED")
    if folded == "CODIGO PENAL":
        return DiplomaNormalization("CP", "recognized_uncovered_alias")
    if folded.startswith("CODIGO DE DE"):
        return DiplomaNormalization(None, "unknown", has_critical_ocr=True, warning="CRITICAL_OCR")
    law = _LAW_IDENTITY.fullmatch(folded)
    if law:
        number, year = law.group("number").replace(".", ""), law.group("year")
        normalized = f"{'LC' if law.group('complementar') else 'LEI'}:{number}:{year}"
        aliases = {
            "LEI:13105:2015": "CPC", "LEI:4737:1965": "CE", "LC:64:1990": "LC:64:1990",
            "LEI:5452:1943": "CLT", "LEI:3689:1941": "CPP", "LEI:1001:1969": "CPM",
            "LEI:8078:1990": "CDC", "LEI:10406:2002": "CC",
        }
        return DiplomaNormalization(aliases.get(normalized, normalized), "statute_number_alias")
    matches = {diploma for alias, diploma in _LONG_SAFE_ALIASES.items() if _single_edit(folded, alias)}
    if len(matches) == 1:
        return DiplomaNormalization(matches.pop(), "unique_single_edit_full_name", warning="NONCRITICAL_OCR_REPAIR")
    if matches:
        return DiplomaNormalization(None, "unknown", has_critical_ocr=True, warning="CRITICAL_OCR")
    return DiplomaNormalization(None, "unknown", warning="DIPLOMA_UNKNOWN")


def _normalize_article(raw: str) -> str:
    value = raw.upper().rstrip("ºªO")
    number, separator, suffix = value.partition("-")
    return number.replace(".", "") + (f"-{suffix}" if separator else "")


def _header_diploma(header: str) -> str | None:
    """Identifique somente a fonte que acompanha o cabeçalho do artigo."""
    source = header[:160]
    statute = _HEADER_STATUTE.search(source)
    if statute is not None:
        number = statute.group("number").replace(".", "")
        year = statute.group("year")
        kind = _fold(statute.group("kind"))
        key = f"{'LC' if kind == 'LEI COMPLEMENTAR' else 'LEI'}:{number}:{year}"
        aliases = {
            "LEI:13105:2015": "CPC", "LEI:4737:1965": "CE", "LC:64:1990": "LC:64:1990",
            "LEI:5452:1943": "CLT", "LEI:3689:1941": "CPP", "LEI:1001:1969": "CPM",
            "LEI:8078:1990": "CDC", "LEI:10406:2002": "CC",
        }
        return aliases.get(key, key)
    folded = _fold(source)
    if "CONSTITUICAO FEDERAL" in folded or "CONSTITUICAO DA REPUBLICA" in folded:
        return "CF"
    named = {
        "CONSOLIDACAO DAS LEIS DO TRABALHO": "CLT",
        "CODIGO DE PROCESSO PENAL": "CPP",
        "CODIGO PENAL MILITAR": "CPM",
        "CODIGO DE DEFESA DO CONSUMIDOR": "CDC",
        "CODIGO CIVIL": "CC",
        "CODIGO ELEITORAL": "CE",
        "CODIGO DE PROCESSO CIVIL": "CPC",
    }
    for phrase, diploma in named.items():
        if phrase in folded:
            return diploma
    return None


def _extract_entry(row: object) -> LegalCatalogEntry | None:
    identifier, _documento_id, text = row  # type: ignore[misc]
    if not isinstance(text, str):
        return None
    header = text[:_HEADER_LIMIT]
    article = _ARTICLE_HEADER.match(header)
    if article is None:
        return None
    diploma = _header_diploma(header)
    if diploma is None:
        return None
    return LegalCatalogEntry(int(identifier), _normalize_article(article.group("number")), diploma)


class LegalCanonicalCatalog:
    """Índice imutável derivado exclusivamente do banco fornecido."""

    def __init__(self, entries: tuple[LegalCatalogEntry, ...] = ()) -> None:
        grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
        for entry in entries:
            if entry.normalized_article and entry.normalized_diploma:
                grouped[(entry.normalized_article, entry.normalized_diploma)].append(entry.id_canonico)
        self._entries = tuple(entries)
        self._by_key = MappingProxyType({key: tuple(sorted(ids)) for key, ids in grouped.items()})
        self._covered_namespaces = frozenset(entry.normalized_diploma for entry in entries)

    @classmethod
    def from_database(cls, connection: sqlite3.Connection) -> "LegalCanonicalCatalog":
        rows = connection.execute(
            "SELECT id, documento_id, texto FROM documentos WHERE natureza = 'dispositivo' ORDER BY id"
        ).fetchall()
        return cls(tuple(entry for row in rows if (entry := _extract_entry(row)) is not None))

    @property
    def entries(self) -> tuple[LegalCatalogEntry, ...]:
        return self._entries

    @property
    def covered_namespaces(self) -> frozenset[str]:
        return self._covered_namespaces

    def lookup(self, normalized_article: str, normalized_diploma: str) -> tuple[int, ...]:
        if not normalized_article or not normalized_diploma:
            return ()
        return self._by_key.get((normalized_article, normalized_diploma), ())

    def is_covered_namespace(self, normalized_diploma: str) -> bool:
        return normalized_diploma in self._covered_namespaces


# Compatibility object is intentionally empty. Runtime callers construct a
# catalog from their SQLite connection through CitationResolver.
DEFAULT_LEGAL_CATALOG = LegalCanonicalCatalog()
