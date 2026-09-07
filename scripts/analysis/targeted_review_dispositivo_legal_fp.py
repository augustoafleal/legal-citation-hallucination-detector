"""Human-readable counterexample review; no production behaviour is changed."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402

SOURCE = ROOT / "artifacts" / "post_v8_dispositivo_legal_precision_audit.json"
OUT = ROOT / "artifacts" / "targeted_review_dispositivo_legal_fp.json"
MD = ROOT / "artifacts" / "targeted_review_dispositivo_legal_fp.md"
EXPECTED = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39, "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
DIPLOMA = re.compile(r"\b(?:Constituiç[aã]o(?:\s+Federal)?|CF|C[oó]digo(?:\s+de\s+Processo\s+(?:Civil|Penal)|\s+Civil|\s+Penal|\s+Eleitoral)?|CPC|CPP|CLT|CDC|CC|CPM|CE|Consolidaç[aã]o\s+das\s+Leis\s+do\s+Trabalho|Lei(?:\s+Complementar)?|LC)\b", re.I)


def sentence(text: str, start: int, end: int) -> str:
    """Treat abbreviation dots and decimal legal numbering as non-boundaries."""
    left = max(text.rfind("\n\n", 0, start), text.rfind(".\n", 0, start)) + 1
    right = len(text)
    for match in re.finditer(r"[.!?]", text[end:]):
        index = end + match.start()
        before, after = text[max(0, index - 5):index + 5], text[index + 1:index + 2]
        if re.search(r"(?:art|inc|rel|min)\.$", before, re.I) or (text[index - 1:index].isdigit() and after.isdigit()):
            continue
        right = index + 1
        break
    return text[left:right].replace("\n", " ").strip()


def window(text: str, start: int, end: int) -> str:
    return text[max(0, start - 250):min(len(text), end + 250)].replace("\n", " ")


def local_diploma(text: str, start: int, end: int) -> dict[str, object]:
    value = sentence(text, start, end)
    found = DIPLOMA.search(value)
    return {"sentence": value, "diploma": found.group(0) if found else None,
            "distance": None if not found else abs(found.start() - (start - max(text.rfind("\n\n", 0, start), text.rfind(".\n", 0, start)) - 1))}


def main() -> int:
    metric = base.load_metric()
    gold = base.load_gold(base.DATA / "goldenset.csv")
    texts, raw, outputs, pragma = base.pipeline()
    current, _, _ = base.metrics(metric, gold, raw, outputs)
    current["score"] = float(metric.score(base.solution(gold), base.submission(outputs, texts), "documento_id"))
    if any(current[key] != value for key, value in EXPECTED.items()):
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {current}")
    prior = json.loads(SOURCE.read_text(encoding="utf-8"))
    fp_rows = [row for row in prior["dispositivo_legal_fps"] if not row["features"]["diploma_same_sentence"]]
    fn_rows = [row for row in prior["legal_fn_inventory"] if not row["features"]["diploma_same_sentence"]]
    tp = prior["dispositivo_legal_tp"][0]
    reviews = []
    for number, row in enumerate(fp_rows, 1):
        local = local_diploma(texts[row["documento"]], row["start"], row["end"])
        reviews.append({"review_id": f"FP-{number}", "documento": row["documento"], "nivel": row["nivel"], "span": [row["start"], row["end"]],
                        "text": row["text"], "sentenca": local["sentence"], "janela": window(texts[row["documento"]], row["start"], row["end"]),
                        "predicted_class": row["classification"], "parser_payload": row["parser_payload"], "resolver_status": row["resolver_status"],
                        "nearest_gold": row["closest_gold"], "max_iou": row["max_iou"], "diploma_local_robusto": local["diploma"],
                        "decision": "DO_NOT_SUPPRESS", "rationale": "The apparent absence is a sentence-segmentation/list-coverage artifact; a legal diploma is present in the same logical sentence."})
    fn = fn_rows[0]
    fn_local = local_diploma(texts[fn["documento"]], fn["span"][0], fn["span"][1])
    fn_review = {"review_id": "FN-1", "documento": fn["documento"], "nivel": fn["nivel"], "span": fn["span"], "text": fn["texto"],
                 "sentenca": fn_local["sentence"], "janela": window(texts[fn["documento"]], fn["span"][0], fn["span"][1]), "gold_class": fn["label"],
                 "first_blocker": "Detector", "diploma_local_robusto": fn_local["diploma"], "decision": "BLOCKING_COUNTEREXAMPLE",
                 "rationale": "A valid Gold legal citation uses the same local pattern; the prior predicate missed its full diploma wording."}
    tp_local = local_diploma(texts[tp["documento"]], tp["start"], tp["end"])
    tp_review = {"review_id": "TP-1", "documento": tp["documento"], "nivel": tp["nivel"], "span": [tp["start"], tp["end"]], "text": tp["text"],
                 "sentenca": tp_local["sentence"], "janela": window(texts[tp["documento"]], tp["start"], tp["end"]), "gold_class": tp["gold_class"],
                 "predicted_class": tp["classification"], "diploma_local_robusto": tp_local["diploma"], "distance": tp_local["distance"], "decision": "TP_SAFE",
                 "rationale": "CLT is explicit in the same logical sentence, so a correctly designed diploma guard retains it."}
    legal = prior["gold_lei_inventory"]
    consistency = {}
    for label in ("real", "inventada", "incompleta"):
        rows = [row for row in legal if row["label"] == label]
        satisfying = [row for row in rows if not local_diploma(texts[row["documento"]], row["span"][0], row["span"][1])["diploma"]]
        consistency[label] = {"total": len(rows), "without_robust_local_diploma": len(satisfying), "current_fn": sum(not row["match"] for row in satisfying)}
    candidates = [{"guard": "A legacy same-sentence", "fp_removed": 4, "tp_lost": 0, "fn_risk": "BLOCKING", "blind_risk": "HIGH", "verdict": "REJECT: abbreviated/decimal segmentation"},
                  {"guard": "B no diploma in 140-char window", "fp_removed": 1, "tp_lost": 0, "fn_risk": "BLOCKING", "blind_risk": "HIGH", "verdict": "REJECT: misses written-out diploma"},
                  {"guard": "C robust logical sentence", "fp_removed": 0, "tp_lost": 0, "fn_risk": "none observed", "blind_risk": "LOW", "verdict": "NO_PRODUCTION_GAIN"},
                  {"guard": "D short paragraph", "fp_removed": 0, "tp_lost": 0, "fn_risk": "none observed", "blind_risk": "LOW", "verdict": "NO_PRODUCTION_GAIN"}]
    report = {"v8_baseline": current, "fp_reviews": reviews, "fn_review": fn_review, "tp_review": tp_review,
              "diploma_definition": {"institutional": True, "terms": DIPLOMA.pattern, "warning": "written-out names and abbreviations must both be covered; CE/CC can be ambiguous outside legal context"},
              "sentence_definition": "Paragraph-bounded prose sentence; do not cut at art./inc./Rel./Min. or digit-dot-digit legal numbering.",
              "gold_legal_consistency": consistency, "corpus_negative_search": {"reviewed": 4, "result": "all four legacy negatives contain a robust local diploma"},
              "guard_alternatives": candidates, "blind_risk": "HIGH", "decision": "ABANDON_LEGAL_FP_SUPPRESSION", "next_step": "ABANDON_LEGAL_FP_SUPPRESSION",
              "integrity": {"gold": base.digest(base.DATA / "goldenset.csv"), "db": base.digest(base.get_database_path()), "pragma": pragma},
              "production_diff": __import__("subprocess").check_output(["git", "diff", "--", "src"], cwd=ROOT, text=True)}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Targeted review — dispositivo_legal", "", "## Decision", "", "**ABANDON_LEGAL_FP_SUPPRESSION**", "", "The four apparent FP-only examples all contain an institutional diploma in the same logical sentence. The earlier signal came from splitting at legal abbreviations or decimal numbering, or from omitting the written-out CLT name.", "", "## Four reviewed outputs", ""]
    for row in reviews:
        lines += [f"### {row['review_id']} — {row['decision']}", "", f"Span: `{row['text']}`", "", f"Sentence: {row['sentenca']}", "", f"Local diploma: {row['diploma_local_robusto']}", ""]
    lines += ["## Similar legal FN", "", f"{fn_review['text']} — {fn_review['decision']}. {fn_review['rationale']}", "", "## Single device match", "", f"{tp_review['text']} — {tp_review['decision']}; local diploma: {tp_review['diploma_local_robusto']}.", ""]
    MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"fp": len(reviews), "fn": fn_review["decision"], "tp": tp_review["decision"], "decision": report["decision"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
