"""Detecção determinística e superficial de citações."""

from __future__ import annotations

from dataclasses import dataclass
import re


_CNJ_PATTERN = re.compile(
    r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b"
)
_TRIBUNAL_PATTERN = re.compile(
    r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|Tribunal Superior Eleitoral|Tribunal Superior do Trabalho)\b",
    re.IGNORECASE,
)
_PROCESS_CLASS_PATTERN = re.compile(
    r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|Agravo|Recurso Especial|Recurso Extraordinário|Habeas Corpus|Reclamação)\b",
    re.IGNORECASE,
)
_SUMULA_PATTERN = re.compile(
    r"\bS[ÚU]MULA(?:\s+VINCULANTE)?\s*(?:N[ºO.]?\s*)?\d+",
    re.IGNORECASE,
)
_ARTICLE_PATTERN = re.compile(
    r"\b(?:art(?:igo)?[.]?\s*\d+|§\s*\d+|inciso\s+[IVXLCDM]+)",
    re.IGNORECASE,
)
_DIPLOMA_PATTERN = re.compile(
    r"\b(?:Constituiç[aã]o|C[oó]digo|Lei(?: Complementar)?\s*(?:n[ºo.]?\s*)?\d+|CLT|CPC|CPP|CC|CDC|CPM)\b",
    re.IGNORECASE,
)

_PROCESS_PATTERN = re.compile(
    r"\b(?:(?:AgInt|AgRg|EDcl|ED)\s+(?:no|n[oº.]+)\s+)?(?:AREsp|REsp|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|Recurso\s+(?:Especial|Extraordin[aá]rio)|Agravo(?:\s+Regimental)?|Habeas\s+Corpus|Reclamaç[aã]o)\s*(?:n[ºo.]?\s*)?\d[\d.\-/ ]{2,}\d(?:\s*-\s*[A-Z]{2})?",
    re.IGNORECASE,
)
_LAW_WITH_DIPLOMA_PATTERN = re.compile(
    r"\b(?:art(?:igo)?[.]?\s*\d+(?:[ºo])?(?:\s*,?\s*§\s*\d+(?:[ºo])?)?)(?:\s+do|\s+da)?\s+(?:Constituiç[aã]o(?: Federal)?|C[oó]digo [A-Za-zÀ-ÿ ]+|Lei(?: Complementar)?\s*(?:n[ºo.]?\s*)?\d+[./-]?[\d./-]*)",
    re.IGNORECASE,
)
_ARTICLE_DETECT_PATTERN = re.compile(
    r"\b(?:art(?:igo)?[.]?\s*\d+(?:[ºo])?|§\s*\d+(?:[ºo])?)",
    re.IGNORECASE,
)
_COURT_CONTEXT_PATTERN = re.compile(
    r"\b(?:STF|STJ|TSE|TST|STM)\b[^\n]{0,100}\b(?:20\d{2}|Relator|Relatora)\b",
    re.IGNORECASE,
)
_GENERAL_JURISPRUDENCE_PATTERN = re.compile(
    r"\b(?:jurisprudência\s+(?:pacífica|consolidada)(?:\s+(?:desta|dos)\s+(?:Corte|tribunais superiores))?|orientação jurisprudencial(?:\s+consolidada)?|entendimento sumulado(?:\s+sobre\s+a\s+matéria)?|precedentes desta Casa(?:\s+em\s+20\d{2})?|verbete sumular aplicável)\b",
    re.IGNORECASE,
)

_RULES = (
    ("cnj", _CNJ_PATTERN, "jurisprudencia"),
    ("processo_ou_recurso", _PROCESS_PATTERN, "jurisprudencia"),
    ("sumula_numerada", _SUMULA_PATTERN, "jurisprudencia"),
    ("lei_com_diploma", _LAW_WITH_DIPLOMA_PATTERN, "lei"),
    ("dispositivo_legal", _ARTICLE_DETECT_PATTERN, "lei"),
    ("tribunal_contextual", _COURT_CONTEXT_PATTERN, "jurisprudencia"),
    ("jurisprudencia_geral", _GENERAL_JURISPRUDENCE_PATTERN, "jurisprudencia"),
)


@dataclass(frozen=True)
class CitationCandidate:
    """Um intervalo candidato no texto original; ``end`` é exclusivo."""

    start: int
    end: int
    text: str
    rule: str
    family: str


def _family_for(text: str, citation_type: str) -> str:
    """Replica a classificação superficial usada pela baseline do Notebook 06."""
    if citation_type == "lei":
        if _ARTICLE_PATTERN.search(text) and _DIPLOMA_PATTERN.search(text):
            return "lei_dispositivo_com_diploma"
        if _ARTICLE_PATTERN.search(text):
            return "lei_dispositivo_sem_diploma"
        return "lei_referencia_geral"

    if _SUMULA_PATTERN.search(text):
        return "sumula_numerada"
    if re.search(r"\bS[ÚU]MULA\b", text, re.IGNORECASE):
        return "sumula_sem_numero"
    if _CNJ_PATTERN.search(text):
        return "processo_cnj"
    if _PROCESS_CLASS_PATTERN.search(text) and re.search(r"\d", text):
        return "processo_ou_recurso_numerado"
    if _TRIBUNAL_PATTERN.search(text) and (
        re.search(r"\b20\d{2}\b", text)
        or re.search(r"Relator|Relatora|ministro|ministra", text, re.IGNORECASE)
    ):
        return "jurisprudencia_tribunal_contextual"
    return "jurisprudencia_referencia_geral"


class CitationDetector:
    """Encontra candidatos da baseline sem consultar corpus ou índice de casos."""

    def detect(self, text: str) -> tuple[CitationCandidate, ...]:
        """Retorna candidatos ordenados, deduplicados por intervalo exato."""
        if not isinstance(text, str):
            raise TypeError("text deve ser uma string")

        candidates = [
            CitationCandidate(
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                rule=rule,
                family=_family_for(match.group(0), citation_type),
            )
            for rule, pattern, citation_type in _RULES
            for match in pattern.finditer(text)
        ]

        unique_by_span: dict[tuple[int, int], CitationCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: (item.start, item.end, item.rule)):
            unique_by_span.setdefault((candidate.start, candidate.end), candidate)

        return tuple(unique_by_span.values())
