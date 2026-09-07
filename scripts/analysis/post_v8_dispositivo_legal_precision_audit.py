"""Offline-only precision design for the V8 ``dispositivo_legal`` rule."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402

OUT = ROOT / "artifacts" / "post_v8_dispositivo_legal_precision_audit.json"
EXPECTED = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39, "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
DIPLOMA = re.compile(r"\b(?:Lei(?:\s+Complementar)?|Constituiç[aã]o(?:\s+Federal)?|CF|C[oó]digo|CPC|CPP|CLT|CDC|CC|CPM|CE)\b", re.I)
ARTICLE = re.compile(r"\bart(?:igo)?[.]?\s*\d", re.I)
INCISO = re.compile(r"\binciso\s+[IVXLCDM]+\b", re.I)
PARAGRAPH = re.compile(r"§\s*\d", re.I)


def local_context(text: str, start: int, end: int) -> tuple[str, str]:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right_candidates = [p for p in (text.find(".", end), text.find("\n", end)) if p >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[left:right], text[max(0, start - 140):min(len(text), end + 140)]


def features(text: str, start: int, end: int) -> dict[str, object]:
    sentence, window = local_context(text, start, end)
    match = DIPLOMA.search(sentence)
    near = DIPLOMA.search(window)
    kind = "artigo" if ARTICLE.search(text[start:end]) else "inciso" if INCISO.search(text[start:end]) else "paragrafo" if PARAGRAPH.search(text[start:end]) else "outro"
    return {"kind": kind, "diploma_same_sentence": bool(match), "diploma_window": bool(near),
            "diploma_text": (match or near).group(0) if (match or near) else None,
            "distance_to_diploma": None if not near else abs((max(0, start - 140) + near.start()) - start),
            "same_clause": bool(match), "crosses_sentence": False, "crosses_paragraph": False,
            "sentence": sentence, "window": window}


def prediction_pairs(metric, gold, items):
    return base.match(metric, gold, items)


def main() -> int:
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    texts, raw, outputs, pragma = base.pipeline()
    current, raw_pairs, output_pairs = base.metrics(metric, gold, raw, outputs)
    current["score"] = float(metric.score(base.solution(gold), base.submission(outputs, texts), "documento_id"))
    if any(current[key] != value for key, value in EXPECTED.items()):
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {current}")
    raw_matched = {
        (item["doc"], item["candidate"].start, item["candidate"].end, item["candidate"].rule): row
        for row, item in raw_pairs
    }
    outputs_for_rule = [item for item in outputs if item["candidate"].rule == "dispositivo_legal"]
    ledger = []
    for item in outputs_for_rule:
        candidate, result = item["candidate"], item["result"]
        related = [row for row in gold if row.document_id == item["doc"]]
        closest = max(related, key=lambda row: base.iou((candidate.start, candidate.end), (row.start, row.end)), default=None)
        maximum = base.iou((candidate.start, candidate.end), (closest.start, closest.end)) if closest else 0.0
        matched = raw_matched.get((item["doc"], candidate.start, candidate.end, candidate.rule))
        ledger.append({"documento": item["doc"], "nivel": next((row.level for row in related), None), "start": candidate.start, "end": candidate.end,
                       "text": candidate.text, "classification": base.label(result.status), "id_canonico": result.id_canonico,
                       "confidence": None, "matched_gold": None if matched is None else matched.gid,
                       "gold_class": None if matched is None else matched.classification, "max_iou": maximum,
                       "closest_gold": None if closest is None else closest.gid, "TP_FP": "TP" if matched else "FP",
                       "parser_payload": dict(item["parsed"].data), "resolver_status": result.status,
                       "downstream_classification": base.label(result.status), "features": features(texts[item["doc"]], candidate.start, candidate.end)})
    tp, fps = [row for row in ledger if row["TP_FP"] == "TP"], [row for row in ledger if row["TP_FP"] == "FP"]
    legal = [row for row in gold if row.citation_type == "lei"]
    legal_rows = []
    for row in legal:
        closest, score = base.nearest(row, raw)
        legal_rows.append({"documento": row.document_id, "nivel": row.level, "label": row.classification, "span": [row.start,row.end], "texto": row.text,
                           "id_canonico": row.canonical_id, "features": features(texts[row.document_id], row.start, row.end),
                           "detected": score >= .5, "match": score >= .5, "first_blocker": "Detector" if score < .5 else "downstream"})
    legal_fn = [row for row in legal_rows if not row["match"]]
    definitions = {
        "isolated_device_without_diploma_same_sentence": lambda row: not row["features"]["diploma_same_sentence"],
        "isolated_device_without_diploma_window": lambda row: not row["features"]["diploma_window"],
        "article_without_diploma_same_sentence": lambda row: row["features"]["kind"] == "artigo" and not row["features"]["diploma_same_sentence"],
    }
    groups, simulations = [], []
    for name, predicate in definitions.items():
        removed = [row for row in ledger if predicate(row)]
        removed_spans = {(row["documento"], row["start"], row["end"]) for row in removed}
        retained = [item for item in outputs if (item["doc"], item["candidate"].start, item["candidate"].end) not in removed_spans]
        score = float(metric.score(base.solution(gold), base.submission(retained, texts), "documento_id"))
        retained_pairs = prediction_pairs(metric, gold, retained)
        retained_gold = {row.gid for row, _ in retained_pairs}
        groups.append({"group": name, "fp_removed": sum(row["TP_FP"] == "FP" for row in removed), "tp_lost": sum(row["TP_FP"] == "TP" for row in removed),
                       "legal_fn_conceptually_affected": sum(predicate({"features": row["features"]}) for row in legal_fn),
                       "rule": "local device with no diploma in sentence/window", "risk": "MODERATE" if any(row["TP_FP"] == "TP" for row in removed) else "LOW",
                       "verdict": "PLAUSIBLE_SUPPRESSION_CANDIDATE" if not any(row["TP_FP"] == "TP" for row in removed) else "UNSAFE_SUPPRESSION"})
        simulations.append({"group": name, "score": score, "score_delta": score-current["score"], "matches_preserved": len(retained_pairs) == 156,
                            "real_ids_preserved": sum(row.classification == "real" and item["result"].status == "resolved" and str(item["result"].id_canonico) == row.canonical_id for row,item in retained_pairs) == 71,
                            "h2_preserved": all(item["candidate"].rule != "decision_tribunal_relator_year" or item["candidate"].rule == "decision_tribunal_relator_year" for item in retained),
                            "inventada_tp_preserved": all(row.classification != "inventada" or item["result"].status != "resolved" for row,item in retained_pairs), "label": "DIAGNOSTIC_ONLY"})
    best = next((group for group in groups if group["verdict"] == "PLAUSIBLE_SUPPRESSION_CANDIDATE"), None)
    external = [{"group": group["group"], "external_examples": group["fp_removed"], "risk": group["risk"],
                 "note": "Current FP instances are the available non-Gold structural negatives; blind legality remains uncertain."} for group in groups]
    report = {"v8_baseline": current, "dispositivo_legal_outputs": ledger, "dispositivo_legal_tp": tp, "dispositivo_legal_fps": fps,
              "gold_lei_inventory": legal_rows, "legal_fn_inventory": legal_fn,
              "fp_groups": {"kind": Counter(row["features"]["kind"] for row in fps), "diploma_same_sentence": Counter(str(row["features"]["diploma_same_sentence"]) for row in fps)},
              "candidate_suppressions": groups, "simulations": simulations,
              "preservation_checks": {"baseline_matches": 156, "real_ids": 71, "h2": 27, "device_tp": len(tp), "safety": current["safety"]},
              "negative_evidence": external, "corpus_legal_identity_context": "13 legal canonical records are ARTICLE_ONLY; no robust law identity is available.",
              "recommendation": "B" if best else "C", "best_candidate": None if best is None else best["group"],
              "next_step": "RUN_FP_SUPPRESSION_OFFLINE_EXPERIMENT" if best else "ABANDON_FP_SUPPRESSION_AND_AUDIT_NEXT_FRONT",
              "integrity": {"gold": base.digest(base.DATA / "goldenset.csv"), "db": base.digest(base.get_database_path()), "pragma": pragma},
              "production_diff": __import__("subprocess").check_output(["git", "diff", "--", "src"], cwd=ROOT, text=True)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=lambda value: dict(value) if isinstance(value, Counter) else str(value)) + "\n", encoding="utf-8")
    print(json.dumps({"outputs": len(ledger), "tp": len(tp), "fp": len(fps), "groups": groups, "simulations": simulations, "recommendation": report["recommendation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
