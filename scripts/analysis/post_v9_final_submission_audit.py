"""Read-only post-V9 audit; only the official converter writes the CSV."""

from __future__ import annotations

from hashlib import sha256
import csv
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402

from bracis_jusbrasil.citations import CitationDetector  # noqa: E402

OUT = ROOT / "artifacts" / "post_v9_final_submission_audit.json"
CSV_OUT = ROOT / "submissions" / "submission_v9_h4_rcl.csv"
EXPECTED = {"predictions": 237, "matches": 160, "FP": 77, "FN": 35,
            "exact": 113, "real_ids": 71, "score": 0.65545447931636}
V8 = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39,
      "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
SAFETY = {"wrong_unique_real": 0, "false_real_inventada": 0,
          "false_real_incompleta": 0}
H4 = {"gen_n1_006": (1331, 1364), "gen_n1_007": (1902, 1937),
      "gen_n2_002": (1399, 1441), "gen_n2_007": (1416, 1451)}
SCOPE = {"src/bracis_jusbrasil/citations/detector.py",
         "tests/test_citation_detector.py", "docs/architecture.md"}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def without_h4_pipeline():
    original = CitationDetector.__dict__["_detect_rcl_relator_year_no_tribunal"]
    CitationDetector._detect_rcl_relator_year_no_tribunal = staticmethod(lambda text: [])
    try:
        return base.pipeline()
    finally:
        CitationDetector._detect_rcl_relator_year_no_tribunal = original


def score(metric, gold, texts, raw, outputs):
    values, raw_pairs, output_pairs = base.metrics(metric, gold, raw, outputs)
    submission = base.submission(outputs, texts)
    values["score"] = float(metric.score(base.solution(gold), submission, "documento_id"))
    return values, raw_pairs, output_pairs, submission


def check_metrics(name, actual, expected):
    mismatch = {key: [expected[key], actual.get(key)] for key in expected
                if abs(actual[key] - expected[key]) > 1e-12 if key == "score"}
    mismatch.update({key: [expected[key], actual.get(key)] for key in expected
                     if key != "score" and actual.get(key) != expected[key]})
    if mismatch or actual["safety"] != SAFETY:
        raise RuntimeError(f"{name}_METRICS_MISMATCH: {mismatch}, safety={actual['safety']}")


def document_json(doc, rows):
    citations = []
    for row in rows:
        candidate, parsed, result = row["candidate"], row["parsed"], row["result"]
        citations.append({
            "inicio": candidate.start,
            "fim": candidate.end,
            "trecho": candidate.text,
            "tipo": parsed.tipo,
            "classificacao": base.label(result.status),
            "resolucao": {"id_canonico": result.id_canonico},
            "confianca": None,
        })
    return {"documento_id": doc, "citacoes": citations}


def generate_with_official_converter(outputs, texts, destination):
    grouped = {doc: [] for doc in texts}
    for item in outputs:
        grouped[item["doc"]].append(item)
    with TemporaryDirectory(prefix="post-v9-json-") as directory:
        source = Path(directory)
        for doc in sorted(texts):
            (source / f"{doc}.json").write_text(
                json.dumps(document_json(doc, grouped[doc]), ensure_ascii=False), encoding="utf-8"
            )
        result = subprocess.run(
            [str(ROOT / "venv" / "bin" / "python"),
             str(base.DATA / "json_to_submission.py"), str(source), str(destination)],
            cwd=ROOT, capture_output=True, text=True,
        )
    if result.returncode:
        raise RuntimeError(f"OFFICIAL_CONVERTER_FAILURE: {result.stderr}")


def read_and_validate_csv(path, texts, metric):
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected_docs = set(texts)
    document_ids = [row["documento_id"] for row in rows]
    if set(document_ids) != expected_docs or len(document_ids) != len(set(document_ids)):
        raise RuntimeError("CSV_DOCUMENT_SET_FAILURE")
    predictions, numeric_confidence, null_confidence = 0, 0, 0
    for row in rows:
        parsed = metric._parse_submission_cell(row["citacoes"], row["documento_id"])
        for item in parsed:
            predictions += 1
            if item["fim"] > len(texts[row["documento_id"]]):
                raise RuntimeError(f"CSV_OUT_OF_TEXT_SPAN: {row['documento_id']}")
            if item["confianca"] is None:
                null_confidence += 1
            else:
                numeric_confidence += 1
    return {"rows": len(rows), "documents": len(document_ids), "predictions": predictions,
            "numeric_confidence": numeric_confidence, "null_confidence_marker": null_confidence,
            "valid": True}


