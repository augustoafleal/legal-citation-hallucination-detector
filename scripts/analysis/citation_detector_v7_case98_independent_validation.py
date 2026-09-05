"""Validação independente da V7 contra o commit V6 congelado."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import os
import re
import subprocess
import sys
import time
import types
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
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


BASELINE_COMMIT = "00056f1"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
OUTPUT_PATH = ROOT / "artifacts" / "citation_detector_v7_case98_independent_validation.json"
V6 = {"predictions": 222, "matches": 140, "FP": 82, "FN": 85, "exact": 86, "real_ids": 70, "N1": 0.5985263939056619, "N2": 0.4420774157616263, "final": 0.49422707514297154}
V7 = {"matches": 141, "real_ids": 71, "final": 0.49563800988723955, "safety": {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0}}
CHAIN_SCAN = re.compile(r"(?<!\w)(?:[A-Z]+-){2,}\d{1,8}-\d{2}\.\d{4}\.\d\.\d{2}(?:\.\d{0,4})?(?!\w)")


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def baseline_detector() -> type:
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_COMMIT}:src/bracis_jusbrasil/citations/detector.py"], cwd=ROOT, text=True
    )
    module = types.ModuleType("citation_detector_v6_frozen")
    sys.modules[module.__name__] = module
    exec(compile(source, "citation_detector_v6_frozen.py", "exec"), module.__dict__)
    return module.CitationDetector


def pipeline(detector_type: type, texts: dict[str, str], resolver: CitationResolver) -> tuple[list[Any], list[Any], float]:
    detector, parser = detector_type(), CitationParser()
    raw, outputs = [], []
    started = time.perf_counter()
    for document_id in sorted(texts):
        text = texts[document_id]
        candidates = [CitationCandidate(item.start, item.end, item.text, item.rule, item.family) for item in detector.detect(text)]
        parsed = [parser.parse(candidate, context=text) for candidate in candidates]
        results = [resolver.resolve(item) for item in parsed]
        indexes: dict[int, int] = {}
        for candidate, item, result in zip(candidates, parsed, results):
            indexes[id(candidate)] = len(raw)
            raw.append(post.RawPrediction(len(raw), document_id, candidate, item, result))
        for item in structural_cnj_union_merge(text, candidates, parsed, results, primary_identities=resolver.primary_identities):
            outputs.append(post.Output(len(outputs), document_id, item.candidate, item.parsed, item.resolution, item.reason, tuple(sorted(indexes[id(source)] for source in item.source_candidates))))
    return raw, outputs, time.perf_counter() - started


def score(gold_df: Any, documents: list[str], gold: Sequence[Any], raw: list[Any], outputs: list[Any]) -> dict[str, Any]:
    metric = h1.score_variant(gold_df, documents, gold, raw, outputs)
    submission = official.submission_from_cells(official.output_cells(outputs), documents)
    direct = float(official.METRIC.score(official.solution_from_gold(gold_df), submission, "documento_id"))
    if direct != metric["official_final"]:
        raise RuntimeError(f"scorer direto diverge: {direct} != {metric['official_final']}")
    return {"predictions": metric["predictions"], "matches": metric["official_matches"], "FP": metric["FP"], "FN": metric["FN"], "exact": metric["exact"], "real_ids": metric["real_ids_correct"], "N1": metric["N1"], "N2": metric["N2"], "final": direct, "safety": metric["safety"], "final_outputs": len(outputs)}


def gold_record(gold: Sequence[Any], index: int) -> Any:
    return next(item for item in gold if item.index == index)


def related(items: Sequence[Any], gold: Sequence[Any], index: int) -> list[Any]:
    target = gold_record(gold, index)
    return [item for item in items if item.document_id == target.document_id and post.iou(target.start, target.end, item.candidate.start, item.candidate.end) >= 0.5]


def compound(items: Sequence[Any]) -> list[Any]:
    return [item for item in items if item.candidate.rule == "compound_procedural_chain"]


def snapshot(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [{"document_id": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text, "rule": item.candidate.rule, "family": item.candidate.family, "parsed": post.parsed_dict(item.parsed), "resolver": post.result_dict(item.result)} for item in items]


def independent_adversarials(detector: CitationDetector, parser: CitationParser) -> dict[str, Any]:
    rejected = [
        "E-65-63.2010.5.01.0075",
        "E-ABC-65-63.2010.5.01.0075",
        "ED-TST-RR-65-63.2010.5.01.0075",
        "TST-TST-RR-65-63.2010.5.01.0075",
        "ED - E - RR-65-63.2010.5.01.0075",
        "ED/E/RR-65-63.2010.5.01.0075",
        "ED_E_RR-65-63.2010.5.01.0075",
        "ED.E.RR-65-63.2010.5.01.0075",
        "TST-E-RR-173000-49.2008",
        "ED-E-ED-RR-65-63.2010.5.01",
        "TST-ED-E-ED-ARR-1099-66.2011.5.02.",
        "ABC-E-RR-173000-49.2008.5.15.0024",
        "ED-XYZ-RR-173000-49.2008.5.15.0024",
        "ED-E-FOO-173000-49.2008.5.15.0024",
        "ZZZ-ED-RR-123-45.2020.5.01.0001",
        "ED-E-XYZ-123-45.2020.5.01.0001",
        "RR-FOO-123-45.2020.5.01.0001",
        "TST-ABC-123-45.2020.5.01.0001",
        "ED-ED-ED-RR-65-63.2010.5.01.0075",
        "ED-E-ED-RR-E-RR-65-63.2010.5.01.0075",
        "ED-RR-12345678-23.2020.5.01.0001",
    ]
    rejection_rows = [{"text": text, "compound_count": len([item for item in detector.detect(text) if item.rule == "compound_procedural_chain"])} for text in rejected]
    prefix_rows = []
    for count in range(1, 8):
        text = f"ED-RR-{'1' * count}-23.2020.5.01.0001"
        prefix_rows.append({"digits": count, "compound_count": len([item for item in detector.detect(text) if item.rule == "compound_procedural_chain"])})
    complete = "TST-E-RR-173000-49.2008.5.15.0024"
    complete_candidate = next(item for item in detector.detect(complete) if item.rule == "compound_procedural_chain")
    complete_parsed = parser.parse(complete_candidate)
    manual_rows = []
    for text in ("ED-E-ED-RR-65-63.2010.5.01.0075", "ED-XYZ-RR-65-63.2010.5.01.0075", "TST-E-RR-173000-49.2008"):
        parsed = parser.parse(CitationCandidate(0, len(text), text, "compound_procedural_chain", "processo_ou_recurso_numerado"))
        manual_rows.append({"text": text, "data": dict(parsed.data), "tribunal": parsed.tribunal, "provenance": dict(parsed.provenance)})
    return {"rejected": rejection_rows, "prefixes": prefix_rows, "complete_counterpart": {"candidate": complete_candidate.text, "parsed": post.parsed_dict(complete_parsed)}, "manual_parser": manual_rows}


def corpus_inventory(texts: dict[str, str], v6_raw: Sequence[Any], v7_raw: Sequence[Any]) -> list[dict[str, Any]]:
    by_v6: dict[str, list[Any]] = {}
    by_v7: dict[str, list[Any]] = {}
    for item in v6_raw:
        by_v6.setdefault(item.document_id, []).append(item)
    for item in v7_raw:
        by_v7.setdefault(item.document_id, []).append(item)
    rows = []
    for document_id, text in sorted(texts.items()):
        for match in CHAIN_SCAN.finditer(text):
            span = [match.start(), match.end()]
            v6_overlap = [item for item in by_v6.get(document_id, []) if post.iou(*span, item.candidate.start, item.candidate.end) >= .5]
            v7_overlap = [item for item in by_v7.get(document_id, []) if post.iou(*span, item.candidate.start, item.candidate.end) >= .5]
            candidate = next((item for item in v7_overlap if item.candidate.rule == "compound_procedural_chain"), None)
            rows.append({"document_id": document_id, "span": span, "text": match.group(0), "tokens": re.findall(r"[A-Z]+", match.group(0)), "complete": candidate is not None, "v6_candidates": sorted({item.candidate.rule for item in v6_overlap}), "v7_candidate": None if candidate is None else {"rule": candidate.candidate.rule, "family": candidate.candidate.family, "parsed": post.parsed_dict(candidate.parsed), "resolver": post.result_dict(candidate.result)}})
    return rows


def test_suite() -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, text=True, capture_output=True, env=environment)
    match = re.search(r"Ran (\d+) tests", result.stderr)
    return {"total": int(match.group(1)) if match else None, "passed": result.returncode == 0, "failed": 0 if result.returncode == 0 else None, "output_tail": (result.stdout + result.stderr)[-1000:]}


def build() -> dict[str, Any]:
    if sha256(get_database_path().read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("database hash divergente")
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        gold_df = official.load_gold_df()
        documents = list(gold_df.documento_id.drop_duplicates())
        v6_raw, v6_outputs, v6_seconds = pipeline(baseline_detector(), texts, resolver)
        v7_raw, v7_outputs, v7_seconds = pipeline(CitationDetector, texts, resolver)
        v6_metrics, v7_metrics = score(gold_df, documents, gold, v6_raw, v6_outputs), score(gold_df, documents, gold, v7_raw, v7_outputs)
        if {key: v6_metrics[key] for key in V6} != V6:
            raise RuntimeError(f"V6 baseline divergente: {v6_metrics}")
        detector, parser = CitationDetector(), CitationParser()
        adversarials = independent_adversarials(detector, parser)
        inventory = corpus_inventory(texts, v6_raw, v7_raw)
        v6_signatures = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in v6_raw}
        v7_signatures = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in v7_raw}
        added, removed = v7_signatures - v6_signatures, v6_signatures - v7_signatures
        v6_match, v7_match = post.match_gold(gold, v6_outputs), post.match_gold(gold, v7_outputs)
        target = gold_record(gold, 98)
        target_rows = [item for item in related(v7_outputs, gold, 98) if item.candidate.rule == "compound_procedural_chain"]
        target_output = target_rows[0] if len(target_rows) == 1 else None
        gains = {str(index): any(item.result.status == "resolved" and item.result.id_canonico == gold_record(gold, index).canonical_id for item in related(v7_outputs, gold, index)) for index in (143, 159, 165)}
        unsupported = {str(index): any(item.candidate.rule == "compound_procedural_chain" for item in related(v7_raw, gold, index)) for index in (71, 170, 172)}
        digit_rows = [{"text": item.candidate.text, "raw_digits": re.sub(r"\D", "", item.parsed.data.get("numero_raw", "")), "normalized": item.parsed.data.get("numero_normalizado"), "passed": re.sub(r"\D", "", item.parsed.data.get("numero_raw", "")) == item.parsed.data.get("numero_normalizado")} for item in compound(v7_raw)]
        span_mismatches = [item for item in compound(v7_raw) if texts[item.document_id][item.candidate.start:item.candidate.end] != item.candidate.text]
        added_rows = [item for item in v7_raw if (item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) in added]
        removed_rows = [item for item in v6_raw if (item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) in removed]
        new_fp = [item for item in added_rows if item.index not in set(post.match_gold(gold, v7_raw).values())]
        nonreal_changes = [index for index, item in enumerate(gold) if item.classification != "real" and ((index in v6_match) != (index in v7_match))]
        runs = []
        for _ in range(3):
            raw, outputs, _ = pipeline(CitationDetector, texts, resolver)
            runs.append({"raw": snapshot(raw), "outputs": snapshot(outputs), "score": score(gold_df, documents, gold, raw, outputs)})
        deterministic = all(stable(runs[0]) == stable(run) for run in runs[1:])
        tests = test_suite()
        leakage = subprocess.run(["rg", "-n", "gen_n1_|gen_n2_|goldenset|citacao_id|862182008|65-63[.]2010[.]5[.]01[.]0075", "src/"], cwd=ROOT, text=True, capture_output=True).stdout.splitlines()
        restricted = subprocess.check_output(["git", "diff", "--name-only", BASELINE_COMMIT, "--", "src/bracis_jusbrasil/citations/resolver.py", "src/bracis_jusbrasil/citations/arbitration.py", "src/bracis_jusbrasil/normalization.py", "src/bracis_jusbrasil/database.py", "src/bracis_jusbrasil/cases"], cwd=ROOT, text=True).splitlines()
        diff_files = subprocess.check_output(["git", "diff", "--name-only", BASELINE_COMMIT], cwd=ROOT, text=True).splitlines()
        performance_delta = 100 * (v7_seconds - v6_seconds) / v6_seconds if v6_seconds else None
        performance_class = "NEGLIGIBLE" if performance_delta is not None and abs(performance_delta) <= 5 else "ACCEPTABLE" if performance_delta is not None and performance_delta <= 20 else "MATERIAL" if performance_delta is not None and performance_delta <= 50 else "BLOCKING"
        integrity = {"database_sha256": DB_HASH, "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0], "official_data_diff": subprocess.check_output(["git", "diff", "--name-only", "--", "material_desafio_jusbrasil_bracis"], cwd=ROOT, text=True).splitlines(), "dataset": dict(connection.execute("SELECT natureza, COUNT(*) FROM documentos GROUP BY natureza").fetchall())}
        gates = {"v6_reproduced": True, "scope": not restricted, "adversarials": all(row["compound_count"] == 0 for row in adversarials["rejected"]) and all(row["compound_count"] == 1 for row in adversarials["prefixes"]), "parser_manual": adversarials["manual_parser"][0]["data"].get("numero_normalizado") == "656320105010075" and not adversarials["manual_parser"][1]["data"] and not adversarials["manual_parser"][2]["data"], "corpus": len(inventory) == 7 and sum(row["complete"] for row in inventory) == 6, "case98": bool(target_output and (target_output.candidate.start, target_output.candidate.end) == (target.start, target.end) and target_output.result.id_canonico == 862182008), "v6_matches": set(v6_match) <= set(v7_match), "gains": all(gains.values()), "unsupported": not any(unsupported.values()), "new_detection_fp": not new_fp, "nonreal_regression": not nonreal_changes, "metrics": all(v7_metrics[key] == V7[key] for key in ("matches", "real_ids", "final")), "safety": v7_metrics["safety"] == V7["safety"], "tests": tests["passed"], "determinism": deterministic, "leakage": not leakage, "integrity": integrity["pragma_integrity_check"] == "ok" and not integrity["official_data_diff"]}
        verdict = "V7_VALIDATED_WITH_WARNINGS_FREEZE" if all(gates.values()) else "V7_NOT_READY_FOR_FREEZE"
        return {"repository_state": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(), "diff_files": diff_files}, "environment": {"python": sys.executable, "baseline_commit": BASELINE_COMMIT}, "diff_audit": {"restricted_changes": restricted, "functional": ["src/bracis_jusbrasil/citations/detector.py", "src/bracis_jusbrasil/citations/parser.py"], "support": ["tests/test_citation_detector.py", "tests/test_citation_parser.py", "docs/architecture.md"]}, "v6_reproduction": v6_metrics, "token_grammar": {"enabled": ["TST", "ED", "E", "RR"], "separator": "-", "generic_chain_present": False}, "chain_bounds": {"min": 2, "max": 5, "max_repeated_process_token": 2}, "parser_contract": {"family": "processo_ou_recurso_numerado", "rule": "compound_procedural_chain"}, "independent_adversarials": adversarials, "corpus_inventory": inventory, "candidate_delta": {"V6": len(v6_raw), "V7": len(v7_raw), "exact_preserved": len(v6_signatures & v7_signatures), "added": len(added), "removed": len(removed), "added_rows": snapshot(added_rows), "removed_rows": snapshot(removed_rows)}, "case98": None if target_output is None else {"gold_span": [target.start, target.end], "prediction_span": [target_output.candidate.start, target_output.candidate.end], "text": target_output.candidate.text, "parsed": post.parsed_dict(target_output.parsed), "resolver": post.result_dict(target_output.result)}, "v6_preservation": {"matches": f"{len(set(v6_match) & set(v7_match))}/{len(v6_match)}", "gains": gains}, "unsupported_tail": unsupported, "official_metrics": {"V6": v6_metrics, "V7": v7_metrics, "delta": v7_metrics["final"] - v6_metrics["final"]}, "safety": v7_metrics["safety"], "new_detection_fp": snapshot(new_fp), "inventada_incompleta_changes": nonreal_changes, "span_integrity": {"checked": len(compound(v7_raw)), "mismatches": len(span_mismatches)}, "digit_preservation": digit_rows, "tests": tests, "determinism": {"runs": 3, "identical": deterministic}, "performance": {"V6_seconds": v6_seconds, "V7_seconds": v7_seconds, "delta_percent": performance_delta, "classification": performance_class}, "integrity": integrity, "gold_leakage": leakage, "blind_readiness": not leakage, "gates": gates, "freeze_verdict": verdict, "commit_readiness": "READY_TO_COMMIT" if all(gates.values()) else "NOT_READY_TO_COMMIT"}


def main() -> int:
    report = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"freeze_verdict": report["freeze_verdict"], "commit_readiness": report["commit_readiness"], "gates": report["gates"], "artifact": str(OUTPUT_PATH)}, ensure_ascii=False, sort_keys=True))
    return 0 if report["freeze_verdict"] != "V7_NOT_READY_FOR_FREEZE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
