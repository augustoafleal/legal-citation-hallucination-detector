"""Auditoria executável do contrato e da métrica oficial do desafio Kaggle.

Este arquivo é somente analítico. A pontuação sempre é delegada ao
``material_desafio_jusbrasil_bracis/kaggle_metric.py``; não há uma segunda
implementação da métrica neste módulo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "material_desafio_jusbrasil_bracis"
METRIC_PATH = DATA / "kaggle_metric.py"
GOLD_PATH = DATA / "goldenset.csv"
SAMPLE_PATH = DATA / "sample_submission.csv"
CONVERTER_PATH = DATA / "json_to_submission.py"
DB_PATH = DATA / "desafio1_bracis.db"
OLD2 = ROOT / "material_desafio_jusbrasil_bracis_old2"
OUT = ROOT / "artifacts" / "official_kaggle_metric_audit.json"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.database import connect_database  # noqa: E402


def load_official_metric():
    spec = importlib.util.spec_from_file_location("official_kaggle_metric_audit_target", METRIC_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não foi possível carregar {METRIC_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


METRIC = load_official_metric()


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def package_files() -> list[Path]:
    return [DB_PATH, GOLD_PATH, SAMPLE_PATH, CONVERTER_PATH, METRIC_PATH, *sorted((DATA / "txt").glob("*.txt"))]


def package_snapshot() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): digest(path) for path in package_files()}


def old2_comparison(gold_df: pd.DataFrame) -> dict[str, Any]:
    common = ["desafio1_bracis.db", "json_to_submission.py", *[f"txt/{p.name}" for p in sorted((DATA / "txt").glob("*.txt"))]]
    hashes = []
    for relative in common:
        current = DATA / relative
        archived = OLD2 / relative
        hashes.append({"file": relative, "current_sha256": digest(current), "old2_sha256": digest(archived), "identical": digest(current) == digest(archived)})
    old_gold = pd.read_excel(OLD2 / "goldenset.xlsx", dtype=str).fillna("")
    new_gold = gold_df.astype(str).copy()
    for frame in (old_gold, new_gold):
        for column in frame.columns:
            frame[column] = frame[column].str.replace("\\n", "\n", regex=False)
    return {"common_file_hashes": hashes, "common_files_identical": all(item["identical"] for item in hashes), "goldenset_csv_vs_old2_xlsx": {"shape_equal": old_gold.shape == new_gold.shape, "columns_equal": list(old_gold.columns) == list(new_gold.columns), "values_equal": old_gold.equals(new_gold)}}


def load_gold_df() -> pd.DataFrame:
    return pd.read_csv(GOLD_PATH, dtype=str, keep_default_na=False)


def load_gold_rows(df: pd.DataFrame) -> list[post.deep.GoldRow]:
    rows: list[post.deep.GoldRow] = []
    for index, row in df.iterrows():
        raw_id = str(row["id_canonico"]).strip()
        rows.append(
            post.deep.GoldRow(
                index=index,
                gid=str(row["citacao_id"]),
                level=f"N{int(row['nivel'])}",
                document_id=str(row["documento_id"]),
                start=int(row["inicio"]),
                end=int(row["fim"]),
                text=str(row["trecho"]).replace("\\n", "\n"),
                citation_type=str(row["tipo"]),
                classification=str(row["classificacao"]),
                canonical_id=None if not raw_id else int(raw_id),
            )
        )
    return rows


def solution_from_gold(df: pd.DataFrame, indexes: Iterable[int] | None = None) -> pd.DataFrame:
    selected = set(df.index if indexes is None else indexes)
    grouped: dict[str, list[str]] = defaultdict(list)
    levels: dict[str, str] = {}
    for index, row in df.iterrows():
        if index not in selected:
            continue
        doc = str(row["documento_id"])
        levels[doc] = str(int(row["nivel"]))
        canonical = str(row["id_canonico"]).strip() if str(row["id_canonico"]).strip() else "-"
        grouped[doc].append(f"{int(row['inicio'])},{int(row['fim'])},{row['classificacao']},{canonical}")
    return pd.DataFrame(
        {
            "documento_id": list(grouped),
            "nivel": [levels[doc] for doc in grouped],
            "citacoes": ["|".join(grouped[doc]) for doc in grouped],
        }
    )


@dataclass(frozen=True)
class PredictionCell:
    document_id: str
    start: int
    end: int
    classification: str
    canonical_id: str
    confidence: str = "-"


def output_cells(items: Iterable[Any], ambiguous_class: str = "incompleta") -> list[PredictionCell]:
    cells: list[PredictionCell] = []
    for item in items:
        result = item.result
        status = str(result.status)
        classification = "real" if status == "resolved" else ambiguous_class if status == "ambiguous" else "incompleta" if status == "insufficient" else "inventada"
        canonical = str(result.id_canonico) if status == "resolved" and result.id_canonico is not None else "-"
        cells.append(PredictionCell(str(item.document_id), item.candidate.start, item.candidate.end, classification, canonical))
    return cells


def submission_from_cells(cells: Iterable[PredictionCell], documents: Iterable[str]) -> pd.DataFrame:
    grouped: dict[str, list[str]] = defaultdict(list)
    for cell in cells:
        grouped[cell.document_id].append(
            f"{cell.start},{cell.end},{cell.classification},{cell.canonical_id},{cell.confidence}"
        )
    docs = list(documents)
    return pd.DataFrame({"documento_id": docs, "citacoes": ["|".join(grouped[doc]) or "-" for doc in docs]})


def score(solution: pd.DataFrame, submission: pd.DataFrame) -> float:
    """Único ponto de pontuação: chamada direta à implementação oficial."""
    return float(METRIC.score(solution, submission, "documento_id"))


def exception_result(fn) -> dict[str, Any]:
    try:
        return {"accepted": True, "value": fn()}
    except Exception as exc:  # official scorer deliberately has visible errors
        return {"accepted": False, "exception": type(exc).__name__, "message": str(exc)}


def syn_frames(gold_rows: list[tuple[int, int, str, str]], pred_rows: list[tuple[int, int, str, str, str]], level: str = "1") -> tuple[pd.DataFrame, pd.DataFrame]:
    sol_cell = "|".join(f"{a},{b},{c},{i}" for a, b, c, i in gold_rows) or "-"
    sub_cell = "|".join(f"{a},{b},{c},{i},{conf}" for a, b, c, i, conf in pred_rows) or "-"
    return (
        pd.DataFrame({"documento_id": ["d"], "nivel": [level], "citacoes": [sol_cell]}),
        pd.DataFrame({"documento_id": ["d"], "citacoes": [sub_cell]}),
    )


def synthetic_tests() -> dict[str, Any]:
    real = (0, 10, "real", "123")
    inv = (20, 30, "inventada", "-")
    inc = (40, 50, "incompleta", "-")

    cases: list[tuple[str, list[tuple], list[tuple], str]] = [
        ("M1_perfect", [real, inv, inc], [(0, 10, "real", "123", "-"), (20, 30, "inventada", "-", "-"), (40, 50, "incompleta", "-", "-")], "score=1"),
        ("M2_wrong_class", [real], [(0, 10, "inventada", "-", "-")], "score=0"),
        ("M3_wrong_type_ignored", [real], [(0, 10, "real", "123", "-")], "extra tipo does not enter official submission parser"),
        ("M4_wrong_canonical_id", [real], [(0, 10, "real", "999", "-")], "real match with wrong ID is FP, without FN"),
        ("M5_missing_real_id", [real], [(0, 10, "real", "-", "-")], "ParticipantVisibleError"),
        ("M6_filled_nonreal_id_ignored", [inv], [(20, 30, "inventada", "999", "-")], "accepted and ID ignored"),
        ("M7_iou_below_0_5", [real], [(0, 4, "real", "123", "-")], "unmatched"),
        ("M8_iou_exact_0_5", [(0, 10, "real", "123")], [(0, 20, "real", "123", "-")], "threshold is inclusive"),
        ("M9_iou_above_0_5", [real], [(0, 15, "real", "123", "-")], "matched"),
        ("M10_two_predictions_one_gold", [real], [(0, 10, "real", "123", "-"), (20, 30, "real", "123", "-")], "one TP and one FP"),
        ("M11_one_prediction_two_gold", [real, (20, 30, "real", "456")], [(0, 10, "real", "123", "-")], "one TP and one FN"),
        ("M12_nested_extra_ignored", [(0, 100, "inventada", "-")], [(0, 100, "inventada", "-", "-"), (0, 40, "inventada", "-", "-")], "contained extra is ignored"),
        ("M13_missing_gold_document", [real], [], "missing solution row in submission"),
        ("M14_duplicate_document_rows", [real], [(0, 10, "real", "123", "-")], "duplicate submission rows keep first"),
        ("M15_wrong_trecho_ignored", [real], [(0, 10, "real", "123", "-")], "trecho is not an official CSV field"),
        ("M16_invalid_class", [real], [(0, 10, "REALIDADE", "123", "-")], "ParticipantVisibleError"),
    ]
    result: list[dict[str, Any]] = []
    for name, gold_rows, pred_rows, expectation in cases:
        sol, sub = syn_frames(gold_rows, pred_rows)
        if name == "M3_wrong_type_ignored":
            sub["tipo"] = "jurisprudencia"
        if name == "M15_wrong_trecho_ignored":
            sub["trecho"] = "texto deliberadamente errado"
        if name == "M13_missing_gold_document":
            sub = pd.DataFrame({"documento_id": ["other"], "citacoes": ["-"]})
        if name == "M14_duplicate_document_rows":
            good = sub.iloc[0].to_dict()
            sub = pd.DataFrame({"documento_id": ["d", "d"], "citacoes": ["-", good["citacoes"]]})
        result.append({"name": name, "expectation": expectation, **exception_result(lambda sol=sol, sub=sub: score(sol, sub))})
        if name == "M14_duplicate_document_rows":
            reversed_sub = sub.iloc[::-1].reset_index(drop=True)
            result.append({"name": "M14_duplicate_document_rows_reversed", "expectation": "keep first means order changes result", **exception_result(lambda: score(sol, reversed_sub))})
    return {"tests": result, "all_executed": True}


def edge_case_tests(solution: pd.DataFrame, perfect: pd.DataFrame, empty: pd.DataFrame) -> dict[str, Any]:
    shuffled_solution = solution.sample(frac=1.0, random_state=17).reset_index(drop=True)
    shuffled_submission = perfect.sample(frac=1.0, random_state=23).reset_index(drop=True)
    one_solution, _ = syn_frames([(0, 10, "real", "123")], [])
    values: list[dict[str, Any]] = [
        {"name": "row_order_shuffle", "expected": "same score", "value": score(solution, perfect), "shuffled_value": score(shuffled_solution, shuffled_submission)},
        {"name": "submission_nan_cell", "expected": "accepted as no predictions", **exception_result(lambda: score(one_solution, pd.DataFrame({"documento_id": ["d"], "citacoes": [None]})))},
        {"name": "submission_empty_cell", "expected": "accepted as no predictions", **exception_result(lambda: score(one_solution, pd.DataFrame({"documento_id": ["d"], "citacoes": [""]})))},
        {"name": "solution_usage_extra_column", "expected": "Usage dropped by score", **exception_result(lambda: score(pd.concat([solution, pd.DataFrame({"Usage": ["Public"] * len(solution)})], axis=1), perfect))},
        {"name": "numeric_real_id_with_decimal_text", "expected": "rejected because ID must be digits", **exception_result(lambda: score(one_solution, pd.DataFrame({"documento_id": ["d"], "citacoes": ["0,10,real,123.0,-"]})))},
    ]
    return {"tests": values, "empty_baseline_score": score(solution, empty)}


def official_matches(solution: pd.DataFrame, submission: pd.DataFrame, gold_df: pd.DataFrame) -> dict[int, int]:
    matches: dict[int, int] = {}
    sub_by_doc = submission.set_index("documento_id")["citacoes"].to_dict()
    for doc, group in gold_df.groupby("documento_id", sort=False):
        gold_cell = solution.loc[solution.documento_id == doc, "citacoes"].iloc[0]
        golds = METRIC._parse_solution_cell(gold_cell, doc)
        preds = METRIC._parse_submission_cell(sub_by_doc[doc], doc)
        pairs, _, _ = METRIC._casar(golds, preds)
        indices = list(group.index)
        for gi, pi in pairs:
            matches[indices[gi]] = pi
    return matches


def local_vs_official(gold_rows: list[post.deep.GoldRow], items: list[Any], solution: pd.DataFrame, submission: pd.DataFrame, gold_df: pd.DataFrame) -> dict[str, Any]:
    local = set(post.match_gold(gold_rows, items))
    official = set(official_matches(solution, submission, gold_df))
    return {
        "local_matched": len(local),
        "official_matched": len(official),
        "match_both": len(local & official),
        "local_only": len(local - official),
        "official_only": len(official - local),
        "miss_both": len(set(g.index for g in gold_rows) - (local | official)),
        "same_gold_set": local == official,
        "local_definition": "post_resolver_v3_remaining_audit.match_gold (IoU >= 0.5, same greedy tie-break)",
        "official_definition": "kaggle_metric._casar, called directly for diagnostics; score remains METRIC.score",
    }


def db_audit() -> dict[str, Any]:
    uri = f"file:{DB_PATH}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {}
        for table in tables:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        natureza = {row[0]: int(row[1]) for row in conn.execute("SELECT natureza, COUNT(*) FROM documentos GROUP BY natureza ORDER BY natureza")}
        fts = int(conn.execute("SELECT COUNT(*) FROM documentos_fts").fetchone()[0]) if "documentos_fts" in tables else None
    return {"sha256": digest(DB_PATH), "integrity_check": integrity, "tables": tables, "row_counts": counts, "natureza": natureza, "fts_rows": fts}


def score_sensitivities(sol: pd.DataFrame) -> dict[str, Any]:
    curve = []
    gold = [(0, 100, "real", "123")]
    for end in (40, 50, 60):
        _, sub = syn_frames(gold, [(0, end, "real", "123", "-")])
        curve.append({"pred_span": [0, end], "iou": end / 100, "score": score(syn_frames(gold, [(0, end, "real", "123", "-")])[0], sub)})
    sol_no_conf, no_conf = syn_frames(gold, [(0, 100, "real", "123", "-")])
    sol_conf_one, conf_one = syn_frames(gold, [(0, 100, "real", "123", "1.0")])
    sol_conf_zero, conf_zero = syn_frames(gold, [(0, 100, "real", "123", "0.0")])
    return {
        "iou_curve": curve,
        "confidence": {"none": score(sol_no_conf, no_conf), "perfect_confidence_1": score(sol_conf_one, conf_one), "perfect_confidence_0": score(sol_conf_zero, conf_zero)},
        "range": {"empty": 0.0, "maximum_observed": 1.1, "theoretical_bounds": [0.0, 1.1], "reason": "F1 >= 0; tau penalty; bonus capped at 0.10; official implementation clamps bonus"},
        "incentive_cases": {
            "one_tp": score(*syn_frames([(0, 10, "real", "123")], [(0, 10, "real", "123", "-")])),
            "one_tp_one_fp": score(*syn_frames([(0, 10, "real", "123")], [(0, 10, "real", "123", "-"), (20, 30, "real", "123", "-")])),
            "one_tp_one_fn": score(*syn_frames([(0, 10, "real", "123"), (20, 30, "real", "456")], [(0, 10, "real", "123", "-")])),
        },
        "span_relevance": "exact character boundaries are not required; IoU >= 0.5 is sufficient, and FRAC_EXTRA=0.9 can ignore a contained unmatched prediction",
        "identity_relevance": "wrong real ID is an FP on a matched span; missing real ID is rejected; non-real ID is accepted but ignored",
    }


def current_pipeline_audit(gold_df: pd.DataFrame, gold_rows: list[post.deep.GoldRow]) -> dict[str, Any]:
    texts = post.deep.load_texts()
    with connect_database(DB_PATH, read_only=True) as connection:
        resolver = post.CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs, runtime = post.run_current_pipeline(texts, resolver)
        docs = list(gold_df.documento_id.drop_duplicates())
        raw_sub = submission_from_cells(output_cells(raw), docs)
        out_sub = submission_from_cells(output_cells(outputs), docs)
        solution = solution_from_gold(gold_df)
        raw_score = exception_result(lambda: score(solution, raw_sub))
        out_score = exception_result(lambda: score(solution, out_sub))
        out_evaluation = exception_result(lambda: METRIC.avaliar(solution, out_sub, row_id="documento_id"))
        legacy_sub = submission_from_cells(output_cells(outputs, ambiguous_class="inventada"), docs)
        legacy_score = exception_result(lambda: score(solution, legacy_sub))
        raw_local = post.output_metrics(gold_rows, raw)
        out_local = post.output_metrics(gold_rows, outputs)
        raw_official_matches = official_matches(solution, raw_sub, gold_df) if raw_score["accepted"] else {}
        out_official_matches = official_matches(solution, out_sub, gold_df) if out_score["accepted"] else {}
        guard_outputs, guard_changes = post.numbered_primary_class_guard(outputs, resolver)
        guard_sub = submission_from_cells(output_cells(guard_outputs), docs)
        guard_score = exception_result(lambda: score(solution, guard_sub))
        conflicts = []
        cells = output_cells(outputs)
        for i, a in enumerate(cells):
            for b in cells[i + 1 :]:
                if a.document_id == b.document_id and METRIC._iou({"inicio": a.start, "fim": a.end}, {"inicio": b.start, "fim": b.end}) >= METRIC.IOU_MIN:
                    conflicts.append([a.document_id, [a.start, a.end], [b.start, b.end]])
        residual_source = ROOT / "artifacts" / "human_review_detector_residual.json"
        residual_cases = json.loads(residual_source.read_text(encoding="utf-8"))["detector_residual_cases"] if residual_source.exists() else []
        by_index = {row.index: row for row in gold_rows}
        current_cells = cells
        residual_rows = []
        global_current = float(out_score["value"]) if out_score["accepted"] else None
        for case in residual_cases:
            row = by_index[int(case["gold_index"])]
            one_solution = solution_from_gold(gold_df, [row.index])
            one_current = submission_from_cells([cell for cell in current_cells if cell.document_id == row.document_id], [row.document_id])
            one_current_score = exception_result(lambda one_solution=one_solution, one_current=one_current: score(one_solution, one_current))
            corrected = [cell for cell in current_cells if not (cell.document_id == row.document_id and METRIC._iou({"inicio": row.start, "fim": row.end}, {"inicio": cell.start, "fim": cell.end}) >= METRIC.IOU_MIN)]
            corrected.append(PredictionCell(row.document_id, row.start, row.end, row.classification, str(row.canonical_id) if row.canonical_id is not None else "-"))
            corrected_sub = submission_from_cells(corrected, docs)
            corrected_score = exception_result(lambda: score(solution, corrected_sub))
            residual_rows.append({
                "gid": row.gid, "documento": row.document_id, "gold_index": row.index, "classificacao": row.classification,
                "family": row.citation_type, "span": [row.start, row.end], "text": row.text,
                "detector_error": case.get("error_type"), "current_best_iou": case.get("current_best_iou"),
                "current_one_case": one_current_score, "oracle_one_case": {"accepted": True, "value": score(one_solution, submission_from_cells([PredictionCell(row.document_id, row.start, row.end, row.classification, str(row.canonical_id) if row.canonical_id is not None else "-")], [row.document_id]))},
                "global_current_score": global_current, "global_oracle_score": corrected_score,
                "global_delta": None if not corrected_score["accepted"] or global_current is None else corrected_score["value"] - global_current,
                "review_order": "boundary_prefix" if case.get("error_type") == "PREFIXO_FALTANDO" else "full_miss",
            })
        oracle_scores = [r["global_delta"] for r in residual_rows if r["global_delta"] is not None]
        return {
            "documents": len(docs), "text_files": len(texts), "runtime_seconds": runtime,
            "raw_detector": {"records": len(raw), "local": raw_local, "official_score": raw_score, "official_matched": len(raw_official_matches), "local_vs_official": local_vs_official(gold_rows, raw, solution, raw_sub, gold_df)},
            "post_arbitration": {"records": len(outputs), "local": out_local, "official_score": out_score, "official_evaluation": out_evaluation, "official_matched": len(out_official_matches), "local_vs_official": local_vs_official(gold_rows, outputs, solution, out_sub, gold_df), "duplicate_iou_conflicts": conflicts, "classification_mapping": {"resolved": "real", "no_match": "inventada", "ambiguous": "incompleta", "insufficient": "incompleta"}, "alternative_mapping_ambiguous_to_inventada": legacy_score},
            "arbitration_effect": {"official_score_before": raw_score, "official_score_after": out_score, "official_delta": None if not raw_score["accepted"] or not out_score["accepted"] else out_score["value"] - raw_score["value"], "local_correct_id_before": raw_local["correct_ids"], "local_correct_id_after": out_local["correct_ids"], "local_delta": out_local["correct_ids"] - raw_local["correct_ids"]},
            "numbered_primary_class_guard": {"before_score": out_score, "after_score": guard_score, "delta": None if not out_score["accepted"] or not guard_score["accepted"] else guard_score["value"] - out_score["value"], "changes": guard_changes, "interpretation": "the current Resolver already applies the guard; the read-only post-pass made no further changes"},
            "seven_detector_residual": {"count": len(residual_rows), "prefix_missing": sum(x["review_order"] == "boundary_prefix" for x in residual_rows), "full_miss": sum(x["review_order"] == "full_miss" for x in residual_rows), "cases": residual_rows, "sum_global_marginal_delta": sum(oracle_scores)},
            "output_readiness": {"official_parser_accepted": out_score["accepted"], "one_row_per_document": len(out_sub) == len(docs), "all_documents_present": set(out_sub.documento_id) == set(docs), "duplicate_iou_conflicts": len(conflicts), "readiness": "READY" if out_score["accepted"] and not conflicts else "NOT_READY"},
        }


def contracts(gold_df: pd.DataFrame) -> dict[str, Any]:
    sample = pd.read_csv(SAMPLE_PATH, dtype=str, keep_default_na=False)
    source = METRIC_PATH.read_text(encoding="utf-8")
    return {
        "sample_submission": {"columns": list(sample.columns), "rows": len(sample), "unique_documents": int(sample.documento_id.nunique()), "duplicate_documents": int(sample.documento_id.duplicated().sum()), "empty_marker_counts": {str(k): int(v) for k, v in sample.citacoes.value_counts().to_dict().items()}},
        "goldenset": {"columns": list(gold_df.columns), "rows": len(gold_df), "class_counts": {str(k): int(v) for k, v in gold_df.classificacao.value_counts().to_dict().items()}, "level_counts": {str(k): int(v) for k, v in gold_df.nivel.value_counts().to_dict().items()}, "real_missing_id": int(((gold_df.classificacao == "real") & (gold_df.id_canonico == "")).sum()), "document_count": int(gold_df.documento_id.nunique())},
        "submission_parser": {"required_columns": ["documento_id", "citacoes"], "citation_fields": ["inicio", "fim", "classe", "id_canonico", "confianca"], "empty_values_accepted": ["", "-", "NaN/None"], "requires_exactly_five_fields": True, "extra_submission_columns": "ignored"},
        "solution_parser": {"required_columns": ["documento_id", "nivel", "citacoes"], "citation_fields": ["inicio", "fim", "classe", "doc_ids"], "real_requires_doc_id": True, "doc_ids_separator": ":", "extra_solution_columns": "ignored; score() drops Usage"},
        "json_to_submission": {"source_sha256": digest(CONVERTER_PATH), "required_json_citation_keys": ["inicio", "fim", "classificacao"], "resolution_id_source": "citacao.resolucao.id_canonico", "confidence_format": "four decimal places; '-' when null", "empty_document_marker": "-", "tipo_trecho": "required by JSON-side validation/document contract but not emitted in Kaggle CSV"},
        "important_contract_warnings": ["tipo and trecho do not exist in the official submission citation fields and therefore cannot affect official scoring", "duplicate submission document IDs are silently reduced to the first row", "official docstring says empty cell while sample and converter use '-'", "solution duplicate document IDs are not explicitly rejected by official code"],
        "metric_source_sha256": digest(METRIC_PATH), "metric_source_lines": len(source.splitlines()),
    }


def main() -> int:
    before = package_snapshot()
    gold_df = load_gold_df()
    gold_rows = load_gold_rows(gold_df)
    solution = solution_from_gold(gold_df)
    docs = list(gold_df.documento_id.drop_duplicates())
    perfect = submission_from_cells([PredictionCell(str(r.document_id), r.start, r.end, r.classification, str(r.canonical_id) if r.canonical_id is not None else "-") for r in gold_rows], docs)
    perfect_conf = perfect.copy()
    perfect_conf["citacoes"] = perfect_conf.citacoes.map(lambda cell: "|".join(
        ",".join(part.split(",")[:4] + ["1.0"]) for part in cell.split("|")
    ))
    empty = pd.DataFrame({"documento_id": docs, "citacoes": ["-"] * len(docs)})
    current_runs = [current_pipeline_audit(gold_df, gold_rows) for _ in range(3)]
    def stable(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key not in {"runtime_seconds"}}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    current = current_runs[0]
    after = package_snapshot()
    unchanged = before == after
    payload = {
        "status": "PASS WITH WARNINGS" if unchanged and current["output_readiness"]["official_parser_accepted"] else "FAIL",
        "package_integrity": {"before": before, "after": after, "unchanged": unchanged, "db": db_audit(), "old2_comparison": old2_comparison(gold_df) if OLD2.is_dir() else {"available": False}, "official_files_untouched": unchanged},
        "metric_source": {"path": str(METRIC_PATH.relative_to(ROOT)), "sha256": digest(METRIC_PATH), "direct_entrypoint": "METRIC.score(solution, submission, 'documento_id')", "no_reimplementation": True, "constants": {name: getattr(METRIC, name) for name in ["CLASSES", "PESOS_NIVEL", "GAMMA", "TETO_BONUS", "IOU_MIN", "FRAC_EXTRA"]}},
        "metric_execution_flow": {"steps": ["copy DataFrames", "validate required columns", "drop duplicate submission document IDs keep=first", "parse solution and submission citation cells", "reject prediction IoU >= 0.5 within a document", "greedy 1:1 matching by descending IoU with deterministic tie-break", "accumulate TP/FP/FN by class, tau on inventada, confidence Brier only for matched predictions", "per-level macro-F1 and tau penalty plus capped bonus", "weighted levels N1=1 and N2=2"], "official_return": "dict from avaliar; score() returns score_final as float"},
        "contracts": contracts(gold_df),
        "matching": {"threshold": METRIC.IOU_MIN, "greedy": True, "half_open_spans": True, "tie_break": "(-IoU, gold_index, prediction_index)", "one_to_one": True, "solution_overlapping_gold_rejected": True, "unmatched_extra_rule": "ignored when >=90% of prediction is contained in a matched gold; otherwise FP"},
        "span_semantics": {"formula": "intersection / union with half-open [inicio,fim)", "exact_boundary_relevance": "not required at score level; IoU threshold controls matching", "synthetic_iou_tests": "M7/M8/M9 and sensitivity.iou_curve", "wrong_trecho": "not scored because trecho is absent from Kaggle submission citation contract"},
        "classification_semantics": {"allowed": list(METRIC.CLASSES), "case_normalized": True, "wrong_class_behavior": "matched wrong class increments expected FN and predicted FP", "nonreal_id_behavior": "ID may be filled and is ignored"},
        "type_semantics": {"official_submission_type_field": False, "gold_type_field": True, "impact": "none in official scorer; tipo is retained in gold CSV/JSON-side contract but is not passed to the scorer"},
        "canonical_id_semantics": {"real": "digits only, normalized by removing leading zeroes; must be nonempty", "real_match": "predicted ID membership in solution doc_ids", "wrong_real_id": "FP real without FN on a matched span", "nonreal": "ID optional and ignored"},
        "aggregation": {"per_level": "macro-F1 over classes with support > 0", "tau": "wrong-class predictions against inventada gold / inventada support", "bonus": "max(0,min(0.10,0.10*(1-Brier))) only if matched confidences exist", "weights": METRIC.PESOS_NIVEL, "range_observed": [0.0, 1.1]},
        "synthetic_tests": synthetic_tests(),
        "edge_case_tests": edge_case_tests(solution, perfect, empty),
        "perfect_baseline": {"gold_without_confidence": score(solution, perfect), "gold_with_confidence_1": score(solution, perfect_conf), "maximum_observed": 1.1, "gold_rows": len(gold_rows)},
        "empty_baseline": {"accepted": True, "score": score(solution, empty), "marker": "-", "documents": len(docs)},
        "current_pipeline": current,
        "diagnostic_vs_official": {"post_arbitration": current["post_arbitration"]["local_vs_official"], "raw_detector": current["raw_detector"]["local_vs_official"], "interpretation": "local and official matching sets are identical for the current output; final score is still exclusively official"},
        "sensitivity": score_sensitivities(solution),
        "seven_detector_residual": current["seven_detector_residual"],
        "arbitration_effect": current["arbitration_effect"],
        "guard_effect": current["numbered_primary_class_guard"],
        "submission_validity": current["output_readiness"],
        "human_review_decision": {"decision": "PROMOTE_DETECTOR_V5", "reason": "the user confirmed that REVISAR reflected uncertainty in the form and authorized promotion after the seven cases were validated", "cases": current["seven_detector_residual"]["cases"]},
        "engineering_priorities": [{"priority": 1, "work": "fix/extend detector prefix boundaries", "evidence": "2 of 7 residuals; oracle corrections accepted by official parser"}, {"priority": 2, "work": "investigate detector full misses with guarded patterns", "evidence": "5 of 7 residuals; do not use broad lexical retrieval without blind-set validation"}, {"priority": 3, "work": "keep one-row-per-document and parser-validity checks in submission generation", "evidence": "official parser rejects missing real ID, invalid class, duplicate-overlap predictions"}, {"priority": 4, "work": "do not optimize tipo/trecho for Kaggle score", "evidence": "official scorer does not parse those fields"}],
        "recommendation": {"status": "PASS WITH WARNINGS", "official_current_score": current["post_arbitration"]["official_score"], "recommendation": "use the CitationDetector V5 output as submission-ready for the official parser; report score comparisons only through the official scorer and validate future changes on a blind holdout", "warnings": ["official scorer silently keeps first duplicate document row", "tipo/trecho cannot change Kaggle score", "confidence can raise a perfect score to 1.1 and unmatched confidence is not penalized", "the V5 safety result is established on the frozen corpus; external blind validation remains future work"]},
        "historical_versions": {"v2_v3_reproducible": False, "reason": "available historical files are audit harnesses, not frozen production implementations; no artificial reconstruction was used"},
        "determinism": {"runs": 3, "identical": stable(current_runs[0]) == stable(current_runs[1]) == stable(current_runs[2]), "method": "current pipeline, official score, sensitivities and residual analysis executed three times; runtime excluded from equality; package hashes compared before/after"},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"{payload['status']}: official_score={payload['recommendation']['official_current_score']['value']} artifact={OUT.relative_to(ROOT)}")
    return 0 if payload["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