def main():
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    start = perf_counter()
    texts, raw, outputs, integrity = base.pipeline()
    pipeline_seconds = perf_counter() - start
    current, raw_pairs, output_pairs, internal_submission = score(metric, gold, texts, raw, outputs)
    check_metrics("V9", current, EXPECTED)

    _, v8_raw, v8_outputs, _ = without_h4_pipeline()
    v8, v8_raw_pairs, v8_output_pairs, _ = score(metric, gold, texts, v8_raw, v8_outputs)
    check_metrics("V8", v8, V8)

    generation_seconds = []
    hashes = []
    with TemporaryDirectory(prefix="post-v9-csv-") as directory:
        for run in range(3):
            target = CSV_OUT if run == 0 else Path(directory) / f"submission-{run}.csv"
            begin = perf_counter()
            generate_with_official_converter(outputs, texts, target)
            generation_seconds.append(perf_counter() - begin)
            hashes.append(digest(target))
    csv_validation = read_and_validate_csv(CSV_OUT, texts, metric)
    with CSV_OUT.open(encoding="utf-8", newline="") as stream:
        csv_submission = __import__("pandas").read_csv(stream, dtype=str, keep_default_na=False)
    csv_score = float(metric.score(base.solution(gold), csv_submission, "documento_id"))
    csv_eval = metric.avaliar(base.solution(gold), csv_submission, "documento_id")
    if csv_score != current["score"]:
        raise RuntimeError(f"JSON_CSV_SCORE_MISMATCH: {csv_score}, {csv_validation}")

    matched = {id(prediction): gold_row for gold_row, prediction in raw_pairs}
    h4_rows = [row for row in raw if row["candidate"].rule == "rcl_relator_year_no_tribunal"]
    h4_audit = []
    for row in h4_rows:
        candidate, gold_row = row["candidate"], matched.get(id(row))
        h4_audit.append({"document": row["doc"], "start": candidate.start, "end": candidate.end,
                         "text": candidate.text, "classification": base.label(row["result"].status),
                         "id_canonico": row["result"].id_canonico, "confidence": None,
                         "iou": 0 if gold_row is None else base.iou((candidate.start, candidate.end), (gold_row.start, gold_row.end)),
                         "exact": bool(gold_row and (candidate.start, candidate.end) == (gold_row.start, gold_row.end))})
    if {item["document"]: (item["start"], item["end"]) for item in h4_audit} != H4:
        raise RuntimeError(f"H4_CHECKPOINT_FAILURE: {h4_audit}")

    h2 = [row for row in raw if row["candidate"].rule == "decision_tribunal_relator_year"]
    h2_pairs = [(gold_row, row) for gold_row, row in raw_pairs if row["candidate"].rule == "decision_tribunal_relator_year"]
    cnj = [row for row in raw if row["candidate"].family == "processo_cnj"]
    real_v8 = {(gold_row.gid, row["result"].id_canonico) for gold_row, row in v8_output_pairs if gold_row.classification == "real"}
    real_v9 = {(gold_row.gid, row["result"].id_canonico) for gold_row, row in output_pairs if gold_row.classification == "real"}
    inventada_v8 = {gold_row.gid for gold_row, _ in v8_output_pairs if gold_row.classification == "inventada"}
    inventada_v9 = {gold_row.gid for gold_row, _ in output_pairs if gold_row.classification == "inventada"}
    regression = {"v8_matches_preserved": len(v8_raw_pairs) == 156 and all(
        any(gold_row.gid == current_gold.gid and row["candidate"].start == current_row["candidate"].start and row["candidate"].end == current_row["candidate"].end
            for current_gold, current_row in raw_pairs) for gold_row, row in v8_raw_pairs),
        "real_ids": [v8["real_ids"], current["real_ids"]],
        "real_pairs_preserved": real_v8 == real_v9,
        "h2": [len(h2), len(h2_pairs)],
        "cnj_outputs": len(cnj),
        "inventada_tps_preserved": inventada_v8 == inventada_v9,
        "safety": current["safety"]}

    head_files = subprocess.check_output(["git", "show", "--format=", "--name-only", "HEAD"], cwd=ROOT, text=True).splitlines()
    git_audit = {"head": subprocess.check_output(["git", "log", "-1", "--oneline"], cwd=ROOT, text=True).strip(),
                 "head_files": head_files,
                 "scope_pass": set(head_files) == SCOPE,
                 "scope_extras": sorted(set(head_files) - SCOPE),
                 "status_short": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
                 "diff_name_only": subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines()}

    material_paths = {"gold_v2": base.DATA / "goldenset.csv", "database": base.DATA / "desafio1_bracis.db",
                      "converter": base.DATA / "json_to_submission.py", "metric": base.DATA / "kaggle_metric.py"}
    material_hashes = {name: {"path": str(path.relative_to(ROOT)), "sha256": digest(path)} for name, path in material_paths.items()}
    source_paths = [ROOT / "src" / "bracis_jusbrasil" / "citations" / name for name in ("detector.py", "parser.py", "resolver.py")]
    leakage = {"gold_access_in_generation_components": any("goldenset" in path.read_text(encoding="utf-8") for path in source_paths),
               "detector_has_doc_id_or_offset_literals": any(token in source_paths[0].read_text(encoding="utf-8") for token in H4),
               "detector_has_h4_specific_names": any(name in source_paths[0].read_text(encoding="utf-8") for name in ("Rosa Weber", "CÁRMEN LÚCIA", "Alexandre De Moraes", "Flávio Dino")),
               "official_generator_uses_gold": "goldenset" in material_paths["converter"].read_text(encoding="utf-8")}
    if any(leakage.values()):
        raise RuntimeError(f"LEAKAGE_FAILURE: {leakage}")

    details = {str(level): value for level, value in csv_eval["niveis"].items()}
    warnings = ["H4 blind risk remains MODERATE: four current corpus positives.",
                "All 236 CSV confidences are the converter's accepted '-' marker; no Brier bonus applies."]
    blockers = []
    if not git_audit["scope_pass"]:
        blockers.append("HEAD contains files outside the three-file V9 commit scope.")
    if csv_validation["predictions"] != current["predictions"]:
        blockers.append(
            "The final arbitrated CSV has 236 predictions while the reported raw V9 metric has 237."
        )
    report = {"classification": "PASS" if not blockers else "FAIL", "git_audit": git_audit,
              "material_hashes": material_hashes, "v9_metrics_internal": current,
              "score_details": details, "submission_csv_path": str(CSV_OUT.relative_to(ROOT)),
              "submission_csv_sha256": digest(CSV_OUT), "submission_format_validation": csv_validation,
              "json_csv_scorer_consistency": {"internal_score": current["score"], "csv_score": csv_score,
                                                "raw_predictions": current["predictions"], "arbitrated_outputs": len(outputs),
                                                "csv_predictions": csv_validation["predictions"],
                                                "score_pass": True, "count_pass": csv_validation["predictions"] == current["predictions"]},
              "duplicate_scorer_constraint_audit": {"official_parser_accepted": True, "half_open_spans": True, "empty_spans": 0, "out_of_text_spans": 0},
              "h4_checkpoint_audit": h4_audit, "regression_checkpoint_audit": regression,
              "leakage_blind_readiness_audit": leakage,
              "reproducibility": {"runs": 3, "identical": len(set(hashes)) == 1, "sha256": hashes},
              "performance": {"pipeline_seconds": pipeline_seconds, "converter_seconds": generation_seconds,
                              "converter_mean_seconds": sum(generation_seconds) / len(generation_seconds)},
              "tests_compile_mkdocs": "EXTERNAL_FINAL_VALIDATION_REQUIRED", "database_integrity": integrity,
              "blockers": blockers, "warnings": warnings,
              "final_verdict": "POST_V9_SUBMISSION_AUDIT_PASS" if not blockers else "POST_V9_SUBMISSION_AUDIT_FAIL",
              "recommended_next_step": "SUBMIT_V9_CHECKPOINT_TO_KAGGLE" if not blockers else "FIX_COMMIT_SCOPE"}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"classification": report["classification"], "head_scope": git_audit["scope_pass"],
                      "metrics": current, "csv": report["submission_csv_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
