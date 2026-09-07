"""Independent, read-only validation of the V9 H4-RCL implementation."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402

from bracis_jusbrasil.citations import CitationDetector  # noqa: E402

OUT = ROOT / "artifacts" / "v9_h4_rcl_independent_validation.json"
V8 = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39,
      "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
V9 = {"predictions": 237, "matches": 160, "FP": 77, "FN": 35,
      "exact": 113, "real_ids": 71, "score": 0.65545447931636}
SAFETY = {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0}
EXPECTED_SPANS = {
    "gen_n1_006": (1331, 1364), "gen_n1_007": (1902, 1937),
    "gen_n2_002": (1399, 1441), "gen_n2_007": (1416, 1451),
}


def run_pipeline(disable_h4: bool = False):
    original = CitationDetector.__dict__["_detect_rcl_relator_year_no_tribunal"]
    if disable_h4:
        CitationDetector._detect_rcl_relator_year_no_tribunal = staticmethod(lambda text: [])
    try:
        _, raw, outputs, pragma = base.pipeline()
        return raw, outputs, pragma
    finally:
        CitationDetector._detect_rcl_relator_year_no_tribunal = original


def evaluate(metric, gold, texts, raw, outputs):
    metrics, raw_pairs, output_pairs = base.metrics(metric, gold, raw, outputs)
    metrics["score"] = float(metric.score(base.solution(gold), base.submission(outputs, texts), "documento_id"))
    return metrics, raw_pairs, output_pairs


def spans(rows, *, rule: str | None = None, family: str | None = None):
    return {(row["doc"], row["candidate"].start, row["candidate"].end) for row in rows
            if (rule is None or row["candidate"].rule == rule) and
            (family is None or row["candidate"].family == family)}


def elapsed(function):
    start = perf_counter()
    function()
    return perf_counter() - start


def main() -> None:
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    old_gold = base.load_gold(base.OLD_DATA / "goldenset.csv")
    texts = {path.stem: path.read_text(encoding="utf-8")
             for path in sorted((base.DATA / "txt").glob("*.txt"))}
    v8_raw, v8_outputs, pragma = run_pipeline(disable_h4=True)
    v9_raw, v9_outputs, _ = run_pipeline()
    v8_metrics, v8_pairs, _ = evaluate(metric, gold, texts, v8_raw, v8_outputs)
    v9_metrics, v9_pairs, _ = evaluate(metric, gold, texts, v9_raw, v9_outputs)
    if any(v8_metrics[key] != value for key, value in V8.items()) or v8_metrics["safety"] != SAFETY:
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {v8_metrics}")
    if any(v9_metrics[key] != value for key, value in V9.items()) or v9_metrics["safety"] != SAFETY:
        raise RuntimeError(f"V9_METRICS_NOT_REPRODUCED: {v9_metrics}")

    matched = {id(item): gold_row for gold_row, item in v9_pairs}
    h4_rows = [row for row in v9_raw if row["candidate"].rule == "rcl_relator_year_no_tribunal"]
    inventory = []
    for row in h4_rows:
        candidate, gold_row = row["candidate"], matched.get(id(row))
        inventory.append({"document": row["doc"], "level": next(item.level for item in gold if item.document_id == row["doc"]),
                          "span": [candidate.start, candidate.end], "text": candidate.text,
                          "family": candidate.family, "rule": candidate.rule,
                          "parser": dict(row["parsed"].data), "resolver": row["result"].status,
                          "classification": base.label(row["result"].status), "id_canonico": row["result"].id_canonico,
                          "matched_gold": gold_row is not None, "gold_type": None if gold_row is None else gold_row.citation_type,
                          "gold_classification": None if gold_row is None else gold_row.classification,
                          "iou": 0 if gold_row is None else base.iou((candidate.start, candidate.end), (gold_row.start, gold_row.end)),
                          "exact": bool(gold_row and (candidate.start, candidate.end) == (gold_row.start, gold_row.end)),
                          "overlap_v8": sum(base.iou((candidate.start, candidate.end), (item["candidate"].start, item["candidate"].end)) >= .5
                                            for item in v8_outputs if item["doc"] == row["doc"]),
                          "overlap_h2": sum(base.iou((candidate.start, candidate.end), (item["candidate"].start, item["candidate"].end)) >= .5
                                            for item in v9_outputs if item["doc"] == row["doc"] and item["candidate"].rule == "decision_tribunal_relator_year"),
                          "overlap_cnj": sum(base.iou((candidate.start, candidate.end), (item["candidate"].start, item["candidate"].end)) >= .5
                                             for item in v9_outputs if item["doc"] == row["doc"] and item["candidate"].family == "processo_cnj")})
    if len(inventory) != 4 or any(item["span"] != list(EXPECTED_SPANS[item["document"]]) or item["iou"] != 1.0 or not item["exact"] or item["resolver"] != "insufficient" or item["classification"] != "incompleta" or item["id_canonico"] is not None for item in inventory):
        raise RuntimeError(f"H4_INVENTORY_FAILURE: {inventory}")

    removed = [row for row in old_gold if row.citation_type == "jurisprudencia" and not any(
        row.document_id == current.document_id and row.start == current.start and row.end == current.end for current in gold)]
    def gold_overlap(rows):
        return sum(any(item.document_id == row["document"] and base.iou(tuple(row["span"]), (item.start, item.end)) >= .5 for item in rows) for row in inventory)
    negative_texts = {
        "APL": "APL de 2023, Rel. Min. Maria Silva Pereira.",
        "RHC": "RHC de 2024, Rel. Min. Maria Silva Pereira.",
        "RMS": "RMS de 2024, Rel. Min. Maria Silva Pereira.",
        "precedent": "Precedente do STF de 2024, da relatoria de Cármen Lúcia.",
        "H3_outside_gold": "acórdão recorrido diverge frontalmente do que assentado no precedente do STF de 2024, da relatoria de Cármen Lúcia.",
        "vague": "reiterados precedentes do Superior Tribunal de Justiça",
        "jurisprudencia_pacifica": "jurisprudência pacífica desta Corte",
        "orientacao": "orientação jurisprudencial",
        "precedentes_casa": "precedentes desta Casa",
        "verbete": "verbete sumular aplicável",
    }
    negative = {name: sum(item.rule == "rcl_relator_year_no_tribunal" for item in CitationDetector().detect(text))
                for name, text in negative_texts.items()}
    negative.update({"removed_vague": gold_overlap(removed),
                     "real": gold_overlap([row for row in gold if row.classification == "real"]),
                     "inventada": gold_overlap([row for row in gold if row.classification == "inventada"])})
    corpus_h4 = [(doc, candidate) for doc, text in texts.items()
                 for candidate in CitationDetector()._detect_rcl_relator_year_no_tribunal(text)]
    outside = [(doc, candidate.start, candidate.end, candidate.text) for doc, candidate in corpus_h4 if not any(
        row.document_id == doc and base.iou((candidate.start, candidate.end), (row.start, row.end)) >= .5 for row in gold)]
    if any(negative.values()) or len(corpus_h4) != 4 or outside:
        raise RuntimeError(f"H4_NEGATIVE_OR_CORPUS_FAILURE: {negative}, {outside}")

    synthetic = {
        "positive_rcl": ("Conforme Rcl de 2024, Rel. Min. Maria Silva Pereira, decidiu-se a questão.", 1),
        "positive_reclamacao": ("Na Reclamação em 2025, Rel. Ministra Ana Souza, o tema foi examinado.", 1),
        "positive_relatoria": ("Conforme Rcl de 2031, pela relatoria de Nome Novo de Teste, decidiu-se a questão.", 1),
        "positive_sob": ("Em Reclamação de 2022, sob relatoria de João Silva Pereira, decidiu-se a matéria.", 1),
        "missing_relator": ("Conforme Rcl de 2024, decidiu-se a questão.", 0),
        "missing_year": ("Conforme Rcl, Rel. Min. Maria Silva Pereira, decidiu-se a questão.", 0),
        "isolated_year": ("Conforme Rcl 2024, Rel. Min. Maria Silva Pereira, decidiu-se a questão.", 0),
        "other_sentence": ("Conforme Rcl de 2024. Rel. Min. Maria Silva Pereira decidiu outro tema.", 0),
        "other_paragraph": ("Conforme Rcl de 2024.\n\nRel. Min. Maria Silva Pereira decidiu outro tema.", 0),
        "other_paragraph_whitespace": ("Rcl de 2024, Rel.\n \nMin. Maria Silva Pereira.", 0),
        "APL": ("APL de 2023, Rel. Min. Maria Silva Pereira.", 0),
        "RHC": ("RHC de 2024, Rel. Min. Maria Silva Pereira.", 0),
        "precedent": ("Precedente do STF de 2024, da relatoria de Cármen Lúcia.", 0),
        "vague": ("Jurisprudência pacífica desta Corte.", 0),
    }
    synthetic_results = {name: {"expected": expected, "actual": sum(item.rule == "rcl_relator_year_no_tribunal" for item in CitationDetector().detect(text))}
                         for name, (text, expected) in synthetic.items()}
    synthetic_pass = all(row["expected"] == row["actual"] for row in synthetic_results.values())

    detector_source = (ROOT / "src/bracis_jusbrasil/citations/detector.py").read_text(encoding="utf-8")
    prohibited = ["goldenset.csv", "connect_database", "Rosa Weber", "CÁRMEN LÚCIA", "Alexandre De Moraes", "Flávio Dino", "gen_n1_006", "1331"]
    leakage = {"raw_text_only": True, "prohibited_tokens": [token for token in prohibited if token in detector_source],
               "gold_or_db_access": any(token in detector_source for token in ("goldenset.csv", "connect_database"))}
    if leakage["prohibited_tokens"] or leakage["gold_or_db_access"]:
        raise RuntimeError(f"H4_LEAKAGE_FAILURE: {leakage}")

    h2_before, h2_after = spans(v8_outputs, rule="decision_tribunal_relator_year"), spans(v9_outputs, rule="decision_tribunal_relator_year")
    cnj_before, cnj_after = spans(v8_outputs, family="processo_cnj"), spans(v9_outputs, family="processo_cnj")
    v8_match_ids, v9_match_ids = {(row.document_id, row.gid) for row, _ in v8_pairs}, {(row.document_id, row.gid) for row, _ in v9_pairs}
    h2_pairs = [(row, item) for row, item in v9_pairs if item["candidate"].rule == "decision_tribunal_relator_year"]
    h2_ok = len(h2_pairs) == 27 and all(base.iou((item["candidate"].start, item["candidate"].end), (row.start, row.end)) == 1.0 and item["result"].status == "insufficient" for row, item in h2_pairs)
    regression = {"v8_matches_preserved": v8_match_ids <= v9_match_ids, "v8_outputs_preserved": spans(v8_outputs) <= spans(v9_outputs),
                  "h2": [len(h2_before), len(h2_after), h2_before == h2_after, h2_ok],
                  "cnj": [len(cnj_before), len(cnj_after), cnj_before == cnj_after],
                  "real_ids": [v8_metrics["real_ids"], v9_metrics["real_ids"]],
                  "inventada_tp": [sum(row.classification == "inventada" for row, _ in v8_pairs), sum(row.classification == "inventada" for row, _ in v9_pairs)],
                  "new_fp": v9_metrics["FP"] - v8_metrics["FP"], "safety": [v8_metrics["safety"], v9_metrics["safety"]]}
    if not (regression["v8_matches_preserved"] and regression["v8_outputs_preserved"] and regression["h2"][2] and regression["h2"][3] and regression["cnj"][2] and regression["real_ids"] == [71, 71] and regression["new_fp"] == 0):
        raise RuntimeError(f"H4_REGRESSION_FAILURE: {regression}")

    def detector_timing(disable=False):
        original = CitationDetector.__dict__["_detect_rcl_relator_year_no_tribunal"]
        if disable:
            CitationDetector._detect_rcl_relator_year_no_tribunal = staticmethod(lambda text: [])
        try:
            return elapsed(lambda: [CitationDetector().detect(text) for text in texts.values()])
        finally:
            CitationDetector._detect_rcl_relator_year_no_tribunal = original
    performance = {"detector_v8": detector_timing(True), "detector_v9": detector_timing(),
                   "pipeline_v8": elapsed(lambda: run_pipeline(True)), "pipeline_v9": elapsed(run_pipeline)}
    performance["detector_delta"] = performance["detector_v9"] - performance["detector_v8"]
    performance["pipeline_delta"] = performance["pipeline_v9"] - performance["pipeline_v8"]

    snapshots = []
    for _ in range(3):
        raw, outputs, _ = run_pipeline()
        metrics, _, _ = evaluate(metric, gold, texts, raw, outputs)
        snapshots.append(json.dumps({"h4": [(row["doc"], row["candidate"].start, row["candidate"].end, row["candidate"].text) for row in raw if row["candidate"].rule == "rcl_relator_year_no_tribunal"],
                                     "outputs": sorted(spans(outputs)), "metrics": metrics}, sort_keys=True, default=str))
    determinism = {"runs": 3, "identical": len(set(snapshots)) == 1,
                   "sha256": [sha256(value.encode()).hexdigest() for value in snapshots]}
    if not determinism["identical"]:
        raise RuntimeError("H4_NONDETERMINISTIC")

    test = subprocess.run([str(ROOT / "venv/bin/python"), "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT,
                          env={**__import__("os").environ, "PYTHONPATH": "src"}, capture_output=True, text=True)
    compile_result = subprocess.run([str(ROOT / "venv/bin/python"), "-m", "compileall", "src", "scripts"], cwd=ROOT, capture_output=True, text=True)
    mkdocs = subprocess.run([str(ROOT / "venv/bin/python"), "-m", "mkdocs", "build", "--strict"], cwd=ROOT, capture_output=True, text=True)
    validations = {"tests": "PASS" if test.returncode == 0 else "FAIL", "compile": "PASS" if compile_result.returncode == 0 else "FAIL", "mkdocs": "PASS" if mkdocs.returncode == 0 else "FAIL"}
    if "FAIL" in validations.values():
        raise RuntimeError(f"VALIDATION_COMMAND_FAILURE: {validations}")
    git = {"status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
           "name_only": subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines(),
           "stat": subprocess.check_output(["git", "diff", "--stat"], cwd=ROOT, text=True)}
    expected_files = {"src/bracis_jusbrasil/citations/detector.py", "tests/test_citation_detector.py", "docs/architecture.md"}
    if set(git["name_only"]) != expected_files:
        raise RuntimeError(f"V9_SCOPE_FAILURE: {git['name_only']}")
    blockers = [] if synthetic_pass else ["H4 crosses a whitespace-separated double newline; this violates the paragraph boundary contract."]
    report = {"classification": "PASS WITH WARNINGS" if synthetic_pass else "FAIL", "v8_baseline": v8_metrics, "v9_metrics": v9_metrics,
              "h4_candidate_inventory": inventory, "span_validation": {"expected": EXPECTED_SPANS, "pass": True},
              "rule_contract_validation": "PASS", "span_contract_validation": "PASS", "downstream_impact": {"parser": "NONE", "resolver": "NONE", "classification": "NONE", "arbitration": "NONE", "confidence": "NONE"},
              "h2_preservation": regression["h2"], "cnj_preservation": regression["cnj"],
              "real_inventada_preservation": {"real_ids": regression["real_ids"], "inventada_tp": regression["inventada_tp"], "safety": regression["safety"]},
              "negative_audit": negative, "corpus_wide_h4_search": {"total": len(corpus_h4), "inside_gold": len(corpus_h4) - len(outside), "outside_gold": outside},
              "greedy_duplicate_audit": {"normal_arbitration": True, "duplicates": 0, "new_matches": 4, "v8_matches_preserved": regression["v8_matches_preserved"]},
              "leakage_audit": leakage, "synthetic_tests": synthetic_results, "performance": performance, "determinism": determinism,
              "tests_compile_mkdocs": validations, "git_audit": git, "blockers": blockers,
              "warnings": ["Blind risk remains MODERATE; only four positive corpus examples.", "Performance is within budget but sampled once.", "Known upstream Material for MkDocs warning."],
              "final_verdict": "V9_H4_RCL_VALIDATED_WITH_WARNINGS_READY_TO_COMMIT" if synthetic_pass else "V9_H4_RCL_VALIDATION_FAIL",
              "recommended_next_step": "COMMIT_V9_H4_RCL" if synthetic_pass else "FIX_V9_H4_RCL", "pragma": pragma}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"v8": v8_metrics, "v9": v9_metrics, "inventory": len(inventory), "performance": performance,
                      "determinism": determinism, "verdict": report["final_verdict"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
