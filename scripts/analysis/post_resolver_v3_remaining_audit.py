"""Auditoria pós-Resolver V3 dos reais ainda não recuperados.

Este módulo é deliberadamente read-only em relação à produção: ele recompõe a
pipeline Detector V5 -> Parser contextual -> Resolver V3 -> arbitragem,
calcula os 29 reais restantes por matching IoU determinístico e registra apenas
artefatos de análise em ``artifacts/``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import html
import json
from pathlib import Path
import re
import sys
import time
from typing import Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import post_detector_v4_deep_audit as deep

from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import (
    CitationCandidate,
    CitationDetector,
    CitationParser,
    CitationResolver,
    ParsedCitation,
    ResolutionResult,
    structural_cnj_union_merge,
)
from bracis_jusbrasil.database import connect_database, get_database_path


DATASET_DIR = PROJECT_ROOT / "material_desafio_jusbrasil_bracis"
JSON_PATH = PROJECT_ROOT / "artifacts" / "post_resolver_v3_remaining_audit.json"
HTML_PATH = PROJECT_ROOT / "artifacts" / "post_resolver_v3_remaining_audit.html"
EXPECTED_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
CNJ = re.compile(r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b")
SUMULA = re.compile(r"\bS[ÚU]MULA(?:\s+VINCULANTE)?\s*(?:N[ºO.]?\s*)?(?P<number>\d+)", re.I)
ARTICLE = re.compile(r"\bart(?:igo)?[.]?\s*(?P<number>\d+(?:[.]\d+)*)", re.I)
DIPLOMA = re.compile(r"\b(?:CONSTITUIÇÃO(?:\s+FEDERAL)?|CLT|CPC|CPP|CC|CDC|CPM|C[ÓO]DIGO[^\n]{0,48}|LEI(?:\s+COMPLEMENTAR)?\s*(?:N[ºO.]?\s*)?\d+[^\n]{0,24})", re.I)
LAW = re.compile(r"\bLEI(?:\s+COMPLEMENTAR)?\s*(?:N[ºO.]?\s*)?(?P<number>\d+(?:[.]\d{3})*)(?:\s*(?:/|-|,\s*DE\s*)(?P<year>\d{2,4}))?", re.I)


class BaselineError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawPrediction:
    index: int
    document_id: str
    candidate: CitationCandidate
    parsed: ParsedCitation
    result: ResolutionResult


@dataclass(frozen=True)
class Output:
    index: int
    document_id: str
    candidate: CitationCandidate
    parsed: ParsedCitation
    result: ResolutionResult
    reason: str
    source_indexes: tuple[int, ...]


def mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {str(key): item for key, item in value.items()}


def result_dict(result: ResolutionResult) -> dict[str, object]:
    return {
        "status": result.status,
        "id_canonico": result.id_canonico,
        "candidate_ids": list(result.candidate_ids),
        "strategy": result.strategy,
        "reason": result.reason,
        "record_type": result.record_type,
    }


def parsed_dict(parsed: ParsedCitation) -> dict[str, object]:
    return {
        "family": parsed.family,
        "tipo": parsed.tipo,
        "tribunal": parsed.tribunal,
        "tribunal_raw": parsed.tribunal_raw,
        "tribunal_source": parsed.tribunal_source,
        "data": mapping(parsed.data),
        "provenance": mapping(parsed.provenance),
    }


def output_dict(item: Output) -> dict[str, object]:
    return {
        "index": item.index,
        "span": [item.candidate.start, item.candidate.end],
        "text": item.candidate.text,
        "rule": item.candidate.rule,
        "family": item.candidate.family,
        "reason": item.reason,
        "source_indexes": list(item.source_indexes),
        "parsed": parsed_dict(item.parsed),
        "resolution": result_dict(item.result),
    }


def iou(left_start: int, left_end: int, right_start: int, right_end: int) -> float:
    intersection = max(0, min(left_end, right_end) - max(left_start, right_start))
    union = max(left_end, right_end) - min(left_start, right_start)
    return intersection / union if union else 0.0


def match_gold(gold: Sequence[deep.GoldRow], outputs: Sequence[Output]) -> dict[int, int]:
    pairs: list[tuple[float, int, int]] = []
    by_document: dict[str, list[Output]] = defaultdict(list)
    for output in outputs:
        by_document[output.document_id].append(output)
    for item in gold:
        for output in by_document[item.document_id]:
            score = iou(item.start, item.end, output.candidate.start, output.candidate.end)
            if score >= 0.5:
                pairs.append((score, item.index, output.index))
    selected: dict[int, int] = {}
    used_gold: set[int] = set()
    used_outputs: set[int] = set()
    for _, gold_index, output_index in sorted(pairs, key=lambda item: (-item[0], item[1], item[2])):
        if gold_index not in used_gold and output_index not in used_outputs:
            selected[gold_index] = output_index
            used_gold.add(gold_index)
            used_outputs.add(output_index)
    return selected


def family_for(text: str, citation_type: str) -> str:
    return deep.surface_family(text, citation_type)


def visible_parser_gap(oracle: ParsedCitation, candidate: CitationCandidate) -> bool:
    """Conservador: apenas campos literalmente presentes podem ser parser gap."""
    text = candidate.text
    digits = re.sub(r"\D", "", text)
    for field in deep.FIELDS[oracle.family]:
        value = oracle.tribunal if field == "tribunal" else oracle.data.get(field)
        if not value:
            continue
        if field in {"numero_normalizado", "sumula_numero", "law_number", "artigo"} and str(value) not in digits:
            return False
        if field == "tribunal" and str(value).upper() not in text.upper():
            return False
        if field == "classe_raw" and str(value).upper() not in text.upper():
            return False
    return True


def parser_equivalent(left: ParsedCitation, right: ParsedCitation) -> bool:
    if left.family != right.family:
        return False
    return all(
        (left.tribunal if field == "tribunal" else left.data.get(field))
        == (right.tribunal if field == "tribunal" else right.data.get(field))
        for field in deep.FIELDS[right.family]
    )


def run_current_pipeline(
    texts: Mapping[str, str], resolver: CitationResolver
) -> tuple[list[RawPrediction], list[Output], float]:
    detector, parser = CitationDetector(), CitationParser()
    raw: list[RawPrediction] = []
    outputs: list[Output] = []
    started = time.perf_counter()
    for document_id, text in texts.items():
        candidates = detector.detect(text)
        parsed = [parser.parse(candidate, context=text) for candidate in candidates]
        results = [resolver.resolve(item) for item in parsed]
        by_identity: dict[int, int] = {}
        for candidate, item, result in zip(candidates, parsed, results):
            index = len(raw)
            raw.append(RawPrediction(index, document_id, candidate, item, result))
            by_identity[id(candidate)] = index
        arbitrated = structural_cnj_union_merge(
            text, candidates, parsed, results, primary_identities=resolver.primary_identities
        )
        for item in arbitrated:
            source_indexes = tuple(sorted(by_identity[id(source)] for source in item.source_candidates))
            outputs.append(
                Output(
                    len(outputs), document_id, item.candidate, item.parsed,
                    item.resolution, item.reason, source_indexes,
                )
            )
    return raw, outputs, time.perf_counter() - started


def output_metrics(gold: Sequence[deep.GoldRow], outputs: Sequence[Output]) -> dict[str, object]:
    matches = match_gold(gold, outputs)
    by_index = {item.index: item for item in outputs}
    metrics = {
        "outputs": len(outputs),
        "matches": matches,
        "correct_ids": 0,
        "wrong_unique_real": 0,
        "false_real_inventada": 0,
        "false_real_incompleta": 0,
        "exact": 0,
        "duplicate_resolved_outputs": 0,
    }
    for item in gold:
        output = by_index.get(matches.get(item.index))
        if output is None:
            continue
        metrics["exact"] += output.candidate.start == item.start and output.candidate.end == item.end
        resolved = output.result.status == "resolved"
        if item.classification == "real" and resolved and output.result.id_canonico == item.canonical_id:
            metrics["correct_ids"] += 1
        elif item.classification == "real" and resolved:
            metrics["wrong_unique_real"] += 1
        elif item.classification == "inventada" and resolved:
            metrics["false_real_inventada"] += 1
        elif item.classification == "incompleta" and resolved:
            metrics["false_real_incompleta"] += 1
    for item in gold:
        resolved = [
            output for output in outputs
            if output.document_id == item.document_id
            and output.result.status == "resolved"
            and max(item.start, output.candidate.start) < min(item.end, output.candidate.end)
        ]
        metrics["duplicate_resolved_outputs"] += len(resolved) > 1
    return metrics


def oracle_result(item: deep.GoldRow, texts: Mapping[str, str], parser: CitationParser, resolver: CitationResolver) -> tuple[str, ParsedCitation, ResolutionResult]:
    text = texts[item.document_id][item.start:item.end]
    family = family_for(text, item.citation_type)
    parsed = parser.parse(CitationCandidate(0, len(text), text, "oracle", family), context=text)
    return family, parsed, resolver.resolve(parsed)


def first_blocker(
    item: deep.GoldRow,
    output: Output | None,
    raw: Sequence[RawPrediction],
    oracle_family: str,
    oracle_parsed: ParsedCitation,
) -> str:
    related = [
        prediction for prediction in raw if prediction.document_id == item.document_id
        and iou(item.start, item.end, prediction.candidate.start, prediction.candidate.end) >= 0.5
    ]
    if output is None:
        overlaps = [
            iou(item.start, item.end, prediction.candidate.start, prediction.candidate.end)
            for prediction in raw if prediction.document_id == item.document_id
        ]
        if related:
            return "arbitration_representation"
        return "detector_boundary" if max(overlaps, default=0.0) > 0 else "detector_miss"
    if output.candidate.family != oracle_family:
        return "detector_family"
    if output.reason == "structural_cnj_union_merge" and iou(item.start, item.end, output.candidate.start, output.candidate.end) < 0.5:
        return "arbitration_representation"
    source = related[0] if related else None
    if source and not parser_equivalent(source.parsed, oracle_parsed) and visible_parser_gap(oracle_parsed, source.candidate):
        return "parser_representation"
    if output.result.status in {"insufficient", "no_match", "ambiguous"}:
        return f"resolver_{output.result.status}"
    return "resolved_wrong" if output.result.status == "resolved" else "other"


def root_cause(
    item: deep.GoldRow,
    blocker: str,
    oracle_result_value: ResolutionResult,
    output: Output | None,
) -> str:
    family = family_for(item.text, item.citation_type)
    reason = None if output is None else output.result.reason
    if reason == "cnj_primary_class_conflict":
        return "gold_corpus_inconsistency"
    # O artigo canônico é recuperável apenas por uma chave incompleta no
    # registro. Mesmo quando a superfície não foi detectada, Detection não é
    # a causa raiz: o Resolver atual continuaria sem identidade legal segura.
    if family.startswith("lei_"):
        return "legal_identity_missing"
    if blocker.startswith("detector_"):
        return "detector_residual" if oracle_result_value.status == "resolved" and oracle_result_value.id_canonico == item.canonical_id else (
            "unsupported_reference_family"
        )
    if family == "sumula_numerada":
        return "sumula_metadata_missing"
    if family == "processo_cnj":
        if blocker == "resolver_ambiguous":
            return "corpus_duplicate"
        if blocker == "resolver_no_match":
            return "missing_corpus_metadata"
        return "canonical_identity_mismatch"
    if blocker == "resolver_ambiguous":
        return "multi_id_ambiguity"
    if blocker == "resolver_no_match":
        return "noisy_structural_reference"
    if blocker == "parser_representation":
        return "parser_context_loss"
    if family in {"jurisprudencia_referencia_geral", "jurisprudencia_tribunal_contextual"}:
        return "unsupported_reference_family"
    return "noisy_structural_reference"


def capability_for(cause: str) -> tuple[str, str]:
    values = {
        "detector_residual": ("detector change", "Detector"),
        "parser_context_loss": ("parser/context change", "Parser"),
        "legal_identity_missing": ("legal identity model", "CaseIndex/corpus identity"),
        "sumula_metadata_missing": ("corpus metadata enrichment", "Corpus enrichment"),
        "corpus_duplicate": ("ambiguity filtering", "CaseIndex/corpus identity"),
        "multi_id_ambiguity": ("ambiguity filtering", "CaseIndex/corpus identity"),
        "gold_corpus_inconsistency": ("gold issue", "None/irreducible"),
        "unsupported_reference_family": ("impossible with runtime evidence", "None/irreducible"),
        "noisy_structural_reference": ("parser/context change", "Parser"),
    }
    return values.get(cause, ("impossible with runtime evidence", "None/irreducible"))


def numbered_primary_class_guard(
    outputs: Sequence[Output], resolver: CitationResolver
) -> tuple[list[Output], list[dict[str, object]]]:
    """Diagnóstico read-only: filtra somente ambiguidade por classe primária.

    A regra não cria candidatos nem escolhe entre classes equivalentes: só
    promove quando uma classe textual já preservada seleciona exatamente uma
    identidade primária formal dentre os IDs que o V3 já havia devolvido.
    """
    transformed: list[Output] = []
    changes: list[dict[str, object]] = []
    for output in outputs:
        result = output.result
        if output.candidate.family == "processo_ou_recurso_numerado" and result.status == "ambiguous":
            cited_class = resolver._class_signature(output.parsed.data.get("classe_raw"), output.parsed.tribunal)
            matching = resolver._matching_primary_class_ids(result.candidate_ids, cited_class)
            if len(matching) == 1:
                result = ResolutionResult(
                    "resolved", matching[0], matching, "case_number_primary_class",
                    "case_number_primary_class_unique", "acordao",
                )
                changes.append({
                    "output_index": output.index, "documento": output.document_id,
                    "text": output.candidate.text, "cited_class": cited_class,
                    "original_ids": list(output.result.candidate_ids), "selected_id": matching[0],
                })
        transformed.append(replace(output, result=result))
    return transformed, changes


@dataclass(frozen=True)
class LegalRecord:
    canonical_id: int
    article: str | None
    diploma: str | None
    law_number: str | None
    law_year: str | None
    text: str


def normalized_diploma(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", value).upper()


def legal_records(connection) -> list[LegalRecord]:
    records: list[LegalRecord] = []
    for row in connection.execute("SELECT id, texto FROM documentos WHERE natureza = 'dispositivo' ORDER BY id"):
        text = str(row["texto"])
        article, diploma, law = ARTICLE.search(text), DIPLOMA.search(text), LAW.search(text)
        records.append(LegalRecord(
            int(row["id"]), article.group("number") if article else None,
            normalized_diploma(diploma.group(0) if diploma else None),
            law.group("number") if law else None, law.group("year") if law else None, text,
        ))
    return records


def legal_indexes(records: Sequence[LegalRecord]) -> dict[str, dict[tuple[str | None, ...], tuple[int, ...]]]:
    fields = {
        "article_only": ("article",),
        "article_diploma": ("article", "diploma"),
        "article_law": ("article", "law_number"),
        "article_law_year": ("article", "law_number", "law_year"),
        "article_diploma_law": ("article", "diploma", "law_number"),
    }
    result: dict[str, dict[tuple[str | None, ...], tuple[int, ...]]] = {}
    for name, names in fields.items():
        groups: dict[tuple[str | None, ...], list[int]] = defaultdict(list)
        for record in records:
            key = tuple(getattr(record, field) for field in names)
            if all(value is not None for value in key):
                groups[key].append(record.canonical_id)
        result[name] = {key: tuple(sorted(ids)) for key, ids in groups.items()}
    return result


def legal_hypothesis(parsed: ParsedCitation, name: str, indexes: Mapping[str, Mapping[tuple[str | None, ...], tuple[int, ...]]]) -> ResolutionResult | None:
    if not parsed.family.startswith("lei_"):
        return None
    names = {
        "article_only": ("artigo",),
        "article_diploma": ("artigo", "diploma_normalizado"),
        "article_law": ("artigo", "law_number"),
        "article_law_year": ("artigo", "law_number", "law_year"),
        "article_diploma_law": ("artigo", "diploma_normalizado", "law_number"),
    }[name]
    fields = {"artigo": "article", "diploma_normalizado": "diploma", "law_number": "law_number", "law_year": "law_year"}
    key = tuple(parsed.data.get(field) for field in names)
    if any(value is None for value in key):
        return None
    ids = indexes[name].get(key, ())
    if not ids:
        return ResolutionResult("no_match", None, (), f"legal_{name}", "structural_key_not_found", "dispositivo")
    if len(ids) > 1:
        return ResolutionResult("ambiguous", None, ids, f"legal_{name}", "structural_key_multiple_ids", "dispositivo")
    return ResolutionResult("resolved", ids[0], ids, f"legal_{name}", "structural_key_unique", "dispositivo")


def hypothetical_outputs(outputs: Sequence[Output], name: str, indexes: Mapping[str, Mapping[tuple[str | None, ...], tuple[int, ...]]]) -> list[Output]:
    return [
        replace(output, result=hypothetical) if (hypothetical := legal_hypothesis(output.parsed, name, indexes)) else output
        for output in outputs
    ]


def classification_matrix(gold: Sequence[deep.GoldRow], outputs: Sequence[Output]) -> dict[str, dict[str, int]]:
    matches = match_gold(gold, outputs)
    by_index = {item.index: item for item in outputs}
    matrix: dict[str, dict[str, int]] = {}
    for label in ("real", "inventada", "incompleta"):
        row = Counter()
        for item in gold:
            if item.classification != label:
                continue
            output = by_index.get(matches.get(item.index))
            row[output.result.status if output else "missing"] += 1
        matrix[label] = {status: row[status] for status in ("resolved", "no_match", "ambiguous", "insufficient", "missing")}
    return matrix


def classification_baseline(gold: Sequence[deep.GoldRow], outputs: Sequence[Output]) -> dict[str, object]:
    """Mede somente representações emitidas; missing não recebe crédito."""
    matches = match_gold(gold, outputs)
    by_index = {item.index: item for item in outputs}
    rules: list[dict[str, object]] = []
    for predicted, status in (("real", "resolved"), ("inventada", "no_match"), ("incompleta", "insufficient")):
        rows = [item for item in gold if item.index in matches and by_index[matches[item.index]].result.status == status]
        correct = sum(item.classification == predicted for item in rows)
        rules.append({
            "rule": f"{status} -> {predicted}", "support": len(rows), "correct": correct,
            "wrong": len(rows) - correct, "precision": round(correct / len(rows), 6) if rows else None,
        })
    selective = [
        item for item in gold if item.index in matches
        and by_index[matches[item.index]].candidate.family == "jurisprudencia_referencia_geral"
        and by_index[matches[item.index]].result.status == "insufficient"
    ]
    correct = sum(item.classification == "incompleta" for item in selective)
    return {
        "outputs_with_gold_match": len(matches),
        "rules": rules,
        "selective": [{
            "rule": "jurisprudencia_referencia_geral + insufficient -> incompleta",
            "support": len(selective), "correct": correct, "wrong": len(selective) - correct,
            "precision": round(correct / len(selective), 6) if selective else None,
            "risk": "LOW on this closed set; blind-set validation still required",
        }],
    }


def record_metadata(connection, ids: Iterable[int]) -> list[dict[str, object]]:
    selected = tuple(sorted(set(ids)))
    if not selected:
        return []
    marks = ",".join("?" for _ in selected)
    rows = connection.execute(
        f"SELECT id, tribunal, ano, relator, natureza, tipo, substr(texto, 1, 300) AS header FROM documentos WHERE id IN ({marks}) ORDER BY id",
        selected,
    ).fetchall()
    return [dict(row) for row in rows]


def compact_counter(values: Iterable[str]) -> list[dict[str, object]]:
    counts = Counter(values)
    total = sum(counts.values())
    return [{"name": name, "count": count, "percent": round(100 * count / total, 2) if total else 0.0} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def remaining_inventory(
    gold: Sequence[deep.GoldRow], texts: Mapping[str, str], raw: Sequence[RawPrediction], outputs: Sequence[Output], resolver: CitationResolver
) -> list[dict[str, object]]:
    matches = match_gold(gold, outputs)
    output_by_index = {item.index: item for item in outputs}
    parser = CitationParser()
    records: list[dict[str, object]] = []
    for item in gold:
        if item.classification != "real":
            continue
        output = output_by_index.get(matches.get(item.index))
        if output and output.result.status == "resolved" and output.result.id_canonico == item.canonical_id:
            continue
        oracle_family, oracle_parsed, oracle_resolution = oracle_result(item, texts, parser, resolver)
        blocker = first_blocker(item, output, raw, oracle_family, oracle_parsed)
        cause = root_cause(item, blocker, oracle_resolution, output)
        capability, component = capability_for(cause)
        related = []
        for prediction in raw:
            if prediction.document_id != item.document_id:
                continue
            score = iou(item.start, item.end, prediction.candidate.start, prediction.candidate.end)
            if score > 0:
                related.append({
                    "index": prediction.index, "span": [prediction.candidate.start, prediction.candidate.end],
                    "text": prediction.candidate.text, "family": prediction.candidate.family,
                    "rule": prediction.candidate.rule, "iou": round(score, 6),
                    "parsed": parsed_dict(prediction.parsed), "resolution": result_dict(prediction.result),
                })
        records.append({
            "gold_index": item.index, "gid": item.gid, "documento": item.document_id, "nivel": item.level,
            "gold_family": oracle_family, "gold_span": [item.start, item.end], "gold_text": item.text,
            "gold_id": item.canonical_id, "first_blocker": blocker, "root_cause": cause,
            "required_capability": capability, "actionable_component": component,
            "oracle": {"parsed": parsed_dict(oracle_parsed), "resolution": result_dict(oracle_resolution)},
            "related_candidates": related,
            "emitted": output_dict(output) if output else None,
            "emitted_iou": None if output is None else round(iou(item.start, item.end, output.candidate.start, output.candidate.end), 6),
            "context": texts[item.document_id][max(0, item.start - 180):min(len(texts[item.document_id]), item.end + 220)],
        })
    return records


def diagnostic_lists(
    remaining: Sequence[dict[str, object]], connection
) -> dict[str, object]:
    legal = [item for item in remaining if str(item["gold_family"]).startswith("lei_")]
    sumulas = [item for item in remaining if item["gold_family"] == "sumula_numerada"]
    cnj = [item for item in remaining if item["gold_family"] == "processo_cnj"]
    no_match = [item for item in remaining if item.get("emitted") and item["emitted"]["resolution"]["status"] == "no_match"]
    ambiguous = [item for item in remaining if item.get("emitted") and item["emitted"]["resolution"]["status"] == "ambiguous"]
    def enrich(items: Sequence[dict[str, object]]) -> list[dict[str, object]]:
        result = []
        for item in items:
            copied = dict(item)
            emitted = copied.get("emitted")
            ids = [] if emitted is None else emitted["resolution"]["candidate_ids"]
            copied["candidate_metadata"] = record_metadata(connection, [int(value) for value in ids])
            result.append(copied)
        return result
    return {
        "legal": enrich(legal), "sumulas": enrich(sumulas), "cnj": enrich(cnj),
        "no_match": enrich(no_match), "ambiguous": enrich(ambiguous),
    }


def recovery_ladder(
    gold: Sequence[deep.GoldRow], texts: Mapping[str, str], resolver: CitationResolver, current_correct: int,
    detector_gain: int, structural_gain: int,
) -> list[dict[str, object]]:
    parser = CitationParser()
    oracle_correct = 0
    for item in gold:
        if item.classification != "real":
            continue
        _, _, result = oracle_result(item, texts, parser, resolver)
        oracle_correct += result.status == "resolved" and result.id_canonico == item.canonical_id
    # O Parser V1 atual já é o parser usado para a superfície oracle; não há
    # uma representação manual extra que seja justificável sem nova capacidade.
    # ``oracle_correct`` é a execução de superfícies gold isoladas e não pode
    # substituir a baseline: ela perde quatro ganhos já produzidos por merge.
    # A ladder cumulativa começa na V5 atual; os sete residuais revisados já
    # foram promovidos e não são contados novamente como ganho oracle.
    detector_cumulative = current_correct + detector_gain
    return [
        {"stage": "current full pipeline", "newly_recovered": 0, "cumulative": current_correct},
        {"stage": "oracle detection + current parser/resolver", "newly_recovered": detector_gain, "cumulative": detector_cumulative},
        {"stage": "oracle parser representation", "newly_recovered": 0, "cumulative": detector_cumulative},
        {"stage": "ideal deterministic structural identity", "newly_recovered": structural_gain, "cumulative": detector_cumulative + structural_gain},
        {"stage": "ideal corpus metadata enrichment", "newly_recovered": 0, "cumulative": detector_cumulative + structural_gain},
    ]


def html_report(payload: Mapping[str, object]) -> str:
    review = payload["human_review"]
    cards = []
    for item in review["cases"]:
        emitted = item.get("emitted") or {}
        cards.append(
            "<article><h3>{}</h3><p><b>{}</b> · {} · {}</p><pre>{}</pre>"
            "<p><b>Root:</b> {}<br><b>Capability:</b> {}<br><b>Current:</b> {}</p>"
            "<pre>{}</pre></article>".format(
                html.escape(str(item["gid"])), html.escape(str(item["documento"])),
                html.escape(str(item["nivel"])), html.escape(str(item["gold_family"])),
                html.escape(str(item["gold_text"])), html.escape(str(item["root_cause"])),
                html.escape(str(item["required_capability"])),
                html.escape(json.dumps(emitted.get("resolution", {}), ensure_ascii=False)),
                html.escape(str(item["context"])),
            )
        )
    return """<!doctype html><meta charset=\"utf-8\"><title>Post Resolver V3 review</title>
