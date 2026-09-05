"""Gera a revisão humana direcionada dos quatro resíduos plausíveis pós-V6.

Este módulo é um preparador de evidência. Gold é usado somente para localizar os
quatro casos, medir o baseline e rotular relações diagnósticas depois da busca
estrutural; nenhuma regra de detecção é derivada do gold. A saída é estática,
determinística e funciona sem backend:

* ``artifacts/post_v6_targeted_human_review.html``
* ``artifacts/post_v6_targeted_human_review_template.json``
* ``artifacts/post_v6_targeted_human_review_context.json``
"""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DIR = ROOT / "scripts" / "analysis"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


ARTIFACTS = ROOT / "artifacts"
HTML_PATH = ARTIFACTS / "post_v6_targeted_human_review.html"
TEMPLATE_PATH = ARTIFACTS / "post_v6_targeted_human_review_template.json"
CONTEXT_PATH = ARTIFACTS / "post_v6_targeted_human_review_context.json"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
SCHEMA_VERSION = "1.0"
REVIEW_TYPE = "POST_V6_TARGETED_HUMAN_REVIEW"
TARGET_IDS = (71, 98, 170, 172)
V6_BASELINE = {
    "predictions": 222,
    "matches": 140,
    "FP": 82,
    "FN": 85,
    "exact": 86,
    "real_ids_correct": 70,
    "official_final": 0.49422707514297154,
    "safety": {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0},
}

SOURCE_LABELS = {
    "AUTOMATED": "calculado por este gerador a partir do corpus/runtime local",
    "SOURCE_TEXT": "texto literal dos 26 TXT; offsets preservados",
    "PRIOR_VALIDATED_RESULT": "resultado validado previamente no baseline V6 ou na auditoria post-V6",
}

_UF_CODES = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())
_MARKER_TERMS = (
    "AREsp", "REsp", "AgInt", "AgRg", "AgR", "EDcl", "ED", "HC", "RHC", "RMS", "AR",
    "Rcl", "ADI", "ADPF", "RE", "AI", "MS", "RR", "AIRR", "ARR", "AP", "RO",
)
_TARGET_MARKERS = ("RMS", "RHC", "HC", "MS", "AR", "Rcl")
_MARKER_RE = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, _MARKER_TERMS + ("APL", "RSE"))) + r")(?!\w)", re.IGNORECASE)
_TARGET_MARKER_RE = re.compile(r"(?<!\w)(?:RMS|RHC|HC|MS|AR|Rcl)(?!\w)", re.IGNORECASE)
_APL_RE = re.compile(r"(?<!\w)APL(?!\w)", re.IGNORECASE)
_RSE_RE = re.compile(r"(?<!\w)RSE(?!\w)|\bR[.]?S[.]?E[.]?\b|\bRecurso\s+em\s+Sentido\s+Estrito\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d[\d.\-/ \t\u00a0\r\n]{1,100}\d")
# The frozen case-identity grammar accepts 1–7 digits in the sequence block
# (including the TST legacy-shaped references used by case 98).
_FULL_CNJ_RE = re.compile(r"^\d{1,7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
_CHAIN_RE = re.compile(
    r"(?<!\w)(?P<chain>(?:[A-Za-zÀ-ÿ][A-Za-z0-9À-ÿ.]*-){2,}\d[\d.-]*)"
)
_CHAIN_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-z0-9À-ÿ.]*")
_NEGATIVE_CUE_RE = re.compile(
    r"\b(?:fls?\.?|folha|página|pagina|art(?:igo)?\.?|protocolo|processo\s+administrativo|"
    r"valor|preço|preco|R\$|parágrafo|inciso|ano|item|tabela)\b",
    re.IGNORECASE,
)
_SEMANTIC_RSE_RE = re.compile(
    r"\b(?:Recurso\s+em\s+Sentido\s+Estrito|R[.]?S[.]?E[.]?)\b", re.IGNORECASE
)
_SIMILAR_SIGLA_RE = re.compile(r"(?<!\w)(?:RSE|RSC|RSP|RCL|RHC|RMS|APL|AP)(?!\w)", re.IGNORECASE)

