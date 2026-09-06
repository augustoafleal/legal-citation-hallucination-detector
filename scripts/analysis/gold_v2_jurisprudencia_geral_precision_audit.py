"""Auditoria offline de ``jurisprudencia_geral`` no Gold V2, sem mudar produção."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import csv
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "material_desafio_jusbrasil_bracis"
OLD = ROOT / "material_desafio_jusbrasil_bracis_old3"
OUT = ROOT / "artifacts" / "gold_v2_jurisprudencia_geral_precision_audit.json"
GOLD_HASH = "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1"
OLD_HASH = "c86f91c54c216411260f3ff4d6cd0ba60c3d28a1a2a7e8d30521b2b329864c8f"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import gold_v2_baseline_recalibration as baseline  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402

_TRIBUNAL = re.compile(r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b", re.I)
_RELATOR = re.compile(r"\b(?:relatoria|rel\.?\s*(?:min\.)?|relator(?:a)?)\b", re.I)
_YEAR = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
_PROCESS_CLASS = re.compile(r"\b(?:reclamação|rcl|recurso|agravo|habeas corpus|acórdão|julgado)\b", re.I)
_BODY = re.compile(r"\b(?:turma|corte|tribunais superiores|plenário|seção)\b", re.I)
_IDENTIFIER = re.compile(r"\b(?:n[ºo.]?\s*)?\d{1,7}(?:[./-]\d{1,7})+\b", re.I)


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def gold_rows(path: Path) -> list[Any]:
    rows = []
    with path.open(encoding="utf-8", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            rows.append(post.deep.GoldRow(index, row["citacao_id"], f"N{row['nivel']}", row["documento_id"], int(row["inicio"]), int(row["fim"]), row["trecho"].replace("\\n", "\n"), row["tipo"], row["classificacao"], int(row["id_canonico"]) if row["id_canonico"] else None))
    return rows


def anchors(text: str) -> dict[str, bool]:
    return {
        "TRIBUNAL": bool(_TRIBUNAL.search(text)), "RELATOR": bool(_RELATOR.search(text)), "YEAR_OR_DATE": bool(_YEAR.search(text)),
        "PROCESS_CLASS": bool(_PROCESS_CLASS.search(text)), "JUDGING_BODY": bool(_BODY.search(text)), "CASE_IDENTIFIER": bool(_IDENTIFIER.search(text)),
        "NAMED_NORM_SOURCE": False,
    }


def group_name(features: dict[str, bool]) -> str:
    if features["TRIBUNAL"] and features["RELATOR"] and features["YEAR_OR_DATE"]:
        return "tribunal_relator_year"
    if features["TRIBUNAL"] and features["YEAR_OR_DATE"]:
        return "tribunal_year"
    if features["TRIBUNAL"] and features["RELATOR"]:
        return "tribunal_relator"
    if features["PROCESS_CLASS"] and features["TRIBUNAL"]:
        return "process_class_tribunal"
    if features["JUDGING_BODY"] and features["YEAR_OR_DATE"]:
        return "judging_body_year"
    return "other_concrete_context"


def classification(item: Any) -> str:
    return official.output_cells([item])[0].classification


def pipeline(texts: dict[str, str], resolver: CitationResolver, *, suppress_general: bool) -> tuple[list[Any], list[Any]]:
    detector, parser = CitationDetector(), CitationParser()
    raw, outputs = [], []
    for document_id in sorted(texts):
        candidates = list(detector.detect(texts[document_id]))
        if suppress_general:
            candidates = [item for item in candidates if item.rule != "jurisprudencia_geral"]
        parsed = [parser.parse(item, context=texts[document_id]) for item in candidates]
        results = [resolver.resolve(item) for item in parsed]
        source_indexes: dict[int, int] = {}
        for candidate, parsed_item, result in zip(candidates, parsed, results):
            source_indexes[id(candidate)] = len(raw)
            raw.append(post.RawPrediction(len(raw), document_id, candidate, parsed_item, result))
        for item in structural_cnj_union_merge(texts[document_id], candidates, parsed, results, primary_identities=resolver.primary_identities):
            sources = tuple(sorted(source_indexes[id(source)] for source in item.source_candidates))
            outputs.append(post.Output(len(outputs), document_id, item.candidate, item.parsed, item.resolution, "merged" if len(sources) > 1 else "single", sources))
    return raw, outputs


def score(gold_df: Any, documents: list[str], gold: Sequence[Any], raw: list[Any], outputs: list[Any]) -> dict[str, Any]:
    result = baseline.metrics(gold_df, documents, gold, raw, outputs)
    return {key: result[key] for key in ("predictions", "matches", "FP", "FN", "exact", "correct_real_ids", "N1", "N2", "final", "safety", "final_outputs")}


def output_sources(item: Any, raw: Sequence[Any]) -> set[str]:
    return {raw[index].candidate.rule for index in item.source_indexes}


def relation(gold: Sequence[Any], outputs: Sequence[Any], raw: Sequence[Any], *, old_gold: bool) -> tuple[dict[int, int], dict[int, str]]:
    matched = post.match_gold(gold, outputs)
    outputs_by_index = {item.index: item for item in outputs}
    labels: dict[int, str] = {}
    for output in outputs:
        if "jurisprudencia_geral" not in output_sources(output, raw):
            continue
        gold_index = next((index for index, output_index in matched.items() if output_index == output.index), None)
        if gold_index is None:
            labels[output.index] = "CURRENT_GOLD_FP" if not old_gold else "OLD_GOLD_FP"
            continue
        item = gold[gold_index]
        correct = classification(output) == item.classification and (item.classification != "real" or output.result.id_canonico == item.canonical_id)
        labels[output.index] = "CURRENT_GOLD_TP" if correct and not old_gold else "OLD_GOLD_TP" if correct else "CURRENT_GOLD_WRONG_CLASS" if not old_gold else "OLD_GOLD_WRONG_CLASS"
    return matched, labels


def class_metrics(gold: Sequence[Any], outputs: Sequence[Any]) -> list[dict[str, Any]]:
    matched = post.match_gold(gold, outputs)
    output_by_index = {item.index: item for item in outputs}
    output_gold = {output_index: gold_index for gold_index, output_index in matched.items()}
    rows = []
    for level in ("N1", "N2"):
        for label in ("real", "inventada", "incompleta"):
            tp = fp = fn = 0
            for item in outputs:
                if not item.document_id.startswith("gen_" + level.lower() + "_"):
                    continue
                predicted = classification(item)
                gold_index = output_gold.get(item.index)
                expected = None if gold_index is None else gold[gold_index]
                correct = expected is not None and predicted == expected.classification and (predicted != "real" or item.result.id_canonico == expected.canonical_id)
                if predicted == label and correct:
                    tp += 1
                elif predicted == label:
                    fp += 1
                if expected is not None and expected.classification == label and not correct:
                    fn += 1
            for item in gold:
                if item.level == level and item.index not in matched and item.classification == label:
                    fn += 1
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            rows.append({"level": level, "class": label, "TP": tp, "FP": fp, "FN": fn, "precision": precision, "recall": recall, "F1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0})
    return rows


def rule_row(item: Any, raw: Sequence[Any], final: bool) -> dict[str, Any]:
    return {"doc": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text, "family": item.candidate.family, "rule": item.candidate.rule, "parser": post.parsed_dict(item.parsed), "resolver": post.result_dict(item.result), "arbitration": item.reason if final else "not_yet_arbitrated", "final_output": final, "final_class": classification(item) if final else None, "source_rules": sorted(output_sources(item, raw)) if final else [item.candidate.rule]}


def template(text: str) -> str:
    value = text.lower().replace("\n", " ")
    if "jurisprudência" in value:
        return "jurisprudência_pacífica_ou_consolidada"
    if "orientação jurisprudencial" in value:
        return "orientação_jurisprudencial"
    if "entendimento sumulado" in value:
        return "entendimento_sumulado"
    if "precedentes desta casa" in value:
        return "precedentes_desta_casa"
    if "verbete sumular" in value:
        return "verbete_sumular"
    return "other"


def main() -> int:
    if digest(DATA / "goldenset.csv") != GOLD_HASH or digest(OLD / "goldenset.csv") != OLD_HASH or digest(get_database_path()) != DB_HASH:
        raise RuntimeError("integridade Gold/DB divergente")
    texts, current_gold = post.deep.load_texts(), post.deep.load_gold()
    old_gold = gold_rows(OLD / "goldenset.csv")
    gold_df, documents = official.load_gold_df(), list(official.load_gold_df().documento_id.drop_duplicates())
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw0, out0 = pipeline(texts, resolver, suppress_general=False)
        raw1, out1 = pipeline(texts, resolver, suppress_general=True)
        db_ok = connection.execute("PRAGMA integrity_check").fetchone()[0]
    s0, s1 = score(gold_df, documents, current_gold, raw0, out0), score(gold_df, documents, current_gold, raw1, out1)
    expected = {"predictions": 218, "matches": 129, "FP": 89, "FN": 66, "exact": 82, "correct_real_ids": 71, "N1": 0.5231572080887149, "N2": 0.39974937343358397, "final": 0.44088531831862765}
    if any(s0[key] != value for key, value in expected.items()):
        raise RuntimeError(f"GOLD_V2_BASELINE_NOT_REPRODUCED: {s0}")

    raw_rule = [item for item in raw0 if item.candidate.rule == "jurisprudencia_geral"]
    final_rule = [item for item in out0 if "jurisprudencia_geral" in output_sources(item, raw0)]
    current_match, current_relation = relation(current_gold, out0, raw0, old_gold=False)
    old_match, old_relation = relation(old_gold, out0, raw0, old_gold=True)
    current_gold_by = {item.index: item for item in current_gold}
    old_gold_by = {item.index: item for item in old_gold}
    current_by_output = {output: gold for gold, output in current_match.items()}
    old_by_output = {output: gold for gold, output in old_match.items()}
    current_keys = {(item.document_id, item.gid) for item in current_gold}
    removed_old = [item for item in old_gold if (item.document_id, item.gid) not in current_keys]
    newly_fp, preexisting = [], []
    for item in final_rule:
        old_item = old_gold_by.get(old_by_output.get(item.index))
        base = rule_row(item, raw0, True)
        if old_item is not None and old_relation[item.index] == "OLD_GOLD_TP" and (old_item.document_id, old_item.gid) not in current_keys:
            newly_fp.append({"old_gold": old_item.gid, "doc": old_item.document_id, "level": old_item.level, "text": old_item.text, "current_rule": item.candidate.rule, "current_class": classification(item), "Gold_V2_relation": current_relation[item.index], **base})
        elif old_relation[item.index] == "OLD_GOLD_FP":
            preexisting.append({"old_gold": None, **base})

    groups = []
    for name, items in sorted(((name, [item for item in final_rule if template(item.candidate.text) == name]) for name in sorted({template(item.candidate.text) for item in final_rule})), key=lambda pair: pair[0]):
        groups.append({"template": name, "occurrences": len(items), "docs": sorted({item.document_id for item in items}), "old_TP": sum(old_relation[item.index] == "OLD_GOLD_TP" for item in items), "gold_v2_TP": sum(current_relation[item.index] == "CURRENT_GOLD_TP" for item in items), "gold_v2_FP": sum(current_relation[item.index] == "CURRENT_GOLD_FP" for item in items), "anchor_features": {key: sum(anchors(item.candidate.text)[key] for item in items) for key in anchors("")}})

    removed_juris = [item for item in removed_old if item.citation_type == "jurisprudencia"]
    surviving = [item for item in current_gold if item.classification == "incompleta" and item.citation_type == "jurisprudencia"]
    removed_inventory = [{"gold": item.gid, "doc": item.document_id, "text": item.text, "anchors": anchors(item.text)} for item in removed_juris]
    surviving_ledger, oracle_rows = [], []
    with connect_database(get_database_path(), read_only=True) as connection:
        oracle_resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for item in surviving:
            related = [candidate for candidate in raw0 if candidate.document_id == item.document_id and post.iou(item.start, item.end, candidate.candidate.start, candidate.candidate.end) > 0]
            family = post.deep.surface_family(item.text, item.citation_type)
            candidate = CitationCandidate(item.start, item.end, item.text, "oracle_incomplete_surface", family)
            parser = CitationParser().parse(candidate, context=texts[item.document_id])
            result = oracle_resolver.resolve(parser)
            oracle_class = "real" if result.status == "resolved" else "inventada" if result.status == "no_match" else "incompleta"
            features = anchors(item.text)
            detected = [candidate for candidate in related if post.iou(item.start, item.end, candidate.candidate.start, candidate.candidate.end) >= 0.5]
            emitted = [output for output in out0 if output.document_id == item.document_id and post.iou(item.start, item.end, output.candidate.start, output.candidate.end) >= 0.5]
            blocker = "DETECTOR" if not detected else "ARBITRATION" if not emitted else "CLASSIFICATION"
            row = {"gold": item.gid, "doc": item.document_id, "level": item.level, "text": item.text, "anchors": features, "group": group_name(features), "nearby_candidates": [rule_row(candidate, raw0, False) for candidate in related], "first_blocker": blocker, "oracle_family": family, "oracle_result": post.result_dict(result), "oracle_class": oracle_class}
            surviving_ledger.append(row)
            oracle_rows.append(row)
    group_rows = []
    for name in sorted({row["group"] for row in surviving_ledger}):
        items = [row for row in surviving_ledger if row["group"] == name]
        group_rows.append({"group": name, "cases": len(items), "docs": len({row["doc"] for row in items}), "N1": sum(row["level"] == "N1" for row in items), "N2": sum(row["level"] == "N2" for row in items), "anchors": [key for key in anchors("") if any(row["anchors"][key] for row in items)], "first_blocker": dict(Counter(row["first_blocker"] for row in items)), "concrete_decision": all(any(row["anchors"].values()) for row in items), "oracle_incomplete": sum(row["oracle_class"] == "incompleta" for row in items)})
    # A tribunal alone is common prose.  Only the repeated tribunal + relator +
    # year shape is sufficiently concrete to call a safe experiment hypothesis.
    safe = [row for row in group_rows if row["group"] == "tribunal_relator_year" and row["cases"] >= 2 and row["docs"] >= 2 and row["oracle_incomplete"] == row["cases"]]
    plausible = [row for row in group_rows if row not in safe and row["cases"] >= 2 and row["oracle_incomplete"] == row["cases"]]
    negative_pattern = re.compile(r"\b(?:STF|STJ|TSE|TST|STM)\b[^\n]{0,100}\b20\d{2}\b", re.I)
    negatives = []
    for doc, text in texts.items():
        for match in negative_pattern.finditer(text):
            if not any(match.start() < item.end and item.start < match.end() for item in current_gold if item.document_id == doc):
                negatives.append({"doc": doc, "text": match.group(0)})

    signatures0 = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in raw0}
    signatures1 = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in raw1}
    outputs0 = {(item.document_id, item.candidate.start, item.candidate.end, tuple(sorted(output_sources(item, raw0)))) for item in out0}
    outputs1 = {(item.document_id, item.candidate.start, item.candidate.end, tuple(sorted(output_sources(item, raw1)))) for item in out1}
    class0, class1 = class_metrics(current_gold, out0), class_metrics(current_gold, out1)
    preservation = {label: sum(item.classification == label and index in current_match for index, item in enumerate(current_gold)) == sum(item.classification == label and index in post.match_gold(current_gold, out1) for index, item in enumerate(current_gold)) for label in ("real", "inventada", "incompleta")}
    decisions = {"rule": "RETIRE_JURISPRUDENCIA_GERAL_EXPERIMENT" if not any(value == "CURRENT_GOLD_TP" for value in current_relation.values()) and not preexisting else "NEED_MORE_EVIDENCE_ON_JURISPRUDENCIA_GERAL", "incomplete": "DESIGN_CONCRETE_INCOMPLETE_EXPERIMENT" if safe else "TARGETED_HUMAN_REVIEW_CONCRETE_INCOMPLETE"}
    next_step = "EXPERIMENT_RETIRE_JURISPRUDENCIA_GERAL" if decisions["rule"] == "RETIRE_JURISPRUDENCIA_GERAL_EXPERIMENT" else "TARGETED_HUMAN_REVIEW_CONCRETE_INCOMPLETE"
    snapshot = {"raw_rule": [rule_row(item, raw0, False) for item in raw_rule], "final_rule": [rule_row(item, raw0, True) for item in final_rule], "s0": s0, "s1": s1, "surviving": surviving_ledger, "decisions": decisions}
    deterministic = True
    for _ in range(3):
        with connect_database(get_database_path(), read_only=True) as connection:
            repeat_resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
            repeat_raw, repeat_out = pipeline(texts, repeat_resolver, suppress_general=False)
            repeat_raw_s1, repeat_out_s1 = pipeline(texts, repeat_resolver, suppress_general=True)
        repeat = {"raw_rule": [rule_row(item, repeat_raw, False) for item in repeat_raw if item.candidate.rule == "jurisprudencia_geral"], "final_rule": [rule_row(item, repeat_raw, True) for item in repeat_out if "jurisprudencia_geral" in output_sources(item, repeat_raw)], "s0": score(gold_df, documents, current_gold, repeat_raw, repeat_out), "s1": score(gold_df, documents, current_gold, repeat_raw_s1, repeat_out_s1), "surviving": surviving_ledger, "decisions": decisions}
        deterministic = deterministic and stable(snapshot) == stable(repeat)
    report = {"schema_version": "1.0", "baseline": s0, "rule_definition": {"source": "src/bracis_jusbrasil/citations/detector.py:_GENERAL_JURISPRUDENCE_PATTERN", "family": "jurisprudencia_referencia_geral", "rule": "jurisprudencia_geral", "parser": "empty data/provenance", "resolver": "insufficient", "classification": "insufficient -> incompleta", "arbitration": "not an allowed structural CNJ companion"}, "rule_semantic_scope": "INTRINSICALLY_VAGUE", "raw_rule_candidates": [rule_row(item, raw0, False) for item in raw_rule], "final_rule_predictions": [rule_row(item, raw0, True) for item in final_rule], "gold_v2_relation": dict(Counter(current_relation.values())), "old_gold_relation": dict(Counter(old_relation.values())), "newly_fp": newly_fp, "preexisting_rule_fp": preexisting, "phrase_groups": groups, "removed_anchor_features": removed_inventory, "surviving_incomplete_anchor_features": surviving_ledger, "anchor_distribution": {"removed_21": {key: sum(row["anchors"][key] for row in removed_inventory) for key in anchors("")}, "surviving_33": {key: sum(row["anchors"][key] for row in surviving_ledger) for key in anchors("")}}, "class_metrics": {"S0": class0, "S1": class1}, "S0": s0, "S1": s1, "S1_delta": {key: s1[key] - s0[key] for key in ("predictions", "matches", "FP", "FN", "exact", "correct_real_ids", "N1", "N2", "final")}, "S1_candidate_delta": {"raw_removed": len(signatures0 - signatures1), "final_removed": len(outputs0 - outputs1), "unmasked": len(outputs1 - outputs0), "other_outputs_changed": len({item for item in outputs0 ^ outputs1 if "jurisprudencia_geral" not in item[3]})}, "S1_safety": s1["safety"], "S1_preservation": preservation, "surviving_incomplete_blockers": dict(Counter(row["first_blocker"] for row in surviving_ledger)), "surviving_incomplete_groups": group_rows, "incomplete_oracles": oracle_rows, "safe_incomplete_groups": safe, "plausible_incomplete_groups": plausible, "corpus_negatives": {"tribunal_year_non_gold_count": len(negatives), "examples": negatives[:20]}, "rule_decision": decisions["rule"], "incomplete_decision": decisions["incomplete"], "next_step": next_step, "determinism": {"runs": 3, "identical": deterministic}, "integrity": {"gold_v2_sha256": GOLD_HASH, "old_gold_sha256": OLD_HASH, "database_sha256": digest(get_database_path()), "pragma_integrity_check": db_ok, "src_diff_sha256": sha256(__import__("subprocess").check_output(["git", "diff", "--", "src"], cwd=ROOT)).hexdigest()}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rule_decision": report["rule_decision"], "incomplete_decision": report["incomplete_decision"], "next_step": report["next_step"], "artifact": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
