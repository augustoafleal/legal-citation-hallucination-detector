"""Contrato de saída para a submissão oficial do desafio."""

from __future__ import annotations

from collections.abc import Iterable

from .citations import ArbitrationResult


DEFAULT_SUBMISSION_CONFIDENCE = 0.85

_CLASSIFICATION_BY_STATUS = {
    "resolved": "real",
    "no_match": "inventada",
    "ambiguous": "incompleta",
    "insufficient": "incompleta",
}


def build_submission_record(
    document_id: str,
    outputs: Iterable[ArbitrationResult],
) -> dict[str, object]:
    """Serializa somente outputs finais arbitrados para o contrato JSON."""
    citations = []
    for output in outputs:
        candidate, parsed, result = output.candidate, output.parsed, output.resolution
        citations.append(
            {
                "inicio": candidate.start,
                "fim": candidate.end,
                "trecho": candidate.text,
                "tipo": parsed.tipo,
                "classificacao": _CLASSIFICATION_BY_STATUS[result.status],
                "resolucao": {"id_canonico": result.id_canonico},
                "confianca": DEFAULT_SUBMISSION_CONFIDENCE,
            }
        )
    return {"documento_id": document_id, "citacoes": citations}
