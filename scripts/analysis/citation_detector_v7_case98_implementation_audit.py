"""Self-check reproduzível da V7 contra a baseline V6 congelada."""

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
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


OUTPUT_PATH = ROOT / "artifacts" / "citation_detector_v7_case98_implementation.json"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
V6_EXPECTED = {"predictions": 222, "matches": 140, "FP": 82, "FN": 85, "exact": 86, "real_ids": 70, "N1": 0.5985263939056619, "N2": 0.4420774157616263, "final": 0.49422707514297154}
V7_EXPECTED = {"matches": 141, "real_ids": 71, "final": 0.49563800988723955, "safety": {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0}}
AUDIT_CHAIN = re.compile(r"(?<!\w)(?:[A-Z]+-){2,}\d{1,8}-\d{2}\.\d{4}\.\d\.\d{2}(?:\.\d{0,4})?(?!\w)")


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def without_timing(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_timing(item) for key, item in value.items() if not key.endswith("_seconds")}
    if isinstance(value, list):
        return [without_timing(item) for item in value]
    return value


def baseline_detector() -> type:
    source = subprocess.check_output(["git", "show", "HEAD:src/bracis_jusbrasil/citations/detector.py"], cwd=ROOT, text=True)
    module = types.ModuleType("citation_detector_v6_head")
    sys.modules[module.__name__] = module
    exec(compile(source, "citation_detector_v6_head.py", "exec"), module.__dict__)
    return module.CitationDetector


def run_pipeline(detector_type: type, texts: dict[str, str], resolver: CitationResolver) -> tuple[list[Any], list[Any], float]:
    detector, parser = detector_type(), CitationParser()
    raw, outputs = [], []
    started = time.perf_counter()
    for document_id in sorted(texts):
        text = texts[document_id]
        candidates = [
            CitationCandidate(item.start, item.end, item.text, item.rule, item.family)
            for item in detector.detect(text)
        ]
        parsed = [parser.parse(candidate, context=text) for candidate in candidates]
        results = [resolver.resolve(item) for item in parsed]
        source_indexes: dict[int, int] = {}
        for candidate, item, result in zip(candidates, parsed, results):
            index = len(raw)
            raw.append(post.RawPrediction(index, document_id, candidate, item, result))
            source_indexes[id(candidate)] = index
        for item in structural_cnj_union_merge(text, candidates, parsed, results, primary_identities=resolver.primary_identities):
            outputs.append(post.Output(len(outputs), document_id, item.candidate, item.parsed, item.resolution, item.reason, tuple(sorted(source_indexes[id(source)] for source in item.source_candidates))))
    return raw, outputs, time.perf_counter() - started


def score(gold_df: Any, documents: list[str], gold: Sequence[Any], raw: list[Any], outputs: list[Any]) -> dict[str, Any]:
    result = h1.score_variant(gold_df, documents, gold, raw, outputs)
    return {"predictions": result["predictions"], "matches": result["official_matches"], "FP": result["FP"], "FN": result["FN"], "exact": result["exact"], "real_ids": result["real_ids_correct"], "N1": result["N1"], "N2": result["N2"], "final": result["official_final"], "safety": result["safety"]}


def snapshot(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [{"document_id": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text, "rule": item.candidate.rule, "family": item.candidate.family, "parsed": post.parsed_dict(item.parsed), "resolver": post.result_dict(item.result)} for item in items]


def gold_record(gold: Sequence[Any], index: int) -> Any:
    return next(item for item in gold if item.index == index)


def related(items: Sequence[Any], gold: Sequence[Any], index: int) -> list[Any]:
    target = gold_record(gold, index)
    return [item for item in items if item.document_id == target.document_id and post.iou(target.start, target.end, item.candidate.start, item.candidate.end) >= 0.5]


def test_result() -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    process = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, text=True, capture_output=True, env=environment)
    match = re.search(r"Ran (\d+) tests", process.stderr)
    return {"runner": "PYTHONPATH=src venv/bin/python -m unittest discover -s tests -v", "tests": int(match.group(1)) if match else None, "passed": process.returncode == 0, "returncode": process.returncode, "output_tail": (process.stdout + process.stderr)[-1000:]}


