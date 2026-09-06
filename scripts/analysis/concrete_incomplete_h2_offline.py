"""Reproducible, offline-only implementation of the H2 concrete-incomplete test.

This module is intentionally confined to ``scripts/analysis``.  It composes the
current production pipeline without changing it, and injects H2 candidates only
in memory.  Its JSON artifact is the sole output.
"""

from __future__ import annotations

import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "concrete_incomplete_h2_offline.json"

if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

# The prior H2 module is a local, offline library.  It contains the bounded
# grammar and in-memory Parser -> Resolver -> Arbitration implementation.
import concrete_incomplete_h2_experiment as h2  # noqa: E402


def metric_view(metrics: dict[str, Any]) -> dict[str, Any]:
    """Keep exactly the public scorecard fields, including safety."""
    return {key: metrics[key] for key in (*h2.BASELINE, "safety")}


def evaluate(texts: dict[str, str], gold: list[Any], gold_df: Any, documents: list[str], raw: list[Any], outputs: list[Any]) -> dict[str, Any]:
    return h2.baseline.metrics(gold_df, documents, gold, raw, outputs)


def h2_output_rows(outputs: list[Any], matches: dict[int, Any]) -> list[dict[str, Any]]:
    """Produce a complete, directly auditable inventory of temporary candidates."""
    rows = []
    for output in outputs:
        if output.candidate.rule != "decision_tribunal_relator_year":
            continue
        gold = matches.get(output.index)
        rows.append(
            {
                "gold_id": None if gold is None else gold.gid,
                "document_id": output.document_id,
                "span": [output.candidate.start, output.candidate.end],
                "text": output.candidate.text,
                "family": output.candidate.family,
                "rule": output.candidate.rule,
                "iou": 0.0 if gold is None else h2.post.iou(gold.start, gold.end, output.candidate.start, output.candidate.end),
                "parser": h2.post.parsed_dict(output.parsed),
                "resolver": h2.post.result_dict(output.result),
                "classification": h2.class_label(output),
            }
        )
    return rows


def overlap_audit(values: list[Any], raw: list[Any]) -> list[dict[str, Any]]:
    candidates = [item for item in raw if item.candidate.rule == "decision_tribunal_relator_year"]
    findings = []
    for item in values:
        overlaps = [
            [candidate.candidate.start, candidate.candidate.end]
            for candidate in candidates
            if candidate.document_id == item.document_id
            and h2.post.iou(item.start, item.end, candidate.candidate.start, candidate.candidate.end) >= 0.5
        ]
        findings.append({"gold_id": item.gid, "document_id": item.document_id, "text": item.text, "h2_overlaps": overlaps})
    return findings


def fingerprint(outputs: list[Any], raw: list[Any], *, synthetic: bool) -> dict[str, Any]:
    return {
        h2.stable(h2.output_fingerprint(item, raw)): h2.output_fingerprint(item, raw)
        for item in outputs
        if (item.candidate.rule == "decision_tribunal_relator_year") == synthetic
    }


