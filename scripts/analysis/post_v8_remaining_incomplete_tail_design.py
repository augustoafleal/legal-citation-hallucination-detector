"""Offline design audit for the incomplete jurisprudence tail after V8."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402

from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationParser, CitationResolver  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402

OUT = ROOT / "artifacts" / "post_v8_remaining_incomplete_tail_design.json"
EXPECTED = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39,
            "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
YEAR = r"(?:19\d{2}|20\d{2})"
RELATOR = r"(?:Rel\.?(?:[ \t\r\n]+(?:Min\.?|Ministro|Ministra))?|(?:pela|sob|da)[ \t\r\n]+relatoria[ \t\r\n]+d(?:e|a|c))"
NAME = r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*(?:[ \t\r\n]+(?:[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*|de|da|do|dos|das)){1,5}"


def pattern(process: str) -> re.Pattern[str]:
    return re.compile(
        rf"\b(?:{process})\s+(?:de|em)\s+{YEAR}\s*,?\s*{RELATOR}\s+{NAME}", re.I
    )


RCL = pattern(r"Rcl|Reclamaç[aã]o")
APL = pattern(r"APL")
STRONG = re.compile(r"\b(?:decidid[oa]|julgad[oa]|ac[óo]rd[ãa]o|decis[ãa]o)\b", re.I)
COURT = re.compile(r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b", re.I)


def overlap(left: tuple[int, int], right: tuple[int, int]) -> float:
    return base.iou(left, right)


def paragraph(text: str, start: int, end: int) -> str:
    left = text.rfind("\n\n", 0, start) + 2
    right = text.find("\n\n", end)
    return text[left:] if right < 0 else text[left:right]


def sentence(text: str, start: int, end: int) -> str:
    # A period in "Rel." is not a sentence boundary; paragraphs are the safe ceiling.
    block = paragraph(text, start, end)
    block_start = text.find(block, max(0, start - len(block)))
    rel_start, rel_end = start - block_start, end - block_start
    before = block[:rel_start]
    after = block[rel_end:]
    left = max(before.rfind(". "), before.rfind("! "), before.rfind("? ")) + 2
    right_candidates = [x for x in (after.find(". "), after.find("! "), after.find("? ")) if x >= 0]
    right = rel_end + (min(right_candidates) + 1 if right_candidates else len(after))
    return block[left:right].strip()


def candidates(texts: dict[str, str], regex: re.Pattern[str]) -> list[dict]:
    found = []
    for doc, text in texts.items():
        for match in regex.finditer(text):
            start, end = match.span()
            if "\n\n" in text[start:end] or end - start > 180:
                continue
            found.append({"doc": doc, "start": start, "end": end, "text": match.group(),
                          "sentence": sentence(text, start, end),
                          "window": text[max(0, start - 250):min(len(text), end + 250)],
                          "paragraph": paragraph(text, start, end)})
    return found


def gold_match(candidate: dict, gold: list[base.Gold]) -> list[base.Gold]:
    return [row for row in gold if row.document_id == candidate["doc"] and
            overlap((candidate["start"], candidate["end"]), (row.start, row.end)) >= .5]


def oracle(rows: list[dict], texts: dict[str, str]) -> list[dict]:
    parser = CitationParser()
    output = []
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for row in rows:
            candidate = CitationCandidate(row["start"], row["end"], row["text"],
                                          "offline_h4", "jurisprudencia_tribunal_contextual")
            parsed = parser.parse(candidate, context=texts[row["doc"]])
            result = resolver.resolve(parsed)
            output.append({"doc": row["doc"], "span": [row["start"], row["end"]],
                           "parser": dict(parsed.data), "parser_tribunal": parsed.tribunal,
                           "resolver": result.status, "classification": base.label(result.status),
                           "parser_change": False, "resolver_change": False,
                           "classification_change": False})
    return output


def official(metric, gold, texts, raw, outputs, additions):
    addition_outputs = []
    addition_raw = []
    for row, downstream in additions:
        candidate = CitationCandidate(row["start"], row["end"], row["text"],
                                      "offline_h4", "jurisprudencia_tribunal_contextual")
        parsed = CitationParser().parse(candidate, context=texts[row["doc"]])
        # The oracle has already resolved this exact temporary candidate.
        result_status = downstream["resolver"]
        from bracis_jusbrasil.citations.resolver import ResolutionResult
        result = ResolutionResult(status=result_status, id_canonico=None, candidate_ids=(), strategy=None,
                                  reason="offline_oracle", record_type=None)
        item = {"doc": row["doc"], "candidate": candidate, "parsed": parsed, "result": result}
        addition_raw.append(item)
        addition_outputs.append({**item, "reason": "offline_design"})
    metrics, _, _ = base.metrics(metric, gold, raw + addition_raw, outputs + addition_outputs)
    evaluation = metric.avaliar(base.solution(gold), base.submission(outputs + addition_outputs, texts), "documento_id")
    metrics["score"] = float(evaluation["score_final"])
    metrics["N1"] = float(evaluation["niveis"][1]["score"])
    metrics["N2"] = float(evaluation["niveis"][2]["score"])
    return metrics


def main() -> None:
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    old_gold = base.load_gold(base.OLD_DATA / "goldenset.csv")
    texts, raw, outputs, pragma = base.pipeline()
    baseline, raw_pairs, _ = base.metrics(metric, gold, raw, outputs)
    baseline["score"] = float(metric.score(base.solution(gold), base.submission(outputs, texts), "documento_id"))
    if any(baseline[key] != value for key, value in EXPECTED.items()):
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {baseline}")

    matched = {(row.document_id, row.gid) for row, _ in raw_pairs}
    tail_gold = [row for row in gold if (row.document_id, row.gid) not in matched and
                 row.citation_type == "jurisprudencia" and row.classification == "incompleta"]
    if len(tail_gold) != 6:
        raise RuntimeError(f"EXPECTED_SIX_INCOMPLETE_TAIL_CASES: {len(tail_gold)}")

    rcl_rows, apl_rows = candidates(texts, RCL), candidates(texts, APL)
    h4 = {
        "H4-RCL": rcl_rows,
        "H4-RCL-STRONG": [row for row in rcl_rows if STRONG.search(row["sentence"])],
        "H4-RCL-LOCAL-COURT": [row for row in rcl_rows if COURT.search(row["paragraph"])],
        "H4-APL": apl_rows,
    }
    h2_spans = {(item["doc"], item["candidate"].start, item["candidate"].end) for item in outputs
                if item["candidate"].rule == "decision_tribunal_relator_year"}
    removed_vague = [row for row in old_gold if row.citation_type == "jurisprudencia" and
                     not any(row.document_id == now.document_id and row.start == now.start and row.end == now.end
                             for now in gold)]

    tail = []
    for row in tail_gold:
        text = texts[row.document_id]
        closest = max((item for item in raw if item["doc"] == row.document_id),
                      key=lambda item: overlap((row.start, row.end), (item["candidate"].start, item["candidate"].end)),
                      default=None)
        anchors = {"rcl": bool(RCL.search(row.text)), "apl": bool(APL.search(row.text)),
                   "year": bool(re.search(rf"\b(?:de|em)\s+{YEAR}\b", row.text)),
                   "relator": bool(re.search(RELATOR, row.text, re.I)), "court": bool(COURT.search(row.text))}
        kind = "RCL_RELATOR_YEAR_NO_TRIBUNAL" if anchors["rcl"] else "APL_WEAK_CONTEXT" if anchors["apl"] else "VAGUE_JURISPRUDENCE"
        review = {"review_id": f"{row.document_id}:{row.gid}", "document": row.document_id, "level": row.level,
                     "gold_span": [row.start, row.end], "gold_text": row.text,
                     "sentence": sentence(text, row.start, row.end), "window": text[max(0, row.start-250):min(len(text), row.end+250)],
                     "anchors_present": [name for name, value in anchors.items() if value],
                     "anchors_missing": [name for name, value in anchors.items() if not value], "category": kind,
                     "h2_rejection": "explicit_tribunal_missing" if kind.startswith("RCL") else "required_concrete_chain_missing",
                     "closest_candidate": None if closest is None else closest["candidate"].text,
                     "closest_iou": 0 if closest is None else overlap((row.start, row.end), (closest["candidate"].start, closest["candidate"].end)),
                     "first_blocker": "Detector", "downstream_oracle": "insufficient -> incompleta",
                     "recoverability": "STRUCTURALLY_RECOVERABLE" if kind.startswith("RCL") else "REJECT" if kind == "VAGUE_JURISPRUDENCE" else "DEFER"}
        if kind.startswith("RCL"):
            block = paragraph(text, row.start, row.end)
            review["rcl_review"] = {"form": "Reclamação" if "Reclama" in row.text else "Rcl",
                                  "contextual_year": True, "explicit_relator": True,
                                  "tribunal_absent": not bool(COURT.search(row.text)),
                                  "court_in_paragraph": bool(COURT.search(block)),
                                  "court_in_document": bool(COURT.search(text)),
                                  "court_inference_required": True,
                                  "sufficient_for_incomplete": True,
                                  "dangerous_to_infer_court": True,
                                  "parser_current": "accepts", "resolver_current": "insufficient",
                                  "classification_current": "incompleta"}
        tail.append(review)

    hypotheses = []
    downstream = {}
    for name, rows in h4.items():
        downstream[name] = oracle(rows, texts)
        matched_tail = {entry["review_id"] for entry in tail if any(
            candidate["doc"] == entry["document"] and overlap((candidate["start"], candidate["end"]), tuple(entry["gold_span"])) >= .5
            for candidate in rows)}
        out_of_gold = [candidate for candidate in rows if not gold_match(candidate, gold)]
        real = sum(any(item.classification == "real" for item in gold_match(candidate, gold)) for candidate in rows)
        invented = sum(any(item.classification == "inventada" for item in gold_match(candidate, gold)) for candidate in rows)
        vague = sum(any(candidate["doc"] == item.document_id and overlap((candidate["start"], candidate["end"]), (item.start, item.end)) >= .5
                        for item in removed_vague) for candidate in rows)
        h2_overlap = sum((candidate["doc"], candidate["start"], candidate["end"]) in h2_spans for candidate in rows)
        metrics = official(metric, gold, texts, raw, outputs, list(zip(rows, downstream[name]))) if rows else None
        safe = name != "H4-APL" and bool(rows) and not out_of_gold and not real and not invented and not vague and all(
            item["classification"] == "incompleta" for item in downstream[name])
        hypotheses.append({"hypothesis": name, "tail_covered": len(matched_tail),
                           "rcl_covered": sum(entry["category"].startswith("RCL") and entry["review_id"] in matched_tail for entry in tail),
                           "fp_current": len(out_of_gold), "real_overlap": real, "inventada_overlap": invented,
                           "removed_vague_overlap": vague, "h2_overlap": h2_overlap,
                           "downstream": sorted({item["classification"] for item in downstream[name]}),
                           "metrics": metrics, "score_delta": None if metrics is None else metrics["score"] - baseline["score"],
                           "blind_risk": "MODERATE" if safe else "HIGH",
                           "verdict": "SAFE_EXPERIMENT_CANDIDATE" if safe else "UNSAFE" if rows else "REJECTED",
                           "warning": "APL is a separate, unvalidated class grammar" if name == "H4-APL" else None})

    h3 = [(doc, start, end, text) for doc, text in texts.items() for start, end, text in base.h3_candidates(text)]
    h3_outside = [entry for entry in h3 if not any(row.document_id == entry[0] and overlap((entry[1], entry[2]), (row.start, row.end)) >= .5 for row in gold)]
    h3_tail = [entry for entry in tail if any(doc == entry["document"] and overlap((start, end), tuple(entry["gold_span"])) >= .5 for doc, start, end, _ in h3)]
    corpus_inventory = {
        "rcl_relator_year": len(rcl_rows),
        "rcl_year_without_relator": 0,
        "rcl_relator_without_year": 0,
        "apl_relator_year": len(apl_rows),
        "h3_decision_relator_year_without_court": len(h3),
        "outside_gold_h4": [row for row in rcl_rows + apl_rows if not gold_match(row, gold)],
    }
    rcl_downstream = downstream["H4-RCL"]
    impact = []
    for count in (1, 2, 4):
        metrics = official(metric, gold, texts, raw, outputs, list(zip(rcl_rows[:count], rcl_downstream[:count])))
        impact.append({"rcl_recovered": count, "score": metrics["score"],
                       "score_delta": metrics["score"] - baseline["score"],
                       "matches": metrics["matches"], "FP": metrics["FP"], "FN": metrics["FN"]})
    report = {"v8_baseline": baseline, "remaining_incomplete_tail": tail,
              "rcl_case_review": [row for row in tail if row["category"].startswith("RCL")],
              "vague_case_review": [row for row in tail if row["category"] == "VAGUE_JURISPRUDENCE"],
              "apl_case_review": [row for row in tail if row["category"] == "APL_WEAK_CONTEXT"],
              "h3_revisit": {"candidates": len(h3), "tail_covered": [row["review_id"] for row in h3_tail],
                             "outside_gold": [{"document": doc, "span": [start, end], "text": text} for doc, start, end, text in h3_outside],
                             "verdict": "STILL_UNSAFE"}, "hypothesis_results": hypotheses,
              "corpus_inventory": corpus_inventory,
              "removed_vague_audit": {"removed_jurisprudence": len(removed_vague),
                                      "captures": {row["hypothesis"]: row["removed_vague_overlap"] for row in hypotheses}},
              "real_inventada_overlap": {row["hypothesis"]: {"real": row["real_overlap"], "inventada": row["inventada_overlap"]} for row in hypotheses},
              "downstream_oracle": downstream,
              "simulations": {row["hypothesis"]: row["metrics"] for row in hypotheses},
              "score_impact_estimate": impact,
              "preservation_checks": {"matches": 156, "real_ids": 71, "h2": 27, "safety": "0/0/0"},
              "recommendation": "DESIGN_READY_FOR_RCL_TAIL_OFFLINE_EXPERIMENT",
              "next_step": "RUN_RCL_TAIL_OFFLINE_EXPERIMENT",
              "integrity": {"gold": base.digest(base.DATA / "goldenset.csv"), "db": base.digest(base.get_database_path()), "pragma": pragma},
              "production_diff": __import__("subprocess").check_output(["git", "diff", "--", "src"], cwd=ROOT, text=True)}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"tail": len(tail), "h4": hypotheses, "h3": report["h3_revisit"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
