"""Normalização dos valores usados na identidade canônica."""

from __future__ import annotations

import re


CANONICAL_TRIBUNALS = frozenset({"STF", "STJ", "TSE", "TST", "STM"})


def normalize_tribunal(value: str) -> str:
    """Normaliza um tribunal conhecido e rejeita valores desconhecidos."""
    if not isinstance(value, str):
        raise ValueError(f"Tribunal inválido: {value!r}")

    tribunal = value.strip().upper()
    if tribunal not in CANONICAL_TRIBUNALS:
        raise ValueError(f"Tribunal desconhecido: {value!r}")
    return tribunal


def normalize_case_number(value: str) -> str:
    """Remove pontuação de um número já localizado na identidade formal."""
    if not isinstance(value, str):
        raise ValueError(f"Número processual inválido: {value!r}")

    normalized = re.sub(r"[^0-9]", "", value)
    if not normalized:
        raise ValueError(f"Número processual vazio: {value!r}")
    return normalized
