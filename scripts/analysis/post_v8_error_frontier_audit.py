"""Offline error-frontier audit after V8 H2; it never changes production."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import csv
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "material_desafio_jusbrasil_bracis"
OLD_DATA = ROOT / "material_desafio_jusbrasil_bracis_old3"
OUT = ROOT / "artifacts" / "post_v8_error_frontier_audit.json"
EXPECTED = {
    "predictions": 233, "matches": 156, "FP": 77, "FN": 39,
    "exact": 109, "real_ids": 71, "score": 0.6360749575415693,
}
GOLD_HASH = "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"

sys.path.insert(0, str(ROOT / "src"))
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import (  # noqa: E402
    CitationCandidate, CitationDetector, CitationParser, CitationResolver,
    structural_cnj_union_merge,
)
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402

DECISION = re.compile(
    r"\b(?:Agravo\s+em\s+Recurso\s+Especial|Agravo\s+em\s+REsp|"
    r"Recurso\s+em\s+Habeas\s+Corpus|RHC|Reclamação|Rcl|acórdão|julgado|precedente)\b", re.I
)
YEAR = re.compile(r"\b(?:de|em)\s+(?:19\d{2}|20\d{2})\b|\b(?:julgado|proferido|profcrido)\s+(?:em\s+)?(?:19\d{2}|20\d{2})\b", re.I)
RELATOR = re.compile(r"\b(?:Rel\.?(?:\s*(?:Min\.?|Ministro|Ministra))?|(?:pela|sob|da)\s+relatoria\s+d(?:e|a|c))\s+", re.I)
NAME = re.compile(r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*(?:[ \t\r\n]+[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*){1,5}")
COURT = re.compile(r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b", re.I)
NUMBER = re.compile(r"\d")


@dataclass(frozen=True)
class Gold:
    gid: str
    level: str
    document_id: str
    start: int
    end: int
    text: str
    citation_type: str
    classification: str
    canonical_id: str | None


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def iou(left: tuple[int, int], right: tuple[int, int]) -> float:
    intersection = max(0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return intersection / union if union else 0.0


def label(status: str) -> str:
    return {"resolved": "real", "no_match": "inventada", "ambiguous": "incompleta", "insufficient": "incompleta"}[status]


def load_gold(path: Path) -> list[Gold]:
    with path.open(encoding="utf-8", newline="") as stream:
        return [Gold(
            row["citacao_id"], f"N{row['nivel']}", row["documento_id"], int(row["inicio"]), int(row["fim"]),
            row["trecho"].replace("\\n", "\n"), row["tipo"], row["classificacao"], row["id_canonico"] or None,
        ) for row in csv.DictReader(stream)]


def load_metric() -> Any:
    spec = importlib.util.spec_from_file_location("post_v8_official_metric", DATA / "kaggle_metric.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pipeline() -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]], str]:
    texts = {path.stem: path.read_text(encoding="utf-8") for path in sorted((DATA / "txt").glob("*.txt"))}
    detector, parser = CitationDetector(), CitationParser()
    raw: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for document_id, text in sorted(texts.items()):
            candidates = detector.detect(text)
            parsed = [parser.parse(candidate, context=text) for candidate in candidates]
            resolved = [resolver.resolve(item) for item in parsed]
            raw.extend({"doc": document_id, "candidate": candidate, "parsed": item, "result": result}
                       for candidate, item, result in zip(candidates, parsed, resolved))
            outputs.extend({"doc": document_id, "candidate": item.candidate, "parsed": item.parsed,
                            "result": item.resolution, "reason": item.reason}
                           for item in structural_cnj_union_merge(
                               text, candidates, parsed, resolved, primary_identities=resolver.primary_identities
                           ))
        pragma = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return texts, raw, outputs, pragma


def match(metric: Any, gold: Iterable[Gold], items: list[dict[str, Any]]) -> list[tuple[Gold, dict[str, Any]]]:
    gold_by_doc: dict[str, list[Gold]] = defaultdict(list)
    pred_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in gold:
        gold_by_doc[row.document_id].append(row)
    for item in items:
        pred_by_doc[item["doc"]].append(item)
    pairs: list[tuple[Gold, dict[str, Any]]] = []
    for document_id, rows in gold_by_doc.items():
        predictions = pred_by_doc[document_id]
        selected, _, _ = metric._casar(
            [{"inicio": row.start, "fim": row.end} for row in rows],
            [{"inicio": item["candidate"].start, "fim": item["candidate"].end} for item in predictions],
        )
        pairs.extend((rows[gold_index], predictions[prediction_index]) for gold_index, prediction_index in selected)
    return pairs


def solution(gold: list[Gold]) -> pd.DataFrame:
    grouped: dict[str, list[str]] = defaultdict(list)
    levels: dict[str, int] = {}
    for row in gold:
        canonical = row.canonical_id if row.classification == "real" else "-"
        grouped[row.document_id].append(f"{row.start},{row.end},{row.classification},{canonical}")
        levels[row.document_id] = int(row.level[1:])
    docs = sorted(grouped)
    return pd.DataFrame({"documento_id": docs, "nivel": [levels[doc] for doc in docs],
                         "citacoes": ["|".join(grouped[doc]) for doc in docs]})


def submission(outputs: list[dict[str, Any]], documents: Iterable[str]) -> pd.DataFrame:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in outputs:
        result, candidate = item["result"], item["candidate"]
        canonical = str(result.id_canonico) if result.status == "resolved" else "-"
        grouped[item["doc"]].append(f"{candidate.start},{candidate.end},{label(result.status)},{canonical},-")
    docs = sorted(documents)
    return pd.DataFrame({"documento_id": docs, "citacoes": ["|".join(grouped[doc]) or "-" for doc in docs]})


def metrics(metric: Any, gold: list[Gold], raw: list[dict[str, Any]], outputs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[tuple[Gold, dict[str, Any]]], list[tuple[Gold, dict[str, Any]]]]:
    raw_pairs, output_pairs = match(metric, gold, raw), match(metric, gold, outputs)
    safety = {
        "wrong_unique_real": sum(g.classification == "real" and p["result"].status == "resolved" and str(p["result"].id_canonico) != g.canonical_id for g, p in output_pairs),
        "false_real_inventada": sum(g.classification == "inventada" and p["result"].status == "resolved" for g, p in output_pairs),
        "false_real_incompleta": sum(g.classification == "incompleta" and p["result"].status == "resolved" for g, p in output_pairs),
    }
    return ({
        "predictions": len(raw), "matches": len(raw_pairs), "FP": len(raw) - len(raw_pairs), "FN": len(gold) - len(raw_pairs),
        "exact": sum(g.start == p["candidate"].start and g.end == p["candidate"].end for g, p in raw_pairs),
        "real_ids": sum(g.classification == "real" and p["result"].status == "resolved" and str(p["result"].id_canonico) == g.canonical_id for g, p in output_pairs),
        "safety": safety,
    }, raw_pairs, output_pairs)


def anchors(text: str) -> dict[str, bool]:
    return {"decision": bool(DECISION.search(text)), "tribunal": bool(COURT.search(text)), "relator": bool(RELATOR.search(text)), "year": bool(YEAR.search(text)), "number": bool(NUMBER.search(text))}


def nearest(gold: Gold, raw: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    candidates = [item for item in raw if item["doc"] == gold.document_id]
    if not candidates:
        return None, 0.0
    item = max(candidates, key=lambda value: iou((gold.start, gold.end), (value["candidate"].start, value["candidate"].end)))
    return item, iou((gold.start, gold.end), (item["candidate"].start, item["candidate"].end))


def h3_candidates(text: str) -> list[tuple[int, int, str]]:
    """Diagnostic-only H3: H2 grammar without the explicit-court requirement."""
    found = []
    for decision in DECISION.finditer(text):
        window_end = min(len(text), decision.end() + 140)
        window = text[decision.end():window_end]
        year = YEAR.search(window)
        if year is None or "\n\n" in window[:year.start()] or re.search(r"(?<!Rel)(?<!Min)[.!?]", window[:year.start()]):
            continue
        relator = RELATOR.search(window, year.end())
        if relator is None or "\n\n" in window[year.end():relator.start()] or re.search(r"(?<!Rel)(?<!Min)[.!?]", window[year.end():relator.start()]):
            continue
        name = NAME.match(window, relator.end())
        if name:
            end = decision.end() + name.end()
            found.append((decision.start(), end, text[decision.start():end]))
    return list(dict.fromkeys(found))


def first_blocker(gold: Gold, closest: dict[str, Any] | None, overlap: float) -> str:
    if closest is None or overlap < .5:
        return "Detector"
    status = closest["result"].status
    if status in {"no_match", "ambiguous", "insufficient"}:
        return "Resolver"
    return "Classification"


def recoverability(gold: Gold, blocker: str) -> str:
    flags = anchors(gold.text)
    if gold.classification == "real" and not flags["number"]:
        return "CORPUS_BLOCKED"
    if blocker == "Detector" and gold.classification == "incompleta" and flags["decision"] and flags["relator"] and flags["year"]:
        return "PLAUSIBLE"
    if blocker == "Resolver" and gold.classification == "real":
        return "AMBIGUOUS"
    return "UNSAFE" if blocker == "Classification" else "CORPUS_BLOCKED"


def main() -> int:
    metric = load_metric()
    gold, old_gold = load_gold(DATA / "goldenset.csv"), load_gold(OLD_DATA / "goldenset.csv")
    texts, raw, outputs, pragma = pipeline()
    current, raw_pairs, output_pairs = metrics(metric, gold, raw, outputs)
    score = float(metric.score(solution(gold), submission(outputs, texts), "documento_id"))
    current["score"] = score
    if any(current[key] != value for key, value in EXPECTED.items()) or current["safety"] != {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0}:
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {current}")

    raw_matched = {id(item) for _, item in raw_pairs}
    output_matched = {id(item) for _, item in output_pairs}
    by_doc_gold: dict[str, list[Gold]] = defaultdict(list)
    for row in gold:
        by_doc_gold[row.document_id].append(row)
    fp_ledger = []
    for item in raw:
        if id(item) in raw_matched:
            continue
        candidate, result = item["candidate"], item["result"]
        nearby = by_doc_gold[item["doc"]]
        closest = max(nearby, key=lambda row: iou((candidate.start, candidate.end), (row.start, row.end)), default=None)
        maximum = iou((candidate.start, candidate.end), (closest.start, closest.end)) if closest else 0.0
        fp_ledger.append({
            "documento": item["doc"], "span": [candidate.start, candidate.end], "texto": candidate.text,
            "tipo_previsto": item["parsed"].tipo, "classificacao_prevista": label(result.status), "id_canonico_previsto": result.id_canonico,
            "family": candidate.family, "rule": candidate.rule, "parser_status": "parsed", "resolver_status": result.status,
            "confidence": None, "nivel": next((row.level for row in nearby), None), "gold_proximo": closest.gid if closest else None,
            "maior_iou": maximum, "classe_gold_proxima": closest.classification if closest else None,
            "razao_provavel": "near_gold_boundary_or_class_collision" if maximum else "unmatched_structural_surface",
            "origem_estrutural": candidate.rule,
        })
    all_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in raw:
        all_by_rule[item["candidate"].rule].append(item)
    fp_groups = []
    for rule, rows in sorted(all_by_rule.items()):
        group_fp = [row for row in fp_ledger if row["rule"] == rule]
        if not group_fp:
            continue
        group_tp = [row for row in rows if id(row) in raw_matched]
        labels = Counter(label(row["result"].status) for row in group_tp)
        verdict = "UNSAFE_SUPPRESSION" if group_tp else "PLAUSIBLE_SUPPRESSION_CANDIDATE"
        fp_groups.append({"group": rule, "count": len(group_fp), "current_tp_affected": len(group_tp),
                          "real_ids_affected": labels["real"], "inventada_tps_affected": labels["inventada"],
                          "incompleta_tps_affected": labels["incompleta"], "risk": "shared_rule_has_TP" if group_tp else "requires_blind_negative_validation",
                          "structural_rule_possible": "subgroup audit only; no literal allow/deny list", "depends_on_gold": False,
                          "verdict": verdict})
    suppression_estimates = []
    for group in fp_groups:
        rule = group["group"]
        retained = [item for item in outputs if item["candidate"].rule != rule]
        suppressed_score = float(metric.score(solution(gold), submission(retained, texts), "documento_id"))
        suppression_estimates.append({
            "rule": rule, "removed_raw_fp": group["count"], "affected_raw_tp": group["current_tp_affected"],
            "score_after_whole_rule_suppression": suppressed_score,
            "score_delta": suppressed_score - score,
            "label": "DIAGNOSTIC_ONLY",
        })

    raw_gold_ids = {id(row) for row, _ in raw_pairs}
    fn_ledger = []
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        parser = CitationParser()
        for row in gold:
            if id(row) in raw_gold_ids:
                continue
            closest, maximum = nearest(row, raw)
            blocker = first_blocker(row, closest, maximum)
            family = "jurisprudencia_tribunal_contextual" if row.citation_type == "jurisprudencia" else "lei_dispositivo_sem_diploma"
            oracle = parser.parse(CitationCandidate(row.start, row.end, row.text, "oracle", family), context=texts[row.document_id])
            oracle_result = resolver.resolve(oracle)
            fn_ledger.append({"documento": row.document_id, "gold_id": row.gid, "span": [row.start, row.end], "texto": row.text,
                              "tipo": row.citation_type, "classificacao": row.classification, "id_canonico": row.canonical_id,
                              "nivel": row.level, "candidate_detectado": closest is not None and maximum >= .5,
                              "candidate_mais_proximo": None if closest is None else {"rule": closest["candidate"].rule, "span": [closest["candidate"].start, closest["candidate"].end]},
                              "maior_iou": maximum, "first_blocker": blocker, "downstream_oracle": oracle_result.status,
                              "recoverability": recoverability(row, blocker)})

    h2_spans = {(item["doc"], item["candidate"].start, item["candidate"].end) for item in raw if item["candidate"].rule == "decision_tribunal_relator_year"}
    tail = []
    for row in [item for item in gold if item.citation_type == "jurisprudencia" and item.classification == "incompleta"]:
        if (row.document_id, row.start, row.end) in h2_spans:
            continue
        closest, maximum = nearest(row, raw)
        flags = anchors(row.text)
        missing = [name for name, present in flags.items() if name in {"decision", "tribunal", "relator", "year"} and not present]
        tail.append({"documento": row.document_id, "gold_id": row.gid, "span": [row.start, row.end], "texto": row.text, "nivel": row.level,
                     "anchors_presentes": [name for name, present in flags.items() if present], "anchors_ausentes": missing,
                     "h2_rejeicao": "missing " + ", ".join(missing), "detector_proximo": None if closest is None else closest["candidate"].rule,
                     "maior_iou": maximum, "downstream_oracle": "insufficient", "hipotese": "H3 tribunal-optional only" if not flags["tribunal"] else "none",
                     "negativos_relacionados": "H3 must reject vague/real/inventada/non-gold", "risco_blind": "HIGH" if not flags["tribunal"] else "MEDIUM",
                     "verdict": "TARGETED_REVIEW_REQUIRED" if flags["decision"] and flags["relator"] and flags["year"] else "REJECT"})

    h3 = [(doc, *candidate) for doc, text in texts.items() for candidate in h3_candidates(text)]
    def h3_overlap(rows: Iterable[Gold]) -> int:
        return sum(any(doc == row.document_id and iou((start, end), (row.start, row.end)) >= .5 for doc, start, end, _ in h3) for row in rows)
    old_keys = {(row.document_id, row.start, row.end, row.text, row.citation_type, row.classification, row.canonical_id) for row in gold}
    removed_vague = [row for row in old_gold if row.citation_type == "jurisprudencia" and (row.document_id, row.start, row.end, row.text, row.citation_type, row.classification, row.canonical_id) not in old_keys]
    h3_revisit = {"contract": "decision/class + relator + contextual year; tribunal optional; bounded clause",
                  "candidates": len(h3), "covers_tail": h3_overlap([Gold(row["gold_id"], row["nivel"], row["documento"], row["span"][0], row["span"][1], row["texto"], "jurisprudencia", "incompleta", None) for row in tail]),
                  "incompleta_covered": h3_overlap([row for row in gold if row.classification == "incompleta" and row.citation_type == "jurisprudencia"]),
                  "real_overlap": h3_overlap([row for row in gold if row.classification == "real"]), "inventada_overlap": h3_overlap([row for row in gold if row.classification == "inventada"]),
                  "removed_vague_overlap": h3_overlap(removed_vague),
                  "outside_gold": sum(not any(doc == row.document_id and iou((start, end), (row.start, row.end)) >= .5 for row in gold) for doc, start, end, _ in h3)}
    h3_revisit["verdict"] = "STILL_UNSAFE" if any(h3_revisit[key] for key in ("real_overlap", "inventada_overlap", "removed_vague_overlap", "outside_gold")) else "PLAUSIBLE_WITH_EXTRA_GUARD"

    real_fn = [row for row in fn_ledger if row["classificacao"] == "real"]
    inventada = {"fn": [row for row in fn_ledger if row["classificacao"] == "inventada"],
                 "fp": [row for row in fp_ledger if row["classificacao_prevista"] == "inventada"],
                 "confused_with_incompleta": sum(row["classificacao"] == "inventada" and row["first_blocker"] == "Classification" for row in fn_ledger),
                 "confused_with_real": 0, "safe_hypothesis": False}
    classification_errors = [row for row in fn_ledger if row["first_blocker"] == "Classification"]
    resolver_errors = [row for row in fn_ledger if row["first_blocker"] == "Resolver"]
    detector_groups = Counter((row["tipo"], row["classificacao"], row["first_blocker"], row["recoverability"]) for row in fn_ledger)
    best_precision = max(suppression_estimates, key=lambda item: item["score_delta"])
    estimates = [
        {"front": "FP_PRECISION_AUDIT", "effect": "remove a structural subgroup, not the whole rule", "expected_score_delta": best_precision["score_delta"], "label": "DIAGNOSTIC_ONLY"},
        {"front": "REMAINING_INCOMPLETE_TAIL_EXPERIMENT", "effect": f"up to {h3_revisit['covers_tail']} TP plus unsafe exposure", "expected_score_delta": "not estimated: H3 remains unsafe", "label": "NOT_PRODUCTION_GAIN"},
        {"front": "REAL_RECALL_TAIL_EXPERIMENT", "effect": f"{len(real_fn)} real detector/resolver misses", "expected_score_delta": "no safe candidate identified", "label": "NOT_PRODUCTION_GAIN"},
    ]
    experiments = [
        {"name": "SUPPRESS_SPECIFIC_FP_GROUP", "objective": "find a structural FP-only subgroup inside a shared rule", "scope": "offline detector precision audit", "files_if_promoted": ["detector.py", "test_citation_detector.py"], "expected_gain": "unknown", "risk": "shared-rule TP loss", "required_negatives": "all current TPs plus non-Gold corpus", "validation_gates": "no lost TP/real ID; official score increase", "why_now": "77 FP remain", "why_not_overfit": "no document/offset/text list"},
        {"name": "RECOVER_REMAINING_INCOMPLETE_TAIL", "objective": "test an extra guard for tribunal-optional chains", "scope": "offline only", "files_if_promoted": ["detector.py", "test_citation_detector.py"], "expected_gain": f"at most {h3_revisit['covers_tail']} tail cases", "risk": h3_revisit["verdict"], "required_negatives": "real, inventada, removed vague, outside-Gold", "validation_gates": "zero unsafe overlap", "why_now": "six tail cases", "why_not_overfit": "bounded grammar only"},
        {"name": "SAFE_REAL_RECALL_TAIL", "objective": "catalog resolver/corpus blockers before any recall rule", "scope": "analysis only", "files_if_promoted": ["detector.py", "parser.py", "resolver.py"], "expected_gain": "none currently justified", "risk": "canonical false-real", "required_negatives": "all real and invented collisions", "validation_gates": "safety 0/0/0", "why_now": "real recall is safety-critical", "why_not_overfit": "requires reusable identity evidence"},
    ]
    recommendation = "FP_PRECISION_AUDIT" if best_precision["score_delta"] > 0 else "STOP_AND_PREPARE_CHECKPOINT_SUBMISSION"
    report = {
        "v8_metrics": current, "fp_ledger": fp_ledger, "fp_groups": fp_groups, "fn_ledger": fn_ledger,
        "remaining_incomplete_tail": tail, "h3_revisit": h3_revisit,
        "real_fn_analysis": {"count": len(real_fn), "by_blocker": Counter(row["first_blocker"] for row in real_fn), "safe_candidate": 0},
        "inventada_error_analysis": {"fn_count": len(inventada["fn"]), "fp_count": len(inventada["fp"]), "confused_with_incompleta": inventada["confused_with_incompleta"], "confused_with_real": 0, "safe_hypothesis": False},
        "classification_frontier": {"count": len(classification_errors), "verdict": "NOT_PRIORITARY" if not classification_errors else "REVIEW_REQUIRED"},
        "resolver_frontier": {"count": len(resolver_errors), "verdict": "CORPUS_BLOCKED" if not resolver_errors else "AMBIGUOUS"},
        "detector_frontier": [{"pattern": list(key), "count": count} for key, count in sorted(detector_groups.items())],
        "score_impact_estimates": estimates, "whole_rule_suppression_diagnostics": suppression_estimates, "candidate_next_experiments": experiments,
        "recommendation": recommendation, "blockers": ["No FP-only production suppression group is evidenced", "H3 tribunal-optional diagnostic is unsafe"],
        "warnings": ["All proposals are diagnostic-only and must not use Gold literals", "Current commit scope is intentionally ignored for this offline audit"],
        "next_step": "DESIGN_NEXT_FP_SUPPRESSION" if recommendation == "FP_PRECISION_AUDIT" else "GENERATE_SUBMISSION_CHECKPOINT", "integrity": {"gold": digest(DATA / "goldenset.csv"), "db": digest(get_database_path()), "pragma": pragma},
        "determinism": {"runs": 3, "identical": True}, "production_diff_before": subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT, text=True),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=lambda value: dict(value) if isinstance(value, Counter) else str(value)) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": current, "fp_groups": fp_groups, "tail": len(tail), "h3": h3_revisit, "recommendation": recommendation, "artifact": str(OUT)}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
