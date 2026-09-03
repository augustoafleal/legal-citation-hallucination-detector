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
    r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|RHC|RMS|AR|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|Agravo|Recurso Especial|Recurso Extraordinário|Habeas Corpus|Reclamação)\b",
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
_UF_CODES = (
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR "
    "SC SP SE TO"
).split()
_V5_PROCESS_PATTERN = re.compile(
    r"\b(?:RHC|RMS|AR)\s*(?:n[ºo.]?\s*)?[ \t\u00a0]*(?:\n[ \t\u00a0]*)?"
    r"\d[\d.\-/ \t\u00a0]*\d"
    r"(?:[ \t\u00a0]*(?:[-/]\s*|\(\s*|[ \t\u00a0]+)(?:"
    + "|".join(_UF_CODES)
    + r")(?:\s*\))?)?(?=$|[^\w])",
    re.IGNORECASE,
)
_PROCESS_UF_TAIL = re.compile(
    r"^[ \t\u00a0]*(?:[-/][ \t\u00a0]*|\([ \t\u00a0]*|[ \t\u00a0]+)(?:"
    + "|".join(_UF_CODES)
    + r")(?:[ \t\u00a0]*\))?\b"
)

# V5 keeps the experimental mechanisms deliberately narrow.  These patterns
# are structural extensions of an already numbered process candidate; they
# are not general legal-language or OCR normalizers.
_H1_PREFIX_CHAIN = re.compile(
    r"(?P<prefix>(?<!\w)"
    r"(?:(?:Embargos?[ \t\u00a0]+de[ \t\u00a0]+Declaraç[aã]o|EDcl|"
    r"Agravo[ \t\u00a0]+Interno|AgInt|Agravo[ \t\u00a0]+Regimental|AgRg)"
    r"[ \t\u00a0]+(?:no|nos|na|nas|em)[ \t\u00a0]+"
    r"|Agravo[ \t\u00a0]+em[ \t\u00a0]+)+"
    r"|(?:(?:Primeiro|Segundo|Terceiro|Quarto|Quinto)[ \t\u00a0]+)?"
    r"AG[.]?[ \t\u00a0]*REG[.]?[ \t\u00a0]+n(?:o|a)[ \t\u00a0]+"
    r")$",
    re.IGNORECASE,
)
_H2_DOTTED_CLASS = re.compile(
    r"\b(?:(?:AgInt|AgRg|EDcl|ED)[ \t\u00a0]+(?:no|nos|na|nas)[ \t\u00a0]+)*"
    r"(?:Rec[ \t\u00a0]*[.][ \t\u00a0]*Esp[ \t\u00a0]*[.]|H[ \t\u00a0]*[.][ \t\u00a0]*C[ \t\u00a0]*[.])[ \t\u00a0]*"
    r"n(?:[º°.]|o)?[ \t\u00a0]*(?:\n[ \t\u00a0]*)?"
    r"\d(?:[\d.\-/ \t\u00a0]*\d)?"
    r"(?:[ \t\u00a0]*(?:[-/][ \t\u00a0]*|\([ \t\u00a0]*|[ \t\u00a0]+)(?:"
    + "|".join(_UF_CODES)
    + r")(?:[ \t\u00a0]*\))?)?\b",
    re.IGNORECASE,
)
_H3_OCR_NUMERIC_TAIL = re.compile(
    r"^(?P<tail>[OIlS]\d(?:[\d.\-/ \t\u00a0OIlS]*\d)?"
    r"(?:[ \t\u00a0]*(?:[-/][ \t\u00a0]*|\([ \t\u00a0]*|[ \t\u00a0]+)(?:"
    + "|".join(_UF_CODES)
    + r")(?:[ \t\u00a0]*\))?)?)\b",
    re.IGNORECASE,
)
_V5_CNJ_PROCEDURAL_PREFIX = re.compile(
    r"(?P<prefix>(?<!\w)(?:"
    r"Recurso[ \t\u00a0]+Especial[ \t\u00a0]+Eleitoral|"
    r"Agravo[ \t\u00a0]+Regimental[ \t\u00a0]+no[ \t\u00a0]+"
    r"Agravo[ \t\u00a0]+de[ \t\u00a0]+Instrumento"
    r")[ \t\u00a0]+n(?:[º°.]|o)?[ \t\u00a0]*)$",
    re.IGNORECASE,
)
_V5_STANDALONE_MODIFIER = re.compile(
    r"\b(?:AgInt|AgRg|EDcl|ED)\s+n(?:[º°.]|o)?[ \t\u00a0]+"
    r"(?P<number>\d[\d.\-/ \t\u00a0]*\d)"
    r"(?:[ \t\u00a0]*(?:[-/]\s*|\(\s*|[ \t\u00a0]+)(?:"
    + "|".join(_UF_CODES)
    + r")(?:\s*\))?)?(?=$|[^\w])",
    re.IGNORECASE,
)
_V5_COMPOUND_PROCEDURE_TITLE = re.compile(
    r"\bAgravo[ \t\u00a0\r\n]+Interno[ \t\u00a0\r\n]+n(?:a|o)[ \t\u00a0\r\n]+"
    r"Suspens[aã]o[ \t\u00a0\r\n]+de[ \t\u00a0\r\n]+"
    r"(?:Liminar(?:[ \t\u00a0\r\n]+e[ \t\u00a0\r\n]+de[ \t\u00a0\r\n]+Senten[cç]a|)|"
    r"Seguran[cç]a)[ \t\u00a0\r\n]*"
    r"n(?:[º°.]|o)?[ \t\u00a0\r\n]*(?:\n[ \t\u00a0]*)?"
    r"\d[\d.\-/ \t\u00a0]*\d"
    r"(?:[ \t\u00a0]*(?:[-/]\s*|\(\s*|[ \t\u00a0]+)(?:"
    + "|".join(_UF_CODES)
    + r")(?:\s*\))?)?(?=$|[^\w])",
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
    """Encontra candidatos V5 sem consultar corpus ou índice de casos."""

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

        # Recupera somente a UF horizontal imediatamente ligada a um processo.
        candidates = [
            self._expand_process_uf(candidate, text) for candidate in candidates
        ]
        candidates.extend(self._detect_v5_process_forms(text))
        candidates = [
            self._expand_cnj_prefix(candidate, text) for candidate in candidates
        ]
        candidates = [
            self._expand_process_prefix(candidate, text) for candidate in candidates
        ]
        candidates.extend(self._detect_dotted_classes(text))
        candidates.extend(self._detect_v5_standalone_modifiers(text))
        candidates.extend(self._detect_v5_compound_titles(text))
        candidates = [
            self._expand_process_ocr_tail(candidate, text) for candidate in candidates
        ]

        unique_by_span: dict[tuple[int, int], CitationCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: (item.start, item.end, item.rule)):
            unique_by_span.setdefault((candidate.start, candidate.end), candidate)

        return tuple(unique_by_span.values())

    @staticmethod
    def _detect_v5_process_forms(text: str) -> list[CitationCandidate]:
        """Detecta RHC, RMS e AR com marcador/número local e UF opcional."""
        return [
            CitationCandidate(
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                rule="processo_ou_recurso",
                family="processo_ou_recurso_numerado",
            )
            for match in _V5_PROCESS_PATTERN.finditer(text)
        ]

    @staticmethod
    def _expand_cnj_prefix(candidate: CitationCandidate, text: str) -> CitationCandidate:
        """Expande um CNJ por um título processual formal imediatamente anterior."""
        if candidate.rule != "cnj" or candidate.family != "processo_cnj":
            return candidate
        window_start = max(0, candidate.start - 120)
        match = _V5_CNJ_PROCEDURAL_PREFIX.search(text[window_start : candidate.start])
        if match is None:
            return candidate
        start = window_start + match.start("prefix")
        return CitationCandidate(
            start=start,
            end=candidate.end,
            text=text[start : candidate.end],
            rule=candidate.rule,
            family=candidate.family,
        )

    @staticmethod
    def _expand_process_uf(candidate: CitationCandidate, text: str) -> CitationCandidate:
        """Expande um processo até sua UF adjacente, sem atravessar newline."""
        if candidate.rule != "processo_ou_recurso" or candidate.family != "processo_ou_recurso_numerado":
            return candidate
        tail = _PROCESS_UF_TAIL.match(text[candidate.end :])
        if tail is None:
            return candidate
        end = candidate.end + tail.end()
        return CitationCandidate(
            start=candidate.start,
            end=end,
            text=text[candidate.start:end],
            rule=candidate.rule,
            family=candidate.family,
        )

    @staticmethod
    def _expand_process_prefix(candidate: CitationCandidate, text: str) -> CitationCandidate:
        """Expande um processo por uma cadeia processual imediatamente anterior."""
        if (
            candidate.rule != "processo_ou_recurso"
            or candidate.family != "processo_ou_recurso_numerado"
        ):
            return candidate

        window_start = max(0, candidate.start - 140)
        match = _H1_PREFIX_CHAIN.search(text[window_start:candidate.start])
        if match is None:
            return candidate

        start = window_start + match.start("prefix")
        return CitationCandidate(
            start=start,
            end=candidate.end,
            text=text[start:candidate.end],
            rule=candidate.rule,
            family=candidate.family,
        )

    @staticmethod
    def _detect_dotted_classes(text: str) -> list[CitationCandidate]:
        """Detecta somente os aliases pontuados aprovados pela auditoria."""
        return [
            CitationCandidate(
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                rule="processo_ou_recurso",
                family="processo_ou_recurso_numerado",
            )
            for match in _H2_DOTTED_CLASS.finditer(text)
        ]

    @staticmethod
    def _detect_v5_standalone_modifiers(text: str) -> list[CitationCandidate]:
        """Detecta modificador processual seguido diretamente de identificador."""
        candidates = []
        for match in _V5_STANDALONE_MODIFIER.finditer(text):
            number = match.group("number")
            if _CNJ_PATTERN.fullmatch(number):
                continue
            candidates.append(
                CitationCandidate(
                    start=match.start(),
                    end=match.end(),
                    text=match.group(0),
                    rule="processo_ou_recurso",
                    family="processo_ou_recurso_numerado",
                )
            )
        return candidates

    @staticmethod
    def _detect_v5_compound_titles(text: str) -> list[CitationCandidate]:
        """Detecta títulos compostos de agravo com marcador numérico local."""
        return [
            CitationCandidate(
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                rule="processo_ou_recurso",
                family="processo_ou_recurso_numerado",
            )
            for match in _V5_COMPOUND_PROCEDURE_TITLE.finditer(text)
        ]

    @staticmethod
    def _expand_process_ocr_tail(candidate: CitationCandidate, text: str) -> CitationCandidate:
        """Estende uma referência existente por uma cauda OCR numérica local."""
        if candidate.family != "processo_ou_recurso_numerado":
            return candidate
        match = _H3_OCR_NUMERIC_TAIL.match(text[candidate.end :])
        if match is None:
            return candidate

        end = candidate.end + match.end("tail")
        return CitationCandidate(
            start=candidate.start,
            end=end,
            text=text[candidate.start:end],
            rule=candidate.rule,
            family=candidate.family,
        )
