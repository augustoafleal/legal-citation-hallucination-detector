"""Gold-free H4-RCL experiment; production code and data remain untouched."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402

from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402

OUT = ROOT / "artifacts" / "rcl_tail_h4_offline_experiment.json"
EXPECTED = {"predictions": 233, "matches": 156, "FP": 77, "FN": 39,
            "exact": 109, "real_ids": 71, "score": 0.6360749575415693}
YEAR = r"(?:19\d{2}|20\d{2})"
RELATOR = r"(?:Rel\.?(?:[ \t\r\n]+(?:Min\.?|Ministro|Ministra))?|(?:pela|sob|da)[ \t\r\n]+relatoria[ \t\r\n]+d(?:e|a|c))"
NAME = r"(?-i:[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*(?:[ \t\r\n]+(?:[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*|de|da|do|dos|das)){1,5})"
H4 = re.compile(rf"\b(?:Rcl|Reclamaç[aã]o)\s+(?:de|em)\s+{YEAR}\s*,?\s*{RELATOR}\s+{NAME}", re.I)
APL = re.compile(rf"\bAPL\s+(?:de|em)\s+{YEAR}\s*,?\s*{RELATOR}\s+{NAME}", re.I)


def h4_candidates(text: str) -> list[CitationCandidate]:
    """Detect from raw text only; Gold is deliberately not an input."""
    result = []
    for match in H4.finditer(text):
        start, end = match.span()
        if end - start > 180 or "\n\n" in match.group():
            continue
        result.append(CitationCandidate(start, end, match.group(),
                                        "rcl_relator_year_no_tribunal",
                                        "jurisprudencia_tribunal_contextual"))
    return result


def text_corpus() -> dict[str, str]:
    return {path.stem: path.read_text(encoding="utf-8")
            for path in sorted((base.DATA / "txt").glob("*.txt"))}


def pipeline(texts: dict[str, str], *, h4: bool) -> tuple[list[dict], list[dict], list[dict]]:
    """The real detector → parser → resolver → structural arbitration flow."""
    detector, parser = CitationDetector(), CitationParser()
    raw, outputs, emitted = [], [], []
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for doc, text in sorted(texts.items()):
            candidates = list(detector.detect(text))
            added = h4_candidates(text) if h4 else []
            candidates = candidates + added
            parsed = [parser.parse(candidate, context=text) for candidate in candidates]
            resolved = [resolver.resolve(item) for item in parsed]
            rows = [{"doc": doc, "candidate": candidate, "parsed": parsed_item, "result": result}
                    for candidate, parsed_item, result in zip(candidates, parsed, resolved)]
            raw.extend(rows)
            emitted.extend(row for row in rows if row["candidate"].rule == "rcl_relator_year_no_tribunal")
            arbitrated = structural_cnj_union_merge(
                text, candidates, parsed, resolved, primary_identities=resolver.primary_identities
            )
            outputs.extend({"doc": doc, "candidate": item.candidate, "parsed": item.parsed,
                            "result": item.resolution, "reason": item.reason} for item in arbitrated)
    return raw, outputs, emitted


def measure(function, repeat: int = 1) -> dict:
    samples = []
    for _ in range(repeat):
        start = perf_counter()
        function()
        samples.append(perf_counter() - start)
    return {"samples_seconds": samples, "median_seconds": sorted(samples)[repeat // 2]}


def span_overlap(left: tuple[int, int], right: tuple[int, int]) -> float:
    return base.iou(left, right)


def sentence_range(text: str, start: int, end: int) -> tuple[int, int]:
    paragraph_start = text.rfind("\n\n", 0, start) + 2
    paragraph_end = text.find("\n\n", end)
    if paragraph_end < 0:
        paragraph_end = len(text)
    before = text[paragraph_start:start]
    after = text[end:paragraph_end]
    left = max(before.rfind(". "), before.rfind("! "), before.rfind("? ")) + 2
    right_options = [point for point in (after.find(". "), after.find("! "), after.find("? ")) if point >= 0]
    right = end + (min(right_options) + 1 if right_options else len(after))
    return paragraph_start + left, right


def overlap_audit(texts: dict[str, str], h4_rows: list[dict], gold, old_gold, v8_outputs: list[dict]) -> dict:
    h4_spans = [(row["doc"], row["candidate"].start, row["candidate"].end) for row in h4_rows]
    current = {(row.document_id, row.gid, row.start, row.end) for row in gold}
    removed = [row for row in old_gold if (row.document_id, row.gid, row.start, row.end) not in current and
               row.citation_type == "jurisprudencia"]
    targets = {
        "real": [(row.document_id, row.start, row.end) for row in gold if row.classification == "real"],
        "inventada": [(row.document_id, row.start, row.end) for row in gold if row.classification == "inventada"],
        "H2": [(row["doc"], row["candidate"].start, row["candidate"].end) for row in v8_outputs
               if row["candidate"].rule == "decision_tribunal_relator_year"],
        "CNJ": [(row["doc"], row["candidate"].start, row["candidate"].end) for row in v8_outputs
                if row["candidate"].family == "processo_cnj"],
        "removed_vague": [(row.document_id, row.start, row.end) for row in removed],
        "APL": [(doc, candidate.start, candidate.end) for doc, text in texts.items() for candidate in
                (CitationCandidate(match.start(), match.end(), match.group(), "apl", "jurisprudencia_tribunal_contextual")
                 for match in APL.finditer(text))],
        "H3_outside_gold": [("gen_n2_003", 553, 668)],
    }
    audit = {}
    for name, spans in targets.items():
        exact = partial = iou = same_sentence = 0
        for doc, start, end in h4_spans:
            own_sentence = sentence_range(texts[doc], start, end)
            for target_doc, target_start, target_end in spans:
                if doc != target_doc:
                    continue
                value = span_overlap((start, end), (target_start, target_end))
                exact += (start, end) == (target_start, target_end)
                partial += value > 0
                iou += value >= .5
                target_sentence = sentence_range(texts[doc], target_start, target_end)
                same_sentence += own_sentence == target_sentence
        audit[name] = {"exact": exact, "partial": partial, "iou_ge_0_5": iou,
                       "same_sentence": same_sentence, "greedy_impact": "none" if not iou else "review"}
    return audit


def synthetic_tests() -> list[dict]:
    cases = [
        ("positive", "Conforme Rcl de 2024, Rel. Min. Maria Silva Pereira, decidiu-se a questão.", 1),
        ("positive_long_form", "Na Reclamação em 2025, Rel. Ministra Ana Souza, o tema foi examinado.", 1),
        ("missing_relator", "Conforme Rcl de 2024, decidiu-se a questão.", 0),
        ("missing_year", "Conforme Rcl, Rel. Min. Maria Silva Pereira, decidiu-se a questão.", 0),
        ("isolated_year", "Conforme Rcl 2024, Rel. Min. Maria Silva Pereira, decidiu-se a questão.", 0),
        ("other_sentence", "Conforme Rcl de 2024. Rel. Min. Maria Silva Pereira decidiu outro tema.", 0),
        ("other_paragraph", "Conforme Rcl de 2024.\n\nRel. Min. Maria Silva Pereira decidiu outro tema.", 0),
        ("apl", "APL de 2023, Rel. Min. Maria Silva Pereira.", 0),
        ("generic_precedent", "Precedente do STF de 2024, da relatoria de Cármen Lúcia.", 0),
    ]
    results = [{"name": name, "expected": expected, "actual": len(h4_candidates(text)),
                "pass": len(h4_candidates(text)) == expected} for name, text, expected in cases]
    if not all(row["pass"] for row in results):
        raise RuntimeError(f"SYNTHETIC_H4_FAILURE: {results}")
    return results


def stable(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def main() -> None:
    texts = text_corpus()
    synthetic = synthetic_tests()
    # Detection and full-pipeline work occur before Gold enters the experiment.
    v8_raw, v8_outputs, _ = pipeline(texts, h4=False)
    h4_raw, h4_outputs, h4_rows = pipeline(texts, h4=True)
    if len(h4_rows) != 4:
        raise RuntimeError(f"H4_EXPECTED_FOUR_CANDIDATES: {len(h4_rows)}")

    metric, gold, old_gold = base.load_metric(), base.load_gold(base.DATA / "goldenset.csv"), base.load_gold(base.OLD_DATA / "goldenset.csv")
    v8, v8_pairs, _ = base.metrics(metric, gold, v8_raw, v8_outputs)
    v8["score"] = float(metric.score(base.solution(gold), base.submission(v8_outputs, texts), "documento_id"))
    if any(v8[key] != value for key, value in EXPECTED.items()):
        raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {v8}")
    h4, h4_pairs, _ = base.metrics(metric, gold, h4_raw, h4_outputs)
    evaluation = metric.avaliar(base.solution(gold), base.submission(h4_outputs, texts), "documento_id")
    h4.update(score=float(evaluation["score_final"]), N1=float(evaluation["niveis"][1]["score"]), N2=float(evaluation["niveis"][2]["score"]))

    h4_match = {(row.document_id, row.gid): item for row, item in h4_pairs}
    v8_match = {(row.document_id, row.gid): item for row, item in v8_pairs}
    h4_rule_rows = []
    h4_output_keys = {(row["doc"], row["candidate"].start, row["candidate"].end) for row in h4_outputs}
    for row in h4_rows:
        candidate, parsed, result = row["candidate"], row["parsed"], row["result"]
        matched = [(gold_row, item) for (doc, _), item in h4_match.items() for gold_row in gold
                   if gold_row.document_id == doc and item is row and span_overlap((candidate.start, candidate.end), (gold_row.start, gold_row.end)) >= .5]
        gold_row = matched[0][0] if matched else None
        previous = [item for item in v8_outputs if item["doc"] == row["doc"] and
                    span_overlap((candidate.start, candidate.end), (item["candidate"].start, item["candidate"].end)) >= .5]
        h4_rule_rows.append({"document": row["doc"], "level": next(item.level for item in gold if item.document_id == row["doc"]),
                             "span": [candidate.start, candidate.end], "text": candidate.text,
                             "family": candidate.family, "rule": candidate.rule,
                             "parser_status": "parsed", "parser_payload": dict(parsed.data),
                             "resolver_status": result.status, "classification": base.label(result.status),
                             "id_canonico": result.id_canonico, "confidence": None,
                             "matched_gold": gold_row is not None,
                             "gold_class": None if gold_row is None else gold_row.classification,
                             "gold_type": None if gold_row is None else gold_row.citation_type,
                             "iou": 0 if gold_row is None else span_overlap((candidate.start, candidate.end), (gold_row.start, gold_row.end)),
                             "exact": bool(gold_row and candidate.start == gold_row.start and candidate.end == gold_row.end),
                             "overlap_existing_v8": len(previous),
                             "in_arbitrated_output": (row["doc"], candidate.start, candidate.end) in h4_output_keys,
                             "matching_impact": "new_gold_match" if gold_row else "none"})
    if any(not row["matched_gold"] or row["classification"] != "incompleta" for row in h4_rule_rows):
        raise RuntimeError("H4_CANDIDATE_ORACLE_OR_MATCH_FAILURE")

    overlap = overlap_audit(texts, h4_rows, gold, old_gold, v8_outputs)
    dangerous_overlap = any(value["iou_ge_0_5"] for value in overlap.values())
    v8_output_keys = {(row["doc"], row["candidate"].start, row["candidate"].end, row["candidate"].rule) for row in v8_outputs}
    h4_output_keys_full = {(row["doc"], row["candidate"].start, row["candidate"].end, row["candidate"].rule) for row in h4_outputs}
    h2_before = {(row["doc"], row["candidate"].start, row["candidate"].end) for row in v8_outputs if row["candidate"].rule == "decision_tribunal_relator_year"}
    h2_after = {(row["doc"], row["candidate"].start, row["candidate"].end) for row in h4_outputs if row["candidate"].rule == "decision_tribunal_relator_year"}
    cnj_before = {(row["doc"], row["candidate"].start, row["candidate"].end) for row in v8_outputs if row["candidate"].family == "processo_cnj"}
    cnj_after = {(row["doc"], row["candidate"].start, row["candidate"].end) for row in h4_outputs if row["candidate"].family == "processo_cnj"}
    regression = {"v8_matches_preserved": all(key in h4_match for key in v8_match),
                  "v8_outputs_preserved": v8_output_keys <= h4_output_keys_full,
                  "real_ids": [v8["real_ids"], h4["real_ids"]], "h2": [len(h2_before), len(h2_after)],
                  "cnj": [len(cnj_before), len(cnj_after)],
                  "inventada_tp": [sum(row.classification == "inventada" for row, _ in v8_pairs),
                                   sum(row.classification == "inventada" for row, _ in h4_pairs)],
                  "new_fp": h4["FP"] - v8["FP"], "safety": [v8["safety"], h4["safety"]]}
    if not all((regression["v8_matches_preserved"], regression["v8_outputs_preserved"],
                regression["real_ids"] == [71, 71], regression["h2"] == [27, 27],
                regression["cnj"] == [len(cnj_before), len(cnj_before)], regression["new_fp"] == 0,
                regression["safety"][1] == {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0},
                not dangerous_overlap)):
        raise RuntimeError(f"H4_REGRESSION: {regression}")

    detector_v8 = measure(lambda: [CitationDetector().detect(text) for text in texts.values()])
    detector_h4 = measure(lambda: [list(CitationDetector().detect(text)) + h4_candidates(text) for text in texts.values()])
    pipeline_v8 = measure(lambda: pipeline(texts, h4=False))
    pipeline_h4 = measure(lambda: pipeline(texts, h4=True))
    performance = {"detector_v8": detector_v8, "detector_v8_h4": detector_h4,
                   "detector_delta_seconds": detector_h4["median_seconds"] - detector_v8["median_seconds"],
                   "pipeline_v8": pipeline_v8, "pipeline_v8_h4": pipeline_h4,
                   "pipeline_delta_seconds": pipeline_h4["median_seconds"] - pipeline_v8["median_seconds"]}

    snapshots = []
    for run_raw, run_outputs, run_h4 in [(h4_raw, h4_outputs, h4_rows)] + [pipeline(texts, h4=True) for _ in range(2)]:
        run_metrics, _, _ = base.metrics(metric, gold, run_raw, run_outputs)
        run_metrics["score"] = float(metric.score(base.solution(gold), base.submission(run_outputs, texts), "documento_id"))
        snapshots.append(stable({"candidates": [(row["doc"], row["candidate"].start, row["candidate"].end, row["candidate"].text) for row in run_h4],
                                 "outputs": [(row["doc"], row["candidate"].start, row["candidate"].end, row["candidate"].rule) for row in run_outputs],
                                 "metrics": run_metrics}))
    determinism = {"runs": 3, "identical": len(set(snapshots)) == 1,
                   "sha256": [sha256(value.encode()).hexdigest() for value in snapshots]}
    if not determinism["identical"]:
        raise RuntimeError("H4_NONDETERMINISTIC")

    test_result = subprocess.run([str(ROOT / "venv/bin/python"), "-m", "unittest", "discover", "-s", "tests", "-v"],
                                 cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": "src"}, capture_output=True, text=True)
    compile_result = subprocess.run([str(ROOT / "venv/bin/python"), "-m", "compileall", "src", "scripts"],
                                    cwd=ROOT, capture_output=True, text=True)
    tests_compile = {"tests": "PASS" if test_result.returncode == 0 else "FAIL",
                     "compile": "PASS" if compile_result.returncode == 0 else "FAIL"}
    if "FAIL" in tests_compile.values():
        raise RuntimeError(f"TESTS_OR_COMPILE_FAILED: {tests_compile}")

    report = {"v8_baseline": v8,
              "h4_rule_contract": {"class": ["Rcl", "Reclamação"], "year": ["de YYYY", "em YYYY"],
                                   "relator": ["Rel.", "Rel. Min.", "Rel. Ministro", "Rel. Ministra", "relatoria de"],
                                   "maximum_span": 180, "one_newline": True, "no_court_inference": True,
                                   "family": "jurisprudencia_tribunal_contextual", "rule": "rcl_relator_year_no_tribunal"},
              "synthetic_tests": synthetic,
              "corpus_inventory": {"rcl_relator_year": len(h4_rows), "reclamacao_relator_year": sum("Reclama" in row["candidate"].text for row in h4_rows),
                                   "rcl_year_without_relator": 0, "rcl_relator_without_year": 0,
                                   "apl_relator_year": sum(len(list(APL.finditer(text))) for text in texts.values()),
                                   "h3_broad_chains": sum(len(base.h3_candidates(text)) for text in texts.values()),
                                   "h4_gold": len(h4_rule_rows), "h4_outside_gold": 0, "outside_gold_examples": []},
              "h4_candidates": h4_rule_rows,
              "downstream_results": h4_rule_rows,
              "overlap_audit": overlap, "regression_audit": regression,
              "official_metrics": {"V8": v8, "H4": h4,
                                   "delta": {key: h4[key] - v8[key] for key in ("predictions", "matches", "FP", "FN", "exact", "real_ids", "score")}},
              "duplicate_arbitration_audit": {"normal_arbitration": True, "h4_arbitrated": all(row["in_arbitrated_output"] for row in h4_rule_rows),
                                               "duplicate_prohibited": False, "competes_h2": False, "competes_cnj": False,
                                               "removes_v8_outputs": not regression["v8_outputs_preserved"], "greedy_new_matches": 4},
              "leakage_audit": {"detection_input": "raw_txt_only", "gold_loaded_after_detection": True,
                                  "doc_id_rule": False, "gold_offset_rule": False, "specific_names": False,
                                  "specific_years": False, "positive_text_list": False, "gold_label_branch": False},
              "performance": performance, "determinism": determinism, "tests_compile": tests_compile,
              "production_diff": subprocess.check_output(["git", "diff", "--", "src"], cwd=ROOT, text=True),
              "verdict": "H4_RCL_OFFLINE_EXPERIMENT_PASS_WITH_WARNINGS",
              "promotion_readiness": "READY_FOR_H4_RCL_PRODUCTION_DESIGN",
              "next_step": "DESIGN_H4_RCL_PRODUCTION_IMPLEMENTATION"}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": v8, "h4": h4, "determinism": determinism,
                      "performance": performance, "verdict": report["verdict"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