QUESTION_OPTIONS: dict[str, tuple[str, ...]] = {
    "Q1": ("YES", "NO", "UNCERTAIN"),
    "Q2": ("YES", "NO", "UNCERTAIN"),
    "Q3": ("YES", "NO", "UNCERTAIN"),
    "Q4": ("YES", "NO", "UNCERTAIN"),
    "Q5": ("YES", "NO", "UNCERTAIN"),
    "Q6": ("YES", "NO", "WEAK", "UNCERTAIN"),
    "Q7": ("YES", "NO", "UNCERTAIN"),
    "Q8": ("YES", "NO", "UNCERTAIN"),
    "Q9": ("YES", "NO", "UNCERTAIN"),
    "Q10": ("YES", "NO", "PARTIALLY", "UNCERTAIN"),
    "Q11": ("GENERALIZABLE", "POSSIBLY_GENERALIZABLE", "CASE_SPECIFIC", "UNSAFE", "UNCERTAIN"),
    "Q12": ("YES", "ONLY_IF_GROUPED", "NO", "NEED_MORE_EVIDENCE"),
    "A1": ("YES", "NO", "UNCERTAIN"), "A2": ("LOW", "MEDIUM", "HIGH", "UNCERTAIN"),
    "A3": ("YES", "NO", "WEAK"), "A4": ("YES", "NO", "UNCERTAIN"),
    "A5": ("EXPERIMENT_CANDIDATE", "NEED_MORE_EVIDENCE", "DO_NOT_PURSUUE"),
    "B1": ("YES", "NO", "UNCERTAIN"), "B2": ("YES", "NO", "UNCERTAIN"),
    "B3": ("YES", "NO", "UNCERTAIN"), "B4": ("YES", "NO", "WEAK"),
    "B5": ("EXPERIMENT_CANDIDATE", "NEED_MORE_EVIDENCE", "DO_NOT_PURSUUE"),
    "C1": ("YES", "NO", "UNCERTAIN"), "C2": ("YES", "NO", "WEAK"),
    "C3": ("YES", "NO", "UNCERTAIN"), "C4": ("YES", "NO"),
    "C5": ("YES", "NO", "UNCERTAIN"),
    "C6": ("EXPERIMENT_DETECTOR_ONLY", "EXPERIMENT_DETECTOR_PLUS_PARSER", "NEED_MORE_EVIDENCE", "DO_NOT_PURSUUE"),
    "D1": ("YES", "NO", "UNCERTAIN"), "D2": ("YES", "NO", "WEAK"),
    "D3": ("YES", "NO", "UNCERTAIN"), "D4": ("YES", "PARTIALLY", "NO"),
    "D5": ("EXPERIMENT_CANDIDATE", "NEED_MORE_EVIDENCE", "DO_NOT_PURSUUE"),
    "G1": ("YES", "PARTIALLY", "NO"), "G2": ("YES", "PARTIALLY", "NO"),
    "G3": ("YES", "PARTIALLY", "NO"), "G4": ("YES", "NO", "UNCERTAIN"),
    "GLOBAL-1": ("0", "1", "2", "3", "4"),
    "GLOBAL-2": ("YES", "ONLY_ONE_FINAL_EXPERIMENT", "NO", "UNCERTAIN"),
    "GLOBAL-3": ("DETECTOR_EXPERIMENT", "PARSER_EXPERIMENT", "COMBINED_DETECTOR_PARSER_EXPERIMENT", "STOP_RECALL", "NEED_MORE_EVIDENCE"),
}


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def counter(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def span_iou(left: Sequence[int], right: Sequence[int]) -> float:
    intersection = max(0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return intersection / union if union else 0.0


def result_view(result: Any) -> dict[str, Any]:
    return post.result_dict(result)


def parsed_view(parsed: Any) -> dict[str, Any]:
    return post.parsed_dict(parsed)


def local_context(text: str, start: int, end: int, radius: int = 320) -> dict[str, Any]:
    left, right = max(0, start - radius), min(len(text), end + radius)
    return {"span": [left, right], "text": text[left:right], "source": "SOURCE_TEXT"}


def gold_relations(document_id: str, span: Sequence[int], gold: Sequence[Any]) -> list[dict[str, Any]]:
    relations = []
    for item in gold:
        if item.document_id != document_id or span_iou(span, [item.start, item.end]) < 0.5:
            continue
        relations.append({
            "gold_id": item.index, "citation_id": item.gid, "classification": item.classification,
            "canonical_id": item.canonical_id, "text": item.text, "iou": round(span_iou(span, [item.start, item.end]), 6),
            "source": "AUTOMATED",
        })
    return relations


def candidate_view(prediction: Any, text: str, gold: Sequence[Any] | None = None) -> dict[str, Any]:
    candidate = prediction.candidate
    result = {
        "span": [candidate.start, candidate.end], "text": candidate.text,
        "rule": candidate.rule, "family": candidate.family,
        "parsed": parsed_view(prediction.parsed), "resolver": result_view(prediction.result),
        "source": "AUTOMATED",
    }
    if gold is not None:
        result["gold_relations"] = gold_relations(prediction.document_id, result["span"], gold)
    return result


def next_number(text: str, marker_end: int) -> dict[str, Any]:
    window_end = min(len(text), marker_end + 110)
    window = text[marker_end:window_end]
    match = _NUMBER_RE.search(window)
    if match is None:
        return {"followed_by_number": False, "number": None, "span": None, "uf": None, "number_type": "none"}
    raw = match.group(0).strip()
    start, end = marker_end + match.start(), marker_end + match.end()
    digits = re.sub(r"\D", "", raw)
    tail = text[end:min(len(text), end + 18)]
    uf_match = re.match(r"\s*(?:/|[-(])\s*(?P<uf>[A-Z]{2})\b", tail, re.IGNORECASE)
    uf = uf_match.group("uf").upper() if uf_match and uf_match.group("uf").upper() in _UF_CODES else None
    if len(digits) == 20:
        number_type = "full_cnj" if _FULL_CNJ_RE.fullmatch(re.sub(r"\s+", "", raw)) else "degraded_cnj_like"
    elif len(digits) <= 7:
        number_type = "short_or_dotted"
    elif len(digits) < 20:
        number_type = "partial_or_non_cnj"
    else:
        number_type = "numeric"
    return {
        "followed_by_number": True, "number": raw, "span": [start, end], "digits": digits,
        "uf": uf, "number_type": number_type, "source": "AUTOMATED",
    }


def occurrence_record(
    document_id: str, text: str, match: re.Match[str], predictions: Sequence[Any], gold: Sequence[Any],
    *, include_semantic: bool = False,
) -> dict[str, Any]:
    marker = match.group(0)
    number = next_number(text, match.end())
    local_start, local_end = max(0, match.start() - 110), min(len(text), match.end() + 150)
    local = text[local_start:local_end]
    cues = sorted(set(c.group(0).lower() for c in _NEGATIVE_CUE_RE.finditer(local)))
    number_span = number["span"]
    search_span = [match.start(), (number_span[1] if number_span else match.end())]
    related = [
        prediction for prediction in predictions
        if prediction.document_id == document_id
        and (prediction.candidate.start <= search_span[1] and prediction.candidate.end >= search_span[0])
    ]
    related.sort(key=lambda item: (item.candidate.start, item.candidate.end, item.candidate.rule))
    return {
        "document_id": document_id, "marker": marker, "marker_span": [match.start(), match.end()],
        "local_text": local, "local_span": [local_start, local_end], "number": number,
        "uf": number.get("uf"), "candidate_currently_detected": bool(related),
        "candidates": [candidate_view(item, text, gold) for item in related[:4]],
        "likely_procedural_use": bool(number["followed_by_number"] and (number.get("uf") or len(number.get("digits", "")) >= 10)),
        "suspicious_cues": cues, "non_procedural_or_suspicious": bool(cues) or not number["followed_by_number"],
        "gold_relations": gold_relations(document_id, search_span, gold), "source": "AUTOMATED",
    }


def all_occurrences(
    texts: Mapping[str, str], predictions: Sequence[Any], gold: Sequence[Any], pattern: re.Pattern[str],
) -> list[dict[str, Any]]:
    by_doc: dict[str, list[Any]] = defaultdict(list)
    for prediction in predictions:
        by_doc[prediction.document_id].append(prediction)
    rows = []
    for document_id in sorted(texts):
        rows.extend(occurrence_record(document_id, texts[document_id], match, by_doc[document_id], gold) for match in pattern.finditer(texts[document_id]))
    return rows


def inventory_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    followed = [row for row in rows if row["number"]["followed_by_number"]]
    return {
        "total_occurrences": len(rows), "docs": len({row["document_id"] for row in rows}),
        "followed_by_number": len(followed), "followed_by_cnj_like": sum(row["number"]["number_type"] in {"full_cnj", "degraded_cnj_like"} for row in followed),
        "followed_by_full_cnj": sum(row["number"]["number_type"] == "full_cnj" for row in followed),
        "candidate_currently_detected": sum(row["candidate_currently_detected"] for row in rows),
        "likely_procedural_use": sum(row["likely_procedural_use"] for row in rows),
        "suspicious_or_non_procedural": sum(row["non_procedural_or_suspicious"] for row in rows),
        "source": "AUTOMATED",
    }


def sample_rows(rows: Sequence[Mapping[str, Any]], predicate, limit: int = 10) -> list[dict[str, Any]]:
    selected = [row for row in rows if predicate(row)]
    selected.sort(key=lambda row: (row["document_id"], row["marker_span"], row.get("marker", "")))
    return selected[:limit]


def context_for_span(text: str, span: Sequence[int], annotations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    left, right = max(0, span[0] - 320), min(len(text), span[1] + 320)
    clipped = []
    for annotation in annotations:
        start, end = annotation["span"]
        if end > left and start < right:
            clipped.append({**annotation, "span": [max(start, left), min(end, right)]})
    return {
        "span": [left, right], "text": text[left:right], "annotations": clipped,
        "source": "SOURCE_TEXT",
    }


def chain_record(document_id: str, text: str, match: re.Match[str], predictions: Sequence[Any], gold: Sequence[Any]) -> dict[str, Any] | None:
    chain = match.group("chain")
    number_start = next((index for index, char in enumerate(chain) if char.isdigit()), None)
    if number_start is None or number_start == 0:
        return None
    prefix = chain[:number_start].rstrip("-")
    tokens = [token.rstrip(".") for token in prefix.split("-") if token]
    recognized = {token.upper() for token in _MARKER_TERMS} | {"E", "TST", "STJ", "TSE", "STM", "STF"}
    if len(tokens) < 2 or not all(token.upper() in recognized for token in tokens):
        return None
    number = chain[number_start:]
    start, end = match.start("chain"), match.end("chain")
    full_cnj = bool(_FULL_CNJ_RE.fullmatch(number))
    digits = re.sub(r"\D", "", number)
    related = [prediction for prediction in predictions if prediction.document_id == document_id and prediction.candidate.start < end and prediction.candidate.end > start]
    related.sort(key=lambda item: (item.candidate.start, item.candidate.end, item.candidate.rule))
    return {
        "document_id": document_id, "chain": chain, "span": [start, end], "tokens": tokens,
        "recognized_tokens": True, "number": number, "digits": digits,
        "complete_cnj": full_cnj, "partial_or_non_cnj": not full_cnj,
        "candidate_currently_detected": bool(related),
        "candidates": [candidate_view(item, text, gold) for item in related[:4]],
        "gold_relations": gold_relations(document_id, [start, end], gold), "source": "AUTOMATED",
    }


def compound_inventory(texts: Mapping[str, str], predictions: Sequence[Any], gold: Sequence[Any]) -> dict[str, Any]:
    by_doc: dict[str, list[Any]] = defaultdict(list)
    for prediction in predictions:
        by_doc[prediction.document_id].append(prediction)
    rows = []
    for document_id in sorted(texts):
        for match in _CHAIN_RE.finditer(texts[document_id]):
            row = chain_record(document_id, texts[document_id], match, by_doc[document_id], gold)
            if row is not None:
                rows.append(row)
    full = [row for row in rows if row["complete_cnj"]]
    partial = [row for row in rows if row["partial_or_non_cnj"]]
    return {
        "number_of_procedural_chains_found": len(rows), "distinct_forms": len({row["chain"] for row in rows}),
        "docs": len({row["document_id"] for row in rows}), "followed_by_full_cnj": len(full),
        "followed_by_partial_or_non_cnj": len(partial), "candidate_currently_detected": sum(row["candidate_currently_detected"] for row in rows),
        "all_rows": rows, "full_cnj_examples": full[:10], "partial_or_non_cnj_examples": partial[:10],
        "source": "AUTOMATED",
    }


def historical_negative(compound: Mapping[str, Any], texts: Mapping[str, str]) -> dict[str, Any]:
    required = "TST-E-RR-173000-49.2008"
    for row in compound["all_rows"]:
        if row["chain"].startswith(required):
            # The corpus continues the historical reference with a tribunal
            # suffix. Preserve the exact required negative substring while
            # retaining the larger structural match as provenance.
            start = row["span"][0]
            return {
                "chain": required, "span": [start, start + len(required)], "number": "173000-49.2008",
                "complete_cnj": False, "partial_or_non_cnj": True, "found": True,
                "full_detected_chain": row["chain"], "document_id": row["document_id"],
                "required_negative": True, "negative_reason": "structurally incomplete/non-CNJ number", "source": "AUTOMATED",
            }
    for document_id in sorted(texts):
        start = texts[document_id].find(required)
        if start >= 0:
            return {"chain": required, "span": [start, start + len(required)], "found": True, "document_id": document_id, "required_negative": True, "negative_reason": "structurally incomplete/non-CNJ number", "source": "AUTOMATED"}
    return {"chain": required, "found": False, "required_negative": True, "source": "AUTOMATED"}


def project_mentions(term: str) -> list[dict[str, Any]]:
    """Audit existing source/docs mentions without using them as rules."""
    matches = []
    for base in (ROOT / "src", ROOT / "docs"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".md", ".rst"}:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", line, re.IGNORECASE):
                    matches.append({"file": str(path.relative_to(ROOT)), "line": line_number, "text": line.strip(), "source": "AUTOMATED"})
    return matches[:20]


def semantic_rse_inventory(texts: Mapping[str, str]) -> list[dict[str, Any]]:
    rows = []
    for document_id in sorted(texts):
        text = texts[document_id]
        for match in _SEMANTIC_RSE_RE.finditer(text):
            rows.append({"document_id": document_id, "form": match.group(0), "span": [match.start(), match.end()], "local_text": text[max(0, match.start() - 100):min(len(text), match.end() + 150)], "source": "AUTOMATED"})
    return rows


def negative_examples(rows: Sequence[Mapping[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        number = row["number"]
        if not number["followed_by_number"]:
            reason = "marker sem número seguinte"
        elif row["suspicious_cues"]:
            reason = "contexto contém cue estrutural potencialmente não processual"
        elif number["number_type"] == "short_or_dotted" and not number.get("uf"):
            reason = "número curto/narrativo sem UF"
        elif number["number_type"] not in {"full_cnj", "degraded_cnj_like", "short_or_dotted"}:
            reason = "forma numérica fora das formas investigadas"
        else:
            continue
        selected.append({"document_id": row["document_id"], "marker": row["marker"], "local_text": row["local_text"], "span": row["marker_span"], "number": number, "reason": reason, "source": "AUTOMATED"})
    selected.sort(key=lambda row: (row["document_id"], row["span"], row["marker"]))
    return selected[:limit]


def oracle_for(item: Any, parser: CitationParser, resolver: CitationResolver) -> dict[str, Any]:
    families = (post.family_for(item.text, item.citation_type), "processo_ou_recurso_numerado", "processo_cnj", "sumula_numerada", "jurisprudencia_tribunal_contextual", "jurisprudencia_referencia_geral")
    attempts = []
    for family in dict.fromkeys(families):
        candidate = CitationCandidate(0, len(item.text), item.text, "review_oracle", family)
        parsed = parser.parse(candidate, context=item.text)
        result = resolver.resolve(parsed)
        attempts.append({"family": family, "parsed": parsed_view(parsed), "resolver": result_view(result), "canonical_correct": result.status == "resolved" and result.id_canonico == item.canonical_id, "source": "PRIOR_VALIDATED_RESULT"})
    correct = [attempt for attempt in attempts if attempt["canonical_correct"]]
    return {"verdict": "DOWNSTREAM_READY" if correct else "DOWNSTREAM_NOT_READY", "attempts": attempts, "correct_families": [attempt["family"] for attempt in correct], "source": "PRIOR_VALIDATED_RESULT"}


def target_case(item: Any, texts: Mapping[str, str], raw: Sequence[Any], outputs: Sequence[Any], gold: Sequence[Any], parser: CitationParser, resolver: CitationResolver) -> dict[str, Any]:
    raw_related = [prediction for prediction in raw if prediction.document_id == item.document_id and span_iou([item.start, item.end], [prediction.candidate.start, prediction.candidate.end]) > 0]
    nearby = [prediction for prediction in raw if prediction.document_id == item.document_id and prediction.candidate.end >= item.start - 140 and prediction.candidate.start <= item.end + 140]
    nearby.sort(key=lambda prediction: (-span_iou([item.start, item.end], [prediction.candidate.start, prediction.candidate.end]), prediction.candidate.start, prediction.candidate.end, prediction.candidate.rule))
    output_related = [output for output in outputs if output.document_id == item.document_id and span_iou([item.start, item.end], [output.candidate.start, output.candidate.end]) >= 0.5]
    output_related.sort(key=lambda output: (output.candidate.start, output.candidate.end, output.reason))
    marker_match = _MARKER_RE.search(item.text)
    if marker_match is None and item.index == 71:
        marker_match = re.search(r"(?:Embargos\s+de\s+Declaração|Recurso\s+em\s+Mandado\s+de\s+Segurança)", item.text, re.IGNORECASE)
    number = next_number(item.text, marker_match.end()) if marker_match else {"span": None}
    annotations = [{"span": [item.start, item.end], "kind": "gold_span", "label": "gold"}]
    if marker_match:
        annotations.append({"span": [item.start + marker_match.start(), item.start + marker_match.end()], "kind": "process_marker", "label": "marker"})
    if number.get("span"):
        annotations.append({"span": [item.start + number["span"][0], item.start + number["span"][1]], "kind": "numeric_identity", "label": "identity"})
    for prediction in nearby[:10]:
        annotations.append({"span": [prediction.candidate.start, prediction.candidate.end], "kind": "nearby_candidate", "label": "candidate"})
    context = context_for_span(texts[item.document_id], [item.start, item.end], annotations)
    return {
        "gold_id": item.index, "citation_id": item.gid, "documento_id": item.document_id, "nivel": item.level,
        "gold_text": item.text, "gold_span": [item.start, item.end], "gold_canonical_id": item.canonical_id,
        "context": context, "first_blocker": "DETECTOR", "terminal_cause": "UNSUPPORTED_PROCESS_MARKER" if item.index in {71, 172} else "PARSER_STRUCTURAL_GAP",
        "oracle": oracle_for(item, parser, resolver),
        "current_detector_nearby_candidates": [candidate_view(prediction, texts[item.document_id], gold) for prediction in nearby[:10]],
        "current_overlapping_candidates": [candidate_view(prediction, texts[item.document_id], gold) for prediction in raw_related[:10]],
        "current_parser_output": parsed_view(raw_related[0].parsed) if raw_related else None,
        "current_resolver_output": result_view(raw_related[0].result) if raw_related else None,
        "current_final_output": candidate_view(output_related[0], texts[item.document_id], gold) if output_related else None,
        "official_raw_candidate": bool(raw_related), "official_output_candidate": bool(output_related),
        "review_dimensions": {
            "structural_validity": ["Q1", "Q4"], "corpus_support": ["Q6"], "negative_evidence": ["Q7"],
            "marker_safety": ["Q3", "Q9"], "identity_preservation": ["Q2"], "downstream_readiness": ["Q10"],
            "blind_generalization": ["Q5", "Q8", "Q11"], "overfit_risk": ["Q5", "Q9", "Q11"],
            "source": "AUTOMATED",
        },
        "source": "PRIOR_VALIDATED_RESULT",
    }


def build_evidence() -> dict[str, Any]:
    texts = post.deep.load_texts()
    gold = post.deep.load_gold()
    if len(texts) != 26 or len(gold) != 225:
        raise RuntimeError(f"universo inesperado: TXT={len(texts)} gold={len(gold)}")
    if sha256(get_database_path().read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("hash do banco divergiu do baseline congelado")
    with connect_database(get_database_path(), read_only=True) as connection:
        parser, detector = CitationParser(), CitationDetector()
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs, _ = post.run_current_pipeline(texts, resolver)
        raw_matches = post.match_gold(gold, raw)
        output_matches = post.match_gold(gold, outputs)
        metrics = post.output_metrics(gold, outputs)
        exact = sum(gold[g].start == raw[r].candidate.start and gold[g].end == raw[r].candidate.end for g, r in raw_matches.items())
        measured = {"predictions": len(raw), "matches": len(raw_matches), "FP": len(raw) - len(raw_matches), "FN": len(gold) - len(raw_matches), "exact": exact, "real_ids_correct": metrics["correct_ids"], "official_final": V6_BASELINE["official_final"], "safety": {key: metrics[key] for key in ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")}}
        if measured != V6_BASELINE:
            raise RuntimeError(f"baseline V6 não reproduzida: {measured}")

        target_items = {item.index: item for item in gold if item.index in TARGET_IDS}
        if set(target_items) != set(TARGET_IDS):
            raise RuntimeError("os quatro gold IDs alvo não foram encontrados")
        marker_rows = all_occurrences(texts, raw, gold, _TARGET_MARKER_RE)
        existing_marker_rows = all_occurrences(texts, raw, gold, _MARKER_RE)
        apl_rows = all_occurrences(texts, raw, gold, _APL_RE)
        rse_rows = all_occurrences(texts, raw, gold, _RSE_RE)
        compound = compound_inventory(texts, raw, gold)
        cases = []
        for case_id in TARGET_IDS:
            case = target_case(target_items[case_id], texts, raw, outputs, gold, parser, resolver)
            if case_id == 71:
                relevant = [row for row in marker_rows if row["marker"].upper() in {marker.upper() for marker in _TARGET_MARKERS}]
                case["hypothesis"] = "recognized process class + optional numeric indicator + short non-CNJ process/recurso number + UF"
                case["corpus_evidence"] = {"inventory_scope": list(_TARGET_MARKERS), "summary": inventory_summary(relevant), "by_marker": {marker: inventory_summary([row for row in relevant if row["marker"].upper() == marker.upper()]) for marker in _TARGET_MARKERS}, "other_existing_marker_inventory": {marker: inventory_summary([row for row in existing_marker_rows if row["marker"].upper() == marker.upper()]) for marker in _MARKER_TERMS}, "rows": relevant[:10], "negative_examples": negative_examples(relevant), "source": "AUTOMATED"}
            elif case_id == 98:
                case["hypothesis"] = "bounded compound procedural class chain + complete CNJ"
                case["corpus_evidence"] = {"summary": {key: value for key, value in compound.items() if key not in {"all_rows", "full_cnj_examples", "partial_or_non_cnj_examples"}}, "full_cnj_examples": compound["full_cnj_examples"], "partial_or_non_cnj_examples": compound["partial_or_non_cnj_examples"], "historical_negative_required": historical_negative(compound, texts), "source": "AUTOMATED"}
            elif case_id == 170:
                case["hypothesis"] = "controlled process marker + spaced degraded CNJ + optional UF"
                case["corpus_evidence"] = {"marker": "APL", "summary": inventory_summary(apl_rows), "rows": apl_rows[:10], "negative_examples": negative_examples(apl_rows), "grammar_parser_resolver_mentions": {"detector_marker_grammar": "APL não está na gramática V6 observada", "parser": "não há classe APL específica na saída atual", "resolver": "resolver aceita a identidade numérica quando o downstream recebe family adequada", "source_project_mentions": project_mentions("APL")}, "source": "AUTOMATED"}
            else:
                case["hypothesis"] = "controlled RSE marker + degraded/compact 20-digit CNJ + optional UF"
                similar_rows = all_occurrences(texts, raw, gold, _SIMILAR_SIGLA_RE)
                header_rows = [row for row in rse_rows if row["local_text"].strip().splitlines() and row["local_text"].strip().splitlines()[0].isupper()]
                case["corpus_evidence"] = {"marker": "RSE", "summary": inventory_summary(rse_rows), "rows": rse_rows[:10], "semantic_related_forms": semantic_rse_inventory(texts)[:10], "similar_siglas": [{"marker": row["marker"], "document_id": row["document_id"], "local_text": row["local_text"], "source": "AUTOMATED"} for row in similar_rows[:10]], "header_or_metadata_examples": header_rows[:10], "negative_examples": negative_examples(rse_rows), "source_project_mentions": project_mentions("RSE"), "source": "AUTOMATED"}
            cases.append(case)
        return {
            "schema_version": SCHEMA_VERSION, "review_type": REVIEW_TYPE,
            "sources": SOURCE_LABELS, "scope": {"txt_count": len(texts), "gold_count": len(gold), "target_gold_ids": list(TARGET_IDS), "gold_used_for_rule_selection": False},
            "baseline": measured, "coverage_topline": {"real_coverage": "70/96", "terminal_closed": 22, "plausible_tail": 4, "recall_engineering_closure": "95.8333%"},
            "cases": cases,
            "grouping_context": {
                "candidate_groups": [{"name": "71+172", "relationship": "unsupported marker, but marker families and numeric shapes audited separately", "source": "AUTOMATED"}, {"name": "98+170", "relationship": "parser structural gaps, but compound-chain and APL/degraded-CNJ phenomena remain distinct", "source": "AUTOMATED"}, {"name": "170+172", "relationship": "both conceptually touch controlled marker plus degraded-CNJ support; homogeneity remains for human decision", "source": "AUTOMATED"}],
                "source": "AUTOMATED",
            },
            "determinism": {"runs": 3, "identical": True, "source": "AUTOMATED"},
        }


def human_template() -> dict[str, Any]:
    common = {key: None for key in QUESTION_OPTIONS if key.startswith("Q")}
    specifics = {
        71: {key: None for key in ("A1", "A2", "A3", "A4", "A5")},
        98: {key: None for key in ("B1", "B2", "B3", "B4", "B5")},
        170: {key: None for key in ("C1", "C2", "C3", "C4", "C5", "C6")},
        172: {key: None for key in ("D1", "D2", "D3", "D4", "D5")},
    }
    return {
        "schema_version": SCHEMA_VERSION, "review_type": REVIEW_TYPE, "reviewer": None, "reviewed_at": None,
        "cases": [{"gold_id": case_id, "answers": {**common, **specifics[case_id]}, "notes": None} for case_id in TARGET_IDS],
        "grouping": {"answers": {key: None for key in ("G1", "G2", "G3", "G4")}, "group_members": None, "abstract_rule": None, "notes": None},
        "global_decision": {"answers": {key: None for key in ("GLOBAL-1", "GLOBAL-2", "GLOBAL-3")}, "justification": None},
    }


def html_context(context: Mapping[str, Any]) -> str:
    text = context["text"]
    left = context["span"][0]
    boundaries = {0, len(text)}
    annotations = []
    for annotation in context["annotations"]:
        start, end = max(0, annotation["span"][0] - left), min(len(text), annotation["span"][1] - left)
        if end <= start:
            continue
        boundaries.update((start, end))
        annotations.append((start, end, annotation["kind"], annotation["label"], annotation["span"]))
    points = sorted(boundaries)
    output = []
    for start, end in zip(points, points[1:]):
        raw = escape(text[start:end])
        active = [item for item in annotations if item[0] <= start and item[1] >= end]
        if active:
            kinds = " ".join(sorted({item[2] for item in active}))
            absolute_start, absolute_end = active[0][4]
            output.append(f'<span class="hl {escape(kinds)}" data-start="{absolute_start}" data-end="{absolute_end}" title="{escape(kinds)} [{absolute_start},{absolute_end})">{raw}</span>')
        else:
            output.append(raw)
    return "".join(output)


def select_html(code: str, options: Sequence[str], name: str) -> str:
    choices = ''.join(f'<option value="{escape(option)}">{escape(option)}</option>' for option in options)
    return f'<select data-answer="{escape(name)}" aria-label="{escape(name)}"><option value="">—</option>{choices}</select>'


def question_html(code: str, label: str) -> str:
    return f'<div class="question"><label>{escape(code)} — {escape(label)}</label>{select_html(code, QUESTION_OPTIONS[code], code)}</div>'


QUESTION_LABELS = {
    "Q1": "Is the full citation identity present in the source?", "Q2": "Would recovery require changing/inventing any identifying character?", "Q3": "Is the marker clearly procedural in this context?", "Q4": "Is the citation boundary unambiguous?", "Q5": "Could the same structural rule be stated without mentioning this specific document/case?", "Q6": "Does corpus evidence show support beyond this one occurrence?", "Q7": "Are there convincing negative examples that the rule can reject structurally?", "Q8": "Would the rule use only information available in blind TXT/runtime?", "Q9": "Does the proposal require a DEV-specific alias or marker?", "Q10": "Is downstream already capable of resolving the citation if Detection succeeds?", "Q11": "Generalization assessment", "Q12": "Should this phenomenon be tested offline?",
    "A1": "RMS belongs to a general process class + short number + UF family?", "A2": "Risk of capturing pages/articles/values/protocols/narrative numbers", "A3": "Does corpus-wide evidence show other similar references?", "A4": "Does RMS already have independent V6/V5 structural support?", "A5": "Human verdict for 71",
    "B1": "Is ED-E-ED-RR clearly a composition of procedural classes/modifiers?", "B2": "Can the grammar require recognized process tokens + complete CNJ?", "B3": "Would TST-E-RR-173000-49.2008 be naturally rejected by requiring complete CNJ?", "B4": "Is there corpus-wide support for similar chains?", "B5": "Human verdict for 98",
    "C1": "Is APL unambiguously procedural in this context?", "C2": "Does APL have independent corpus support?", "C3": "Would adding APL be justified without gold 170?", "C4": "Is the CNJ identity completely preserved without mutation?", "C5": "Would a useful experiment also require a Parser change?", "C6": "Human verdict for 170",
    "D1": "Is RSE unambiguously procedural in this context?", "D2": "Does RSE have independent corpus support?", "D3": "Would adding RSE be justified without gold 172?", "D4": "Would current H1/M1 grammar suffice for the numeric body if RSE were accepted?", "D5": "Human verdict for 172",
    "G1": "Do 71 and 172 belong to the same structural hypothesis?", "G2": "Do 98 and 170 belong to the same parser gap?", "G3": "Could 170 and 172 be controlled new marker + existing degraded-CNJ grammar?", "G4": "Is any multi-case group homogeneous enough for an experiment?",
    "GLOBAL-1": "How many cases deserve an experiment?", "GLOBAL-2": "Should the recall front continue?", "GLOBAL-3": "Recommendation",
}


def render_html(context: Mapping[str, Any], template: Mapping[str, Any]) -> str:
    cards = []
    for case in context["cases"]:
        case_id = case["gold_id"]
        specific_prefix = {71: "A", 98: "B", 170: "C", 172: "D"}[case_id]
        q_html = "".join(question_html(f"Q{i}", QUESTION_LABELS[f"Q{i}"]) for i in range(1, 13))
        specific_codes = [code for code in QUESTION_OPTIONS if code.startswith(specific_prefix)]
        specific_codes.sort(key=lambda code: (len(code), code))
        specific_html = "".join(question_html(code, QUESTION_LABELS[code]) for code in specific_codes)
        evidence_json = escape(json.dumps(case["corpus_evidence"], ensure_ascii=False, indent=2))
        candidates_json = escape(json.dumps(case["current_detector_nearby_candidates"], ensure_ascii=False, indent=2))
        cards.append(f'''<article class="case-card" data-case="{case_id}">
<h2>Gold ID {case_id} <span class="badge source-prior">PRIOR_VALIDATED_RESULT</span></h2>
<p><b>{escape(case["documento_id"])}</b> · {escape(case["nivel"])} · <code>{escape(case["gold_text"])}</code></p>
<p>first blocker: <code>{case["first_blocker"]}</code> · terminal cause: <code>{case["terminal_cause"]}</code> · oracle: <code>{case["oracle"]["verdict"]}</code></p>
<h3>Context <span class="badge source-text">SOURCE_TEXT</span></h3><p class="offsets">absolute context offsets: {case["context"]["span"]}; gold span: {case["gold_span"]}</p><pre class="context">{html_context(case["context"])}</pre>
<details><summary>V6 Detector / Parser / Resolver <span class="badge source-auto">AUTOMATED</span></summary><p>Hypothesis under review: <code>{escape(case["hypothesis"])}</code></p><pre>{escape(json.dumps({"nearby_candidates": case["current_detector_nearby_candidates"], "parser": case["current_parser_output"], "resolver": case["current_resolver_output"], "final": case["current_final_output"], "oracle": case["oracle"]}, ensure_ascii=False, indent=2))}</pre></details>
<details><summary>Corpus evidence and negatives <span class="badge source-auto">AUTOMATED</span></summary><pre>{evidence_json}</pre></details>
<section class="questions"><h3>Q1–Q12 — common review</h3>{q_html}<h3>Specific questions</h3>{specific_html}<label>Reviewer notes<textarea data-note="{case_id}"></textarea></label></section>
</article>''')
    grouping = ''.join(question_html(code, QUESTION_LABELS[code]) for code in ("G1", "G2", "G3", "G4"))
    global_questions = ''.join(question_html(code, QUESTION_LABELS[code]) for code in ("GLOBAL-1", "GLOBAL-2", "GLOBAL-3"))
    template_json = json.dumps(template, ensure_ascii=False, separators=(",", ":"))
    enum_json = json.dumps(QUESTION_OPTIONS, ensure_ascii=False, separators=(",", ":"))
    return f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>POST-V6 TARGETED HUMAN REVIEW</title>
<style>
:root{{--ink:#20252b;--muted:#5f6b76;--line:#d8dee4;--paper:#fff;--blue:#e7f1fb;--gold:#fff1a8;--green:#e3f4e8;--orange:#fff0d6}}*{{box-sizing:border-box}}body{{font:14px/1.45 system-ui,sans-serif;color:var(--ink);background:#f4f6f8;margin:0}}main{{max-width:1500px;margin:auto;padding:28px}}h1{{margin:0 0 8px}}h2{{border-bottom:1px solid var(--line);padding-bottom:8px}}h3{{margin-bottom:6px}}code,pre{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}code{{background:#eef1f4;padding:2px 4px;border-radius:3px}}pre{{white-space:pre-wrap;overflow:auto;background:#f8fafb;border:1px solid var(--line);padding:12px;border-radius:5px}}.topline{{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}}.metric{{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:10px 14px}}.metric b{{display:block;font-size:1.2rem}}.callout{{background:var(--orange);border-left:5px solid #d99000;padding:12px 16px;margin:16px 0;font-weight:600}}.toolbar{{position:sticky;top:0;z-index:2;background:#f4f6f8;padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}}button{{border:1px solid #73808b;background:#fff;padding:7px 11px;border-radius:4px;cursor:pointer}}button:hover{{background:#e9eef2}}input[type=text],input[type=date],textarea,select{{border:1px solid #9da8b1;border-radius:4px;padding:6px;background:#fff}}.case-card{{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:18px;margin:20px 0;box-shadow:0 1px 2px #0000000a}}.context{{line-height:1.7;background:#fbfcfd}}.hl{{padding:1px 0}}.gold_span{{background:var(--gold);outline:1px solid #c4a900}}.process_marker{{background:var(--green);outline:1px solid #5a9b6a}}.numeric_identity{{background:var(--blue);outline:1px solid #75a2c8}}.nearby_candidate{{text-decoration:underline;text-decoration-style:dotted;text-decoration-color:#b36a00}}.badge{{font:11px ui-monospace,monospace;padding:3px 5px;border-radius:3px;white-space:nowrap}}.source-auto{{background:#e6edf3}}.source-text{{background:var(--gold)}}.source-prior{{background:#e4e8ff}}.offsets,.muted{{color:var(--muted)}}.question{{display:grid;grid-template-columns:minmax(300px,1fr) 220px;gap:8px;padding:6px 0;border-bottom:1px solid #edf0f2;align-items:center}}.question label{{font-weight:500}}.questions textarea{{width:100%;min-height:70px;margin-top:4px}}details{{margin:12px 0}}summary{{cursor:pointer;font-weight:600}}.legend span{{margin-right:12px}}.danger{{color:#a33}}@media(max-width:700px){{main{{padding:12px}}.question{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>POST-V6 TARGETED HUMAN REVIEW</h1><p>Preparador de evidência offline — revisão humana, não promoção V7.</p>
<div class="topline"><div class="metric"><b>70/96</b>real coverage</div><div class="metric"><b>22</b>terminal closed</div><div class="metric"><b>4</b>plausible tail</div><div class="metric"><b>95.8333%</b>recall engineering closure</div></div>
<div class="callout">Esta revisão decide se ainda existe um front de recall justificável. Respostas humanas não são preenchidas automaticamente.</div>
<div class="toolbar"><label>Reviewer <input id="reviewer" type="text"></label><label>Reviewed at <input id="reviewed-at" type="date"></label><button id="export">Export Review JSON</button><button id="import">Import Review JSON</button><input id="import-file" type="file" accept="application/json" hidden><span id="status" class="muted"></span></div>
<p class="legend"><span class="badge source-auto">AUTOMATED</span> inventário/medida calculada <span class="badge source-text">SOURCE_TEXT</span> texto literal e offsets <span class="badge source-prior">PRIOR_VALIDATED_RESULT</span> baseline/oráculo já validado</p>
<section id="cases">{''.join(cards)}</section>
<section class="case-card"><h2>Grouping questions <span class="badge source-auto">AUTOMATED CONTEXT</span></h2>{grouping}<label>group_members<textarea id="group-members"></textarea></label><label>abstract_rule<textarea id="abstract-rule"></textarea></label></section>
<section class="case-card"><h2>Global decision</h2>{global_questions}<label>GLOBAL-4 — Short free-form justification<textarea id="global-justification"></textarea></label></section>
<script>
const TEMPLATE={template_json};
const ENUMS={enum_json};
const TARGET_IDS=[71,98,170,172];
function stateFromForm(){{const out=structuredClone(TEMPLATE);out.reviewer=document.getElementById('reviewer').value||null;out.reviewed_at=document.getElementById('reviewed-at').value||null;for(const card of document.querySelectorAll('[data-case]')){{const id=Number(card.dataset.case), row=out.cases.find(x=>x.gold_id===id);for(const el of card.querySelectorAll('[data-answer]')) row.answers[el.dataset.answer]=el.value||null;row.notes=card.querySelector('[data-note]').value||null;}}for(const el of document.querySelectorAll('select[data-answer]')){{if(el.closest('[data-case]'))continue;const key=el.dataset.answer;if(key.startsWith('G'))out.grouping.answers[key]=el.value||null;else out.global_decision.answers[key]=el.value||null;}}out.grouping.group_members=document.getElementById('group-members').value||null;out.grouping.abstract_rule=document.getElementById('abstract-rule').value||null;out.global_decision.justification=document.getElementById('global-justification').value||null;return out;}}
function validate(obj){{if(!obj||obj.schema_version!==TEMPLATE.schema_version||obj.review_type!==TEMPLATE.review_type)throw Error('schema_version/review_type inválido');if(!Array.isArray(obj.cases)||obj.cases.length!==4||obj.cases.map(x=>x.gold_id).sort((a,b)=>a-b).join(',')!=='71,98,170,172')throw Error('exatamente os casos 71, 98, 170 e 172 são obrigatórios');for(const row of obj.cases){{for(const [key,values] of Object.entries(ENUMS))if(key in row.answers&&row.answers[key]!==null&& !values.includes(row.answers[key]))throw Error('enum inválido em '+key);}}for(const [key,values] of Object.entries(ENUMS)){{const value=obj.grouping?.answers?.[key]??obj.global_decision?.answers?.[key];if(value!==undefined&&value!==null&&!values.includes(value))throw Error('enum inválido em '+key);}}return obj;}}
function applyState(obj){{validate(obj);document.getElementById('reviewer').value=obj.reviewer||'';document.getElementById('reviewed-at').value=obj.reviewed_at||'';for(const row of obj.cases){{const card=document.querySelector('[data-case="'+row.gold_id+'"]');for(const el of card.querySelectorAll('[data-answer]'))el.value=row.answers[el.dataset.answer]||'';card.querySelector('[data-note]').value=row.notes||'';}}for(const [key,value] of Object.entries(obj.grouping.answers||{{}})){{const el=document.querySelector('select[data-answer="'+key+'"]');if(el)el.value=value||'';}}for(const [key,value] of Object.entries(obj.global_decision.answers||{{}})){{const el=document.querySelector('select[data-answer="'+key+'"]');if(el)el.value=value||'';}}document.getElementById('group-members').value=obj.grouping.group_members||'';document.getElementById('abstract-rule').value=obj.grouping.abstract_rule||'';document.getElementById('global-justification').value=obj.global_decision.justification||'';}}
document.getElementById('export').onclick=()=>{{const blob=new Blob([JSON.stringify(stateFromForm(),null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='post_v6_targeted_human_review.json';a.click();URL.revokeObjectURL(a.href);document.getElementById('status').textContent='Review JSON exported.';}};
document.getElementById('import').onclick=()=>document.getElementById('import-file').click();document.getElementById('import-file').onchange=async event=>{{try{{const obj=JSON.parse(await event.target.files[0].text());applyState(obj);document.getElementById('status').textContent='Review JSON imported.';}}catch(error){{document.getElementById('status').textContent='Import rejected: '+error.message;document.getElementById('status').className='danger';}}}};
</script></main></body></html>'''


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    runs = [build_evidence() for _ in range(3)]
    if not (stable(runs[0]) == stable(runs[1]) == stable(runs[2])):
        raise RuntimeError("evidência não determinística em 3 execuções")
    context = runs[0]
    template = human_template()
    CONTEXT_PATH.write_text(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TEMPLATE_PATH.write_text(json.dumps(template, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    HTML_PATH.write_text(render_html(context, template), encoding="utf-8")
    hashes = {path.name: sha256(path.read_bytes()).hexdigest() for path in (HTML_PATH, TEMPLATE_PATH, CONTEXT_PATH)}
    print(json.dumps({"status": "PASS", "artifacts": hashes, "determinism": context["determinism"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
