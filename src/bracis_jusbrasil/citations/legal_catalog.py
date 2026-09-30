"""Catálogo canônico fechado para dispositivos legais da V11.

O catálogo contém somente metadados auditados contra o SQLite congelado e
fontes normativas oficiais.  Ele não consulta rede, Gold ou textos do corpus e
não expõe resolução por artigo isolado.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata


CANONICAL_DATABASE_SHA256 = "78f0708b0a21c11655dfdd882382fea75c62a75415d8d3b118888c0a340bef4c"

SOURCE_PROVENANCE_HASHES: Mapping[str, str] = MappingProxyType(
    {
        "CE": "17581b8ecbac026728ad2fbf2fa02af97d27184670aa2f28264c93b6370f21c8",
        "CPM": "12fa3fee6d007cdace15b4cff2d67b1a2a649c41fb757a15cfbd9a27bea3d2d6",
        "CDC": "860ed8f38e0a03b7597b21abbccd621660463a51cbeda019242d00bec59b3872",
        "CF": "f332fa0ddf986336fe1324939c0c7fb10c4fd1fcc5aa5911f6f47d3a1029a834",
        "CLT": "b7bfd6fd6e1c187f69744c5d903cf4f38f5b9330e5b7d56dbb2b9f1e999f648d",
        "CPP": "524d95de6fe83c3476ce3763bd1c4984d7260496887152b256ccf41297e8fa30",
        "CC": "6907322e88dc529f267925d6675f23237314ce3f159862bfebdf187ede0f1192",
        "LC:64:1990": "cccc9b1a416964cc38c1beb9b9d781cb0f3dde9c4f385361d95f92216d60bfe5",
        "CPC": "3178b474868fca8c6462f8b417978039d26c3973c072c5da8d10d1cf4f794066",
        "LEI:9504:1997": "fc9f2afb6b25b122f164643246dfaf2384e1cd23366e213660e4c36e69ac634a",
        "LEI:13467:2017": "bc6c3477fdac84a43f4ebcc4098e235aeaacb97c2818160ae0e9275c58ced8ca",
    }
)


@dataclass(frozen=True)
class LegalCatalogEntry:
    """Uma identidade artigo+diploma comprovada no banco canônico."""

    id_canonico: int
    normalized_article: str
    normalized_diploma: str
    accepted_aliases: tuple[str, ...]
    source_provenance_hash: str
    source_type: str
    provenance_status: str
    risk: str


@dataclass(frozen=True)
class DiplomaNormalization:
    """Resultado auditável da normalização de um diploma capturado."""

    normalized: str | None
    alias_source: str
    guarded: bool = False
    has_critical_ocr: bool = False
    warning: str | None = None


_ENTRY_ROWS = (
    (10577194, "276", "CE", ("Código Eleitoral", "Lei nº 4.737/1965", "Lei n. 4.737/1965", "CE"), "PROVEN_STRONG"),
    (10590194, "290", "CPM", ("CPM", "Código Penal Militar"), "PROVEN_WITH_TEXT_VARIATION"),
    (10606184, "14", "CDC", ("CDC", "Código de Defesa do Consumidor"), "PROVEN_STRONG"),
    (10626510, "93", "CF", ("Constituição Federal", "Constituição da República", "Constituição da República Federativa do Brasil", "CF", "CF/88"), "PROVEN_WITH_TEXT_VARIATION"),
    (10637358, "896", "CLT", ("CLT", "Consolidação das Leis do Trabalho"), "PROVEN_WITH_TEXT_VARIATION"),
    (10641213, "7", "CF", ("Constituição Federal", "Constituição da República", "Constituição da República Federativa do Brasil", "CF", "CF/88"), "PROVEN_WITH_TEXT_VARIATION"),
    (10641516, "5", "CF", ("Constituição Federal", "Constituição da República", "Constituição da República Federativa do Brasil", "CF", "CF/88"), "PROVEN_WITH_TEXT_VARIATION"),
    (10647746, "818", "CLT", ("CLT", "Consolidação das Leis do Trabalho"), "PROVEN_WITH_TEXT_VARIATION"),
    (10652044, "312", "CPP", ("CPP", "Código de Processo Penal"), "PROVEN_WITH_TEXT_VARIATION"),
    (10710324, "477", "CLT", ("CLT", "Consolidação das Leis do Trabalho"), "PROVEN_WITH_TEXT_VARIATION"),
    (10718759, "186", "CC", ("Código Civil", "CC"), "PROVEN_STRONG"),
    (11304039, "1", "LC:64:1990", ("Lei Complementar nº 64/1990", "Lei Complementar n. 64/1990", "LC nº 64/1990", "LC 64/1990"), "PROVEN_WITH_TEXT_VARIATION"),
    (28893055, "373", "CPC", ("CPC", "Código de Processo Civil", "Código de Processo Civil de 2015", "Lei nº 13.105/2015", "Lei n. 13.105/2015"), "PROVEN_STRONG"),
)


LEGAL_CATALOG_ENTRIES = tuple(
    LegalCatalogEntry(
        id_canonico=id_canonico,
        normalized_article=article,
        normalized_diploma=diploma,
        accepted_aliases=aliases,
        source_provenance_hash=SOURCE_PROVENANCE_HASHES[diploma],
        source_type="official_federal_legislation_snapshot",
        provenance_status=status,
        risk="LOW" if status == "PROVEN_STRONG" else "MODERATE",
    )
    for id_canonico, article, diploma, aliases, status in _ENTRY_ROWS
)

COVERED_DIPLOMA_NAMESPACES = frozenset(SOURCE_PROVENANCE_HASHES)
GUARDED_DIPLOMA_ALIASES = frozenset({"CF", "CF/88", "CC", "CE"})


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    upper = re.sub(r"\s+", " ", plain.upper()).strip()
    upper = re.sub(r"\s*/\s*", "/", upper)
    upper = re.sub(r"\bN\s*[º°.O]\s*", "N ", upper)
    return upper


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
    _fold(alias): diploma
    for diploma, aliases in _SAFE_ALIASES.items()
    for alias in aliases
}
_LONG_SAFE_ALIASES = {
    folded: diploma
    for folded, diploma in _SAFE_ALIAS_INDEX.items()
    if len(folded) >= 16 and folded.replace(" ", "").isalpha()
}
_LAW_IDENTITY = re.compile(r"^LEI(?P<complementar> COMPLEMENTAR)?(?: N)? (?P<number>\d+(?:[.]\d+)*)/(?P<year>\d{4})$")


def _single_edit(left: str, right: str) -> bool:
    # Somente uma substituição em nome longo: inserção/remoção pode representar
    # truncamento e, por segurança, não é reparada.
    return len(left) == len(right) and sum(a != b for a, b in zip(left, right)) == 1


def normalize_diploma_alias(raw: str, *, strong_legal_context: bool) -> DiplomaNormalization:
    """Normalize somente aliases fechados; ruído crítico nunca é reparado."""
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
        number = law.group("number").replace(".", "")
        year = law.group("year")
        prefix = "LC" if law.group("complementar") else "LEI"
        normalized = f"{prefix}:{number}:{year}"
        aliases = {
            "LEI:13105:2015": "CPC",
            "LEI:4737:1965": "CE",
            "LC:64:1990": "LC:64:1990",
        }
        return DiplomaNormalization(aliases.get(normalized, normalized), "statute_number_alias")

    # Reparação genérica e conservadora de um único caractere em nomes longos.
    # Ela é única, não se aplica a siglas/números e mantém warning auditável.
    matches = {diploma for alias, diploma in _LONG_SAFE_ALIASES.items() if _single_edit(folded, alias)}
    if len(matches) == 1:
        return DiplomaNormalization(
            matches.pop(), "unique_single_edit_full_name", warning="NONCRITICAL_OCR_REPAIR"
        )
    if matches:
        return DiplomaNormalization(None, "unknown", has_critical_ocr=True, warning="CRITICAL_OCR")
    return DiplomaNormalization(None, "unknown", warning="DIPLOMA_UNKNOWN")


class LegalCanonicalCatalog:
    """Índice imutável por chave composta, sem caminho por artigo isolado."""

    def __init__(self, entries: tuple[LegalCatalogEntry, ...] = LEGAL_CATALOG_ENTRIES) -> None:
        by_key: dict[tuple[str, str], tuple[int, ...]] = {}
        seen_ids: set[int] = set()
        for entry in entries:
            key = (entry.normalized_article, entry.normalized_diploma)
            if (
                not entry.normalized_article
                or not entry.normalized_diploma
                or not entry.accepted_aliases
                or len(entry.source_provenance_hash) != 64
            ):
                raise ValueError("entrada legal canônica incompleta")
            if key in by_key or entry.id_canonico in seen_ids:
                raise ValueError("catálogo legal exige chaves e IDs únicos")
            by_key[key] = (entry.id_canonico,)
            seen_ids.add(entry.id_canonico)
        if len(entries) != 13:
            raise ValueError("catálogo legal canônico deve conter exatamente 13 entradas")
        self._entries = tuple(entries)
        self._by_key = MappingProxyType(by_key)

    @property
    def entries(self) -> tuple[LegalCatalogEntry, ...]:
        return self._entries

    @property
    def covered_namespaces(self) -> frozenset[str]:
        return COVERED_DIPLOMA_NAMESPACES

    def lookup(self, normalized_article: str, normalized_diploma: str) -> tuple[int, ...]:
        """Retorne candidatos somente para a chave artigo+diploma completa."""
        if not normalized_article or not normalized_diploma:
            return ()
        return self._by_key.get((normalized_article, normalized_diploma), ())

    def is_covered_namespace(self, normalized_diploma: str) -> bool:
        return normalized_diploma in self.covered_namespaces


DEFAULT_LEGAL_CATALOG = LegalCanonicalCatalog()
