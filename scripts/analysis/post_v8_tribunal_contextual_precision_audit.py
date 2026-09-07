"""Offline precision audit for legacy ``tribunal_contextual`` after V8."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402

OUT = ROOT / "artifacts" / "post_v8_tribunal_contextual_precision_audit.json"
EXPECTED = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39, "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
DECISION = re.compile(r"\b(?:acórdão|julgado|precedente|reclamação|rcl|agravo|recurso|habeas corpus)\b", re.I)
RELATOR = re.compile(r"\b(?:rel\.?|relator(?:ia)?|ministro|ministra)\b", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
VAGUE = re.compile(r"\b(?:jurisprud[êe]ncia|orientação jurisprudencial|precedentes|verbete sumular)\b", re.I)


def context(text: str, start: int, end: int) -> str:
    left, right = max(0, start - 180), min(len(text), end + 180)
    return text[left:right].replace("\n", " ")


def main() -> int:
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    texts, raw, outputs, pragma = base.pipeline()
    current, raw_pairs, output_pairs = base.metrics(metric, gold, raw, outputs)
    current["score"] = float(metric.score(base.solution(gold), base.submission(outputs, texts), "documento_id"))
    if any(current[key] != value for key, value in EXPECTED.items()):
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {current}")
    raw_matched = {(item["doc"], item["candidate"].start, item["candidate"].end, item["candidate"].rule): row for row, item in raw_pairs}
    h2 = [item for item in outputs if item["candidate"].rule == "decision_tribunal_relator_year"]
    old = [item for item in outputs if item["candidate"].rule == "tribunal_contextual"]
    ledger = []
    for item in old:
        candidate, result = item["candidate"], item["result"]
        key = (item["doc"], candidate.start, candidate.end, candidate.rule)
        matched = raw_matched.get(key)
        related = [row for row in gold if row.document_id == item["doc"]]
        closest = max(related, key=lambda row: base.iou((candidate.start,candidate.end),(row.start,row.end)), default=None)
        maximum = base.iou((candidate.start,candidate.end),(closest.start,closest.end)) if closest else 0.0
        overlaps = [base.iou((candidate.start,candidate.end),(other["candidate"].start,other["candidate"].end)) for other in h2 if other["doc"] == item["doc"]]
        text = candidate.text
        flags = {"decision": bool(DECISION.search(text)), "relator": bool(RELATOR.search(text)), "year": bool(YEAR.search(text)), "vague": bool(VAGUE.search(context(texts[item["doc"]],candidate.start,candidate.end)))}
        ledger.append({"documento": item["doc"], "nivel": next((row.level for row in related),None), "span":[candidate.start,candidate.end], "text":text,
                       "context":context(texts[item["doc"]],candidate.start,candidate.end), "family":candidate.family,"rule":candidate.rule,
                       "classification":base.label(result.status),"id_canonico":result.id_canonico,"confidence":None,
                       "matched_gold":None if matched is None else matched.gid,"gold_class":None if matched is None else matched.classification,
                       "gold_type":None if matched is None else matched.citation_type,"max_iou_gold":maximum,"closest_gold":None if closest is None else closest.gid,
                       "TP_FP":"TP" if matched else "FP","parser_payload":dict(item["parsed"].data),"resolver_status":result.status,
                       "flags":flags,"h2_same_doc":any(other["doc"]==item["doc"] for other in h2),"h2_iou":max(overlaps,default=0.0),
                       "h2_relation":"H2_REDUNDANT_CONTEXTUAL" if max(overlaps,default=0.0)>=.5 else "INDEPENDENT_CONTEXTUAL_FP" if not matched else "CONTEXTUAL_TP_TO_PRESERVE"})
    tps, fps = [row for row in ledger if row["TP_FP"]=="TP"], [row for row in ledger if row["TP_FP"]=="FP"]
    predicates = {
        "contextual_without_decision_anchor": lambda row: not row["flags"]["decision"],
        "tribunal_without_relator": lambda row: not row["flags"]["relator"],
        "tribunal_without_year_or_relator": lambda row: not row["flags"]["year"] and not row["flags"]["relator"],
        "fragment_redundant_with_h2": lambda row: row["h2_iou"] >= .5,
        "vague_contextual": lambda row: row["flags"]["vague"],
    }
    groups, simulations = [], []
    for name, predicate in predicates.items():
        selected = [row for row in ledger if predicate(row)]
        spans = {(row["documento"],*row["span"]) for row in selected}
        retained = [item for item in outputs if (item["doc"],item["candidate"].start,item["candidate"].end) not in spans]
        score = float(metric.score(base.solution(gold),base.submission(retained,texts),"documento_id"))
        retained_pairs = base.match(metric,gold,retained)
        groups.append({"group":name,"fp_removed":sum(row["TP_FP"]=="FP" for row in selected),"tp_lost":sum(row["TP_FP"]=="TP" for row in selected),
                       "h2_affected":sum(row["rule"]=="decision_tribunal_relator_year" for row in selected),"score_delta":score-current["score"],
                       "risk":"LOW" if selected and not any(row["TP_FP"]=="TP" for row in selected) else "HIGH",
                       "verdict":"PLAUSIBLE_SUPPRESSION_CANDIDATE" if selected and not any(row["TP_FP"]=="TP" for row in selected) else "UNSAFE_SUPPRESSION"})
        simulations.append({"group":name,"score":score,"matches":len(retained_pairs),"real_ids":sum(g.classification=="real" and p["result"].status=="resolved" and str(p["result"].id_canonico)==g.canonical_id for g,p in retained_pairs),"label":"DIAGNOSTIC_ONLY"})
    best = max((group for group in groups if group["verdict"]=="PLAUSIBLE_SUPPRESSION_CANDIDATE" and group["score_delta"] > 0),key=lambda row:row["score_delta"],default=None)
    report={"v8_baseline":current,"contextual_outputs":ledger,"h2_outputs":[{"doc":item["doc"],"span":[item["candidate"].start,item["candidate"].end],"status":item["result"].status} for item in h2],
            "old_contextual_tps":tps,"old_contextual_fps":fps,"fp_groups":{"flags":Counter(flag for row in fps for flag,value in row["flags"].items() if value),"h2_relation":Counter(row["h2_relation"] for row in fps)},
            "h2_interactions":{"h2_count":len(h2),"old_iou_ge_half":sum(row["h2_iou"]>=.5 for row in ledger)},
            "removed_vague_audit":{"vague_fp":sum(row["flags"]["vague"] for row in fps),"verdict":"VAGUE_CONTEXTUAL_SUPPRESSION_CANDIDATE" if any(row["flags"]["vague"] for row in fps) else "NO_VAGUE_CONTEXTUAL_GROUP"},
            "candidate_suppressions":groups,"simulations":simulations,"preservation_checks":{"h2":len(h2)==27,"baseline_matches":156,"real_ids":71,"old_tp":len(tps)},
            "non_gold_corpus_audit":{"legacy_fp":len(fps),"warning":"Current corpus FPs are insufficient blind evidence."},
            "recommendation":"ADVANCE_TO_TRIBUNAL_CONTEXTUAL_SUPPRESSION_EXPERIMENT" if best else "AUDIT_CNJ_PRECISION_NEXT","best_candidate":None if best is None else best["group"],
            "next_step":"RUN_TRIBUNAL_CONTEXTUAL_SUPPRESSION_OFFLINE_EXPERIMENT" if best else "AUDIT_CNJ_PRECISION_NEXT","integrity":{"gold":base.digest(base.DATA/"goldenset.csv"),"db":base.digest(base.get_database_path()),"pragma":pragma},"production_diff":__import__("subprocess").check_output(["git","diff","--","src"],cwd=ROOT,text=True)}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=lambda value:dict(value) if isinstance(value,Counter) else str(value))+"\n",encoding="utf-8")
    print(json.dumps({"old":len(old),"tp":len(tps),"fp":len(fps),"groups":groups,"recommendation":report["recommendation"]},ensure_ascii=False))


if __name__ == "__main__":
    main()
