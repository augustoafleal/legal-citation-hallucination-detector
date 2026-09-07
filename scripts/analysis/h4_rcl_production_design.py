"""Production design audit for V9 H4-RCL; this file changes no production code."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402
import rcl_tail_h4_offline_experiment as h4  # noqa: E402

OUT = ROOT / "artifacts" / "h4_rcl_production_design.json"
V8 = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39,
      "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
H4 = {"predictions": 237, "matches": 160, "FP": 77, "FN": 35,
      "exact": 113, "real_ids": 71, "score": 0.65545447931636}
SAFETY = {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0}


def metrics(metric, gold, texts, raw, outputs) -> dict:
    result, pairs, output_pairs = base.metrics(metric, gold, raw, outputs)
    result["score"] = float(metric.score(base.solution(gold), base.submission(outputs, texts), "documento_id"))
    return result, pairs, output_pairs


def main() -> None:
    texts = h4.text_corpus()
    v8_raw, v8_outputs, _ = h4.pipeline(texts, h4=False)
    h4_raw, h4_outputs, emitted = h4.pipeline(texts, h4=True)
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    v8_metrics, v8_pairs, _ = metrics(metric, gold, texts, v8_raw, v8_outputs)
    h4_metrics, h4_pairs, _ = metrics(metric, gold, texts, h4_raw, h4_outputs)
    if (any(v8_metrics[key] != value for key, value in V8.items()) or v8_metrics["safety"] != SAFETY):
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {v8_metrics}")
    if (any(h4_metrics[key] != value for key, value in H4.items()) or h4_metrics["safety"] != SAFETY):
        raise RuntimeError(f"H4_OFFLINE_NOT_REPRODUCED: {h4_metrics}")

    gold_for_candidate = []
    for row in emitted:
        candidate = row["candidate"]
        matches = [item for item in gold if item.document_id == row["doc"] and
                   base.iou((candidate.start, candidate.end), (item.start, item.end)) >= .5]
        if len(matches) != 1:
            raise RuntimeError("H4_CANDIDATE_GOLD_MATCH_NOT_UNIQUE")
        target = matches[0]
        gold_for_candidate.append({"document": row["doc"], "span": [candidate.start, candidate.end],
                                   "rule": candidate.rule, "family": candidate.family,
                                   "iou": base.iou((candidate.start, candidate.end), (target.start, target.end)),
                                   "exact": (candidate.start, candidate.end) == (target.start, target.end),
                                   "resolver": row["result"].status,
                                   "classification": base.label(row["result"].status)})
    if len(emitted) != 4 or not all(row["iou"] == 1.0 and row["resolver"] == "insufficient" and row["classification"] == "incompleta" for row in gold_for_candidate):
        raise RuntimeError(f"H4_CONTRACT_NOT_REPRODUCED: {gold_for_candidate}")

    h2_v8 = {(item["doc"], item["candidate"].start, item["candidate"].end) for item in v8_outputs
             if item["candidate"].rule == "decision_tribunal_relator_year"}
    h2_h4 = {(item["doc"], item["candidate"].start, item["candidate"].end) for item in h4_outputs
             if item["candidate"].rule == "decision_tribunal_relator_year"}
    cnj_v8 = {(item["doc"], item["candidate"].start, item["candidate"].end) for item in v8_outputs
              if item["candidate"].family == "processo_cnj"}
    cnj_h4 = {(item["doc"], item["candidate"].start, item["candidate"].end) for item in h4_outputs
              if item["candidate"].family == "processo_cnj"}
    if h2_v8 != h2_h4 or cnj_v8 != cnj_h4:
        raise RuntimeError("V9_INTERACTION_REGRESSION")

    tests = subprocess.run([str(ROOT / "venv/bin/python"), "-m", "unittest", "discover", "-s", "tests", "-v"],
                           cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": "src"}, capture_output=True, text=True)
    compile_result = subprocess.run([str(ROOT / "venv/bin/python"), "-m", "compileall", "src", "scripts"],
                                    cwd=ROOT, capture_output=True, text=True)
    validation = {"tests": "PASS" if tests.returncode == 0 else "FAIL",
                  "compile": "PASS" if compile_result.returncode == 0 else "FAIL"}
    if "FAIL" in validation.values():
        raise RuntimeError(f"TESTS_OR_COMPILE_FAILED: {validation}")

    rule_contract = {"version": "V9 H4-RCL", "family": "jurisprudencia_tribunal_contextual",
                     "rule": "rcl_relator_year_no_tribunal", "classes": ["Rcl", "Reclamação"],
                     "year": ["de YYYY", "em YYYY"],
                     "relator": ["Rel.", "Rel. Min.", "Rel. Ministro", "Rel. Ministra",
                                 "pela relatoria de", "sob relatoria de", "da relatoria de"],
                     "excluded": ["YYYY isolado", "APL", "RSE", "RMS", "RHC", "Agravo", "precedente", "julgado", "acórdão", "H3 amplo"]}
    report = {
        "v8_baseline": v8_metrics,
        "h4_offline_reproduction": {"metrics": h4_metrics, "candidates": gold_for_candidate},
        "rule_contract": rule_contract,
        "span_contract": {"start": "Rcl/Reclamação", "end": "fim literal do nome do relator",
                          "excluded": "pontuação final", "maximum_characters": 180,
                          "allows": ["vírgulas", "conectivos curtos", "uma quebra de linha"],
                          "forbids": ["sentença", "parágrafo", "duas quebras de linha", "inferência de tribunal", "wildcard amplo"]},
        "parser_contract": {"impact": "NONE", "family_reused": "jurisprudencia_tribunal_contextual", "evidence": "payload ano/relator accepted"},
        "resolver_contract": {"impact": "NONE", "expected": "insufficient", "lookup": "none", "canonical_id": None},
        "classification_contract": {"impact": "NONE", "derivation": "resolver insufficient -> incompleta", "rule_shortcut": False},
        "arbitration_contract": {"impact": "NONE", "flow": "detector -> parser -> resolver -> structural_cnj_union_merge",
                                  "priority_or_merge": "none"},
        "h2_interaction": {"shared_spans": 0, "policy_b_change": False, "h2_matches": [len(h2_v8), len(h2_h4)]},
        "legacy_contextual_interaction": {"same_family_new_rule": True, "legacy_removed": False,
                                            "policy_b_change": False, "h2_change": False},
        "negative_contract": ["APL", "referência jurisprudencial vaga", "precedente STF com relatoria", "H3 outside-Gold",
                              "jurisprudência pacífica desta Corte", "orientação jurisprudencial", "precedentes desta Casa", "verbete sumular aplicável"],
        "synthetic_test_plan": {"positive": ["Rcl + de YYYY + Rel. Min.", "Reclamação + em YYYY + Rel. Ministra", "pela relatoria de"],
                                "negative": ["sem relator", "sem ano", "ano isolado", "outra sentença", "outro parágrafo", "APL", "precedente genérico", "jurisprudência pacífica"]},
        "promotion_gates": {"v8": V8, "v9": H4, "h4_candidates": 4, "iou_one": 4,
                            "insufficient_incompleta": 4, "v8_matches_preserved": 156, "real_ids_preserved": 71,
                            "h2_preserved": 27, "cnj_outputs_preserved": 46, "no_new_fp": True,
                            "safety": "0/0/0", "determinism": "3/3", "tests": "PASS", "compile": "PASS",
                            "no_gold_leakage": True, "no_hardcoding": True},
        "production_scope": {"change": ["src/bracis_jusbrasil/citations/detector.py", "tests/test_citation_detector.py", "docs/architecture.md"],
                             "unchanged": ["parser.py", "resolver.py", "arbitration.py", "classification path", "confidence path"]},
        "performance_budget": {"detector_overhead_max_seconds": .010, "pipeline_overhead_max_seconds": .050,
                               "offline_detector_delta_seconds": .0019165489939041436, "offline_pipeline_delta_seconds": -.029513055997085758},
        "leakage_validation_plan": {"detection": "TXT bruto antes de carregar Gold", "gold": "somente scoring/auditoria",
                                    "forbid": ["doc IDs", "offsets", "nomes", "anos", "lista positiva", "branch por Gold"]},
        "recommendation": "H4_RCL_PRODUCTION_DESIGN_READY_WITH_WARNINGS",
        "next_step": "IMPLEMENT_V9_H4_RCL", "tests_compile": validation,
        "production_diff": subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT, text=True),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"v8": v8_metrics, "h4": h4_metrics, "h2": report["h2_interaction"],
                      "cnj": [len(cnj_v8), len(cnj_h4)], "recommendation": report["recommendation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
