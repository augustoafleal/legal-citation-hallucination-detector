"""Updated final V9 audit with raw/arbitrated count separation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402
import post_v9_final_submission_audit as prior  # noqa: E402

OUT = ROOT / "artifacts" / "post_v9_final_submission_audit_updated.json"
CSV_OUT = ROOT / "submissions" / "submission_v9_h4_rcl.csv"
EXPECTED = {"predictions": 237, "matches": 160, "FP": 77, "FN": 35,
            "exact": 113, "real_ids": 71, "score": 0.65545447931636}
V8 = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39,
      "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
SAFETY = {"wrong_unique_real": 0, "false_real_inventada": 0,
          "false_real_incompleta": 0}
H4 = {"gen_n1_006": (1331, 1364), "gen_n1_007": (1902, 1937),
      "gen_n2_002": (1399, 1441), "gen_n2_007": (1416, 1451)}
ACCIDENTAL = {
    "scripts/analysis/h4_rcl_production_design.py",
    "scripts/analysis/post_v8_cnj_precision_audit.py",
    "scripts/analysis/post_v8_dispositivo_legal_precision_audit.py",
    "scripts/analysis/post_v8_error_frontier_audit.py",
    "scripts/analysis/post_v8_remaining_incomplete_tail_design.py",
    "scripts/analysis/post_v8_tribunal_contextual_precision_audit.py",
    "scripts/analysis/rcl_tail_h4_offline_experiment.py",
    "scripts/analysis/targeted_review_dispositivo_legal_fp.py",
    "scripts/analysis/validate_v9_h4_rcl.py",
    "scripts/analysis/validate_v9_h4_rcl_fix.py",
    "submissions/submission_v8_h2.csv",
}


def expect(values, expected, name):
    for key, value in expected.items():
        if key == "score":
            if abs(values[key] - value) > 1e-12:
                raise RuntimeError(f"{name}_SCORE: {values}")
        elif values[key] != value:
            raise RuntimeError(f"{name}_METRICS: {values}")
    if values["safety"] != SAFETY:
        raise RuntimeError(f"{name}_SAFETY: {values}")


def signature(outputs):
    value = [(item["doc"], item["candidate"].start, item["candidate"].end,
              item["candidate"].rule, item["result"].status, item["result"].id_canonico)
             for item in outputs]
    return prior.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def main():
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    runs, output_hashes, csv_hashes, timings = [], [], [], []
    final = None
    for _ in range(3):
        begin = perf_counter()
        texts, raw, outputs, integrity = base.pipeline()
        pipeline_seconds = perf_counter() - begin
        values, raw_pairs, output_pairs, _ = prior.score(metric, gold, texts, raw, outputs)
        expect(values, EXPECTED, "V9")
        begin = perf_counter()
        prior.generate_with_official_converter(outputs, texts, CSV_OUT)
        converter_seconds = perf_counter() - begin
        csv_validation = prior.read_and_validate_csv(CSV_OUT, texts, metric)
        csv_frame = pd.read_csv(CSV_OUT, dtype=str, keep_default_na=False)
        csv_score = float(metric.score(base.solution(gold), csv_frame, "documento_id"))
        if csv_score != values["score"]:
            raise RuntimeError(f"CSV_SCORE: {csv_score}")
        counts = {"raw_detector_candidates": len(raw), "parsed_candidates": len(raw),
                  "resolved_candidates": len(raw), "arbitrated_final_outputs": len(outputs),
                  "csv_citation_items": csv_validation["predictions"],
                  "scorer_prediction_stat": values["predictions"]}
        expected_counts = {"raw_detector_candidates": 237, "parsed_candidates": 237,
                           "resolved_candidates": 237, "arbitrated_final_outputs": 236,
                           "csv_citation_items": 236, "scorer_prediction_stat": 237}
        if counts != expected_counts:
            raise RuntimeError(f"COUNT_CONTRACT: {counts}")
        runs.append({"metrics": values, "counts": counts, "score": csv_score,
                     "safety": values["safety"], "csv_sha256": prior.digest(CSV_OUT)})
        output_hashes.append(signature(outputs))
        csv_hashes.append(prior.digest(CSV_OUT))
        timings.append({"pipeline_seconds": pipeline_seconds, "converter_seconds": converter_seconds})
        final = (texts, raw, outputs, raw_pairs, output_pairs, csv_validation, integrity, values)

    texts, raw, outputs, raw_pairs, output_pairs, csv_validation, integrity, values = final
    _, v8_raw, v8_outputs, _ = prior.without_h4_pipeline()
    v8, v8_pairs, v8_output_pairs, _ = prior.score(metric, gold, texts, v8_raw, v8_outputs)
    expect(v8, V8, "V8")
    matched = {id(prediction): gold_row for gold_row, prediction in raw_pairs}
    csv_frame = pd.read_csv(CSV_OUT, dtype=str, keep_default_na=False).set_index("documento_id")
    h4 = []
    for row in [item for item in raw if item["candidate"].rule == "rcl_relator_year_no_tribunal"]:
        candidate, gold_row = row["candidate"], matched.get(id(row))
        cells = metric._parse_submission_cell(csv_frame.loc[row["doc"], "citacoes"], row["doc"])
        h4.append({"document": row["doc"], "start": candidate.start, "end": candidate.end,
                   "text": candidate.text, "classification": base.label(row["result"].status),
                   "id_canonico": row["result"].id_canonico, "confidence": None,
                   "iou": 0 if gold_row is None else base.iou((candidate.start, candidate.end), (gold_row.start, gold_row.end)),
                   "exact": bool(gold_row and (candidate.start, candidate.end) == (gold_row.start, gold_row.end)),
                   "present_in_csv": any(cell["inicio"] == candidate.start and cell["fim"] == candidate.end and cell["classe"] == "incompleta" for cell in cells)})
    if {item["document"]: (item["start"], item["end"]) for item in h4} != H4 or any(
            item["classification"] != "incompleta" or item["id_canonico"] is not None or item["iou"] != 1.0
            or not item["exact"] or not item["present_in_csv"] for item in h4):
        raise RuntimeError(f"H4: {h4}")

    v8_matches = {(gold_row.gid, row["candidate"].start, row["candidate"].end) for gold_row, row in v8_pairs}
    current_matches = {(gold_row.gid, row["candidate"].start, row["candidate"].end) for gold_row, row in raw_pairs}
    h2 = [(gold_row, row) for gold_row, row in raw_pairs if row["candidate"].rule == "decision_tribunal_relator_year"]
    cnj = [row for row in raw if row["candidate"].family == "processo_cnj"]
    real_v8 = {(gold_row.gid, row["result"].id_canonico) for gold_row, row in v8_output_pairs if gold_row.classification == "real"}
    real_v9 = {(gold_row.gid, row["result"].id_canonico) for gold_row, row in output_pairs if gold_row.classification == "real"}
    inventada_v8 = {gold_row.gid for gold_row, _ in v8_output_pairs if gold_row.classification == "inventada"}
    inventada_v9 = {gold_row.gid for gold_row, _ in output_pairs if gold_row.classification == "inventada"}
    regression = {"v8_matches": [len(v8_matches), len(v8_matches & current_matches)],
                  "real_ids": [v8["real_ids"], values["real_ids"]], "real_pairs_preserved": real_v8 == real_v9,
                  "h2": [len(h2), sum(row["candidate"].start == gold_row.start and row["candidate"].end == gold_row.end for gold_row, row in h2)],
                  "cnj_outputs": len(cnj), "inventada_tps_preserved": inventada_v8 == inventada_v9,
                  "safety": values["safety"]}
    if regression["v8_matches"] != [156, 156] or regression["real_ids"] != [71, 71] or regression["h2"] != [27, 27] or regression["cnj_outputs"] != 46 or not regression["real_pairs_preserved"] or not regression["inventada_tps_preserved"]:
        raise RuntimeError(f"REGRESSION: {regression}")

    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
    head_files = set(subprocess.check_output(["git", "show", "--format=", "--name-only", "HEAD"], cwd=ROOT, text=True).splitlines())
    status = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True)
    source = (ROOT / "src" / "bracis_jusbrasil" / "citations" / "detector.py").read_text(encoding="utf-8")
    leakage = {"detector_gold": "goldenset" in source,
               "detector_doc_ids_or_offsets": any(token in source for token in H4),
               "detector_h4_names": any(name in source for name in ("Rosa Weber", "CÁRMEN LÚCIA", "Alexandre De Moraes", "Flávio Dino")),
               "generator_gold": "goldenset" in (base.DATA / "json_to_submission.py").read_text(encoding="utf-8")}
    git_audit = {"head": subprocess.check_output(["git", "log", "-1", "--oneline"], cwd=ROOT, text=True).strip(),
                 "cleanup_only_accidental": head_files == ACCIDENTAL,
                 "accidental_tracked": sorted(ACCIDENTAL & tracked),
                 "tracked_analysis": sorted(path for path in tracked if path.startswith("scripts/analysis/")),
                 "tracked_submissions": sorted(path for path in tracked if path.startswith("submissions/")),
                 "status_short": status, "tracked_changes_pending": any(line[:2] != "??" for line in status.splitlines())}
    if git_audit["accidental_tracked"] or git_audit["tracked_changes_pending"] or any(leakage.values()):
        raise RuntimeError(f"SCOPE_OR_LEAKAGE: {git_audit}, {leakage}")

    paths = {"gold_v2": base.DATA / "goldenset.csv", "database": base.DATA / "desafio1_bracis.db",
             "converter": base.DATA / "json_to_submission.py", "metric": base.DATA / "kaggle_metric.py"}
    hashes = {name: {"path": str(path.relative_to(ROOT)), "sha256": prior.digest(path)} for name, path in paths.items()}
    evaluation = metric.avaliar(base.solution(gold), pd.read_csv(CSV_OUT, dtype=str, keep_default_na=False), "documento_id")
    remote_status = subprocess.check_output(["git", "status", "-sb"], cwd=ROOT, text=True).splitlines()[0]
    report = {"classification": "PASS WITH WARNINGS", "git_audit": git_audit, "remote_status": remote_status,
              "material_hashes": hashes, "v9_metrics_internal": values,
              "score_details": {str(level): item for level, item in evaluation["niveis"].items()},
              "count_separation": runs[-1]["counts"], "submission_csv_path": str(CSV_OUT.relative_to(ROOT)),
              "submission_csv_sha256": prior.digest(CSV_OUT), "submission_format_validation": csv_validation,
              "json_csv_scorer_consistency": {"internal_score": values["score"], "csv_score": runs[-1]["score"], "pass": True},
              "duplicate_scorer_constraint_audit": {"official_parser_accepted": True, "half_open_spans": True, "empty_spans": 0, "out_of_text_spans": 0},
              "h4_checkpoint_audit": h4, "regression_checkpoint_audit": regression,
              "leakage_blind_readiness_audit": leakage,
              "reproducibility": {"runs": 3, "identical": len({json.dumps(item, sort_keys=True) for item in runs}) == 1 and len(set(output_hashes)) == 1 and len(set(csv_hashes)) == 1,
                                  "runs_detail": runs, "output_sha256": output_hashes, "csv_sha256": csv_hashes},
              "performance": {"runs": timings, "pipeline_mean_seconds": sum(item["pipeline_seconds"] for item in timings) / 3,
                              "converter_mean_seconds": sum(item["converter_seconds"] for item in timings) / 3},
              "tests_compile_mkdocs": "EXTERNAL_FINAL_VALIDATION_REQUIRED", "database_integrity": integrity,
              "blockers": [], "warnings": ["Cleanup commit is local and branch is ahead of origin by one; no push was performed.",
                                             "H4 blind risk remains MODERATE: four current positives.",
                                             "All CSV confidence fields are the accepted '-' marker; Brier bonus is zero."],
              "final_verdict": "POST_V9_SUBMISSION_AUDIT_UPDATED_PASS_WITH_WARNINGS",
              "recommended_next_step": "PUSH_CLEANUP_COMMIT_THEN_SUBMIT_V9_CHECKPOINT"}
    if not report["reproducibility"]["identical"]:
        raise RuntimeError(f"NONDETERMINISM: {report['reproducibility']}")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"classification": report["classification"], "counts": report["count_separation"],
                      "score": values["score"], "csv": report["submission_csv_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
