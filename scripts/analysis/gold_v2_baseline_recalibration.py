"""Recalibra, sem alterar produção, a baseline para o gold oficial V2."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import csv
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import types
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "material_desafio_jusbrasil_bracis"
OLD = ROOT / "material_desafio_jusbrasil_bracis_old3"
OUT = ROOT / "artifacts" / "gold_v2_baseline_recalibration.json"
V6_COMMIT = "00056f1"
EXPECTED_HASHES = {
    "old": "c86f91c54c216411260f3ff4d6cd0ba60c3d28a1a2a7e8d30521b2b329864c8f",
    "new": "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1",
}
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import h1_degraded_compact_cnj_experiment as h1  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


PRE_RECALIBRATION_FAILURES = [
    {"test": "test_non_process_candidates_and_validated_candidates_are_preserved", "file": "tests/test_citation_detector.py", "old_expectation": "metrics=(129, 77, 96); 12 generic predictions matched gold", "new_observed": "metrics=(129, 77, 66); 12 generic predictions match zero Gold V2 rows", "category": "SEMANTIC_EXPECTATION_OBSOLETE", "action": "update FN to 66 and assert generic predictions remain emitted but have zero official matches"},
    {"test": "test_v7_candidate_count_offsets_and_exact_matches", "file": "tests/test_citation_detector.py", "old_expectation": "matches=141; exact=88", "new_observed": "matches=129; exact=82", "category": "BASELINE_NUMERIC_CHANGE", "action": "update measured match and exact counts"},
    {"test": "test_v7_metrics_by_level_and_type[global]", "file": "tests/test_citation_detector.py", "old_expectation": "(141, 77, 84)", "new_observed": "(129, 89, 66)", "category": "BASELINE_NUMERIC_CHANGE", "action": "update measured metrics"},
    {"test": "test_v7_metrics_by_level_and_type[N1]", "file": "tests/test_citation_detector.py", "old_expectation": "(84, 37, 32)", "new_observed": "(76, 45, 25)", "category": "BASELINE_NUMERIC_CHANGE", "action": "update measured metrics"},
    {"test": "test_v7_metrics_by_level_and_type[N2]", "file": "tests/test_citation_detector.py", "old_expectation": "(57, 40, 52)", "new_observed": "(53, 44, 41)", "category": "BASELINE_NUMERIC_CHANGE", "action": "update measured metrics"},
    {"test": "test_v7_metrics_by_level_and_type[jurisprudencia]", "file": "tests/test_citation_detector.py", "old_expectation": "(125, 50, 61)", "new_observed": "(113, 62, 52)", "category": "BASELINE_NUMERIC_CHANGE", "action": "update measured metrics"},
    {"test": "test_v7_metrics_by_level_and_type[lei]", "file": "tests/test_citation_detector.py", "old_expectation": "(16, 27, 23)", "new_observed": "(16, 27, 14)", "category": "BASELINE_NUMERIC_CHANGE", "action": "update measured metrics"},
    {"test": "test_oracle_family_inventory_is_reproduced", "file": "tests/test_citation_parser.py", "old_expectation": "jurisprudencia_geral=46; lei_geral=11", "new_observed": "jurisprudencia_geral=25; lei_geral=2", "category": "BASELINE_NUMERIC_CHANGE", "action": "update current oracle-family inventory"},
    {"test": "test_oracle_safe_strategy_matrix_and_critical_errors", "file": "tests/test_citation_resolver.py", "old_expectation": "incompleta/insufficient=65; classified=167", "new_observed": "incompleta/insufficient=35; classified=137", "category": "BASELINE_NUMERIC_CHANGE", "action": "update current resolver matrix and classified total"},
]


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def distribution(rows: Iterable[dict[str, str]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(row[field] for row in rows).items()))


def semantic_group(row: dict[str, str]) -> str:
    text = row["trecho"].lower()
    if row["tipo"] == "lei":
        return "generic_normative_reference" if re.search(r"normas|legisla|lei que|dispositivo", text) else "vague_general_legal_reference"
    if re.search(r"sumulad|verbete", text):
        return "generic_sumula_reference"
    if re.search(r"precedent|jurisprud", text):
        return "generic_precedent_reference"
    return "vague_general_jurisprudence"


def concrete_signals(text: str) -> list[str]:
    signals: list[str] = []
    if re.search(r"\b(STF|STJ|TSE|TST|STM)\b", text, re.I):
        signals.append("tribunal")
    if re.search(r"relatoria|rel\.\s*(min\.)?|relator", text, re.I):
        signals.append("relator")
    if re.search(r"\b20\d{2}\b", text):
        signals.append("ano")
    if re.search(r"reclamação|acórdão|julgado|precedente", text, re.I):
        signals.append("decisao_ou_classe")
    if re.search(r"art(?:igo|\.)|código|lei complementar", text, re.I):
        signals.append("fonte_normativa")
    return signals


def baseline_detector() -> type:
    source = subprocess.check_output(
        ["git", "show", f"{V6_COMMIT}:src/bracis_jusbrasil/citations/detector.py"], cwd=ROOT, text=True
    )
    module = types.ModuleType("citation_detector_v6_gold_v2")
    sys.modules[module.__name__] = module
    exec(compile(source, "citation_detector_v6_gold_v2.py", "exec"), module.__dict__)
    return module.CitationDetector


def pipeline(detector_type: type, texts: dict[str, str], resolver: CitationResolver) -> tuple[list[Any], list[Any], float]:
    detector, parser = detector_type(), CitationParser()
    raw, outputs = [], []
    started = time.perf_counter()
    for document_id in sorted(texts):
        candidates = [CitationCandidate(item.start, item.end, item.text, item.rule, item.family) for item in detector.detect(texts[document_id])]
        parsed = [parser.parse(item, context=texts[document_id]) for item in candidates]
        results = [resolver.resolve(item) for item in parsed]
        source_indexes: dict[int, int] = {}
        for candidate, parsed_item, result in zip(candidates, parsed, results):
            source_indexes[id(candidate)] = len(raw)
            raw.append(post.RawPrediction(len(raw), document_id, candidate, parsed_item, result))
        for item in structural_cnj_union_merge(texts[document_id], candidates, parsed, results, primary_identities=resolver.primary_identities):
            indexes = tuple(sorted(source_indexes[id(source)] for source in item.source_candidates))
            outputs.append(post.Output(len(outputs), document_id, item.candidate, item.parsed, item.resolution, "merged" if len(indexes) > 1 else "single", indexes))
    return raw, outputs, time.perf_counter() - started


def metrics(gold_df: Any, documents: list[str], gold: Sequence[Any], raw: list[Any], outputs: list[Any]) -> dict[str, Any]:
    result = h1.score_variant(gold_df, documents, gold, raw, outputs)
    submission = official.submission_from_cells(official.output_cells(outputs), documents)
    direct = float(official.METRIC.score(official.solution_from_gold(gold_df), submission, "documento_id"))
    if direct != result["official_final"]:
        raise RuntimeError(f"scorer direto diverge: {direct} != {result['official_final']}")
    return {
        "predictions": result["predictions"], "matches": result["official_matches"], "FP": result["FP"], "FN": result["FN"],
        "exact": result["exact"], "correct_real_ids": result["real_ids_correct"], "N1": result["N1"], "N2": result["N2"],
        "final": direct, "safety": result["safety"], "final_outputs": len(outputs),
    }


def candidate_record(item: Any, classification: str) -> dict[str, Any]:
    return {
        "document_id": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text,
        "rule": item.candidate.rule, "family": item.candidate.family, "final_class": classification,
        "resolution": post.result_dict(item.result),
    }


def final_class(item: Any) -> str:
    return official.output_cells([item])[0].classification


def overlapping_outputs(row: dict[str, str], outputs: Sequence[Any]) -> list[Any]:
    return [
        item for item in outputs
        if item.document_id == row["documento_id"]
        and post.iou(int(row["inicio"]), int(row["fim"]), item.candidate.start, item.candidate.end) >= 0.5
    ]


def test_suite() -> dict[str, Any]:
    environment = __import__("os").environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, text=True, capture_output=True, env=environment)
    match = re.search(r"Ran (\d+) tests", result.stderr)
    return {"total": int(match.group(1)) if match else None, "passed": result.returncode == 0, "failures": 0 if result.returncode == 0 else None, "output_tail": (result.stdout + result.stderr)[-1500:]}


def historical_audits() -> list[dict[str, str]]:
    names = subprocess.check_output(["rg", "-l", "225|len\\(gold\\)", "scripts/analysis"], cwd=ROOT, text=True).splitlines()
    current = str(Path(__file__).relative_to(ROOT))
    return [{"script": name, "old_gold_dependency": "fixed 225-row fact or guard", "action": "PRESERVE_HISTORICAL_UNCHANGED"} for name in sorted(names) if name != current]


def build() -> dict[str, Any]:
    old_path, new_path = OLD / "goldenset.csv", DATA / "goldenset.csv"
    old_rows, new_rows = csv_rows(old_path), csv_rows(new_path)
    if {"old": digest(old_path), "new": digest(new_path)} != EXPECTED_HASHES:
        raise RuntimeError("hash de gold inesperado")
    key = lambda row: (row["documento_id"], row["citacao_id"])
    old_by_key, new_by_key = {key(row): row for row in old_rows}, {key(row): row for row in new_rows}
    removed = [old_by_key[item] for item in sorted(old_by_key.keys() - new_by_key.keys())]
    added = [new_by_key[item] for item in sorted(new_by_key.keys() - old_by_key.keys())]
    modified = [item for item in sorted(old_by_key.keys() & new_by_key.keys()) if old_by_key[item] != new_by_key[item]]
    if (len(old_rows), len(new_rows), len(removed), len(added), len(modified)) != (225, 195, 30, 0, 0):
        raise RuntimeError("diff estrutural do Gold V2 inesperado")
    if any(row["classificacao"] != "incompleta" for row in removed):
        raise RuntimeError("remoção fora da classe incompleta")

    shared = ["desafio1_bracis.db", "kaggle_metric.py", "json_to_submission.py", "sample_submission.csv", *[f"txt/{path.name}" for path in sorted((DATA / "txt").glob("*.txt"))]]
    material = [{"artifact": name, "old3_sha256": digest(OLD / name), "current_sha256": digest(DATA / name), "identical": digest(OLD / name) == digest(DATA / name)} for name in shared]
    if not all(item["identical"] for item in material):
        raise RuntimeError("material oficial alterado além do gold")
    if digest(get_database_path()) != DB_HASH:
        raise RuntimeError("hash do banco inesperado")

    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    gold_df = official.load_gold_df()
    documents = list(gold_df.documento_id.drop_duplicates())
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        v6_raw, v6_outputs, v6_seconds = pipeline(baseline_detector(), texts, resolver)
        v7_raw, v7_outputs, v7_seconds = pipeline(CitationDetector, texts, resolver)
        v6, v7 = metrics(gold_df, documents, gold, v6_raw, v6_outputs), metrics(gold_df, documents, gold, v7_raw, v7_outputs)
        db_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        inventory = dict(connection.execute("SELECT natureza, COUNT(*) FROM documentos GROUP BY natureza").fetchall())
    if (v7["predictions"], v7["matches"], v7["FP"], v7["FN"], v7["correct_real_ids"]) != (218, 129, 89, 66, 71):
        raise RuntimeError(f"métricas V7 Gold V2 inesperadas: {v7}")

    removed_inventory = [{**row, "semantic_group": semantic_group(row)} for row in removed]
    surviving = [row for row in new_rows if row["classificacao"] == "incompleta"]
    surviving_inventory = [{**row, "concrete_signals": concrete_signals(row["trecho"].replace("\\n", "\n"))} for row in surviving]
    matches = post.match_gold(gold, v7_outputs)
    outputs_by_index = {item.index: item for item in v7_outputs}
    newly_fp = []
    for row in removed:
        for output in overlapping_outputs(row, v7_outputs):
            newly_fp.append({"old_gold": row["citacao_id"], "doc": row["documento_id"], "level": f"N{row['nivel']}", "type": row["tipo"], "span": [int(row["inicio"]), int(row["fim"])], "text": row["trecho"], **candidate_record(output, final_class(output))})
    newly_fp.sort(key=lambda item: (item["doc"], item["span"], item["rule"]))
    surviving_predictions = []
    for item in gold:
        if item.classification != "incompleta":
            continue
        output = outputs_by_index.get(matches.get(item.index))
        if output is not None:
            surviving_predictions.append({"gold": item.gid, "doc": item.document_id, "level": item.level, "text": item.text, "rule": output.candidate.rule, "family": output.candidate.family, "prediction_class": final_class(output), "matched": True})
    current_run = {"v6": v6, "v7": v7, "newly_fp": newly_fp, "surviving_predictions": surviving_predictions}
    repeat_runs = []
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for _ in range(3):
            raw, outputs, _ = pipeline(CitationDetector, texts, resolver)
            current = metrics(gold_df, documents, gold, raw, outputs)
            current_fp = []
            for row in removed:
                for output in overlapping_outputs(row, outputs):
                    current_fp.append({"doc": row["documento_id"], "span": [int(row["inicio"]), int(row["fim"])], "rule": output.candidate.rule, "family": output.candidate.family})
            repeat_runs.append({"metrics": current, "newly_fp": sorted(current_fp, key=stable)})
    deterministic = all(stable(repeat_runs[0]) == stable(item) for item in repeat_runs[1:])

    return {
        "schema_version": "1.0", "baseline_name": "GOLD_V2_BASELINE", "old_gold": {"path": str(old_path.relative_to(ROOT)), "sha256": EXPECTED_HASHES["old"], "rows": len(old_rows)},
        "new_gold": {"path": str(new_path.relative_to(ROOT)), "sha256": EXPECTED_HASHES["new"], "rows": len(new_rows)},
        "material_integrity": {"shared": material, "all_shared_identical": all(item["identical"] for item in material)},
        "row_diff": {"removed": len(removed), "added": len(added), "modified_surviving": len(modified), "classes_old": distribution(old_rows, "classificacao"), "classes_new": distribution(new_rows, "classificacao"), "types_old": distribution(old_rows, "tipo"), "types_new": distribution(new_rows, "tipo"), "levels_old": distribution(old_rows, "nivel"), "levels_new": distribution(new_rows, "nivel")},
        "removed_inventory": removed_inventory,
        "removed_semantic_groups": distribution(removed_inventory, "semantic_group"),
        "surviving_incomplete_inventory": {"total": len(surviving_inventory), "types": distribution(surviving_inventory, "tipo"), "levels": distribution(surviving_inventory, "nivel"), "concrete_signal_counts": dict(sorted(Counter(signal for row in surviving_inventory for signal in row["concrete_signals"]).items())), "rows": surviving_inventory},
        "test_failures_before": {"total": 101, "passed": 92, "failed": len(PRE_RECALIBRATION_FAILURES), "ledger": PRE_RECALIBRATION_FAILURES},
        "test_failure_classification": dict(sorted(Counter(item["category"] for item in PRE_RECALIBRATION_FAILURES).items())),
        "test_changes": PRE_RECALIBRATION_FAILURES,
        "current_v7_metrics": v7, "current_v6_metrics": v6,
        "v7_vs_v6": {"final_delta": v7["final"] - v6["final"], "matches_delta": v7["matches"] - v6["matches"], "correct_real_ids_delta": v7["correct_real_ids"] - v6["correct_real_ids"]},
        "real_coverage": {"correct_real_ids": v7["correct_real_ids"], "total_real": sum(row["classificacao"] == "real" for row in new_rows), "fraction": "71/96", "percent": 100 * 71 / 96},
        "recall_engineering_closure": {"fraction": "93/96", "percent": 96.875, "official_score": False},
        "removed_prediction_interaction": {"removed": len(removed), "predicted": len(newly_fp), "not_predicted": len(removed) - len(newly_fp)},
        "newly_fp_ledger": newly_fp, "newly_fp_rule_breakdown": distribution(newly_fp, "rule"), "surviving_incomplete_prediction_ledger": surviving_predictions,
        "fp_decomposition": {"pre_existing_old_gold_fp": v7["FP"] - len(newly_fp), "gold_v2_newly_fp": len(newly_fp), "total": v7["FP"]},
        "safety": v7["safety"], "case_98": {"gold_unchanged": True, "correct_real_ids_preserved": v7["correct_real_ids"] == 71},
        "historical_audit_status": historical_audits(), "tests_after": test_suite(), "determinism": {"runs": 3, "identical": deterministic, "snapshots": repeat_runs},
        "integrity": {"database_sha256": digest(get_database_path()), "pragma_integrity_check": db_integrity, "dataset_inventory": inventory, "v6_seconds": v6_seconds, "v7_seconds": v7_seconds, "src_diff_sha256": sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest()},
        "next_front": "JURISPRUDENCIA_GERAL_PRECISION_AUDIT",
    }


def main() -> int:
    report = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": report["baseline_name"], "tests_after": report["tests_after"], "deterministic": report["determinism"]["identical"], "artifact": str(OUT)}, ensure_ascii=False, sort_keys=True))
    return 0 if report["tests_after"]["passed"] and report["determinism"]["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
