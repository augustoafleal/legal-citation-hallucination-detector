"""Gera o artefato offline de revisão humana dos dez resíduos pós-V5.

Este módulo é exclusivamente analítico: recompõe a V5 em modo somente leitura,
gera contexto para o revisor e não propõe nem altera regras de produção.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationParser, CitationResolver  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


CASE_IDS = (71, 98, 125, 143, 159, 165, 170, 172, 204, 215)
DOWNSTREAM_READY = {71, 143, 159, 165, 172}
GROUPS = {
    "A — Estruturas com downstream já pronto": (71, 143, 159, 165, 172),
    "B — Estruturas/OCR ainda incertos": (98, 125, 170, 204, 215),
}
EXPECTED_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
HTML_PATH = PROJECT_ROOT / "artifacts" / "post_v5_human_review.html"
TEMPLATE_PATH = PROJECT_ROOT / "artifacts" / "post_v5_human_review_template.json"
CONTEXT_PATH = PROJECT_ROOT / "artifacts" / "post_v5_human_review_context.json"

HYPOTHESES: dict[str, dict[str, Any]] = {
    "H1_DEGRADED_COMPACT_CNJ": {
        "cases": [143, 159, 165, 170, 172],
        "why": "sequência numérica processual com separadores removidos, deslocados ou substituídos por espaços",
        "evidence": "MODERATE",
        "description": "Hipótese automática candidata: preservar dígitos observados e normalizar apenas separadores/espaços.",
    },
    "H2_SHORT_PROCESS_CLASS_NUMBER_UF": {
        "cases": [71],
        "why": "classe curta RMS seguida de número e UF explícita",
        "evidence": "INSUFFICIENT",
        "description": "Hipótese automática candidata: classe processual curta + número + UF com delimitadores locais.",
    },
    "H3_COMPOUND_TST_CLASS_CHAIN": {
        "cases": [98],
        "why": "cadeia ED-E-ED-RR seguida de CNJ",
        "evidence": "INSUFFICIENT",
        "description": "Hipótese automática candidata: cadeia limitada de modificadores + classe primária + CNJ.",
    },
    "H4_LOCAL_SUMULA_OCR": {
        "cases": [125],
        "why": "possível OCR local 5↔S no token Súmula",
        "evidence": "INSUFFICIENT",
        "description": "Hipótese automática candidata: reparo estritamente local de token lexical, não substituição global.",
    },
    "H5_TERMINAL_NUMERIC_OCR": {
        "cases": [204],
        "why": "caractere O terminal adjacente a número e antes de UF",
        "evidence": "INSUFFICIENT",
        "description": "Hipótese automática candidata: expandir span somente para O terminal em posição numérica plausível.",
    },
    "H6_UNSUPPORTED_INTERNAL_OCR": {
        "cases": [215],
        "why": "caractere interno não numérico em sequência de recurso",
        "evidence": "INSUFFICIENT",
        "description": "Hipótese automática candidata somente para investigação; não presume substituição de g por dígito.",
    },
}

CNJISH = re.compile(r"\d{5,}(?:[ .\-/]+\d{1,6}){2,}|\d{17,22}")
FULL_CNJ = re.compile(r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b")
HYPOTHESIS_PATTERNS = {
    "H1_DEGRADED_COMPACT_CNJ": CNJISH,
    "H2_SHORT_PROCESS_CLASS_NUMBER_UF": re.compile(r"\b(?:RMS|R\.M\.S\.|MS)\b", re.I),
    "H3_COMPOUND_TST_CLASS_CHAIN": re.compile(r"\b(?:ED|E|RR)(?:-(?:ED|E|RR)){1,}", re.I),
    "H4_LOCAL_SUMULA_OCR": re.compile(r"(?:\bS[úu]mula|\b\d[úu]mula)", re.I),
    "H5_TERMINAL_NUMERIC_OCR": re.compile(r"\d[OIlS](?:\s*[(/-]|$)", re.I),
    "H6_UNSUPPORTED_INTERNAL_OCR": re.compile(r"\d[A-Za-z]\d"),
}


def json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def result_view(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "reason": result.reason,
        "candidate_ids": list(result.candidate_ids),
        "id_canonico": result.id_canonico,
        "strategy": result.strategy,
        "record_type": result.record_type,
    }


def parser_view(parsed: Any) -> dict[str, Any]:
    data = dict(parsed.data)
    return {
        "family": parsed.family,
        "classe_raw": data.get("classe_raw"),
        "numero": data.get("numero_raw"),
        "cnj": data.get("numero_normalizado") if data.get("numero_family") == "cnj" else None,
        "numero_normalizado": data.get("numero_normalizado"),
        "uf": data.get("uf"),
        "tribunal": parsed.tribunal,
        "provenance": dict(parsed.provenance),
    }


def rendered_text(text: str) -> str:
    return escape(text).replace("\n", "<br>")


def compact(text: str, limit: int = 180) -> str:
    return " ".join(text.split())[:limit]


def context_view(text: str, start: int, end: int) -> dict[str, Any]:
    left = max(0, start - 350)
    right = min(len(text), end + 350)
    return {
        "context_start": left,
        "context_end": right,
        "before": text[left:start],
        "gold": text[start:end],
        "after": text[end:right],
        "left_ellipsis": left > 0,
        "right_ellipsis": right < len(text),
    }


def span_difference(gold: Any, candidate: Any) -> dict[str, str]:
    if candidate is None:
        return {"gold_only": gold.text, "candidate_only": ""}
    gold_only = ""
    candidate_only = ""
    if candidate.start < gold.start:
        candidate_only += candidate.text[: gold.start - candidate.start]
    if candidate.end > gold.end:
        candidate_only += candidate.text[gold.end - candidate.start :]
    if gold.start < candidate.start:
        gold_only += gold.text[: candidate.start - gold.start]
    if gold.end > candidate.end:
        gold_only += gold.text[candidate.end - gold.start :]
    return {"gold_only": gold_only, "candidate_only": candidate_only}


def oracle_check(gold: Any, parser: CitationParser, resolver: CitationResolver) -> dict[str, Any]:
    """Diagnostic only: literal gold becomes a candidate under each family."""
    families = (
        post.family_for(gold.text, gold.citation_type),
        "processo_ou_recurso_numerado",
        "processo_cnj",
        "sumula_numerada",
    )
    attempts = []
    for family in dict.fromkeys(families):
        candidate = CitationCandidate(0, len(gold.text), gold.text, "oracle", family)
        parsed = parser.parse(candidate, context=gold.text)
        resolved = resolver.resolve(parsed)
        attempt = {
            "family": family,
            "parser": parser_view(parsed),
            "resolver": result_view(resolved),
            "matches_gold": resolved.status == "resolved" and resolved.id_canonico == gold.canonical_id,
        }
        attempts.append(attempt)
        if attempt["matches_gold"]:
            return {"literal_injected": True, "matches_gold": True, "selected": attempt, "attempts": attempts}
    return {"literal_injected": True, "matches_gold": False, "selected": None, "attempts": attempts}


def overlaps_any(start: int, end: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(max(start, other_start) < min(end, other_end) for other_start, other_end in spans)


def negative_reason(hypothesis: str, source: str) -> str:
    descriptions = {
        "H1_DEGRADED_COMPACT_CNJ": "sequência CNJ-like fora do gold; testa números compactos, headers e similares",
        "H2_SHORT_PROCESS_CLASS_NUMBER_UF": "sigla curta relacionada; testa confusão entre classe processual e narrativa",
        "H3_COMPOUND_TST_CLASS_CHAIN": "cadeia de siglas/hífens; testa aceitação excessiva de modificadores",
        "H4_LOCAL_SUMULA_OCR": "token semelhante a Súmula; testa reparo lexical além do contexto local",
        "H5_TERMINAL_NUMERIC_OCR": "dígito seguido de letra ambígua; testa expansão terminal excessiva",
        "H6_UNSUPPORTED_INTERNAL_OCR": "letra em sequência numérica; testa reparo interno sem mapeamento comprovado",
    }
    prefix = {
        "CURRENT_V5_FORMAL_FP": "FP formal atual do Detector; ",
        "GOLD_INVENTADA_OR_INCOMPLETA": "gold não-real relevante; ",
        "CORPUS_WIDE_NON_GOLD": "ocorrência corpus-wide não-gold; ",
    }[source]
    return prefix + descriptions[hypothesis]


def select_negatives(
    hypothesis: str,
    texts: Mapping[str, str],
    gold: list[Any],
    raw: list[Any],
    raw_matches: Mapping[int, int],
) -> list[dict[str, Any]]:
    """Seleciona contraexemplos reais e determinísticos, sem fabricar negativos."""
    pattern = HYPOTHESIS_PATTERNS[hypothesis]
    gold_by_doc: dict[str, list[Any]] = defaultdict(list)
    for item in gold:
        gold_by_doc[item.document_id].append(item)
    matched_raw = set(raw_matches.values())
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()

    def add(document_id: str, start: int, end: int, snippet: str, source: str) -> None:
        key = (document_id, start, end)
        if key in seen or len(selected) >= 5:
            return
        seen.add(key)
        text = texts[document_id]
        selected.append(
            {
                "documento_id": document_id,
                "span": [start, end],
                "snippet": snippet,
                "source": source,
                "why_selected": negative_reason(hypothesis, source),
                "hypothesis": hypothesis,
                "context": context_view(text, start, end),
            }
        )

    # Primeiro, FPs formais da V5 que se aproximam lexical/estruturalmente.
    for item in sorted(raw, key=lambda value: (value.document_id, value.candidate.start, value.index)):
        if item.index in matched_raw or not pattern.search(item.candidate.text):
            continue
        add(item.document_id, item.candidate.start, item.candidate.end, item.candidate.text, "CURRENT_V5_FORMAL_FP")

    # Depois, inventadas/incompletas relevantes; elas não são usadas para classificação.
    for item in gold:
        if item.classification == "real" or not pattern.search(item.text):
            continue
        add(item.document_id, item.start, item.end, item.text, "GOLD_INVENTADA_OR_INCOMPLETA")

    # Por fim, busca corpus-wide em texto não sobreposto a qualquer gold.
    for document_id in sorted(texts):
        text = texts[document_id]
        spans = [(item.start, item.end) for item in gold_by_doc[document_id]]
        patterns = (FULL_CNJ, pattern) if hypothesis == "H1_DEGRADED_COMPACT_CNJ" else (pattern,)
        for scanner in patterns:
            for match in scanner.finditer(text):
                if overlaps_any(match.start(), match.end(), spans):
                    continue
                add(document_id, match.start(), match.end(), match.group(0), "CORPUS_WIDE_NON_GOLD")
                if len(selected) >= 5:
                    break
            if len(selected) >= 5:
                break
        if len(selected) >= 5:
            break
    return selected


def case_template(case_id: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "q1_single_citation": None,
        "q2_gold_boundary": None,
        "q3_primary_phenomenon": None,
        "q3_secondary_phenomena": [],
        "q4_runtime_identity_sufficient": None,
        "q5_identity_preserved_by_normalization": None,
        "q6_general_rule_exists": None,
        "q6_rule_description": None,
        "q7_shared_cases": None,
        "q7_case_ids": [],
        "q8_literal_class_dependency": None,
        "q9_negatives_break_hypothesis": None,
        "q10_simple_guard_exists": None,
        "q10_guard_description": None,
        "q11_risks": [],
        "q12_generalization": None,
        "q13_automated_experiment": None,
        "q14_notes": None,
        "downstream_ready": case_id in DOWNSTREAM_READY,
        "cnj_review": None,
        "ocr_review": None,
    }
    if case_id in DOWNSTREAM_READY:
        entry.update({"d1_detection_only_problem": None, "d2_resolver_identity_sufficient": None, "d3_oracle_interpretation": None})
    if case_id in HYPOTHESES["H1_DEGRADED_COMPACT_CNJ"]["cases"]:
        entry["cnj_review"] = {"c1_separator_only": None, "c2_digit_count_plausible": None, "c3_local_process_prefix": None, "c4_own_case_risk": None, "c5_own_case_guard": None}
    if case_id in {125, 204, 215}:
        entry["ocr_review"] = {"o1_supported_substitution": None, "o2_local_conditioned": None, "o3_unique_repair": None, "o4_before_database": None, "o5_corpus_choice_needed": None}
    return entry


def build_template() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "review_type": "post_v5_detector_residual",
        "reviewer": None,
        "reviewed_at": None,
        "cases": {str(case_id): case_template(case_id) for case_id in CASE_IDS},
        "hypotheses": {
            key: {"human_evidence": None, "positive_case_ids": [], "negative_case_ids": [], "generalizable": None, "simple_guard": None, "risk": None, "notes": None}
            for key in HYPOTHESES
        },
        "summary": {
            "s1_strong_v6_hypothesis": None,
            "s2_hypothesis_ids": [],
            "s3_multi_document_support": None,
            "s4_negative_guard_support": None,
            "s5_requires_fuzzy": None,
            "s6_requires_unsupported_digit_change": None,
            "s7_recommendation": None,
            "s8_approved_hypotheses": [],
            "s9_rejected_hypotheses": [],
            "s10_notes": None,
        },
    }


def build_context() -> dict[str, Any]:
    texts = post.deep.load_texts()
    gold = post.deep.load_gold()
    if len(texts) != 26 or len(gold) != 225:
        raise RuntimeError("dataset oficial inesperado")
    db_path = get_database_path()
    if sha256(db_path.read_bytes()).hexdigest() != EXPECTED_HASH:
        raise RuntimeError("hash do DB divergente")
    by_gold = {item.index: item for item in gold}
    if set(CASE_IDS) - set(by_gold):
        raise RuntimeError("caso obrigatório ausente do gold")

    with connect_database(db_path, read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        parser = CitationParser()
        raw, outputs, _ = post.run_current_pipeline(texts, resolver)
        raw_matches = post.match_gold(gold, raw)
        output_matches = post.match_gold(gold, outputs)
        raw_by_index = {item.index: item for item in raw}
        output_by_index = {item.index: item for item in outputs}
        cases = []
        for case_id in CASE_IDS:
            item = by_gold[case_id]
            raw_index = raw_matches.get(case_id)
            raw_item = raw_by_index.get(raw_index) if raw_index is not None else None
            output_item = output_by_index.get(output_matches.get(case_id))
            v5: dict[str, Any]
            if raw_item is None:
                v5 = {"status": "NO_CANDIDATE", "candidate": None, "parser": None, "resolver": None, "final_output": None}
            else:
                differences = span_difference(item, raw_item.candidate)
                v5 = {
                    "status": "CANDIDATE_MATCHED",
                    "candidate": {
                        "span": [raw_item.candidate.start, raw_item.candidate.end], "text": raw_item.candidate.text,
                        "family": raw_item.candidate.family, "rule": raw_item.candidate.rule,
                        "iou": round(post.iou(item.start, item.end, raw_item.candidate.start, raw_item.candidate.end), 6),
                        "exact": raw_item.candidate.start == item.start and raw_item.candidate.end == item.end,
                        **differences,
                    },
                    "parser": parser_view(raw_item.parsed), "resolver": result_view(raw_item.result),
                    "final_output": None if output_item is None else {
                        "span": [output_item.candidate.start, output_item.candidate.end], "text": output_item.candidate.text,
                        "reason": output_item.reason, "resolution": result_view(output_item.result),
                    },
                }
            hypothesis_ids = [key for key, value in HYPOTHESES.items() if case_id in value["cases"]]
            cases.append(
                {
                    "gold_index": case_id, "citacao_id": item.gid, "documento_id": item.document_id,
                    "nivel": item.level, "gold_span": [item.start, item.end], "gold_text": item.text,
                    "gold_id_canonico": item.canonical_id, "group": "A" if case_id in DOWNSTREAM_READY else "B",
                    "downstream_ready": case_id in DOWNSTREAM_READY, "context": context_view(texts[item.document_id], item.start, item.end),
                    "v5_current": v5, "automated_hypotheses": hypothesis_ids,
                    "oracle_diagnostic": oracle_check(item, parser, resolver) if case_id in DOWNSTREAM_READY else None,
                }
            )
        negatives = {key: select_negatives(key, texts, gold, raw, raw_matches) for key in HYPOTHESES}
        integrity = {"database_sha256": EXPECTED_HASH, "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0]}
    return {
        "artifact_status": "PENDING_HUMAN_REVIEW", "case_ids": list(CASE_IDS),
        "downstream_ready_ids": sorted(DOWNSTREAM_READY), "groups": GROUPS,
        "hypotheses": HYPOTHESES, "cases": cases, "negatives": negatives, "integrity": integrity,
    }


def select_html(path: str, options: list[str], label: str) -> str:
    choices = "".join(f'<option value="{escape(option)}">{escape(option)}</option>' for option in options)
    return f'<label>{escape(label)}<select data-path="{escape(path)}"><option value="">—</option>{choices}</select></label>'


def textarea_html(path: str, label: str) -> str:
    return f'<label>{escape(label)}<textarea data-path="{escape(path)}" rows="3"></textarea></label>'


def checklist_html(path: str, label: str, options: list[str]) -> str:
    choices = "".join(f'<label class="check"><input type="checkbox" data-array-path="{escape(path)}" value="{escape(option)}"> {escape(option)}</label>' for option in options)
    return f'<fieldset><legend>{escape(label)}</legend>{choices}</fieldset>'


def view_json(value: Any) -> str:
    return f'<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre>'


def context_html(context: Mapping[str, Any]) -> str:
    left = "…" if context["left_ellipsis"] else ""
    right = "…" if context["right_ellipsis"] else ""
    return f'<div class="context">{left}{rendered_text(str(context["before"]))}<mark>{rendered_text(str(context["gold"]))}</mark>{rendered_text(str(context["after"]))}{right}</div>'


def negative_html(negatives: list[Mapping[str, Any]]) -> str:
    if not negatives:
        return '<p class="warning">Nenhum negativo relevante foi encontrado para esta hipótese. Evidência automática: INSUFFICIENT.</p>'
    blocks = []
    for item in negatives:
        blocks.append(
            '<article class="negative"><b>ESTE É UM NEGATIVO / CONTRAEXEMPLO POTENCIAL</b>'
            f'<p><code>{escape(str(item["source"]))}</code> — {escape(str(item["why_selected"]))}</p>'
            f'<p>Documento: <code>{escape(str(item["documento_id"]))}</code>; trecho: <mark>{rendered_text(str(item["snippet"]))}</mark></p>'
            f'{context_html(item["context"])}'
            '<p><i>A regra proposta capturaria isto? Deveria capturar? Qual guard runtime o diferencia? Há risco de overfit?</i></p></article>'
        )
    return "".join(blocks)


def questions_html(case: Mapping[str, Any]) -> str:
    cid = str(case["gold_index"])
    base = f"cases.{cid}"
    html = '<section class="human"><h4>HUMAN ANSWER</h4><div class="questions">'
    html += select_html(f"{base}.q1_single_citation", ["YES", "NO", "UNCERTAIN"], "Q1 — Uma única citação?")
    html += select_html(f"{base}.q2_gold_boundary", ["YES", "PARTIAL", "NO", "UNCERTAIN"], "Q2 — Gold span é fronteira natural?")
    html += select_html(f"{base}.q3_primary_phenomenon", ["DEGRADED_CNJ", "COMPACT_CNJ", "NEWLINE", "OCR", "PROCESS_CLASS", "COMPOUND_CLASS_CHAIN", "PREFIX", "SUFFIX", "PUNCTUATION", "FAMILY_MISSING", "OTHER", "UNCERTAIN"], "Q3 — Fenômeno primário")
    html += checklist_html(f"{base}.q3_secondary_phenomena", "Q3 — Fenômenos secundários", ["DEGRADED_CNJ", "COMPACT_CNJ", "NEWLINE", "OCR", "PROCESS_CLASS", "COMPOUND_CLASS_CHAIN", "PREFIX", "SUFFIX", "PUNCTUATION", "FAMILY_MISSING", "OTHER"])
    html += select_html(f"{base}.q4_runtime_identity_sufficient", ["YES", "NO", "UNCERTAIN"], "Q4 — Identidade suficiente só em runtime?")
    html += select_html(f"{base}.q5_identity_preserved_by_normalization", ["YES", "NO", "UNCERTAIN"], "Q5 — Normalização preserva toda identidade observada?")
    html += select_html(f"{base}.q6_general_rule_exists", ["YES", "MAYBE", "NO"], "Q6 — Há regra estrutural geral?")
    html += textarea_html(f"{base}.q6_rule_description", "Q6 — Regra em linguagem natural")
    html += select_html(f"{base}.q7_shared_cases", ["NONE", "ONE", "MULTIPLE", "UNCERTAIN"], "Q7 — Casos que compartilham?")
    html += textarea_html(f"{base}.q7_case_ids", "Q7 — Índices compartilhados (separados por vírgula)")
    html += select_html(f"{base}.q8_literal_class_dependency", ["YES", "NO", "UNCERTAIN"], "Q8 — Depende de classe literal?")
    html += select_html(f"{base}.q9_negatives_break_hypothesis", ["YES", "NO", "SOME", "NO_RELEVANT_NEGATIVES"], "Q9 — Negativos quebram hipótese?")
    html += select_html(f"{base}.q10_simple_guard_exists", ["YES", "MAYBE", "NO"], "Q10 — Há guard simples?")
    html += textarea_html(f"{base}.q10_guard_description", "Q10 — Descreva o guard")
    html += checklist_html(f"{base}.q11_risks", "Q11 — Riscos", ["HEADER_DISTRACTOR", "NARRATIVE_NUMBER", "OVEREXPANSION", "OCR_OVERREPAIR", "FUZZY_IDENTITY", "CLASS_SPECIFIC", "PUNCTUATION_SPECIFIC", "ONE_DOCUMENT_SUPPORT", "GOLD_DEPENDENCY", "CORPUS_LOOKUP_DEPENDENCY", "OTHER", "NONE_EVIDENT"])
    html += select_html(f"{base}.q12_generalization", ["GENERALIZABLE", "POSSIBLY_GENERALIZABLE", "CASE_SPECIFIC", "UNSAFE", "UNCERTAIN"], "Q12 — Generalização")
    html += select_html(f"{base}.q13_automated_experiment", ["YES", "ONLY_IF_GROUPED_WITH_OTHERS", "NO", "UNCERTAIN"], "Q13 — Merece experimento?")
    html += textarea_html(f"{base}.q14_notes", "Q14 — Notas")
    if case["downstream_ready"]:
        html += '<fieldset class="special"><legend>DOWNSTREAM-READY — perguntas adicionais</legend>'
        html += select_html(f"{base}.d1_detection_only_problem", ["YES", "NO", "UNCERTAIN"], "D1 — Problema exclusivamente Detection?")
        html += select_html(f"{base}.d2_resolver_identity_sufficient", ["YES", "NO", "UNCERTAIN"], "D2 — Resolver atual tem identidade suficiente?")
        html += select_html(f"{base}.d3_oracle_interpretation", ["RULE_EVIDENCE", "DOWNSTREAM_ONLY", "BOTH", "UNCERTAIN"], "D3 — Oracle demonstra?")
        html += '</fieldset>'
    if case["gold_index"] in HYPOTHESES["H1_DEGRADED_COMPACT_CNJ"]["cases"]:
        html += '<fieldset class="special"><legend>CNJ DEGRADADO — perguntas específicas</legend>'
        html += select_html(f"{base}.cnj_review.c1_separator_only", ["YES", "NO", "UNCERTAIN"], "C1 — Apenas separadores/espaços?")
        html += select_html(f"{base}.cnj_review.c2_digit_count_plausible", ["YES", "NO", "UNCERTAIN"], "C2 — Quantidade de dígitos plausível?")
        html += select_html(f"{base}.cnj_review.c3_local_process_prefix", ["YES", "NO", "UNCERTAIN"], "C3 — Prefixo processual local?")
        html += select_html(f"{base}.cnj_review.c4_own_case_risk", ["HIGH", "MODERATE", "LOW", "UNCERTAIN"], "C4 — Risco de autos próprios?")
        html += select_html(f"{base}.cnj_review.c5_own_case_guard", ["YES", "MAYBE", "NO"], "C5 — Guard contra autos próprios?")
        html += '</fieldset>'
    if case["gold_index"] in {125, 204, 215}:
        html += '<fieldset class="special"><legend>OCR — perguntas específicas</legend>'
        html += select_html(f"{base}.ocr_review.o1_supported_substitution", ["YES", "NO", "UNCERTAIN"], "O1 — Substituição garantida pelo desafio?")
        html += select_html(f"{base}.ocr_review.o2_local_conditioned", ["YES", "NO", "UNCERTAIN"], "O2 — Local e condicionada?")
        html += select_html(f"{base}.ocr_review.o3_unique_repair", ["YES", "NO", "UNCERTAIN"], "O3 — Reparação única?")
        html += select_html(f"{base}.ocr_review.o4_before_database", ["YES", "NO", "UNCERTAIN"], "O4 — Antes da base?")
        html += select_html(f"{base}.ocr_review.o5_corpus_choice_needed", ["YES", "NO", "UNCERTAIN"], "O5 — Base escolhe reparação?")
        html += '</fieldset>'
    return html + '</div></section>'


def card_html(case: Mapping[str, Any], negatives: Mapping[str, list[Mapping[str, Any]]]) -> str:
    gold = {key: case[key] for key in ("citacao_id", "gold_index", "documento_id", "nivel", "gold_span", "gold_text", "gold_id_canonico")}
    hypothesis_ids = case["automated_hypotheses"]
    hypothesis = HYPOTHESES[hypothesis_ids[0]]
    v5 = case["v5_current"]
    v5_html = '<p><b>NO_CANDIDATE</b> — não há candidato V5 com IoU oficial ≥ 0.5.</p>' if v5["status"] == "NO_CANDIDATE" else view_json(v5)
    oracle = ""
    if case["oracle_diagnostic"]:
        oracle = '<section class="oracle"><h4>ORACLE DETECTION CHECK — diagnóstico, não regra</h4><p>Gold literal injected as candidate; este teste não consulta corpo/ementa nem autoriza implementação.</p>' + view_json(case["oracle_diagnostic"]["selected"]) + '</section>'
    instructions = "PROCURE POR: sequência numérica, ordem dos dígitos, separadores/espaços, UF, classe anterior e delimitadores locais."
    breaking = "TENTE QUEBRAR: números administrativos, autos próprios, OAB/protocolos, reconstruções múltiplas, dígitos inventados e dependência de gold/corpus."
    if case["gold_index"] == 71:
        instructions = "PROCURE POR: RMS explícito, número de recurso, UF e variantes RMS/R.M.S./Recurso em Mandado de Segurança."
        breaking = "TENTE QUEBRAR: MS sem RMS, siglas semelhantes, RMS narrativo e números ordinários próximos."
    elif case["gold_index"] == 98:
        instructions = "PROCURE POR: cadeia ED-E-ED-RR, hífens, CNJ completo e gramática modifier-chain + classe + CNJ."
        breaking = "TENTE QUEBRAR: cadeia arbitrária, E isolado ambíguo, ordem dos modificadores e necessidade de hardcode literal."
    elif case["gold_index"] == 125:
        instructions = "PROCURE POR: OCR local 5↔S, token Súmula, número 211, STJ e contexto estritamente local."
        breaking = "TENTE QUEBRAR: reparo global 5→S, palavras normais e números seguidos de úmula."
    elif case["gold_index"] == 204:
        instructions = "PROCURE POR: O terminal como posição numérica, UF imediata e expansão local mínima de span."
        breaking = "TENTE QUEBRAR: O narrativo após número, palavra seguinte e expansão excessiva."
    elif case["gold_index"] == 215:
        instructions = "PROCURE POR: g interno e se há reparação comprovada sem inventar dígito."
        breaking = "TENTE QUEBRAR: múltiplos dígitos plausíveis, fuzzy matching e dependência de corpus/gold."
    negatives_html = "".join(f'<h5>{escape(hid)} — negativos</h5>{negative_html(negatives[hid])}' for hid in hypothesis_ids)
    return f'''<article class="card" id="case-{case["gold_index"]}">
<h3>Caso {case["gold_index"]} <span class="badge">Grupo {case["group"]}</span></h3>
<section class="gold"><h4>GOLD — DEV aberto, separado da resposta humana</h4>{view_json(gold)}</section>
<section><h4>CONTEXTO ORIGINAL</h4>{context_html(case["context"])}</section>
<section class="current"><h4>V5 CURRENT</h4>{v5_html}</section>
{oracle}
<section class="hypothesis"><h4>AUTOMATED HYPOTHESIS — NOT HUMAN VERDICT</h4><p><b>{escape(hypothesis_ids[0])}</b> ({escape(hypothesis["evidence"])}): {escape(hypothesis["description"])}</p><p>Why grouped: {escape(hypothesis["why"])}</p></section>
<section class="break"><h4>PROCURE POR</h4><p>{escape(instructions)}</p><h4>TENTE QUEBRAR A HIPÓTESE</h4><p>{escape(breaking)}</p></section>
<section><h4>NEGATIVE EVIDENCE</h4>{negatives_html}</section>
{questions_html(case)}
</article>'''


def hypothesis_summary_html(context: Mapping[str, Any]) -> str:
    rows = []
    cases_by_id = {item["gold_index"]: item for item in context["cases"]}
    for hid, info in HYPOTHESES.items():
        docs = {cases_by_id[index]["documento_id"] for index in info["cases"]}
        levels = sorted({cases_by_id[index]["nivel"] for index in info["cases"]})
        negatives = context["negatives"][hid]
        evidence = info["evidence"] if negatives else "INSUFFICIENT"
        rows.append(f'<tr><td>{escape(hid)}</td><td>{", ".join(map(str, info["cases"]))}</td><td>{len(docs)}</td><td>{", ".join(levels)}</td><td>{len(negatives)}</td><td>{escape(evidence)} — resposta humana no JSON</td></tr>')
    fields = "".join(
        f'<h4>{escape(hid)}</h4>'
        + select_html(f"hypotheses.{hid}.human_evidence", ["STRONG", "MODERATE", "WEAK", "INSUFFICIENT"], "Evidência humana")
        + textarea_html(f"hypotheses.{hid}.positive_case_ids", "Positivos revisados (índices, separados por vírgula)")
        + textarea_html(f"hypotheses.{hid}.negative_case_ids", "Negativos relevantes (índices/descrições)")
        + select_html(f"hypotheses.{hid}.generalizable", ["YES", "MAYBE", "NO", "UNCERTAIN"], "Generalizável?")
        + textarea_html(f"hypotheses.{hid}.simple_guard", "Guard simples")
        + textarea_html(f"hypotheses.{hid}.risk", "Risco")
        + textarea_html(f"hypotheses.{hid}.notes", "Notas")
        for hid in HYPOTHESES
    )
    return '<section id="hypothesis-summary"><h2>Part C — Síntese por hipótese</h2><table><thead><tr><th>Hipótese</th><th>Positivos candidatos</th><th>Docs</th><th>N1/N2</th><th>Negativos</th><th>Resposta humana</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table><div class="hypothesis-form">' + fields + '</div></section>'


def final_summary_html() -> str:
    return '''<section id="final-review"><h2>Final Review — síntese humana</h2><div class="questions">
''' + select_html("summary.s1_strong_v6_hypothesis", ["YES", "NO", "UNCERTAIN"], "S1 — Existe hipótese forte para V6?") + textarea_html("summary.s2_hypothesis_ids", "S2 — Hipóteses") + select_html("summary.s3_multi_document_support", ["YES", "NO", "UNCERTAIN"], "S3 — Suporte em múltiplos documentos?") + select_html("summary.s4_negative_guard_support", ["YES", "NO", "UNCERTAIN"], "S4 — Negativos rejeitáveis por guard simples?") + select_html("summary.s5_requires_fuzzy", ["YES", "NO", "UNCERTAIN"], "S5 — Exige fuzzy matching?") + select_html("summary.s6_requires_unsupported_digit_change", ["YES", "NO", "UNCERTAIN"], "S6 — Exige alteração de dígito não coberta?") + select_html("summary.s7_recommendation", ["RUN_AUTOMATED_EXPERIMENT", "KEEP_V5_FROZEN", "REVIEW_MORE_CASES", "UNCERTAIN"], "S7 — Recomendação humana") + textarea_html("summary.s8_approved_hypotheses", "S8 — Hipóteses aprovadas") + textarea_html("summary.s9_rejected_hypotheses", "S9 — Hipóteses rejeitadas + motivo") + textarea_html("summary.s10_notes", "S10 — Observações gerais") + '</div></section>'


def html_document(context: Mapping[str, Any], template: Mapping[str, Any]) -> str:
    group_html = []
    by_id = {item["gold_index"]: item for item in context["cases"]}
    for title, ids in GROUPS.items():
        section_id = "part-a" if ids[0] in DOWNSTREAM_READY else "part-b"
        group_html.append(f'<section id="{section_id}"><h2>{escape(title)}</h2>' + "".join(card_html(by_id[index], context["negatives"]) for index in ids) + '</section>')
    template_json = json.dumps(template, ensure_ascii=False)
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Post-V5 Human Review</title>
<style>
body{{font:16px/1.45 system-ui,sans-serif;max-width:1180px;margin:auto;padding:20px;background:#fafafa;color:#18202a}} a{{color:#175b8c}} nav{{position:sticky;top:0;background:#fff;padding:12px;border:1px solid #ccd6df;z-index:2}} nav a{{margin-right:14px}} .card,section{{background:#fff;border:1px solid #d8e0e7;border-radius:8px;padding:16px;margin:18px 0}} .card{{border-left:5px solid #4c718d}} h1,h2,h3,h4{{margin-top:0}} h4{{font-size:.92rem;letter-spacing:.04em}} pre{{white-space:pre-wrap;word-break:break-word;background:#f4f7f9;padding:10px;border-radius:5px}} .context{{white-space:normal;background:#f7f9fb;padding:12px;border-radius:5px}} mark{{background:#fff0a8;padding:2px}} .gold{{border-left:4px solid #9b6c1f}} .current{{border-left:4px solid #3c708f}} .oracle{{border-left:4px solid #287450;background:#f3fbf6}} .hypothesis{{border-left:4px solid #7d5a9e}} .break{{border-left:4px solid #a75b4b}} .negative{{border:1px dashed #a75b4b;padding:10px;margin:10px 0;background:#fffafa}} .human{{border-left:4px solid #555}} .badge{{font-size:.75rem;background:#e4eef5;padding:3px 7px;border-radius:10px}} .questions{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}} label,fieldset{{display:block;padding:5px}} select,textarea{{display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:6px}} fieldset{{grid-column:1/-1;border:1px solid #ccd6df}} .check{{display:inline-block;width:auto;margin-right:12px}} .check input{{width:auto}} .special{{background:#f6f8fb}} table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccd6df;padding:7px;text-align:left;vertical-align:top}} .warning{{color:#8a481d;font-weight:600}} button{{padding:9px 12px;margin:4px}} #import-file{{display:none}} @media(max-width:700px){{body{{padding:8px}}pre{{font-size:.8rem}}}}
</style></head><body>
<h1>Human Review Artifact — 10 resíduos pós-V5</h1><p><b>PENDING_HUMAN_REVIEW.</b> Este artefato não aprova/rejeita V6, não altera produção e não preenche respostas humanas. Use gold apenas para revisão DEV.</p>
<nav><b>10 casos:</b> 5 downstream-ready · 5 exploratory — <a href="#part-a">Part A</a><a href="#part-b">Part B</a><a href="#hypothesis-summary">Hypothesis Summary</a><a href="#final-review">Final Review</a><button type="button" onclick="exportReview()">Export review JSON</button><button type="button" onclick="document.getElementById('import-file').click()">Import review JSON</button><input id="import-file" type="file" accept="application/json,.json" onchange="importReview(event)"></nav>
<p>Metodologia: procure estrutura local/runtime; tente falsificar com negativos; não use gold ou corpus para adivinhar dígitos. Uma hipótese sem negativos relevantes permanece <b>INSUFFICIENT</b>.</p>
{''.join(group_html)}{hypothesis_summary_html(context)}{final_summary_html()}
<script>
const TEMPLATE = {template_json};
function clone(x){{return JSON.parse(JSON.stringify(x));}}
function setAt(obj,path,value){{let p=path.split('.'), last=p.pop(); for(const k of p){{if(!(k in obj)||obj[k]===null) obj[k]={{}}; obj=obj[k];}} obj[last]=value;}}
function getAt(obj,path){{return path.split('.').reduce((x,k)=>x&&x[k],obj);}}
function csvArray(value){{return value.split(',').map(x=>x.trim()).filter(Boolean).map(x=>/^\\d+$/.test(x)?Number(x):x);}}
function formToReview(){{const out=clone(TEMPLATE); document.querySelectorAll('[data-path]').forEach(el=>{{let value=el.value.trim(); if(['q7_case_ids','positive_case_ids','negative_case_ids','s2_hypothesis_ids','s8_approved_hypotheses','s9_rejected_hypotheses'].some(x=>el.dataset.path.endsWith(x))) value=csvArray(value); else if(value==='') value=null; setAt(out,el.dataset.path,value);}}); document.querySelectorAll('[data-array-path]').forEach(el=>{{if(!el.checked)return; let arr=getAt(out,el.dataset.arrayPath); arr.push(el.value);}}); out.reviewed_at=new Date().toISOString(); const required=['q1_single_citation','q2_gold_boundary','q3_primary_phenomenon','q4_runtime_identity_sufficient','q5_identity_preserved_by_normalization','q6_general_rule_exists','q7_shared_cases','q8_literal_class_dependency','q9_negatives_break_hypothesis','q10_simple_guard_exists','q12_generalization','q13_automated_experiment']; const done=Object.values(out.cases).filter(c=>required.every(k=>c[k]!==null)).length; out.completion={{cases_completed:done,cases_total:10,summary_completed:out.summary.s1_strong_v6_hypothesis!==null&&out.summary.s7_recommendation!==null}}; return out;}}
function exportReview(){{const blob=new Blob([JSON.stringify(formToReview(),null,2)+'\\n'],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='post_v5_human_review.json';a.click();URL.revokeObjectURL(a.href);}}
function importReview(event){{const file=event.target.files[0];if(!file)return;const reader=new FileReader();reader.onload=()=>{{try{{const data=JSON.parse(reader.result);if(data.schema_version!=='1.0'||data.review_type!=='post_v5_detector_residual')throw Error('schema incompatível');document.querySelectorAll('[data-path]').forEach(el=>{{const v=getAt(data,el.dataset.path);if(v===null||v===undefined)return;el.value=Array.isArray(v)?v.join(', '):v;}});document.querySelectorAll('[data-array-path]').forEach(el=>{{const a=getAt(data,el.dataset.arrayPath)||[];el.checked=a.includes(el.value);}});alert('Revisão importada.');}}catch(err){{alert('JSON inválido: '+err.message);}}}};reader.readAsText(file);}}
</script></body></html>'''


def stable_context(context: Mapping[str, Any]) -> str:
    return json.dumps(context, ensure_ascii=False, sort_keys=True)


def main() -> int:
    contexts = [build_context() for _ in range(3)]
    if not (stable_context(contexts[0]) == stable_context(contexts[1]) == stable_context(contexts[2])):
        raise RuntimeError("geração não determinística")
    context = contexts[0]
    template = build_template()
    html = html_document(context, template)
    json_dump(CONTEXT_PATH, context)
    json_dump(TEMPLATE_PATH, template)
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html, encoding="utf-8")
    negative_cards = sum(len(items) for items in context["negatives"].values())
    print(f"cases = {len(context['cases'])}")
    print(f"downstream_ready = {sum(item['downstream_ready'] for item in context['cases'])}")
    print(f"exploratory = {sum(not item['downstream_ready'] for item in context['cases'])}")
    print(f"hypotheses = {len(HYPOTHESES)}")
    print(f"negative_cards = {negative_cards}")
    print(f"html = {HTML_PATH.relative_to(PROJECT_ROOT)}")
    print(f"json_template = {TEMPLATE_PATH.relative_to(PROJECT_ROOT)}")
    print("recommendation = PENDING_HUMAN_REVIEW")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