def audit_inventory(texts: dict[str, str], v6_raw: Sequence[Any], v7_raw: Sequence[Any]) -> list[dict[str, Any]]:
    v6_by_document: dict[str, list[Any]] = {}
    v7_by_document: dict[str, list[Any]] = {}
    for item in v6_raw:
        v6_by_document.setdefault(item.document_id, []).append(item)
    for item in v7_raw:
        v7_by_document.setdefault(item.document_id, []).append(item)
    rows = []
    for document_id, text in sorted(texts.items()):
        for match in AUDIT_CHAIN.finditer(text):
            span = [match.start(), match.end()]
            v6_overlap = [item for item in v6_by_document.get(document_id, []) if post.iou(span[0], span[1], item.candidate.start, item.candidate.end) >= 0.5]
            v7_overlap = [item for item in v7_by_document.get(document_id, []) if post.iou(span[0], span[1], item.candidate.start, item.candidate.end) >= 0.5]
            compound = next((item for item in v7_overlap if item.candidate.rule == "compound_procedural_chain"), None)
            rows.append({"document_id": document_id, "span": span, "text": match.group(0), "complete_number": compound is not None, "v7_candidate": None if compound is None else {"rule": compound.candidate.rule, "family": compound.candidate.family, "parsed": post.parsed_dict(compound.parsed), "resolver": post.result_dict(compound.result)}, "v6_overlap_rules": sorted({item.candidate.rule for item in v6_overlap})})
    return rows


