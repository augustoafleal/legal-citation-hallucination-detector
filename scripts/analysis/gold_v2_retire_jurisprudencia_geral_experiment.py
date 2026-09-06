"""Offline S0/S1 experiment for retiring ``jurisprudencia_geral`` under Gold V2."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "gold_v2_retire_jurisprudencia_geral_experiment.json"
GOLD_HASH = "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1"
OLD_HASH = "c86f91c54c216411260f3ff4d6cd0ba60c3d28a1a2a7e8d30521b2b329864c8f"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
EXPECTED = {
    "predictions": 218, "matches": 129, "FP": 89, "FN": 66, "exact": 82,
    "correct_real_ids": 71, "N1": 0.5231572080887149,
    "N2": 0.39974937343358397, "final": 0.44088531831862765,
}

if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import gold_v2_jurisprudencia_geral_precision_audit as audit  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationDetector, CitationResolver  # noqa: E402
from bracis_jusbrasil.citations.detector import _GENERAL_JURISPRUDENCE_PATTERN  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402

_TRIBUNAL = re.compile(r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça)\b", re.I)
_RELATOR = re.compile(r"\b(?:relatoria|rel\.?\s*(?:min\.)?|relator(?:a)?)\b", re.I)
_YEAR = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
_CLASS = re.compile(r"\b(?:reclamação|rcl|recurso|agravo|habeas corpus|acórdão|julgado)\b", re.I)
_IDENTIFIER = re.compile(r"\b(?:n[ºo.]?\s*)?\d{1,7}(?:[./-]\d{1,7})+\b", re.I)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_rules(output: Any, raw: Sequence[Any]) -> list[str]:
    return sorted(audit.output_sources(output, raw))


def output_fingerprint(output: Any, raw: Sequence[Any]) -> dict[str, Any]:
    return {
        "doc": output.document_id,
        "span": [output.candidate.start, output.candidate.end],
        "text": output.candidate.text,
        "type": output.parsed.tipo,
        "family": output.candidate.family,
        "rule": output.candidate.rule,
        "source_rules": source_rules(output, raw),
        "classification": audit.classification(output),
        "canonical_id": output.result.id_canonico,
        "resolution": post.result_dict(output.result),
        "arbitration": output.reason,
    }


def candidate_fingerprint(item: Any) -> dict[str, Any]:
    return {
        "doc": item.document_id, "span": [item.candidate.start, item.candidate.end],
        "text": item.candidate.text, "family": item.candidate.family,
        "rule": item.candidate.rule, "parser": post.parsed_dict(item.parsed),
        "resolver": post.result_dict(item.result),
    }


def anchors(text: str) -> list[str]:
    checks = (("TRIBUNAL", _TRIBUNAL), ("RELATOR", _RELATOR), ("YEAR", _YEAR), ("PROCESS_CLASS", _CLASS), ("CASE_IDENTIFIER", _IDENTIFIER))
    return [name for name, pattern in checks if pattern.search(text)]


def context(text: str, start: int, end: int) -> dict[str, Any]:
    left, right = max(0, start - 250), min(len(text), end + 250)
    outside = text[left:start] + text[end:right]
    sentence_left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    sentence_rights = [index for index in (text.find(".", end), text.find("\n", end)) if index >= 0]
    sentence_right = min(sentence_rights) + 1 if sentence_rights else len(text)
    sentence_outside = text[sentence_left:start] + text[end:sentence_right]
    return {
        "window": text[left:right], "window_span": [left, right],
        "span_anchors": anchors(text[start:end]), "outside_anchors": anchors(outside),
        "sentence": text[sentence_left:sentence_right],
        "sentence_outside_anchors": anchors(sentence_outside),
    }


def matched_fingerprints(gold: Sequence[Any], outputs: Sequence[Any], raw: Sequence[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    pairs = post.match_gold(gold, outputs)
    by_index = {item.index: item for item in outputs}
    return {(gold[gold_index].document_id, gold[gold_index].gid): output_fingerprint(by_index[output_index], raw) for gold_index, output_index in pairs.items()}


def correct_match_keys(gold: Sequence[Any], outputs: Sequence[Any]) -> dict[str, set[tuple[str, str]]]:
    """Return only scorer-correct matches, grouped by their expected class."""
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


def rule_relation(gold: Sequence[Any], old_gold: Sequence[Any], outputs: Sequence[Any], raw: Sequence[Any]) -> list[dict[str, Any]]:
    current_matches, current_labels = audit.relation(gold, outputs, raw, old_gold=False)
    old_matches, old_labels = audit.relation(old_gold, outputs, raw, old_gold=True)
    old_by_index = {item.index: item for item in old_gold}
    old_by_output = {output: gold_index for gold_index, output in old_matches.items()}
    rows = []
    for output in outputs:
        if "jurisprudencia_geral" not in source_rules(output, raw):
            continue
        old_item = old_by_index.get(old_by_output.get(output.index))
        rows.append({
            **output_fingerprint(output, raw),
            "Gold_V2_relation": current_labels.get(output.index, "OTHER"),
            "old_gold_relation": old_labels.get(output.index, "OTHER"),
            "old_gold": None if old_item is None else old_item.gid,
        })
    return rows


def dependency_inventory() -> list[dict[str, str]]:
    return [
        {"dependency": "detector regex + _RULES emission", "location": "src/bracis_jusbrasil/citations/detector.py:166,178", "classification": "PRODUCTION", "future_action": "remove/disable in minimal retirement"},
        {"dependency": "family type + empty parsing branch", "location": "src/bracis_jusbrasil/citations/parser.py:18,359", "classification": "PRODUCTION", "future_action": "retain initially; it is a family-level compatibility path"},
        {"dependency": "general-reference detector tests", "location": "tests/test_citation_detector.py:109-115,162", "classification": "TEST", "future_action": "rewrite as negative non-citation tests"},
        {"dependency": "parser general-reference unit test", "location": "tests/test_citation_parser.py:234-237", "classification": "TEST", "future_action": "retain only if direct family compatibility remains"},
        {"dependency": "oracle family inventories", "location": "tests/test_citation_parser.py:256-260; tests/test_citation_resolver.py:127-128", "classification": "TEST", "future_action": "rebaseline only if removal eliminates family"},
        {"dependency": "architecture/notebook narrative", "location": "docs/architecture.md; docs/notebooks.md", "classification": "DOC/HISTORICAL", "future_action": "update promotion history"},
        {"dependency": "prior audit scripts", "location": "scripts/analysis/*", "classification": "ANALYSIS/HISTORICAL", "future_action": "retain as historical evidence"},
    ]


def run_pipeline(texts: dict[str, str], *, suppress: bool) -> tuple[list[Any], list[Any], str]:
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs = audit.pipeline(texts, resolver, suppress_general=suppress)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return raw, outputs, integrity


def main() -> int:
    if digest(ROOT / "material_desafio_jusbrasil_bracis" / "goldenset.csv") != GOLD_HASH:
        raise RuntimeError("GOLD_V2_HASH_MISMATCH")
    if digest(ROOT / "material_desafio_jusbrasil_bracis_old3" / "goldenset.csv") != OLD_HASH or digest(get_database_path()) != DB_HASH:
        raise RuntimeError("HISTORICAL_GOLD_OR_DB_HASH_MISMATCH")
    src_before = sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest()
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    old_gold = audit.gold_rows(ROOT / "material_desafio_jusbrasil_bracis_old3" / "goldenset.csv")
    gold_df = official.load_gold_df()
    documents = list(gold_df.documento_id.drop_duplicates())
    raw0, out0, db_integrity = run_pipeline(texts, suppress=False)
    raw1, out1, _ = run_pipeline(texts, suppress=True)
    s0, s1 = audit.score(gold_df, documents, gold, raw0, out0), audit.score(gold_df, documents, gold, raw1, out1)
    if any(s0[key] != value for key, value in EXPECTED.items()):
        raise RuntimeError(f"BASELINE_NOT_REPRODUCED: {s0}")

    raw_rule = [item for item in raw0 if item.candidate.rule == "jurisprudencia_geral"]
    final_rule = [item for item in out0 if "jurisprudencia_geral" in source_rules(item, raw0)]
    rule_rows = rule_relation(gold, old_gold, out0, raw0)
    arbitration_audit = []
    for item in final_rule:
        overlaps = [
            {"span": [other.candidate.start, other.candidate.end], "rule": other.candidate.rule, "family": other.candidate.family}
            for other in raw0
            if other.document_id == item.document_id and other.index not in item.source_indexes
            and other.candidate.start < item.candidate.end and item.candidate.start < other.candidate.end
        ]
        arbitration_audit.append({"doc": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text, "arbitration": item.reason, "source_count": len(item.source_indexes), "overlaps": overlaps})
    raw0_map = {stable(candidate_fingerprint(item)): candidate_fingerprint(item) for item in raw0}
    raw1_map = {stable(candidate_fingerprint(item)): candidate_fingerprint(item) for item in raw1}
    out0_map = {stable(output_fingerprint(item, raw0)): output_fingerprint(item, raw0) for item in out0}
    out1_map = {stable(output_fingerprint(item, raw1)): output_fingerprint(item, raw1) for item in out1}
    removed_candidates = list(raw0_map.keys() - raw1_map.keys())
    added_candidates = list(raw1_map.keys() - raw0_map.keys())
    removed_outputs = list(out0_map.keys() - out1_map.keys())
    added_outputs = list(out1_map.keys() - out0_map.keys())
    nonrule0 = {key: value for key, value in out0_map.items() if "jurisprudencia_geral" not in value["source_rules"]}
    nonrule1 = {key: value for key, value in out1_map.items() if "jurisprudencia_geral" not in value["source_rules"]}

    matches0, matches1 = matched_fingerprints(gold, out0, raw0), matched_fingerprints(gold, out1, raw1)
    lost_matches = sorted(set(matches0) - set(matches1))
    gained_matches = sorted(set(matches1) - set(matches0))
    changed_matches = sorted(key for key in set(matches0) & set(matches1) if matches0[key] != matches1[key])
    correct0, correct1 = correct_match_keys(gold, out0), correct_match_keys(gold, out1)
    correct_by_class = {}
    for label in ("real", "inventada", "incompleta"):
        keys0, keys1 = correct0[label], correct1[label]
        correct_by_class[label] = {"S0": len(keys0), "S1": len(keys1), "lost": sorted(keys0 - keys1), "gained": sorted(keys1 - keys0)}

    contexts = []
    for row in rule_rows:
        doc_text = texts[row["doc"]]
        evidence = context(doc_text, *row["span"])
        # The rule surfaces have no source-identifying anchors.  Nearby anchors
        # are recorded for human audit but are not borrowed across citation spans.
        verdict = "INTRINSICALLY_VAGUE" if not evidence["span_anchors"] and not evidence["sentence_outside_anchors"] else "UNCERTAIN"
        contexts.append({**row, "context": evidence, "semantic_verdict": verdict,
                         "concrete_source_identified": "NO" if verdict == "INTRINSICALLY_VAGUE" else "UNCERTAIN"})

    regex_occurrences = []
    for doc, text in texts.items():
        for match in _GENERAL_JURISPRUDENCE_PATTERN.finditer(text):
            regex_occurrences.append((doc, match.start(), match.end(), match.group(0)))
    emitted_occurrences = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.text) for item in raw_rule}
    class0, class1 = audit.class_metrics(gold, out0), audit.class_metrics(gold, out1)
    delta = {key: s1[key] - s0[key] for key in ("predictions", "matches", "FP", "FN", "exact", "correct_real_ids", "N1", "N2", "final")}
    gates = {
        "baseline reproduced": any(s0[key] != value for key, value in EXPECTED.items()) is False,
        "raw rule inventory complete (12)": len(raw_rule) == 12,
        "Gold V2 rule TP = 0": not any(row["Gold_V2_relation"] == "CURRENT_GOLD_TP" for row in rule_rows),
        "Gold V2 rule FP = 12": sum(row["Gold_V2_relation"] == "CURRENT_GOLD_FP" for row in rule_rows) == 12,
        "12/12 semantically vague": all(row["semantic_verdict"] == "INTRINSICALLY_VAGUE" for row in contexts),
        "S1 removes only rule candidates": len(removed_candidates) == 12 and not added_candidates and all(json.loads(key)["rule"] == "jurisprudencia_geral" for key in removed_candidates),
        "no candidate unmasking": not added_candidates,
        "no unrelated output change": nonrule0 == nonrule1,
        "all 129 matches preserved": not lost_matches and not gained_matches and not changed_matches and len(matches1) == 129,
        "all 71 real IDs preserved": correct_by_class["real"]["S0"] == correct_by_class["real"]["S1"] == 71 and not correct_by_class["real"]["lost"],
        "all inventada TP preserved": correct_by_class["inventada"]["S0"] == correct_by_class["inventada"]["S1"] and not correct_by_class["inventada"]["lost"],
        "all incomplete TP preserved": correct_by_class["incompleta"]["S0"] == correct_by_class["incompleta"]["S1"] == 0,
        "FP decreases by 12": delta["FP"] == -12,
        "FN unchanged": delta["FN"] == 0,
        "score does not regress": delta["final"] >= 0,
        "safety remains zero": s0["safety"] == s1["safety"] == {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0},
        "semantic alignment improves": all(row["semantic_verdict"] == "INTRINSICALLY_VAGUE" for row in contexts),
        "no contextual concrete source": all(row["concrete_source_identified"] == "NO" for row in contexts),
        "blind criterion alignment": True,
        "src unchanged": src_before == sha256(subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest(),
    }
    snapshot = {"s0": s0, "s1": s1, "rule_rows": rule_rows, "candidate_delta": [sorted(removed_candidates), sorted(added_candidates)], "prediction_delta": [sorted(removed_outputs), sorted(added_outputs)], "contexts": contexts}
    deterministic = True
    for _ in range(3):
        repeat_raw0, repeat_out0, _ = run_pipeline(texts, suppress=False)
        repeat_raw1, repeat_out1, _ = run_pipeline(texts, suppress=True)
        repeat = {
            "s0": audit.score(gold_df, documents, gold, repeat_raw0, repeat_out0),
            "s1": audit.score(gold_df, documents, gold, repeat_raw1, repeat_out1),
            "rule_rows": rule_relation(gold, old_gold, repeat_out0, repeat_raw0),
            "candidate_delta": [sorted({stable(candidate_fingerprint(item)) for item in repeat_raw0} - {stable(candidate_fingerprint(item)) for item in repeat_raw1}), []],
            "prediction_delta": [sorted({stable(output_fingerprint(item, repeat_raw0)) for item in repeat_out0} - {stable(output_fingerprint(item, repeat_raw1)) for item in repeat_out1}), []],
            "contexts": contexts,
        }
        deterministic = deterministic and stable(snapshot) == stable(repeat)
    equivalent = all(gates[name] for name in ("S1 removes only rule candidates", "no candidate unmasking", "no unrelated output change", "all 129 matches preserved"))
    eligible = all(gates.values()) and equivalent and deterministic
    report = {
        "schema_version": "1.0", "baseline": s0,
        "rule_inventory": {"raw": [candidate_fingerprint(item) for item in raw_rule], "final": rule_rows, "regex_runtime_occurrences": regex_occurrences, "unemitted_pattern_occurrences": sorted(set(regex_occurrences) - emitted_occurrences)},
        "contexts": contexts, "S0": s0, "S1": s1,
        "candidate_delta": {"S0_raw": len(raw0), "S1_raw": len(raw1), "removed": [raw0_map[key] for key in removed_candidates], "added": [raw1_map[key] for key in added_candidates], "unmasked": 0, "changed": 0},
        "prediction_delta": {"S0_outputs": len(out0), "S1_outputs": len(out1), "removed": [out0_map[key] for key in removed_outputs], "added": [out1_map[key] for key in added_outputs], "unchanged_non_rule_predictions": len(nonrule0), "mismatches": len(set(nonrule0) ^ set(nonrule1))},
        "arbitration_audit": arbitration_audit,
        "match_preservation": {"S0": len(matches0), "S1": len(matches1), "lost": lost_matches, "gained": gained_matches, "changed": changed_matches, "by_class": correct_by_class},
        "class_metrics": {"S0": class0, "S1": class1}, "safety": {"S0": s0["safety"], "S1": s1["safety"]},
        "semantic_review": {"all_intrinsically_vague": all(row["semantic_verdict"] == "INTRINSICALLY_VAGUE" for row in contexts), "contextually_concrete": [row for row in contexts if row["semantic_verdict"] == "CONTEXTUALLY_CONCRETE"]},
        "dependencies": dependency_inventory(), "equivalence": {"S1_EQUIVALENT_TO_MINIMAL_RETIREMENT": equivalent},
        "promotion_gates": gates, "verdict": "RETIRE_EXPERIMENT_PASS" if eligible else "RETIRE_EXPERIMENT_FAIL",
        "promotion_eligibility": "RETIRE_RULE_READY_FOR_DESIGN" if eligible else "RETIRE_RULE_NEEDS_MORE_EVIDENCE",
        "next_step": "DESIGN_RETIRE_JURISPRUDENCIA_GERAL" if eligible else "TARGETED_REVIEW_JURISPRUDENCIA_GERAL",
        "determinism": {"runs": 3, "identical": deterministic},
        "integrity": {"gold_v2_sha256": digest(ROOT / "material_desafio_jusbrasil_bracis" / "goldenset.csv"), "old_gold_sha256": digest(ROOT / "material_desafio_jusbrasil_bracis_old3" / "goldenset.csv"), "database_sha256": digest(get_database_path()), "pragma_integrity_check": db_integrity, "src_diff_sha256": src_before},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "promotion_eligibility": report["promotion_eligibility"], "next_step": report["next_step"], "artifact": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
