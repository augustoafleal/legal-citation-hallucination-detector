"""Serialização determinística do contrato CSV oficial."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path


def _citation_cell(citations: Iterable[Mapping[str, object]]) -> str:
    encoded = []
    for citation in citations:
        resolution = citation["resolucao"]
        identifier = resolution["id_canonico"] if isinstance(resolution, Mapping) else None
        canonical = str(identifier) if citation["classificacao"] == "real" and identifier is not None else "-"
        confidence = citation["confianca"]
        confidence_text = "-" if confidence is None else f"{float(confidence):.4f}"
        encoded.append(
            f"{citation['inicio']},{citation['fim']},{citation['classificacao']},{canonical},{confidence_text}"
        )
    return "|".join(encoded) if encoded else "-"


def write_submission_csv(destination: Path, records: Iterable[Mapping[str, object]]) -> None:
    """Escreva ``documento_id,citacoes`` em ordem estável e sem dados auxiliares."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda record: str(record["documento_id"]))
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("documento_id", "citacoes"))
        for record in ordered:
            citations = record["citacoes"]
            if not isinstance(citations, list):
                raise TypeError("citacoes deve ser uma lista")
            writer.writerow((record["documento_id"], _citation_cell(citations)))