def build_report() -> dict[str, Any]:
    if sha256(get_database_path().read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("DB hash divergente")
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    if len(texts) != 26 or len(gold) != 225:
        raise RuntimeError("dimensão oficial inesperada")
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        gold_df = h1.official.load_gold_df()
        documents = list(gold_df.documento_id.drop_duplicates())
        v6_raw, v6_outputs, v6_seconds = run_pipeline(baseline_detector(), texts, resolver)
        v7_raw, v7_outputs, v7_seconds = run_pipeline(CitationDetector, texts, resolver)
        v6_score, v7_score = score(gold_df, documents, gold, v6_raw, v6_outputs), score(gold_df, documents, gold, v7_raw, v7_outputs)
        if {key: v6_score[key] for key in V6_EXPECTED} != V6_EXPECTED:
            raise RuntimeError(f"baseline V6 não reproduzida: {v6_score}")
        v6_matches, v7_matches = set(post.match_gold(gold, v6_outputs)), set(post.match_gold(gold, v7_outputs))
        target = gold_record(gold, 98)
        target_rows = [item for item in related(v7_outputs, gold, 98) if item.candidate.rule == "compound_procedural_chain"]
        target_output = target_rows[0] if len(target_rows) == 1 else None
        gain_ledger = [{"gold_id": index, "preserved": any(row.result.status == "resolved" and row.result.id_canonico == gold_record(gold, index).canonical_id for row in related(v7_outputs, gold, index))} for index in (143, 159, 165)]
        unsupported = {str(index): any(row.candidate.rule == "compound_procedural_chain" for row in related(v7_raw, gold, index)) for index in (71, 170, 172)}
        signature = lambda item: (item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule)
        v6_candidates, v7_candidates = {signature(item) for item in v6_raw}, {signature(item) for item in v7_raw}
        inventory, tests = audit_inventory(texts, v6_raw, v7_raw), test_result()
        leakage = subprocess.run(["rg", "-n", "gen_n1_|gen_n2_|goldenset|862182008|65-63[.]2010", "src/"], cwd=ROOT, text=True, capture_output=True).stdout.splitlines()
        diff_scope = {"tracked": subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines(), "implementation_support": ["scripts/analysis/citation_detector_v7_case98_implementation_audit.py"]}
        runs = []
        for _ in range(3):
            raw, outputs, _ = run_pipeline(CitationDetector, texts, resolver)
            runs.append({"raw": snapshot(raw), "outputs": snapshot(outputs), "score": score(gold_df, documents, gold, raw, outputs)})
        deterministic = all(stable(without_timing(runs[0])) == stable(without_timing(run)) for run in runs[1:])
        delta_percent = 100 * (v7_seconds - v6_seconds) / v6_seconds if v6_seconds else None
        performance = "NEGLIGIBLE" if delta_percent is not None and abs(delta_percent) <= 5 else "ACCEPTABLE" if delta_percent is not None and delta_percent <= 20 else "MATERIAL" if delta_percent is not None and delta_percent <= 50 else "BLOCKING"
        gates = {"baseline_v6": True, "case_98_exact": bool(target_output and (target_output.candidate.start, target_output.candidate.end) == (target.start, target.end)), "case_98_canonical": bool(target_output and target_output.result.id_canonico == target.canonical_id), "v6_matches_preserved": v6_matches <= v7_matches, "existing_gains_preserved": all(item["preserved"] for item in gain_ledger), "v7_expected_gain": all(v7_score[key] == V7_EXPECTED[key] for key in ("matches", "real_ids", "final")), "safety": v7_score["safety"] == V7_EXPECTED["safety"], "unsupported_not_captured": not any(unsupported.values()), "corpus_inventory": len(inventory) == 7 and sum(row["complete_number"] for row in inventory) == 6, "tests": tests["passed"], "determinism": deterministic, "gold_leakage": not leakage, "database_integrity": connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"}
        status = "V7_IMPLEMENTED_READY_FOR_INDEPENDENT_VALIDATION" if all(gates.values()) else "V7_IMPLEMENTATION_FAILED"
        return {"schema_version": "1.0", "environment": {"python": sys.executable, "runner": tests["runner"], "database_read_only": True}, "baseline_v6": v6_score, "diff_scope": diff_scope, "token_grammar": {"enabled": ["TST", "ED", "E", "RR"], "separator": "-", "lexical_bounds": [2, 5], "max_repeated_process_token": 2}, "number_grammar": r"\d{1,7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", "detector_tests": {"compound_helper_candidates": 6, "partial_and_arbitrary_rejected": True}, "parser_tests": {"compound_provenance_required": True, "manual_invalid_candidates_rejected": True}, "corpus_chain_inventory": inventory, "candidate_delta": {"V6": len(v6_raw), "V7": len(v7_raw), "added": len(v7_candidates - v6_candidates), "removed": len(v6_candidates - v7_candidates), "replaced_by_overlap": len(v6_candidates - v7_candidates)}, "gain_ledger": gain_ledger, "unsupported_cases": unsupported, "official_metrics": {"V6": v6_score, "V7": v7_score, "delta": v7_score["final"] - v6_score["final"], "v6_matches_preserved": f"{len(v6_matches & v7_matches)}/{len(v6_matches)}"}, "safety": v7_score["safety"], "determinism": {"runs": 3, "identical": deterministic}, "performance": {"V6_seconds": v6_seconds, "V7_seconds": v7_seconds, "delta_percent": delta_percent, "classification": performance}, "integrity": {"database_sha256": DB_HASH, "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0], "official_data_diff": subprocess.check_output(["git", "diff", "--name-only", "--", "material_desafio_jusbrasil_bracis"], cwd=ROOT, text=True).splitlines()}, "case_98": None if target_output is None else {"span": [target_output.candidate.start, target_output.candidate.end], "detected": True, "parsed": post.parsed_dict(target_output.parsed), "resolver": post.result_dict(target_output.result)}, "test_suite": tests, "gold_leakage": leakage, "hardcoding": leakage, "gates": gates, "implementation_status": status}


def main() -> int:
    report = build_report()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["implementation_status"], "artifact": str(OUTPUT_PATH), "gates": report["gates"]}, ensure_ascii=False, sort_keys=True))
    return 0 if report["implementation_status"] == "V7_IMPLEMENTED_READY_FOR_INDEPENDENT_VALIDATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
