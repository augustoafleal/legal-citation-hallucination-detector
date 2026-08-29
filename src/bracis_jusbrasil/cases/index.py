"""Índice de recuperação dos acórdãos canônicos do SQLite."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
import sqlite3
from typing import Mapping, TypeAlias

from ..normalization import normalize_case_number, normalize_tribunal
from .parser import parse_case_identity


CaseIndexStats: TypeAlias = dict[str, int]


@dataclass(frozen=True)
class CaseRecord:
    """Um registro individual de acórdão no banco de referência."""

    id_canonico: int
    documento_id: str
    tribunal: str
    numero_raw: str
    numero_normalizado: str
    numero_family: str
    parse_source: str
    marker_source: str


@dataclass(frozen=True)
class Case:
    """Todos os registros que compartilham uma chave de recuperação."""

    tribunal: str
    numero_normalizado: str
    canonical_ids: tuple[int, ...]
    records: tuple[CaseRecord, ...]

    def __post_init__(self) -> None:
        if not self.canonical_ids or len(set(self.canonical_ids)) != len(self.canonical_ids):
            raise ValueError("Case deve conter IDs não vazios e únicos")
        if tuple(record.id_canonico for record in self.records) != self.canonical_ids:
            raise ValueError("canonical_ids deve refletir exatamente os records")


class CaseIndex:
    """Índice determinístico por ``(tribunal, numero_normalizado)``."""

    def __init__(self, cases: Mapping[tuple[str, str], Case]) -> None:
        self._cases = MappingProxyType(dict(cases))
        by_number: dict[str, Case] = {}
        for case in self._cases.values():
            previous = by_number.get(case.numero_normalizado)
            if previous is not None and previous != case:
                raise ValueError("numero_normalizado associado a mais de um Case")
            by_number[case.numero_normalizado] = case
        self._cases_by_number = MappingProxyType(by_number)

    @classmethod
    def from_database(cls, connection: sqlite3.Connection) -> "CaseIndex":
        """Constrói o índice processando todos os acórdãos da conexão."""
        return build_case_index(connection, index_class=cls)

    def lookup(self, tribunal: str, numero: str) -> Case | None:
        """Retorna o feito da chave normalizada ou ``None`` se inexistente."""
        key = (normalize_tribunal(tribunal), normalize_case_number(numero))
        return self._cases.get(key)

    def lookup_by_number(self, numero: str) -> Case | None:
        """Retorna o feito por número, sem escolher IDs internos."""
        return self._cases_by_number.get(normalize_case_number(numero))

    def __len__(self) -> int:
        return len(self._cases)

    def cases(self) -> tuple[Case, ...]:
        """Retorna todos os feitos em ordem estável de chave."""
        return tuple(self._cases[key] for key in sorted(self._cases))

    @property
    def stats(self) -> CaseIndexStats:
        """Retorna as estatísticas atuais do índice."""
        cases = tuple(self._cases.values())
        return {
            "records_total": sum(len(case.records) for case in cases),
            "case_keys": len(cases),
            "single_id_cases": sum(len(case.canonical_ids) == 1 for case in cases),
            "multi_id_cases": sum(len(case.canonical_ids) > 1 for case in cases),
            "max_ids_per_case": max((len(case.canonical_ids) for case in cases), default=0),
        }


def _record_from_row(row: sqlite3.Row) -> CaseRecord:
    """Converte uma linha do SQLite e falha com dados incompletos."""
    document_id = row["documento_id"]
    raw_id = row["id"]
    if not isinstance(document_id, str) or not document_id:
        raise ValueError(f"documento_id inválido: {document_id!r}")
    if raw_id is None:
        raise ValueError(f"ID canônico ausente em {document_id}")

    tribunal = normalize_tribunal(row["tribunal"])
    parsed = parse_case_identity(row["texto"], tribunal)
    if parsed.tribunal != tribunal:
        raise ValueError(f"Tribunal inconsistente em {document_id}")
    if not parsed.numero_normalizado:
        raise ValueError(f"Chave vazia em {document_id}")

    return CaseRecord(
        id_canonico=int(raw_id),
        documento_id=document_id,
        tribunal=tribunal,
        numero_raw=parsed.numero_raw,
        numero_normalizado=parsed.numero_normalizado,
        numero_family=parsed.numero_family,
        parse_source=parsed.parse_source,
        marker_source=parsed.marker_source,
    )


def build_case_index(
    connection: sqlite3.Connection,
    *,
    index_class: type[CaseIndex] = CaseIndex,
) -> CaseIndex:
    """Lê somente acórdãos e constrói o índice de feitos completo."""
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
        raise RuntimeError("Não foi possível ler os acórdãos de referência") from exc

    grouped: dict[tuple[str, str], list[CaseRecord]] = defaultdict(list)
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

    cases: dict[tuple[str, str], Case] = {}
    for key in sorted(grouped):
        records = tuple(sorted(grouped[key], key=lambda item: (item.documento_id, item.id_canonico)))
        cases[key] = Case(
            tribunal=key[0],
            numero_normalizado=key[1],
            canonical_ids=tuple(record.id_canonico for record in records),
            records=records,
        )
    return index_class(cases)
