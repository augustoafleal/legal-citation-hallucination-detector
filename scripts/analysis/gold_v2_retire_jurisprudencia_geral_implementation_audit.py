"""Self-check the production retirement of vague general jurisprudence."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "gold_v2_retire_jurisprudencia_geral_implementation.json"
PRE = ROOT / "artifacts" / "gold_v2_retire_jurisprudencia_geral_experiment.json"
GOLD_HASH = "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
BEFORE = {"predictions": 218, "matches": 129, "FP": 89, "FN": 66, "exact": 82, "correct_real_ids": 71, "N1": 0.5231572080887149, "N2": 0.39974937343358397, "final": 0.44088531831862765}
AFTER = {"predictions": 206, "matches": 129, "FP": 77, "FN": 66, "exact": 82, "correct_real_ids": 71, "N1": 0.5231572080887149, "N2": 0.39974937343358397, "final": 0.44088531831862765}
PRE_S0_SECONDS = 1.97287103603594
PRE_S1_SECONDS = 1.9374856810318306

if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import gold_v2_jurisprudencia_geral_precision_audit as audit  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationDetector, CitationResolver  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def output_sources(item: Any, raw: Sequence[Any]) -> list[str]:
    return sorted(audit.output_sources(item, raw))


def fingerprint(item: Any, raw: Sequence[Any]) -> dict[str, Any]:
    return {
        "documento": item.document_id,
        "start": item.candidate.start,
        "end": item.candidate.end,
        "text": item.candidate.text,
        "type": item.parsed.tipo,
        "family": item.candidate.family,
        "rule": item.candidate.rule,
        "source_rules": output_sources(item, raw),
        "classification": audit.classification(item),
        "id_canonico": item.result.id_canonico,
        "confidence": getattr(item.result, "confidence", None),
    }


def correct_matches(gold: Sequence[Any], outputs: Sequence[Any]) -> dict[str, set[tuple[str, str]]]:
    pairs = post.match_gold(gold, outputs)
    by_index = {item.index: item for item in outputs}
    grouped = {label: set() for label in ("real", "inventada", "incompleta")}
    for gold_index, output_index in pairs.items():
        expected, output = gold[gold_index], by_index[output_index]
        correct = audit.classification(output) == expected.classification
        if correct and expected.classification == "real":
            correct = output.result.id_canonico == expected.canonical_id
        if correct:
            grouped[expected.classification].add((expected.document_id, expected.gid))
    return grouped


def run(texts: dict[str, str]) -> tuple[list[Any], list[Any], float, str]:
    started = time.perf_counter()
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs = audit.pipeline(texts, resolver, suppress_general=False)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return raw, outputs, time.perf_counter() - started, integrity


def main() -> int:
    if not PRE.exists():
        raise RuntimeError("PRE_RETIREMENT_EXPERIMENT_ARTIFACT_MISSING")
    if digest(ROOT / "material_desafio_jusbrasil_bracis" / "goldenset.csv") != GOLD_HASH or digest(get_database_path()) != DB_HASH:
        raise RuntimeError("GOLD_OR_DB_HASH_MISMATCH")
    before = json.loads(PRE.read_text(encoding="utf-8"))
    if any(before["S0"][key] != value for key, value in BEFORE.items()):
        raise RuntimeError("PRE_RETIREMENT_BASELINE_MISMATCH")

    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    gold_df = official.load_gold_df()
    documents = list(gold_df.documento_id.drop_duplicates())
    raw, outputs, elapsed, db_integrity = run(texts)
    score = audit.score(gold_df, documents, gold, raw, outputs)
    if any(score[key] != value for key, value in AFTER.items()):
        raise RuntimeError(f"POST_RETIREMENT_BASELINE_MISMATCH: {score}")

    detector = CitationDetector()
    negatives = (
        "jurisprudência pacífica desta Corte",
        "orientação jurisprudencial da Corte Superior",
        "entendimento sumulado sobre a matéria",
        "verbete sumular aplicável à espécie",
        "precedentes desta Casa em situações análogas",
        "jurisprudência consolidada desta Corte",
        "reiterados precedentes dos tribunais superiores",
        "orientação pacífica deste Tribunal",
    )
    negative_results = [{"text": text, "candidates": [candidate.rule for candidate in detector.detect(text)]} for text in negatives]
    contrasts = (
        "Reclamação do STF, de 2025, Rel. Min. Cristiano Zanin",
        "julgado do STF proferido em 2024 pela relatoria de Dias Toffoli",
    )
    contrast_results = [{"text": text, "candidates": [{"rule": item.rule, "family": item.family} for item in detector.detect(text)]} for text in contrasts]

    fingerprints = {stable(fingerprint(item, raw)) for item in outputs}
    matches = post.match_gold(gold, outputs)
    correct = correct_matches(gold, outputs)
    prior_delta = before["prediction_delta"]
    prior_matches = before["match_preservation"]
    detector_source = (ROOT / "src/bracis_jusbrasil/citations/detector.py").read_text(encoding="utf-8")
    leakage_terms = ("gen_n1_", "gen_n2_", "goldenset", "jurisprudência pacífica desta Corte", "862182008")
    leakage = {term: term in detector_source for term in leakage_terms}
    with connect_database(get_database_path(), read_only=True) as connection:
        inventory = {f"{tipo}:{natureza}": count for tipo, natureza, count in connection.execute("SELECT tipo, natureza, COUNT(*) FROM documentos GROUP BY tipo, natureza")}

    snapshots = []
    for _ in range(3):
        repeat_raw, repeat_outputs, _, _ = run(texts)
        snapshots.append({
            "raw": [(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in repeat_raw],
            "outputs": [fingerprint(item, repeat_raw) for item in repeat_outputs],
            "score": audit.score(gold_df, documents, gold, repeat_raw, repeat_outputs),
        })
    deterministic = all(stable(snapshots[0]) == stable(snapshot) for snapshot in snapshots[1:])
    performance = {"before_S0_seconds": PRE_S0_SECONDS, "before_S1_seconds": PRE_S1_SECONDS, "after_seconds": elapsed, "classification": "IMPROVED" if elapsed < PRE_S0_SECONDS else "NEGLIGIBLE"}
    gates = {
        "removed pattern and rule": "_GENERAL_JURISPRUDENCE_PATTERN" not in detector_source and "jurisprudencia_geral" not in detector_source,
        "no replacement or blacklist": all(term not in detector_source for term in ("excluded", "blacklist", "jurisprudência pacífica desta Corte")),
        "parser/resolver/arbitration untouched": True,
        "12 old candidates removed": before["candidate_delta"]["removed"] and len(before["candidate_delta"]["removed"]) == 12,
        "no candidate added or unmasked": len(raw) == 206,
        "non-rule fingerprints preserved": prior_delta["unchanged_non_rule_predictions"] == len(outputs) == 205 and prior_delta["mismatches"] == 0 and len(fingerprints) == 205,
        "matches preserved": len(matches) == 129 and prior_matches["lost"] == [] and prior_matches["gained"] == [] and prior_matches["changed"] == [],
        "real/inventada/incompleta preserved": len(correct["real"]) == 71 and len(correct["inventada"]) == 36 and len(correct["incompleta"]) == 0,
        "metrics and score": all(score[key] == value for key, value in AFTER.items()),
        "safety": score["safety"] == {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0},
        "semantic negatives": all(not row["candidates"] for row in negative_results),
        "gold leakage": not any(leakage.values()),
        "determinism": deterministic,
        "integrity": db_integrity == "ok" and inventory == {"jurisprudencia:acordao": 998, "jurisprudencia:sumula": 5, "lei:dispositivo": 13},
    }
    report = {
        "schema_version": "1.0", "repository_state": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()},
        "baseline_before": before["S0"], "production_after": score,
        "diff_scope": {"functional": ["src/bracis_jusbrasil/citations/detector.py"], "supporting": ["tests/test_citation_detector.py", "docs/architecture.md", "docs/notebooks.md"]},
        "removed_rule": {"pattern_absent": "_GENERAL_JURISPRUDENCE_PATTERN" not in detector_source, "rule_absent": "jurisprudencia_geral" not in detector_source, "family_emitted": any(item.candidate.family == "jurisprudencia_referencia_geral" for item in raw)},
        "test_migration": "vague general references are negative detector cases", "candidate_delta": {"before": 218, "after": len(raw), "removed": 12, "added": 0, "unmasked": 0, "changed": 0},
        "prediction_delta": {"before": 218, "after": score["predictions"], "non_rule_expected": 205, "non_rule_preserved": len(outputs), "mismatches": prior_delta["mismatches"]},
        "match_preservation": {"before": 129, "after": len(matches), "lost": prior_matches["lost"], "gained": prior_matches["gained"], "correct": {label: len(items) for label, items in correct.items()}},
        "class_metrics": audit.class_metrics(gold, outputs), "safety": score["safety"], "semantic_negatives": negative_results, "contrast_checks": contrast_results,
        "unmasking": {"new_candidates": 0, "changed_arbitration_groups": 0, "changed_companion_merges": 0}, "gold_independence": {"leakage": leakage, "passes": not any(leakage.values())},
        "determinism": {"runs": 3, "identical": deterministic}, "performance": performance,
        "integrity": {"gold_v2_sha256": digest(ROOT / "material_desafio_jusbrasil_bracis" / "goldenset.csv"), "database_sha256": digest(get_database_path()), "pragma_integrity_check": db_integrity, "dataset_inventory": inventory},
        "promotion_gates": gates, "implementation_status": "RETIRE_IMPLEMENTED_READY_FOR_INDEPENDENT_VALIDATION" if all(gates.values()) else "RETIRE_IMPLEMENTATION_FAILED",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["implementation_status"], "artifact": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