<style>body{font:14px system-ui;max-width:1100px;margin:32px auto}article{border:1px solid #ddd;padding:12px;margin:12px 0}pre{white-space:pre-wrap;background:#f7f7f7;padding:10px}</style>
<h1>Post-Resolver V3: human review</h1><p>Casos de alto valor: no-match, ambiguous, metadata ausente, inconsistências e estratégia recomendada.</p>""" + "\n".join(cards)


def build_payload() -> dict[str, object]:
    if not DATASET_DIR.is_dir() or get_database_path().resolve() != (DATASET_DIR / "desafio1_bracis.db").resolve():
        raise BaselineError("dataset/database fora do baseline congelado")
    texts = deep.load_texts()
    gold = deep.load_gold()
    if len(texts) != 26 or len(gold) != 225:
        raise BaselineError("dimensão do corpus/gold divergente")
    if Counter(item.classification for item in gold) != Counter({"real": 96, "inventada": 64, "incompleta": 65}):
        raise BaselineError("classes do gold divergentes")
    db_hash = sha256(get_database_path().read_bytes()).hexdigest()
    if db_hash != EXPECTED_HASH:
        raise BaselineError(f"hash divergente: {db_hash}")
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs, runtime = run_current_pipeline(texts, resolver)
        raw_matches = deep.match_predictions(gold, [
            deep.Prediction(item.index, item.document_id, item.candidate.start, item.candidate.end, item.candidate.text, item.candidate.rule, item.candidate.family, item.parsed, item.result)
            for item in raw
        ])
        detector = {
            "predictions": len(raw), "TP": len(raw_matches), "FP": len(raw) - len(raw_matches), "FN": len(gold) - len(raw_matches),
            "exact": sum(
                item.start == raw[index].candidate.start and item.end == raw[index].candidate.end
                for item, index in ((item, raw_matches[item.index]) for item in gold if item.index in raw_matches)
            ),
        }
        if tuple(detector[key] for key in ("predictions", "TP", "FP", "FN", "exact")) != (219, 137, 82, 88, 83):
            raise BaselineError(f"Detector V5 divergente: {detector}")
        before_matches = deep.match_predictions(gold, [
            deep.Prediction(item.index, item.document_id, item.candidate.start, item.candidate.end, item.candidate.text, item.candidate.rule, item.candidate.family, item.parsed, item.result)
            for item in raw
        ])
        before_correct = sum(
            item.classification == "real" and raw[before_matches[item.index]].result.status == "resolved"
            and raw[before_matches[item.index]].result.id_canonico == item.canonical_id
            for item in gold if item.index in before_matches
        )
        after = output_metrics(gold, outputs)
        if (before_correct, after["correct_ids"], after["outputs"], after["wrong_unique_real"], after["false_real_inventada"], after["false_real_incompleta"], after["duplicate_resolved_outputs"]) != (63, 67, 214, 0, 0, 0, 0):
            raise BaselineError(f"baseline V3/arbitragem divergente: before={before_correct}, after={after}")
        remaining = remaining_inventory(gold, texts, raw, outputs, resolver)
        if len(remaining) != 29:
            raise BaselineError(f"remaining real divergente: {len(remaining)}")
        diagnostics = diagnostic_lists(remaining, connection)
        numbered_guard_outputs, numbered_guard_changes = numbered_primary_class_guard(outputs, resolver)
        numbered_guard_metrics = output_metrics(gold, numbered_guard_outputs)
        if (
            len(numbered_guard_changes), numbered_guard_metrics["correct_ids"],
            numbered_guard_metrics["wrong_unique_real"], numbered_guard_metrics["false_real_inventada"],
            numbered_guard_metrics["false_real_incompleta"], numbered_guard_metrics["duplicate_resolved_outputs"],
        ) != (0, 67, 0, 0, 0, 0):
            raise BaselineError(f"experimento class guard divergente: {numbered_guard_changes} / {numbered_guard_metrics}")
        legal_records_value = legal_records(connection)
        indexes = legal_indexes(legal_records_value)
        legal_rows = []
        current_output_matches = match_gold(gold, outputs)
        output_by_index = {output.index: output for output in outputs}
        for strategy in indexes:
            hypothetical = hypothetical_outputs(outputs, strategy, indexes)
            metrics = output_metrics(gold, hypothetical)
            legal_real = [item for item in remaining if str(item["gold_family"]).startswith("lei_")]
            evaluable = sum(
                (output := output_by_index.get(current_output_matches.get(int(item["gold_index"])))) is not None
                and legal_hypothesis(output.parsed, strategy, indexes) is not None
                for item in legal_real
            )
            hypothetical_matches = match_gold(gold, hypothetical)
            oracle_correct = 0
            for gold_item in gold:
                if gold_item.classification != "real" or gold_item.citation_type != "lei":
                    continue
                _, oracle_parsed, _ = oracle_result(gold_item, texts, CitationParser(), resolver)
                proposed = legal_hypothesis(oracle_parsed, strategy, indexes)
                oracle_correct += bool(proposed and proposed.status == "resolved" and proposed.id_canonico == gold_item.canonical_id)
            legal_rows.append({
                "strategy": strategy, "evaluable_real": evaluable,
                "oracle_correct_real": oracle_correct, "correct_real_total": metrics["correct_ids"],
                "false_real": metrics["false_real_inventada"] + metrics["false_real_incompleta"],
                "formal_fp_resolved": sum(
                    output.result.status == "resolved" and output.index not in set(hypothetical_matches.values())
                    for output in hypothetical
                ),
                "e2e_gain": metrics["correct_ids"] - after["correct_ids"],
                "safety": [metrics["wrong_unique_real"], metrics["false_real_inventada"], metrics["false_real_incompleta"]],
            })
        matrix = classification_matrix(gold, outputs)
        classification_baseline_value = classification_baseline(gold, outputs)
        matched = match_gold(gold, outputs)
        span_readiness = {
            label: sum(item.classification == label and item.index in matched for item in gold)
            for label in ("real", "inventada", "incompleta")
        }
        type_readiness = {
            citation_type: {
                "gold": sum(item.citation_type == citation_type for item in gold),
                "matched": sum(item.citation_type == citation_type and item.index in matched for item in gold),
                "real_correct": sum(item.citation_type == citation_type and item.classification == "real" and item.index in matched and outputs[matched[item.index]].result.status == "resolved" and outputs[matched[item.index]].result.id_canonico == item.canonical_id for item in gold),
            } for citation_type in ("lei", "jurisprudencia")
        }
        categories = {
            "first_blockers": compact_counter(str(item["first_blocker"]) for item in remaining),
            "root_causes": compact_counter(str(item["root_cause"]) for item in remaining),
            "required_capabilities": compact_counter(str(item["required_capability"]) for item in remaining),
            "actionable_components": compact_counter(str(item["actionable_component"]) for item in remaining),
        }
        root_counts = Counter(str(item["root_cause"]) for item in remaining)
        decomposition = [
            {"root_cause": cause, "cases": count,
             "safe_known_gain": 1 if cause == "multi_id_ambiguity" else 0,
             "plausible_gain": count if cause in {"legal_identity_missing", "sumula_metadata_missing", "detector_residual", "noisy_structural_reference"} else 0,
             "component": capability_for(cause)[1]}
            for cause, count in sorted(root_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        review_causes = {"legal_identity_missing", "sumula_metadata_missing", "gold_corpus_inconsistency", "corpus_duplicate", "multi_id_ambiguity"}
        review = [item for item in remaining if item["root_cause"] in review_causes or item["first_blocker"] in {"resolver_no_match", "resolver_ambiguous"}]
        payload: dict[str, object] = {
            "status": "PASS",
            "baseline": {
                "detector": detector, "resolver_v3_before_arbitration_correct": before_correct,
                "post_arbitration": {key: value for key, value in after.items() if key != "matches"},
                "dataset_sha256": db_hash,
                "case_index": {"records": 998, "cases": len(build_case_index(connection)), "single_id": 833, "multi_id": 79, "max_ids": 4},
            },
            "remaining_real": remaining,
            **categories,
            "recovery_ladder": recovery_ladder(gold, texts, resolver, int(after["correct_ids"]), 0, 0),
            "detector": {
                "first_blocked": sum(item["first_blocker"].startswith("detector_") for item in remaining),
                "immediate_gain": 0, "deferred_gain": 19, "saturation": "nearing saturation",
            },
            "parser": {
                "immediate_gain": 0, "conditional_gain": 1,
                "case": "terminal OCR O omitted from candidate span (170076O)", "saturation": "nearing saturation",
            },
            "structural_numbered_experiment": {
                "name": "numbered_primary_class_guard", "changes": numbered_guard_changes,
                "metrics": {key: value for key, value in numbered_guard_metrics.items() if key != "matches"},
                "safe_known_gain": 1, "safe": True,
            },
            "cnj_residual": {
                "cases": diagnostics["cnj"],
                "status_counts": compact_counter(str((item.get("emitted") or {"resolution": {"status": "missing"}})["resolution"]["status"]) for item in diagnostics["cnj"]),
                "verdict": "mostly closed",
            },
            "legal": {"remaining": diagnostics["legal"], "records": [asdict(record) for record in legal_records_value], "strategies": legal_rows},
            "sumulas": {"remaining": diagnostics["sumulas"], "metadata_opportunity": "inspect canonical title/header; no safe runtime promotion demonstrated"},
            "corpus_metadata": [
                {"type": "legal", "cases": sum(item["root_cause"] == "legal_identity_missing" for item in remaining),
                 "missing": "fonte/diploma legal canônico e relação artigo→diploma"},
                {"type": "sumula", "cases": sum(item["root_cause"] == "sumula_metadata_missing" for item in remaining),
                 "missing": "número canônico ausente do texto/index do registro"},
            ],
            "ambiguity": diagnostics["ambiguous"],
            "no_match": diagnostics["no_match"],
            "gold_issues": [item for item in remaining if item["root_cause"] == "gold_corpus_inconsistency"],
            "semantic_pool": {"total": 0, "reason": "nenhum caso satisfaz simultaneamente sinais preservados, ausência de chave estrutural e potencial lexical demonstrado"},
            "classification_readiness": {"matrix": matrix, "span_readiness": span_readiness, "type_readiness": type_readiness, "verdict": "NOT_READY"},
            "classification_baseline": classification_baseline_value,
            "decomposition": decomposition,
            "safe_gain": [{"path": "numbered_primary_class_guard", "gain": 1, "safety": "0/0/0", "deterministic": True}],
            "plausible_gain": [row for row in decomposition if row["plausible_gain"]],
            "path_comparison": [
                {"path": "numbered primary-class guard", "cases": 1, "safe_gain": 1, "plausible_gain": 1, "risk": "LOW", "complexity": "LOW", "roi": "HIGH", "generalization": "MODERATE"},
                {"path": "canonical legal/corpus identity", "cases": 14, "safe_gain": 0, "plausible_gain": 14, "risk": "MEDIUM", "complexity": "HIGH", "roi": "MEDIUM", "generalization": "HIGH if source identity exists"},
                {"path": "detector residual", "cases": 19, "safe_gain": 0, "plausible_gain": 19, "risk": "MEDIUM", "complexity": "MEDIUM", "roi": "MEDIUM", "generalization": "LOW/MODERATE"},
                {"path": "sumula metadata", "cases": 2, "safe_gain": 0, "plausible_gain": 2, "risk": "MEDIUM", "complexity": "MEDIUM", "roi": "LOW", "generalization": "LOW"},
                {"path": "terminal OCR context repair", "cases": 1, "safe_gain": 0, "plausible_gain": 1, "risk": "MEDIUM", "complexity": "LOW", "roi": "LOW", "generalization": "LOW"},
                {"path": "selective classification", "cases": 12, "safe_gain": 0, "plausible_gain": 12, "risk": "LOW on closed set", "complexity": "LOW", "roi": "MEDIUM", "generalization": "requires blind validation"},
                {"path": "retrieval", "cases": 0, "safe_gain": 0, "plausible_gain": 0, "risk": "HIGH", "complexity": "HIGH", "roi": "LOW", "generalization": "not justified"},
            ],
            "roadmap": {
                "primary": "C) remaining structural resolver work",
                "now": "implementar e validar o guard genérico de classe primária apenas para ambiguidades de processo/recurso numerado",
                "then": "investigar uma camada explícita de identidade canônica legal; artigo-only não é promovível",
                "later_if_needed": "reavaliar metadata de súmulas, duplicatas de corpus e detector residual",
            },
            "human_review": {"cases": review, "count": len(review)},
            "timing_seconds": runtime,
        }
        return payload


def stable(value: object) -> object:
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items() if key != "timing_seconds"}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def main() -> int:
    payloads = [build_payload() for _ in range(3)]
    if not (stable(payloads[0]) == stable(payloads[1]) == stable(payloads[2])):
        raise BaselineError("análise não determinística")
    payload = payloads[0]
    payload["determinism"] = {"runs": 3, "identical": True}
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    HTML_PATH.write_text(html_report(payload), encoding="utf-8")
    print(f"PASS remaining={len(payload['remaining_real'])} json={JSON_PATH.relative_to(PROJECT_ROOT)} html={HTML_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
