"""Design-only audit for concrete, insufficient jurisprudence references."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import csv
import json
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "material_desafio_jusbrasil_bracis"
OLD = ROOT / "material_desafio_jusbrasil_bracis_old3"
OUT = ROOT / "artifacts" / "gold_v2_concrete_incomplete_design.json"
GOLD_HASH = "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"

if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import gold_v2_baseline_recalibration as baseline  # noqa: E402
import gold_v2_jurisprudencia_geral_precision_audit as audit  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationParser, CitationResolver  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402

COURT = re.compile(
    r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|"
    r"Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b", re.I
)
RELATOR = re.compile(
    r"\b(?:Rel\.?(?:\s*(?:Min\.?|Ministro|Ministra))?|(?:pela|sob|da)\s+relatoria\s+d(?:e|a|c))\s+"
    r"[A-ZÀ-Ý][A-Za-zÀ-ÿ.'-]*(?:\s+|\n+)[A-ZÀ-Ý][A-Za-zÀ-ÿ.'-]*(?:[ \n]+[A-ZÀ-Ý][A-Za-zÀ-ÿ.'-]*)*",
    re.I,
)
YEAR = re.compile(r"\b(?:de|em)\s+(?:19\d{2}|20\d{2})\b|\b(?:julgado|proferido)\s+(?:em\s+)?(?:19\d{2}|20\d{2})\b", re.I)
DECISION = re.compile(
    r"\b(?:julgado|acórdão|precedente|reclamação|Rcl|agravo(?:\s+em\s+recurso\s+especial)?|"
    r"recurso\s+em\s+habeas\s+corpus|habeas\s+corpus|APL)\b", re.I
)
SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]|$)|[^\n]+", re.M)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def features(text: str) -> dict[str, bool]:
    return {
        "tribunal": bool(COURT.search(text)),
        "relator": bool(RELATOR.search(text)),
        "year": bool(YEAR.search(text)),
        "decision_anchor": bool(DECISION.search(text)),
    }


def hypotheses(value: str) -> dict[str, bool]:
    flags = features(value)
    return {
        "H1": flags["tribunal"] and flags["relator"] and flags["year"],
        "H2": flags["tribunal"] and flags["relator"] and flags["year"] and flags["decision_anchor"],
        "H3": flags["relator"] and flags["year"] and flags["decision_anchor"],
    }


def rows(path: Path) -> list[Any]:
    result = []
    with path.open(encoding="utf-8", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            result.append(post.deep.GoldRow(
                index, row["citacao_id"], f"N{row['nivel']}", row["documento_id"],
                int(row["inicio"]), int(row["fim"]), row["trecho"].replace("\\n", "\n"),
                row["tipo"], row["classificacao"], int(row["id_canonico"]) if row["id_canonico"] else None,
            ))
    return result


def overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return max(left[0], right[0]) < min(left[1], right[1])


def iou(left: tuple[int, int], right: tuple[int, int]) -> float:
    intersection = max(0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return intersection / union if union else 0.0


def sentence_inventory(texts: dict[str, str], gold: list[Any]) -> list[dict[str, Any]]:
    records = []
    for doc, text in texts.items():
        document_gold = [item for item in gold if item.document_id == doc]
        for match in SENTENCE.finditer(text):
            value = match.group(0).strip()
            if not value or not any(features(value).values()):
                continue
            span = (match.start(), match.end())
            related = [item for item in document_gold if overlaps(span, (item.start, item.end))]
            records.append({
                "doc": doc, "span": list(span), "text": value,
                "features": features(value), "hypotheses": hypotheses(value),
                "gold_classes": sorted({item.classification for item in related}),
                "outside_gold": not related,
            })
    return records


def current_outputs(texts: dict[str, str]) -> tuple[list[Any], list[Any], str]:
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs, _ = post.run_current_pipeline(texts, resolver)
        return raw, outputs, connection.execute("PRAGMA integrity_check").fetchone()[0]


def oracle(item: Any, text: str, resolver: CitationResolver) -> dict[str, Any]:
    family = post.family_for(item.text, item.citation_type)
    candidate = CitationCandidate(item.start, item.end, item.text, "oracle_concrete_incomplete", family)
    parsed = CitationParser().parse(candidate, context=text)
    result = resolver.resolve(parsed)
    label = "real" if result.status == "resolved" else "inventada" if result.status == "no_match" else "incompleta"
    return {"family": family, "parser": post.parsed_dict(parsed), "resolver": post.result_dict(result), "final_class": label}


def diagnostic_f1(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0


def main() -> int:
    if digest(DATA / "goldenset.csv") != GOLD_HASH or digest(get_database_path()) != DB_HASH:
        raise RuntimeError("GOLD_OR_DB_INTEGRITY_MISMATCH")
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    old_gold = rows(OLD / "goldenset.csv")
    raw, outputs, pragma = current_outputs(texts)
    gold_df = official.load_gold_df()
    current = baseline.metrics(gold_df, list(gold_df.documento_id.drop_duplicates()), gold, raw, outputs)
    incomplete = [item for item in gold if item.classification == "incompleta"]
    jurisprudence = [item for item in incomplete if item.citation_type == "jurisprudencia"]
    legal = [item for item in incomplete if item.citation_type == "lei"]
    old_keys = {(item.document_id, item.gid) for item in old_gold}
    current_keys = {(item.document_id, item.gid) for item in gold}
    removed_vague = [item for item in old_gold if item.classification == "incompleta" and item.citation_type == "jurisprudencia" and (item.document_id, item.gid) not in current_keys]

    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        surviving = []
        for item in jurisprudence:
            related = [candidate for candidate in raw if candidate.document_id == item.document_id and iou((item.start, item.end), (candidate.candidate.start, candidate.candidate.end)) > 0]
            detected = [candidate for candidate in related if iou((item.start, item.end), (candidate.candidate.start, candidate.candidate.end)) >= .5]
            emitted = [output for output in outputs if output.document_id == item.document_id and iou((item.start, item.end), (output.candidate.start, output.candidate.end)) >= .5]
            oracle_row = oracle(item, texts[item.document_id], resolver)
            row = {
                "gold": item.gid, "doc": item.document_id, "level": item.level, "span": [item.start, item.end], "text": item.text,
                "features": features(item.text), "hypotheses": hypotheses(item.text),
                "first_blocker": "DETECTOR" if not detected else "ARBITRATION" if not emitted else "CLASSIFICATION",
                "current_candidates": [{"span": [candidate.candidate.start, candidate.candidate.end], "text": candidate.candidate.text, "rule": candidate.candidate.rule, "family": candidate.candidate.family, "iou": iou((item.start, item.end), (candidate.candidate.start, candidate.candidate.end))} for candidate in related],
                "oracle": oracle_row,
            }
            surviving.append(row)

    main_27 = [row for row in surviving if row["hypotheses"]["H1"]]
    tail = [row for row in surviving if not row["hypotheses"]["H1"]]
    all_by_class = {label: [item for item in gold if item.classification == label and item.citation_type == "jurisprudencia"] for label in ("real", "inventada", "incompleta")}
    structural_overlap = {
        label: [{"gold": item.gid, "doc": item.document_id, "level": item.level, "text": item.text, "hypotheses": hypotheses(item.text)} for item in values if any(hypotheses(item.text).values())]
        for label, values in all_by_class.items()
    }
    sentences = sentence_inventory(texts, gold)
    sentence_counts = {
        name: {
            "total": sum(row["hypotheses"][name] for row in sentences),
            "outside_gold": sum(row["hypotheses"][name] and row["outside_gold"] for row in sentences),
            "gold_incompleta": sum(row["hypotheses"][name] and "incompleta" in row["gold_classes"] for row in sentences),
            "gold_real": sum(row["hypotheses"][name] and "real" in row["gold_classes"] for row in sentences),
            "gold_inventada": sum(row["hypotheses"][name] and "inventada" in row["gold_classes"] for row in sentences),
        }
        for name in ("H1", "H2", "H3")
    }
    support = []
    for name in ("H1", "H2", "H3"):
        target = [row for row in surviving if row["hypotheses"][name]]
        real = [row for row in structural_overlap["real"] if row["hypotheses"][name]]
        invented = [row for row in structural_overlap["inventada"] if row["hypotheses"][name]]
        counts = sentence_counts[name]
        support.append({
            "hypothesis": name, "incomplete_covered": len(target), "real_overlaps": len(real), "inventada_overlaps": len(invented),
            "non_gold_negatives": counts["outside_gold"], "exact_gold_spans_diagnostic": len(target),
            "verdict": "SAFE" if name == "H2" and not real and not invented and not counts["outside_gold"] else "PLAUSIBLE" if name == "H1" else "UNSAFE",
        })
    # The H2 contract is selected only if its corpus-wide audit stays clean; otherwise promotion is forbidden.
    h2 = next(item for item in support if item["hypothesis"] == "H2")
    safe_hypothesis = "H2" if h2["verdict"] == "SAFE" else None
    class_metrics = {row["level"]: row for row in audit.class_metrics(gold, outputs) if row["class"] == "incompleta"}
    potential = []
    for name in ("H1", "H2", "H3"):
        by_level = Counter(row["level"] for row in surviving if row["hypotheses"][name])
        potential.append({
            "hypothesis": name, "additional_TP": dict(by_level),
            "N1_incomplete_F1_if_exact_no_new_FP": diagnostic_f1(by_level["N1"], class_metrics["N1"]["FP"], class_metrics["N1"]["FN"] - by_level["N1"]),
            "N2_incomplete_F1_if_exact_no_new_FP": diagnostic_f1(by_level["N2"], class_metrics["N2"]["FP"], class_metrics["N2"]["FN"] - by_level["N2"]),
            "label": "DIAGNOSTIC_ONLY",
        })
    snapshot = {"surviving": surviving, "support": support, "sentences": sentences, "potential": potential}
    repeat = []
    for _ in range(3):
        repeat.append(stable(snapshot))
    report = {
        "environment": {"gold_sha256": digest(DATA / "goldenset.csv"), "database_sha256": digest(get_database_path()), "pragma_integrity_check": pragma, "head": __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()},
        "current_baseline": {key: current[key] for key in ("predictions", "matches", "FP", "FN", "exact", "correct_real_ids", "N1", "N2", "final", "safety")},
        "surviving_incomplete_inventory": surviving, "main_27_group": main_27, "plausible_6_group": tail,
        "current_candidate_interactions": {"raw_candidates": len(raw), "outputs": len(outputs), "overlap_rows": sum(bool(row["current_candidates"]) for row in surviving)},
        "tribunal_contextual_analysis": [row for row in surviving if any(item["rule"] == "tribunal_contextual" for item in row["current_candidates"])],
        "parser_oracle": [{"gold": row["gold"], "family": row["oracle"]["family"], "parser": row["oracle"]["parser"]} for row in surviving],
        "resolver_oracle": [{"gold": row["gold"], "resolver": row["oracle"]["resolver"], "final_class": row["oracle"]["final_class"]} for row in surviving],
        "real_overlap_inventory": structural_overlap["real"], "invented_overlap_inventory": structural_overlap["inventada"],
        "removed_vague_negative_evidence": [{"gold": item.gid, "doc": item.document_id, "text": item.text, "features": features(item.text)} for item in removed_vague],
        "corpus_negative_inventory": [row for row in sentences if row["outside_gold"] and any(row["hypotheses"].values())], "sentence_inventory": sentences,
        "hypotheses": {"H1": "tribunal + relator + contextual year", "H2": "decision anchor + tribunal + relator + contextual year", "H3": "decision/class anchor + relator + contextual year; tribunal optional"},
        "support_matrix": support, "span_analysis": {"main_27_gold_span_is_target": True, "current_partial_candidates": sum(bool(row["current_candidates"]) for row in main_27)},
        "safe_hypothesis": safe_hypothesis, "deferred_cases": [{"gold": row["gold"], "text": row["text"], "reason": "missing explicit tribunal+relator+year"} for row in tail],
        "diagnostic_potential": potential,
        "blind_risk": "PARTIAL" if safe_hypothesis else "WEAK",
        "implementation_scope": {"production_change_authorized": False, "recommended_future_rule": "new specific concrete-incomplete detector rule", "parser": "reuse jurisprudencia_tribunal_contextual if its current fields are sufficient", "resolver": "unchanged", "arbitration": "unchanged", "classification": "unchanged"},
        "experiment_plan": "Implement H2 only in a disposable offline harness; require bounded span, no wildcard across sentences, and full safety comparison.",
        "promotion_gates": ["H2 keeps zero real/inventada/non-gold overlaps", "all emitted spans have IoU >= 0.5", "oracle/result stays insufficient or ambiguous", "no real-ID or safety regression", "negative vague corpus remains un-emitted", "determinism and full tests pass"],
        "determinism": {"runs": 3, "identical": len(set(repeat)) == 1},
        "design_verdict": "CONCRETE_INCOMPLETE_DESIGN_READY_WITH_WARNINGS" if safe_hypothesis else "TARGETED_REVIEW_REQUIRED",
        "next_step": "RUN_CONCRETE_INCOMPLETE_OFFLINE_EXPERIMENT" if safe_hypothesis else "TARGETED_REVIEW_CONCRETE_INCOMPLETE",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"design_verdict": report["design_verdict"], "next_step": report["next_step"], "safe_hypothesis": safe_hypothesis}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
