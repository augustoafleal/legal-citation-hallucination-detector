"""Índice de recuperação dos acórdãos canônicos do SQLite."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
import sqlite3
from typing import Mapping

from .canonical_parser import parse_canonical_identity
from .normalization import normalize_case_number, normalize_tribunal


@dataclass(frozen=True)
class CanonicalRecord:
    """Um registro individual de acórdão no banco canônico."""

    id_canonico: int
    documento_id: str
    tribunal: str
    numero_raw: str
    numero_normalizado: str
    numero_family: str
    parse_source: str
    marker_source: str


@dataclass(frozen=True)
class CanonicalCase:
    """Todos os registros que compartilham uma chave de recuperação."""

    tribunal: str
    numero_normalizado: str
    canonical_ids: tuple[int, ...]
    records: tuple[CanonicalRecord, ...]

    def __post_init__(self) -> None:
        if not self.canonical_ids or len(set(self.canonical_ids)) != len(self.canonical_ids):
            raise ValueError("CanonicalCase deve conter IDs não vazios e únicos")
        if tuple(record.id_canonico for record in self.records) != self.canonical_ids:
            raise ValueError("canonical_ids deve refletir exatamente os records")


class CanonicalIndex:
    """Índice determinístico por ``(tribunal, numero_normalizado)``."""

    def __init__(self, cases: Mapping[tuple[str, str], CanonicalCase]) -> None:
        self._cases = MappingProxyType(dict(cases))

    @classmethod
    def from_database(cls, connection: sqlite3.Connection) -> "CanonicalIndex":
        """Constrói o índice processando todos os acórdãos da conexão."""
        return build_canonical_index(connection, index_class=cls)

    def lookup(self, tribunal: str, numero: str) -> CanonicalCase | None:
        """Retorna o feito da chave normalizada ou ``None`` se ela não existir."""
        key = (normalize_tribunal(tribunal), normalize_case_number(numero))
        return self._cases.get(key)

    def __len__(self) -> int:
        return len(self._cases)

    def cases(self) -> tuple[CanonicalCase, ...]:
        """Retorna todos os feitos em ordem estável de chave."""
        return tuple(self._cases[key] for key in sorted(self._cases))

    @property
    def stats(self) -> dict[str, int]:
        """Retorna as estatísticas atuais do índice."""
        cases = tuple(self._cases.values())
        return {
            "records_total": sum(len(case.records) for case in cases),
            "case_keys": len(cases),
            "single_id_cases": sum(len(case.canonical_ids) == 1 for case in cases),
            "multi_id_cases": sum(len(case.canonical_ids) > 1 for case in cases),
            "max_ids_per_case": max((len(case.canonical_ids) for case in cases), default=0),
        }


def _record_from_row(row: sqlite3.Row) -> CanonicalRecord:
    """Converte uma linha do SQLite e falha com dados incompletos."""
    document_id = row["documento_id"]
    raw_id = row["id"]
    if not isinstance(document_id, str) or not document_id:
        raise ValueError(f"documento_id inválido: {document_id!r}")
    if raw_id is None:
        raise ValueError(f"ID canônico ausente em {document_id}")

    tribunal = normalize_tribunal(row["tribunal"])
    parsed = parse_canonical_identity(row["texto"], tribunal)
    if parsed.tribunal != tribunal:
        raise ValueError(f"Tribunal inconsistente em {document_id}")
    if not parsed.numero_normalizado:
        raise ValueError(f"Chave vazia em {document_id}")

    return CanonicalRecord(
        id_canonico=int(raw_id),
        documento_id=document_id,
        tribunal=tribunal,
        numero_raw=parsed.numero_raw,
        numero_normalizado=parsed.numero_normalizado,
        numero_family=parsed.numero_family,
        parse_source=parsed.parse_source,
        marker_source=parsed.marker_source,
    )


def build_canonical_index(
    connection: sqlite3.Connection,
    *,
    index_class: type[CanonicalIndex] = CanonicalIndex,
) -> CanonicalIndex:
    """Lê somente acórdãos e constrói o índice canônico completo."""
    try:
        rows = connection.execute(
            """
            SELECT documento_id, id, tribunal, ano, relator, texto
            FROM documentos
            WHERE natureza = 'acordao'
            ORDER BY documento_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError("Não foi possível ler os acórdãos canônicos") from exc

    grouped: dict[tuple[str, str], list[CanonicalRecord]] = defaultdict(list)
    id_keys: dict[int, tuple[str, str]] = {}
    for row in rows:
        document_id = row["documento_id"]
        try:
            record = _record_from_row(row)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Falha estrutural no acórdão {document_id}") from exc

        key = (record.tribunal, record.numero_normalizado)
        previous_key = id_keys.get(record.id_canonico)
        if previous_key is not None and previous_key != key:
            raise ValueError(f"ID canônico {record.id_canonico} associado a chaves distintas")
        id_keys[record.id_canonico] = key
        grouped[key].append(record)

    cases: dict[tuple[str, str], CanonicalCase] = {}
    for key in sorted(grouped):
        records = tuple(sorted(grouped[key], key=lambda item: (item.documento_id, item.id_canonico)))
        cases[key] = CanonicalCase(
            tribunal=key[0],
            numero_normalizado=key[1],
            canonical_ids=tuple(record.id_canonico for record in records),
            records=records,
        )
    return index_class(cases)