def main() -> int:
    if h2.digest(h2.DATA / "goldenset.csv") != h2.GOLD_HASH or h2.digest(h2.get_database_path()) != h2.DB_HASH:
        raise RuntimeError("GOLD_OR_DB_INTEGRITY_MISMATCH")

    texts, gold = h2.post.deep.load_texts(), h2.post.deep.load_gold()
    gold_df = h2.official.load_gold_df()
    documents = list(gold_df.documento_id.drop_duplicates())

    base_raw, base_outputs, base_elapsed, base_pragma = h2.current_pipeline(texts)
    base_metrics = evaluate(texts, gold, gold_df, documents, base_raw, base_outputs)
    if any(base_metrics[key] != expected for key, expected in h2.BASELINE.items()):
        raise RuntimeError(f"BASELINE_NOT_REPRODUCED: {metric_view(base_metrics)}")

    candidate_raw, candidate_outputs, h2_elapsed, h2_pragma = h2.run_h2_pipeline(texts)
    candidate_metrics = evaluate(texts, gold, gold_df, documents, candidate_raw, candidate_outputs)
    match_indexes = h2.post.match_gold(gold, candidate_outputs)
    # post.match_gold is keyed by gold-list index and stores output index.
    matched_gold = {output_index: gold[gold_index] for gold_index, output_index in match_indexes.items()}
    inventory = h2_output_rows(candidate_outputs, matched_gold)

    old_gold = h2.read_gold(h2.OLD / "goldenset.csv")
    current_ids = {(item.document_id, item.gid) for item in gold}
    removed_vague = [
        item for item in old_gold
        if item.citation_type == "jurisprudencia" and item.classification == "incompleta"
        and (item.document_id, item.gid) not in current_ids
    ]
    real_overlap = overlap_audit([item for item in gold if item.classification == "real"], candidate_raw)
    invented_overlap = overlap_audit([item for item in gold if item.classification == "inventada"], candidate_raw)
    vague_overlap = overlap_audit(removed_vague, candidate_raw)
    corpus_externals = [
        {"document_id": doc, "span": [candidate.start, candidate.end], "text": candidate.text}
        for doc, text in texts.items()
        for candidate in h2.detect_h2(text)
        if not any(
            candidate.start < item.end and item.start < candidate.end
            for item in gold if item.document_id == doc
        )
    ]

    base_preserved = fingerprint(base_outputs, base_raw, synthetic=False)
    h2_preserved = fingerprint(candidate_outputs, candidate_raw, synthetic=False)
    safety_keys = ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")

    runs: list[dict[str, Any]] = []
    baseline_pipeline, h2_pipeline, baseline_detector, h2_detector = [], [], [], []
    for _ in range(3):
        repeat_base_raw, repeat_base_outputs, elapsed_base, _ = h2.current_pipeline(texts)
        repeat_h2_raw, repeat_h2_outputs, elapsed_h2, _ = h2.run_h2_pipeline(texts)
        baseline_pipeline.append(elapsed_base)
        h2_pipeline.append(elapsed_h2)
        started = time.perf_counter()
        detected_base = sum(len(h2.CitationDetector().detect(text)) for text in texts.values())
        baseline_detector.append(time.perf_counter() - started)
        started = time.perf_counter()
        detected_h2 = sum(len(h2.CitationDetector().detect(text)) + len(h2.detect_h2(text)) for text in texts.values())
        h2_detector.append(time.perf_counter() - started)
        runs.append({
            "metrics": metric_view(evaluate(texts, gold, gold_df, documents, repeat_h2_raw, repeat_h2_outputs)),
            "outputs": [h2.output_fingerprint(item, repeat_h2_raw) for item in repeat_h2_outputs],
        })

    determinism = len({h2.stable(item) for item in runs}) == 1
    production_diff = subprocess.check_output(["git", "diff", "HEAD", "--", "src"], cwd=ROOT, text=True)
    checks = {
        "baseline_reproduced": all(base_metrics[key] == expected for key, expected in h2.BASELINE.items()),
        "candidate_count_27": len(inventory) == 27,
        "iou_27_of_27_exact": len(inventory) == 27 and all(row["iou"] == 1.0 for row in inventory),
        "resolver_27_insufficient": len(inventory) == 27 and all(row["resolver"]["status"] == "insufficient" for row in inventory),
        "classification_27_incompleta": len(inventory) == 27 and all(row["classification"] == "incompleta" for row in inventory),
        "real_overlap_zero": not any(row["h2_overlaps"] for row in real_overlap),
        "inventada_overlap_zero": not any(row["h2_overlaps"] for row in invented_overlap),
        "removed_vague_overlap_zero": not any(row["h2_overlaps"] for row in vague_overlap),
        "existing_205_outputs_preserved": len(base_preserved) == len(h2_preserved) == 205 and base_preserved == h2_preserved,
        "safety_zero": all(candidate_metrics["safety"][key] == 0 for key in safety_keys),
        "determinism_3_of_3": determinism,
        "production_diff_none": not production_diff,
    }
    functional_checks = {key: value for key, value in checks.items() if key not in {"determinism_3_of_3", "production_diff_none"}}
    verdict = "READY_FOR_V8_IMPLEMENTATION" if all(checks.values()) else "NEEDS_H2_REFINEMENT"
    report = {
        "scope": "offline only; no production, test, documentation, material, SQLite, or gold mutation",
        "environment": {
            "gold_sha256": h2.digest(h2.DATA / "goldenset.csv"),
            "database_sha256": h2.digest(h2.get_database_path()),
            "baseline_pragma_integrity_check": base_pragma,
            "h2_pragma_integrity_check": h2_pragma,
        },
        "contract": {
            "family": "jurisprudencia_concrete_incomplete",
            "rule": "decision_tribunal_relator_year",
            "parser_payload_family": "jurisprudencia_tribunal_contextual",
            "grammar": {
                "decision": h2._DECISION.pattern,
                "tribunal": h2._COURT.pattern,
                "relator_label": h2._RELATOR_LABEL.pattern,
                "year": h2._YEAR.pattern,
                "locality": "single clause; punctuation and two newlines reject the candidate",
            },
        },
        "baseline": metric_view(base_metrics),
        "h2": metric_view(candidate_metrics),
        "delta": {key: candidate_metrics[key] - base_metrics[key] for key in ("predictions", "matches", "FP", "FN", "exact", "correct_real_ids", "N1", "N2", "final")},
        "candidate_inventory": inventory,
        "downstream": {
            "parser": "27 temporary H2 candidates mapped in memory to the existing contextual tribunal parser payload",
            "resolver": {"insufficient": sum(row["resolver"]["status"] == "insufficient" for row in inventory)},
            "classification": {"incompleta": sum(row["classification"] == "incompleta" for row in inventory)},
        },
        "overlap_audit": {
            "real": real_overlap,
            "inventada": invented_overlap,
            "removed_vague": vague_overlap,
            "non_gold_corpus_candidates": corpus_externals,
        },
        "preservation": {"baseline_outputs": len(base_preserved), "h2_non_synthetic_outputs": len(h2_preserved), "identical": base_preserved == h2_preserved},
        "safety": candidate_metrics["safety"],
        "performance_seconds_median_3_runs": {
            "detector": {"baseline": statistics.median(baseline_detector), "h2": statistics.median(h2_detector), "delta": statistics.median(h2_detector) - statistics.median(baseline_detector), "baseline_count": detected_base, "h2_count": detected_h2},
            "pipeline": {"baseline": statistics.median(baseline_pipeline), "h2": statistics.median(h2_pipeline), "delta": statistics.median(h2_pipeline) - statistics.median(baseline_pipeline)},
            "first_run_reference": {"baseline_pipeline": base_elapsed, "h2_pipeline": h2_elapsed},
        },
        "determinism": {"runs": 3, "identical": determinism},
        "production_diff": "NONE" if not production_diff else production_diff,
        "checks": checks,
        "classification": "PASS WITH WARNINGS" if all(checks.values()) else "FAIL",
        "promotion_readiness": verdict,
        "blockers": [],
        "warnings": [
            "Pipeline median rises by the measured delta; set a production performance budget before V8 promotion.",
            "The offline harness maps the temporary family to the current contextual parser payload; V8 must make that integration explicit.",
        ] if all(functional_checks.values()) else [],
        "primary_recommendation": "Implement H2 as V8 with the bounded grammar and preserve the measured regression tests.",
        "next_step": "IMPLEMENT_V8" if verdict == "READY_FOR_V8_IMPLEMENTATION" else "REFINE_H2",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(OUT), "classification": report["classification"], "promotion_readiness": verdict, "failed_checks": [key for key, value in checks.items() if not value]}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
