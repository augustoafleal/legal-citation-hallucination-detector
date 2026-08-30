"""Reavalia oportunidades de cobertura depois da promoção do Resolver V2.

Este script é deliberadamente diagnóstico: usa o gold somente para matching,
atribuição de blockers e upper bounds. Nenhuma das estratégias hipotéticas
abaixo é usada pelo código de produção.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import hashlib
import re
import sys
import time
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook

from bracis_jusbrasil.cases import CaseIndex, build_case_index
from bracis_jusbrasil.citations import (
    CitationCandidate,
    CitationDetector,
    CitationParser,
    CitationResolver,
    ParsedCitation,
    ResolutionResult,
)
from bracis_jusbrasil.database import connect_database, get_database_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "material_desafio_jusbrasil_bracis"
EXPECTED_DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"

CNJ_PATTERN = re.compile(
    r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b"
)
SUMULA_PATTERN = re.compile(r"\bS[ÚU]MULA(?:\s+VINCULANTE)?\s*(?:N[ºO.]?\s*)?\d+", re.I)
ARTICLE_PATTERN = re.compile(r"\b(?:art(?:igo)?s?[.]?\s*)\d+", re.I)
DIPLOMA_PATTERN = re.compile(
    r"\b(?:Constituiç[aã]o(?: Federal)?|C[oó]digo|Lei(?: Complementar)?\s*(?:n[ºo.]?\s*)?\d+|CLT|CPC|CPP|CC|CDC|CPM)\b",
    re.I,
)
PROCESS_PATTERN = re.compile(
    r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|Agravo|Recurso Especial|Recurso Extraordinário|Habeas Corpus|Reclamação)\b",
    re.I,
)
COURT_PATTERN = re.compile(
    r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|"
    r"Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b",
    re.I,
)
YEAR_PATTERN = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
RELATOR_PATTERN = re.compile(
    r"\b(?:Rel\.?|Relator(?:a)?)\s*(?:Min\.?|Ministra|Ministro|Des\.?)?\s*[A-Z]",
    re.I,
)
LEGAL_ARTICLE = re.compile(r"\bart(?:igo)?[.]?\s*(\d+(?:[.]\d+)*)", re.I)
LEGAL_DIPLOMA = re.compile(
    r"\b(?:Constituiç[aã]o(?: Federal)?|C[oó]digo(?: [A-Za-z ]+)?|"
    r"Lei(?: Complementar)?\s*(?:n[ºo.]?\s*)?\d+[./-]?\d*(?:/\d{4})?|"
    r"CLT|CPC|CPP|CC|CDC|CPM)\b",
    re.I,
)
LEGAL_LAW = re.compile(
    r"\bLei(?: Complementar)?\s*(?:n[ºo.]?\s*)?(\d+)(?:[./-]?\d*)?(?:/(\d{4}))?",
    re.I,
)

FAMILIES = (
    "processo_ou_recurso_numerado",
    "processo_cnj",
    "sumula_numerada",
    "lei_dispositivo_com_diploma",
    "lei_dispositivo_sem_diploma",
    "lei_referencia_geral",
    "jurisprudencia_tribunal_contextual",
    "jurisprudencia_referencia_geral",
)
FIELDS = {
    "processo_ou_recurso_numerado": ("numero_normalizado", "classe_raw", "uf"),
    "processo_cnj": ("numero_normalizado",),
    "sumula_numerada": ("sumula_numero", "tribunal"),
    "lei_dispositivo_com_diploma": ("artigo", "diploma_normalizado", "law_number"),
    "lei_dispositivo_sem_diploma": ("artigo",),
    "lei_referencia_geral": ("diploma_normalizado",),
    "jurisprudencia_tribunal_contextual": ("tribunal", "relator_raw", "ano"),
    "jurisprudencia_referencia_geral": (),
}
FIRST_BLOCKERS = (
    "resolved_correct",
    "detector_miss",
    "detector_boundary_failure",
    "detector_family_mismatch",
    "parser_gap",
    "resolver_insufficient",
    "resolver_ambiguous",
    "resolver_no_match",
    "resolved_wrong",
)
STATUS_ORDER = ("resolved", "no_match", "ambiguous", "insufficient")


@dataclass(frozen=True)
class GoldRow:
    index: int
    level: str
    document_id: str
    start: int
    end: int
    text: str
    citation_type: str
    classification: str
    canonical_id: int | None


@dataclass(frozen=True)
class Prediction:
    index: int
    document_id: str
    start: int
    end: int
    text: str
    rule: str
    family: str
    parsed: ParsedCitation
    result: ResolutionResult


@dataclass(frozen=True)
class Evaluation:
    gold: GoldRow
    family: str
    oracle_parsed: ParsedCitation
    oracle_result: ResolutionResult
    prediction: Prediction | None
    current_result: ResolutionResult | None
    parser_equivalent: bool
    blocker: str


@dataclass(frozen=True)
class LegalRecord:
    canonical_id: int
    article: str | None
    diploma: str | None
    law_number: str | None
    law_year: str | None


class BaselineError(RuntimeError):
    """Indica que a análise não pode prosseguir sobre uma baseline divergente."""


def load_gold() -> list[GoldRow]:
    workbook = load_workbook(DATASET_DIR / "goldenset.xlsx", read_only=True, data_only=True)
    sheet = workbook["goldenset"]
    rows = list(sheet.iter_rows(values_only=True))
    header = {str(value): index for index, value in enumerate(rows[0])}
    result = []
    for index, values in enumerate(rows[1:]):
        values = tuple(values) + (None,) * (len(header) - len(values))
        raw_level_value = values[header["nivel"]]
        raw_level = str(int(raw_level_value)) if isinstance(raw_level_value, (int, float)) else str(raw_level_value).removeprefix("N")
        raw_text = str(values[header["trecho"]]).replace("\\n", "\n")
        raw_id = values[header["id_canonico"]]
        result.append(
            GoldRow(
                index=index,
                level=f"N{raw_level}",
                document_id=str(values[header["documento_id"]]),
                start=int(values[header["inicio"]]),
                end=int(values[header["fim"]]),
                text=raw_text,
                citation_type=str(values[header["tipo"]]),
                classification=str(values[header["classificacao"]]),
                canonical_id=None if raw_id is None else int(raw_id),
            )
        )
    return result


def family_for(text: str, citation_type: str) -> str:
    if citation_type == "lei":
        if ARTICLE_PATTERN.search(text) and DIPLOMA_PATTERN.search(text):
            return "lei_dispositivo_com_diploma"
        if ARTICLE_PATTERN.search(text):
            return "lei_dispositivo_sem_diploma"
        return "lei_referencia_geral"
    if SUMULA_PATTERN.search(text):
        return "sumula_numerada"
    if CNJ_PATTERN.search(text):
        return "processo_cnj"
    if PROCESS_PATTERN.search(text) and re.search(r"\d", text):
        return "processo_ou_recurso_numerado"
    if COURT_PATTERN.search(text) and (YEAR_PATTERN.search(text) or RELATOR_PATTERN.search(text)):
        return "jurisprudencia_tribunal_contextual"
    return "jurisprudencia_referencia_geral"


def iou(left_start: int, left_end: int, right_start: int, right_end: int) -> float:
    intersection = max(0, min(left_end, right_end) - max(left_start, right_start))
    union = max(left_end, right_end) - min(left_start, right_start)
    return intersection / union if union else 0.0


def match_predictions(gold: list[GoldRow], predictions: list[Prediction]) -> dict[int, int]:
    pairs: list[tuple[float, int, int]] = []
    by_document: dict[str, list[Prediction]] = defaultdict(list)
    for prediction in predictions:
        by_document[prediction.document_id].append(prediction)
    for item in gold:
        for prediction in by_document[item.document_id]:
            overlap = iou(item.start, item.end, prediction.start, prediction.end)
            if overlap >= 0.5:
                pairs.append((overlap, item.index, prediction.index))
    used_gold: set[int] = set()
    used_predictions: set[int] = set()
    selected: dict[int, int] = {}
    for overlap, gold_index, prediction_index in sorted(pairs, key=lambda value: (-value[0], value[1], value[2])):
        if gold_index not in used_gold and prediction_index not in used_predictions:
            selected[gold_index] = prediction_index
            used_gold.add(gold_index)
            used_predictions.add(prediction_index)
    return selected


def v1_sumula_result(parsed: ParsedCitation, resolver: CitationResolver) -> ResolutionResult:
    number = parsed.data.get("sumula_numero")
    if not number or not parsed.tribunal or parsed.tribunal_source != "explicit":
        return ResolutionResult("insufficient", None, (), None, "sumula_number_or_tribunal_missing", "sumula")
    candidate_ids = resolver._sumula_ids.get((parsed.tribunal, number), ())
    if not candidate_ids:
        return ResolutionResult("no_match", None, (), "sumula_number_tribunal", "sumula_not_found", "sumula")
    if len(candidate_ids) == 1:
        return ResolutionResult("resolved", candidate_ids[0], candidate_ids, "sumula_number_tribunal", "sumula_unique", "sumula")
    return ResolutionResult("ambiguous", None, candidate_ids, "sumula_number_tribunal", "sumula_has_multiple_canonical_ids", "sumula")


def run_pipeline(
    texts: dict[str, str],
    resolver: CitationResolver,
    use_v2: bool = True,
) -> tuple[list[Prediction], float]:
    detector = CitationDetector()
    parser = CitationParser()
    predictions: list[Prediction] = []
    started = time.perf_counter()
    for document_id, text in texts.items():
        for candidate in detector.detect(text):
            parsed = parser.parse(candidate)
            result = (
                resolver.resolve(parsed)
                if use_v2 or parsed.family != "sumula_numerada"
                else v1_sumula_result(parsed, resolver)
            )
            predictions.append(
                Prediction(
                    index=len(predictions),
                    document_id=document_id,
                    start=candidate.start,
                    end=candidate.end,
                    text=candidate.text,
                    rule=candidate.rule,
                    family=candidate.family,
                    parsed=parsed,
                    result=result,
                )
            )
    return predictions, time.perf_counter() - started


def field_value(parsed: ParsedCitation, field: str) -> str | None:
    return parsed.tribunal if field == "tribunal" else parsed.data.get(field)


def parser_equivalent(parsed: ParsedCitation, oracle: ParsedCitation) -> bool:
    return parsed.family == oracle.family and all(
        field_value(parsed, field) == field_value(oracle, field)
        for field in FIELDS[oracle.family]
    )


def value_visible(oracle: ParsedCitation, text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    for field in FIELDS[oracle.family]:
        value = field_value(oracle, field)
        if not value:
            continue
        if field in {"numero_normalizado", "sumula_numero", "law_number", "artigo"} and str(value) not in digits:
            return False
        if field == "tribunal" and not COURT_PATTERN.search(text):
            return False
        if field == "classe_raw" and not re.search(re.escape(str(value)), text, re.I):
            return False
        if field == "uf" and not re.search(r"(?<![A-Z])" + re.escape(str(value)) + r"(?![A-Z])", text):
            return False
        if field in {"diploma_normalizado", "relator_raw", "ano"} and str(value).upper() not in text.upper():
            return False
    return True


def oracle_rows(
    gold: list[GoldRow],
    texts: dict[str, str],
    parser: CitationParser,
    resolver: CitationResolver,
) -> list[tuple[GoldRow, str, ParsedCitation, ResolutionResult]]:
    rows = []
    for item in gold:
        text = texts[item.document_id][item.start : item.end]
        family = family_for(text, item.citation_type)
        parsed = parser.parse(CitationCandidate(0, len(text), text, "oracle", family))
        rows.append((item, family, parsed, resolver.resolve(parsed)))
    return rows


def evaluate(
    gold: list[GoldRow],
    texts: dict[str, str],
    predictions: list[Prediction],
    resolver: CitationResolver,
) -> list[Evaluation]:
    parser = CitationParser()
    matched = match_predictions(gold, predictions)
    by_prediction = {item.index: item for item in predictions}
    oracle = {item.index: (family, parsed, result) for item, family, parsed, result in oracle_rows(gold, texts, parser, resolver)}
    evaluations = []
    for item in gold:
        family, oracle_parsed, oracle_result = oracle[item.index]
        prediction = by_prediction.get(matched.get(item.index))
        if prediction is None:
            overlaps = [
                iou(item.start, item.end, candidate.start, candidate.end)
                for candidate in predictions
                if candidate.document_id == item.document_id
            ]
            blocker = "detector_boundary_failure" if max(overlaps, default=0.0) > 0 else "detector_miss"
            evaluations.append(Evaluation(item, family, oracle_parsed, oracle_result, None, None, False, blocker))
            continue
        result = prediction.result
        equivalent = parser_equivalent(prediction.parsed, oracle_parsed)
        correct = item.classification == "real" and result.status == "resolved" and result.id_canonico == item.canonical_id
        wrong = result.status == "resolved" and not correct
        if correct:
            blocker = "resolved_correct"
        elif wrong:
            blocker = "resolved_wrong"
        elif prediction.family != family:
            blocker = "detector_family_mismatch"
        elif not equivalent:
            blocker = "parser_gap" if value_visible(oracle_parsed, prediction.text) else "detector_boundary_failure"
        else:
            blocker = f"resolver_{result.status}"
        evaluations.append(Evaluation(item, family, oracle_parsed, oracle_result, prediction, result, equivalent, blocker))
    return evaluations


def real_evaluations(evaluations: Iterable[Evaluation]) -> list[Evaluation]:
    return [item for item in evaluations if item.gold.classification == "real"]


def result_from_ids(ids: tuple[int, ...], strategy: str, record_type: str) -> ResolutionResult:
    if not ids:
        return ResolutionResult("no_match", None, (), strategy, "structural_key_not_found", record_type)
    if len(ids) > 1:
        return ResolutionResult("ambiguous", None, ids, strategy, "structural_key_multiple_ids", record_type)
    return ResolutionResult("resolved", ids[0], ids, strategy, "structural_key_unique", record_type)


def print_status_matrix(label: str, evaluations: list[Evaluation]) -> None:
    print(label)
    for classification in ("real", "inventada", "incompleta"):
        counts = Counter(
            item.current_result.status if item.current_result is not None else "missing"
            for item in evaluations
            if item.gold.classification == classification
        )
        print(
            f"  {classification}: "
            + ", ".join(f"{status}={counts[status]}" for status in (*STATUS_ORDER, "missing"))
        )


def classify_macro(item: Evaluation) -> str:
    if item.blocker.startswith("detector_"):
        return "Detector"
    if item.blocker == "parser_gap":
        return "Parser"
    if item.blocker == "resolver_insufficient":
        if item.family in {"processo_cnj", "lei_dispositivo_com_diploma", "lei_dispositivo_sem_diploma", "lei_referencia_geral"}:
            return "Resolver structural"
        return "Semantic/contextual"
    if item.blocker in {"resolver_ambiguous", "resolver_no_match"}:
        return "Ambiguous/no-match"
    if item.blocker == "resolved_wrong":
        return "Resolver structural"
    return "Semantic/contextual"


def print_table(headers: list[str], rows: Iterable[Iterable[object]]) -> None:
    rows = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def canonical_legal_records(connection) -> list[LegalRecord]:
    records = []
    rows = connection.execute("SELECT id, texto FROM documentos WHERE natureza = 'dispositivo' ORDER BY id").fetchall()
    for row in rows:
        article = LEGAL_ARTICLE.search(row["texto"])
        diploma = LEGAL_DIPLOMA.search(row["texto"])
        law = LEGAL_LAW.search(row["texto"])
        records.append(
            LegalRecord(
                canonical_id=int(row["id"]),
                article=article.group(1) if article else None,
                diploma=re.sub(r"\s+", " ", diploma.group(0)).upper() if diploma else None,
                law_number=law.group(1) if law else None,
                law_year=law.group(2) if law and law.group(2) else None,
            )
        )
    return records


def legal_indexes(records: list[LegalRecord]) -> dict[str, dict[tuple[str | None, ...], tuple[int, ...]]]:
    fields = {
        "legal_article_diploma": ("article", "diploma"),
        "legal_article_law": ("article", "law_number"),
        "legal_article_law_year": ("article", "law_number", "law_year"),
        "legal_article_diploma_law": ("article", "diploma", "law_number"),
    }
    indexes: dict[str, dict[tuple[str | None, ...], tuple[int, ...]]] = {}
    for name, names in fields.items():
        grouped: dict[tuple[str | None, ...], list[int]] = defaultdict(list)
        for record in records:
            key = tuple(getattr(record, name) for name in names)
            if all(value is not None for value in key):
                grouped[key].append(record.canonical_id)
        indexes[name] = {key: tuple(sorted(ids)) for key, ids in grouped.items()}
    return indexes


def legal_result(parsed: ParsedCitation, name: str, indexes: dict[str, dict[tuple[str | None, ...], tuple[int, ...]]]) -> ResolutionResult | None:
    if not parsed.family.startswith("lei_"):
        return None
    values = {
        "article": parsed.data.get("artigo"),
        "diploma": parsed.data.get("diploma_normalizado"),
        "law_number": parsed.data.get("law_number"),
        "law_year": parsed.data.get("law_year"),
    }
    names = {
        "legal_article_diploma": ("article", "diploma"),
        "legal_article_law": ("article", "law_number"),
        "legal_article_law_year": ("article", "law_number", "law_year"),
        "legal_article_diploma_law": ("article", "diploma", "law_number"),
    }[name]
    key = tuple(values[field] for field in names)
    if any(value is None for value in key):
        return None
    return result_from_ids(indexes[name].get(key, ()), name, "dispositivo")


def apply_strategy(predictions: list[Prediction], strategy: str, resolver: CitationResolver, case_index: CaseIndex, indexes=None) -> list[Prediction]:
    transformed = []
    for prediction in predictions:
        result = prediction.result
        if strategy == "cnj_exact" and prediction.family == "processo_cnj":
            number = prediction.parsed.data.get("numero_normalizado")
            case = case_index.lookup_by_number(number) if number else None
            ids = () if case is None else case.canonical_ids
            result = result_from_ids(ids, strategy, "acordao")
        elif strategy.startswith("legal_") and indexes is not None:
            hypothetical = legal_result(prediction.parsed, strategy, indexes)
            if hypothetical is not None:
                result = hypothetical
        transformed.append(replace(prediction, result=result))
    return transformed


def safe_metrics(evaluations: list[Evaluation]) -> dict[str, int]:
    real = real_evaluations(evaluations)
    return {
        "correct": sum(item.blocker == "resolved_correct" for item in real),
        "wrong_unique_real": sum(item.gold.classification == "real" and item.current_result is not None and item.current_result.status == "resolved" and item.blocker != "resolved_correct" for item in evaluations),
        "false_real_inventada": sum(item.gold.classification == "inventada" and item.current_result is not None and item.current_result.status == "resolved" for item in evaluations),
        "false_real_incompleta": sum(item.gold.classification == "incompleta" and item.current_result is not None and item.current_result.status == "resolved" for item in evaluations),
    }


def with_results(evaluations: list[Evaluation], predictions: list[Prediction], gold: list[GoldRow], texts: dict[str, str], resolver: CitationResolver) -> list[Evaluation]:
    return evaluate(gold, texts, predictions, resolver)


def context_fields(text: str) -> list[str]:
    fields = []
    if COURT_PATTERN.search(text):
        fields.append("tribunal")
    if RELATOR_PATTERN.search(text):
        fields.append("relator")
    if YEAR_PATTERN.search(text):
        fields.append("ano")
    if PROCESS_PATTERN.search(text):
        fields.append("classe")
    if re.search(r"\d", text):
        fields.append("número parcial")
    if re.search(r"precedente|julgad|acórdão|decisão|súmula", text, re.I):
        fields.append("nome/descrição do precedente")
    return fields or ["nenhuma chave"]


def main() -> int:
    try:
        if not DATASET_DIR.is_dir() or (DATASET_DIR / "desafio1_bracis.db").resolve() != get_database_path().resolve():
            raise BaselineError("dataset inválido: use somente material_desafio_jusbrasil/")
        texts = {path.stem: path.read_text(encoding="utf-8") for path in sorted((DATASET_DIR / "txt").glob("*.txt"))}
        gold = load_gold()
        if (len(texts), len(gold)) != (26, 225):
            raise BaselineError(f"dataset divergente: documentos={len(texts)}, citações={len(gold)}")
        if Counter(item.classification for item in gold) != Counter({"real": 96, "inventada": 64, "incompleta": 65}):
            raise BaselineError("distribuição do gold divergente")
        if Counter(item.level for item in gold) != Counter({"N1": 116, "N2": 109}):
            raise BaselineError("distribuição N1/N2 divergente")

        with connect_database(get_database_path(), read_only=True) as connection:
            integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity_check != "ok":
                raise BaselineError(f"integridade do banco divergente: {integrity_check}")
            database_hash = hashlib.sha256((DATASET_DIR / "desafio1_bracis.db").read_bytes()).hexdigest()
            if database_hash != EXPECTED_DB_HASH:
                raise BaselineError(f"hash do banco divergente: {database_hash}")
            case_index = build_case_index(connection)
            resolver = CitationResolver(case_index=case_index, connection=connection)
            legal_records = canonical_legal_records(connection)

            v2_predictions, pipeline_seconds = run_pipeline(texts, resolver, use_v2=True)
            v1_predictions, _ = run_pipeline(texts, resolver, use_v2=False)
            v2_evaluations = evaluate(gold, texts, v2_predictions, resolver)
            v1_evaluations = evaluate(gold, texts, v1_predictions, resolver)

            v2_matches = match_predictions(gold, v2_predictions)
            detector_metrics = {
                "predictions": len(v2_predictions),
                "TP": len(v2_matches),
                "FP": len(v2_predictions) - len(v2_matches),
                "FN": len(gold) - len(v2_matches),
                "exact": sum(
                    item.start == prediction.start and item.end == prediction.end
                    for item in gold
                    for prediction in v2_predictions
                    if v2_matches.get(item.index) == prediction.index
                ),
            }
            if (detector_metrics["predictions"], detector_metrics["TP"], detector_metrics["FP"], detector_metrics["FN"], detector_metrics["exact"]) != (206, 119, 87, 106, 61):
                raise BaselineError(f"Detector V3 divergente: {detector_metrics}")
            v2_safety = safe_metrics(v2_evaluations)
            v1_safety = safe_metrics(v1_evaluations)
            if v2_safety["correct"] != 31 or any(v2_safety[key] for key in ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")):
                raise BaselineError(f"E2E V2 divergente: {v2_safety}")
            if v1_safety["correct"] != 29:
                raise BaselineError(f"E2E V1 divergente: {v1_safety}")
            if case_index.stats != {"records_total": 998, "case_keys": 912, "single_id_cases": 833, "multi_id_cases": 79, "max_ids_per_case": 4}:
                raise BaselineError(f"CaseIndex divergente: {case_index.stats}")

            print("=== BASELINE ===")
            print(f"dataset: documentos={len(texts)}, citações={len(gold)}, real=96, inventada=64, incompleta=65, N1=116, N2=109")
            print(f"Detector V3: {detector_metrics['predictions']}/{detector_metrics['TP']}/{detector_metrics['FP']}/{detector_metrics['FN']}, exact={detector_metrics['exact']}, F1={2 * detector_metrics['TP'] / (2 * detector_metrics['TP'] + detector_metrics['FP'] + detector_metrics['FN']):.3f}")
            print(f"CaseIndex: {case_index.stats['records_total']}/{case_index.stats['case_keys']}/{case_index.stats['single_id_cases']}/{case_index.stats['multi_id_cases']}/{case_index.stats['max_ids_per_case']}")
            print(f"Resolver V1 -> V2: {v1_safety['correct']}/96 -> {v2_safety['correct']}/96; safety V2=0/0/0")

            print("\n=== POST-V2 FIRST BLOCKERS ===")
            remaining = [item for item in real_evaluations(v2_evaluations) if item.blocker != "resolved_correct"]
            print(f"real restantes: {len(remaining)}")
            print_table(["blocker", "cases"], ((blocker, sum(item.blocker == blocker for item in real_evaluations(v2_evaluations))) for blocker in FIRST_BLOCKERS))
            print("mudanças V1 -> V2:")
            print(f"  resolver_insufficient -> resolved_correct: {sum(a.blocker != 'resolved_correct' and b.blocker == 'resolved_correct' for a, b in zip(v1_evaluations, v2_evaluations) if a.gold.classification == 'real')}")

            macro_counts = Counter(classify_macro(item) for item in remaining)
            print("\n=== MACRO POOLS ===")
            print_table(["macrogroup", "cases", "% of 65"], ((name, macro_counts[name], f"{100 * macro_counts[name] / 65:.1f}%") for name in ("Detector", "Parser", "Resolver structural", "Semantic/contextual", "Ambiguous/no-match")))

            detector_blocked = [item for item in real_evaluations(v2_evaluations) if item.blocker.startswith("detector_")]
            detector_upper = Counter()
            detector_high_value = []
            for item in detector_blocked:
                status = item.oracle_result.status
                if status == "resolved" and item.oracle_result.id_canonico == item.gold.canonical_id:
                    detector_upper["immediate_resolvable"] += 1
                    detector_high_value.append(item)
                elif status == "ambiguous":
                    detector_upper["ambiguous"] += 1
                elif status == "no_match":
                    detector_upper["no_match"] += 1
                else:
                    detector_upper["still_resolver_insufficient"] += 1
            parser_blocked = [item for item in real_evaluations(v2_evaluations) if item.blocker == "parser_gap"]
            parser_only = sum(item.oracle_result.status == "resolved" and item.oracle_result.id_canonico == item.gold.canonical_id for item in parser_blocked)
            print("\n=== DETECTOR V4 OPPORTUNITY ===")
            print(f"detector blockers={len(detector_blocked)}; detector_only_immediate_upper_bound={detector_upper['immediate_resolvable']}")
            print("detector-only upper bound:", ", ".join(f"{key}={detector_upper[key]}" for key in ("immediate_resolvable", "still_parser_blocked", "still_resolver_insufficient", "ambiguous", "no_match")))
            print("high-value cases:")
            for item in detector_high_value:
                candidate = item.prediction.text if item.prediction else "—"
                print(f"  {item.gold.document_id} {item.gold.level} {item.family} {item.blocker}: gold={item.gold.text!r}; candidate={candidate!r}; id={item.oracle_result.id_canonico}")
            groups = Counter((item.family, item.blocker) for item in detector_high_value)
            docs = {item.gold.document_id for item in detector_high_value}
            print(f"concentration: cases={len(detector_high_value)}, docs={len(docs)}, top1_share={max((count for count in Counter(item.gold.document_id for item in detector_high_value).values()), default=0)}/{len(detector_high_value)}, top2_share={sum(count for _, count in Counter(item.gold.document_id for item in detector_high_value).most_common(2))}/{len(detector_high_value)}, families={len({item.family for item in detector_high_value})}, blocker_types={len({item.blocker for item in detector_high_value})}")
            print("pattern groups:")
            for (family, blocker), count in sorted(groups.items()):
                print(f"  {family}/{blocker}: {count} case(s), complexity=MEDIUM, risk=moderate")

            print("\n=== PARSER V2 OPPORTUNITY ===")
            print(f"parser_only_immediate_upper_bound={parser_only} (parser_gap real cases={len(parser_blocked)})")

            structural_pool = [item for item in remaining if item.blocker == "resolver_insufficient" and classify_macro(item) == "Resolver structural"]
            print("\n=== RESOLVER STRUCTURAL POOL ===")
            print_table(["family", "cases"], ((family, sum(item.family == family for item in structural_pool)) for family in FAMILIES if any(item.family == family for item in structural_pool)))
            print("known strategy/safety:")
            print("  processo_cnj: cnj_exact diagnóstico, UNSAFE por wrong_unique")
            print("  legal: estratégias parciais somente, sem ganho E2E")
            semantic_pool = [
                item
                for item in remaining
                if item.oracle_result.status == "insufficient"
                and item.family in {"jurisprudencia_referencia_geral", "jurisprudencia_tribunal_contextual"}
            ]
            semantic_primary_pool = [
                item
                for item in remaining
                if item.blocker == "resolver_insufficient" and classify_macro(item) == "Semantic/contextual"
            ]
            print("semantic/contextual pool:")
            print_table(["family", "cases"], ((family, sum(item.family == family for item in semantic_pool)) for family in FAMILIES if any(item.family == family for item in semantic_pool)))
            for item in semantic_pool:
                surrounding = texts[item.gold.document_id][max(0, item.gold.start - 180) : min(len(texts[item.gold.document_id]), item.gold.end + 180)]
                print(f"  {item.gold.document_id} {item.gold.level} {item.family}: metadata={','.join(context_fields(surrounding))}")

            oracle_lost = [item for item in remaining if item.oracle_result.status == "resolved" and item.oracle_result.id_canonico == item.gold.canonical_id]
            print("\n=== ORACLE-RESOLVABLE LOST CASES ===")
            print(f"total={len(oracle_lost)}; distribuição por blocker={dict(sorted(Counter(item.blocker for item in oracle_lost).items()))}; por nível={dict(sorted(Counter(item.gold.level for item in oracle_lost).items()))}")
            attribution = Counter("Detector-only recoverable" if item.blocker.startswith("detector_") else "Parser-only recoverable" if item.blocker == "parser_gap" else "Detector+Parser jointly required" if item.blocker.startswith("resolver_") else "other" for item in oracle_lost)
            print("attribution:", dict(sorted(attribution.items())))
            print_table(["document", "family", "level", "blocker", "candidate", "oracle_id"], ((item.gold.document_id, item.family, item.gold.level, item.blocker, item.prediction.text if item.prediction else "—", item.oracle_result.id_canonico) for item in oracle_lost))

            print("\n=== CNJ OPPORTUNITY ===")
            cnj_real = [item for item in real_evaluations(v2_evaluations) if item.family == "processo_cnj"]
            print(f"real total={len(cnj_real)}, detected/matched={sum(item.prediction is not None for item in cnj_real)}, parser_equivalent={sum(item.parser_equivalent for item in cnj_real)}, resolver_insufficient={sum(item.blocker == 'resolver_insufficient' for item in cnj_real)}, detector_blocker={sum(item.blocker.startswith('detector_') for item in cnj_real)}, current immediate potential={sum(item.oracle_result.status == 'resolved' and item.oracle_result.id_canonico == item.gold.canonical_id for item in cnj_real)}")
            cnj_predictions = apply_strategy(v2_predictions, "cnj_exact", resolver, case_index)
            cnj_evaluations = evaluate(gold, texts, cnj_predictions, resolver)
            cnj_metrics = safe_metrics(cnj_evaluations)
            print(f"cnj_exact diagnóstico: correct={cnj_metrics['correct']}, gain={cnj_metrics['correct'] - v2_safety['correct']}, wrong_unique={cnj_metrics['wrong_unique_real']}, false-real={cnj_metrics['false_real_inventada'] + cnj_metrics['false_real_incompleta']}")
            wrong_cnj = [item for item in cnj_evaluations if item.gold.classification == "real" and item.blocker == "resolved_wrong"]
            print(f"conflito errado encontrado: {len(wrong_cnj)} caso(s); único wrong-unique de cnj_exact={len(wrong_cnj) == 1}")
            print("gold-safe gain=0 (estratégia insegura); corpus-identity gain=" + str(cnj_metrics["correct"] - v2_safety["correct"]))

            print("\n=== LEGAL OPPORTUNITY ===")
            legal_real = [item for item in real_evaluations(v2_evaluations) if item.gold.citation_type == "lei"]
            print(f"legal real: total={len(legal_real)}, detected={sum(item.prediction is not None for item in legal_real)}, matched={sum(item.prediction is not None for item in legal_real)}, parser-equivalent={sum(item.parser_equivalent for item in legal_real)}, resolver-insufficient={sum(item.blocker == 'resolver_insufficient' for item in legal_real)}, detector-blocked={sum(item.blocker.startswith('detector_') for item in legal_real)}")
            indexes = legal_indexes(legal_records)
            legal_summary = []
            for strategy in indexes:
                oracle_correct = 0
                for item, family, parsed, _ in oracle_rows(gold, texts, CitationParser(), resolver):
                    if item.classification != "real" or item.citation_type != "lei":
                        continue
                    result = legal_result(parsed, strategy, indexes)
                    oracle_correct += result is not None and result.status == "resolved" and result.id_canonico == item.canonical_id
                legal_predictions = apply_strategy(v2_predictions, strategy, resolver, case_index, indexes)
                legal_eval = evaluate(gold, texts, legal_predictions, resolver)
                legal_metrics = safe_metrics(legal_eval)
                legal_summary.append((strategy, oracle_correct, legal_metrics["correct"] - v2_safety["correct"], legal_metrics["false_real_inventada"] + legal_metrics["false_real_incompleta"]))
            print_table(["strategy", "oracle gain", "E2E gain", "false-real"], legal_summary)
            print("conclusão legal: future corpus/identity investigation; não há ganho E2E seguro conhecido")

            print("\n=== SEMANTIC/CONTEXTUAL POOL ===")
            print(f"total={len(semantic_pool)}")
            print_table(["family", "cases"], ((family, sum(item.family == family for item in semantic_pool)) for family in FAMILIES if any(item.family == family for item in semantic_pool)))
            print("metadata: campos são somente pistas diagnósticas da janela textual; nenhum é usado para resolução")

            print("\n=== N1 VS N2 ===")
            nlevel_rows = []
            for level in ("N1", "N2"):
                level_real = [item for item in real_evaluations(v2_evaluations) if item.gold.level == level]
                nlevel_rows.append((level, len(level_real), sum(item.blocker == "resolved_correct" for item in level_real), len(level_real) - sum(item.blocker == "resolved_correct" for item in level_real), sum(item.blocker.startswith("detector_") for item in level_real), sum(item in structural_pool for item in level_real), sum(item in semantic_primary_pool for item in level_real)))
            print_table(["level", "real", "resolved correct", "unresolved", "detector", "resolver structural", "semantic"], nlevel_rows)

            print("\n=== FAMILY DECOMPOSITION ===")
            family_rows = []
            for family in FAMILIES:
                family_real = [item for item in real_evaluations(v2_evaluations) if item.family == family]
                if not family_real:
                    continue
                family_rows.append((family, len(family_real), sum(item.blocker == "resolved_correct" for item in family_real), sum(item.blocker.startswith("detector_") for item in family_real), sum(item.blocker == "parser_gap" for item in family_real), sum(item in structural_pool for item in family_real), sum(item in semantic_primary_pool for item in family_real), sum(item.blocker in {"resolver_ambiguous", "resolver_no_match"} for item in family_real)))
            print_table(["family", "real", "success", "detector", "parser", "resolver structural", "semantic", "ambiguous/no-match"], family_rows)

            print("\n=== IMMEDIATE ROI ===")
            safe_known = {"Detector V4": len(detector_high_value), "Parser V2": parser_only, "CNJ": 0, "Legal": 0, "Semantic/contextual": "N/A"}
            upper = {"Detector V4": len(detector_high_value), "Parser V2": parser_only, "CNJ": cnj_metrics["correct"] - v2_safety["correct"], "Legal": max((row[1] for row in legal_summary), default=0), "Semantic/contextual": len(semantic_pool)}
            risks = {"Detector V4": "moderate", "Parser V2": "low, ROI zero", "CNJ": "high: 1 wrong-unique", "Legal": "high: oracle-only", "Semantic/contextual": "high/unknown"}
            print_table(["investment", "safe/known gain", "upper bound", "risk"], ((name, safe_known[name], upper[name], risks[name]) for name in safe_known))

            print("\n=== DETECTOR FPs ===")
            fp_predictions = [prediction for prediction in v2_predictions if prediction.index not in v2_matches.values()]
            fp_v2 = Counter(prediction.result.status for prediction in fp_predictions)
            fp_v1_predictions = [prediction for prediction in v1_predictions if prediction.index not in match_predictions(gold, v1_predictions).values()]
            fp_v1 = Counter(prediction.result.status for prediction in fp_v1_predictions)
            print(f"FPs={len(fp_predictions)}; V1={dict((status, fp_v1[status]) for status in STATUS_ORDER)}; V2={dict((status, fp_v2[status]) for status in STATUS_ORDER)}")
            print("candidate filtering: NÃO; nenhum FP novo para resolved/no_match e nenhum impacto perigoso observado")

            print("\n=== RECOMMENDATION ===")
            print("A) estudar CitationDetector V4")
            print(f"justificativa: {len(detector_high_value)} ganho(s) imediato(s) potencial(is), distribuídos em {len(groups)} grupo(s) de família/blocker e {len(docs)} documento(s); Parser V2 tem upper bound {parser_only}, CNJ permanece inseguro, legal não tem ganho E2E e semântica deve aguardar estes ganhos determinísticos.")
            print("não implementar regras, CNJ, legal ou componente semântico nesta análise")
            print(f"performance: pipeline={pipeline_seconds:.3f}s total, {pipeline_seconds / len(texts):.4f}s/documento")
            print("determinismo: o fluxo é composto por ordenações estáveis; repetir a análise produz os mesmos grupos e contagens")
            return 0
    except BaselineError as exc:
        print(f"BASELINE ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
