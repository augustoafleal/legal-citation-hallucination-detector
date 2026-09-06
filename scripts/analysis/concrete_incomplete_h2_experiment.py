"""Offline H2 experiment; it never changes the production detector."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import csv
import json
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "material_desafio_jusbrasil_bracis"
OLD = ROOT / "material_desafio_jusbrasil_bracis_old3"
OUT = ROOT / "artifacts" / "concrete_incomplete_h2_experiment.json"
GOLD_HASH = "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
BASELINE = {"predictions": 206, "matches": 129, "FP": 77, "FN": 66, "exact": 82, "correct_real_ids": 71, "N1": 0.5231572080887149, "N2": 0.39974937343358397, "final": 0.44088531831862765}

if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import gold_v2_baseline_recalibration as baseline  # noqa: E402
import gold_v2_jurisprudencia_geral_precision_audit as audit  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402

_COURT = re.compile(r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b", re.I)
_YEAR = re.compile(r"\b(?:de|em)\s+(?:19\d{2}|20\d{2})\b|\b(?:julgado|proferido|profcrido)\s+(?:em\s+)?(?:19\d{2}|20\d{2})\b", re.I)
_DECISION = re.compile(r"\b(?:Agravo\s+em\s+Recurso\s+Especial|Recurso\s+em\s+Habeas\s+Corpus|Reclamação|Rcl|acórdão|julgado|precedente)\b", re.I)
_RELATOR_LABEL = re.compile(r"\b(?:Rel\.?(?:\s*(?:Min\.?|Ministro|Ministra))?|(?:pela|sob|da)\s+relatoria\s+d(?:e|a|c))\s+", re.I)
_RELATOR_NAME = re.compile(r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*(?:[ \t\r\n]+[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]*){1,5}")


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sentence_boundary(value: str) -> bool:
    """Permit only a single clause; ``Rel.`` and ``Min.`` are abbreviations."""
    return "\n\n" in value or bool(re.search(r"(?<!Rel)(?<!Min)[.!?]", value))


def detect_h2(text: str) -> tuple[CitationCandidate, ...]:
    """Bounded structural grammar, independent of Gold and canonical data."""
    found: list[CitationCandidate] = []
    for decision in _DECISION.finditer(text):
        court = _COURT.search(text, decision.end(), min(len(text), decision.end() + 45))
        if court is None or not re.fullmatch(r"\s*(?:do|da)?\s*", text[decision.end():court.start()], re.I):
            continue
        window = text[court.end():min(len(text), court.end() + 72)]
        year = _YEAR.search(window)
        if year is None or sentence_boundary(window[:year.start()]):
            continue
        label = _RELATOR_LABEL.search(window, year.end())
        if label is None or sentence_boundary(window[year.end():label.start()]):
            continue
        name = _RELATOR_NAME.match(window, label.end())
        if name is None:
            continue
        end = court.end() + name.end()
        found.append(CitationCandidate(decision.start(), end, text[decision.start():end], "decision_tribunal_relator_year", "jurisprudencia_concrete_incomplete"))
    return tuple(sorted({(item.start, item.end, item.text): item for item in found}.values(), key=lambda item: (item.start, item.end)))


def parser_payload(candidate: CitationCandidate, text: str, parser: CitationParser) -> Any:
    """Temporary equivalent of the existing contextual runtime payload."""
    contextual = CitationCandidate(candidate.start, candidate.end, candidate.text, candidate.rule, "jurisprudencia_tribunal_contextual")
    return parser.parse(contextual, context=text)


def run_h2_pipeline(texts: dict[str, str]) -> tuple[list[Any], list[Any], float, str]:
    detector, parser = CitationDetector(), CitationParser()
    raw, outputs = [], []
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        started = time.perf_counter()
        for doc in sorted(texts):
            current = list(detector.detect(texts[doc]))
            synthetic = list(detect_h2(texts[doc]))
            candidates = sorted(current + synthetic, key=lambda item: (item.start, item.end, item.rule))
            parsed = [parser_payload(item, texts[doc], parser) if item.family == "jurisprudencia_concrete_incomplete" else parser.parse(item, context=texts[doc]) for item in candidates]
            resolved = [resolver.resolve(item) for item in parsed]
            source_indexes: dict[int, int] = {}
            for candidate, parsed_item, result in zip(candidates, parsed, resolved):
                source_indexes[id(candidate)] = len(raw)
                raw.append(post.RawPrediction(len(raw), doc, candidate, parsed_item, result))
            for merged in structural_cnj_union_merge(texts[doc], candidates, parsed, resolved, primary_identities=resolver.primary_identities):
                sources = tuple(sorted(source_indexes[id(source)] for source in merged.source_candidates))
                outputs.append(post.Output(len(outputs), doc, merged.candidate, merged.parsed, merged.resolution, merged.reason, sources))
        pragma = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return raw, outputs, time.perf_counter() - started, pragma


def current_pipeline(texts: dict[str, str]) -> tuple[list[Any], list[Any], float, str]:
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs, elapsed = post.run_current_pipeline(texts, resolver)
        return raw, outputs, elapsed, connection.execute("PRAGMA integrity_check").fetchone()[0]


def output_fingerprint(item: Any, raw: Sequence[Any]) -> dict[str, Any]:
    return {"doc": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text, "rule": item.candidate.rule, "family": item.candidate.family, "parsed": post.parsed_dict(item.parsed), "resolver": post.result_dict(item.result), "reason": item.reason, "sources": sorted(raw[index].candidate.rule for index in item.source_indexes)}


def class_label(item: Any) -> str:
    return audit.classification(item)


def read_gold(path: Path) -> list[Any]:
    result = []
    with path.open(encoding="utf-8", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            result.append(post.deep.GoldRow(index, row["citacao_id"], f"N{row['nivel']}", row["documento_id"], int(row["inicio"]), int(row["fim"]), row["trecho"].replace("\\n", "\n"), row["tipo"], row["classificacao"], int(row["id_canonico"]) if row["id_canonico"] else None))
    return result


def main() -> int:
    if digest(DATA / "goldenset.csv") != GOLD_HASH or digest(get_database_path()) != DB_HASH:
        raise RuntimeError("GOLD_OR_DB_INTEGRITY_MISMATCH")
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    gold_df = official.load_gold_df()
    documents = list(gold_df.documento_id.drop_duplicates())
    base_raw, base_outputs, base_elapsed, base_pragma = current_pipeline(texts)
    base_metrics = baseline.metrics(gold_df, documents, gold, base_raw, base_outputs)
    if any(base_metrics[key] != value for key, value in BASELINE.items()):
        raise RuntimeError(f"BASELINE_NOT_REPRODUCED: {base_metrics}")
    h2_raw, h2_outputs, h2_elapsed, h2_pragma = run_h2_pipeline(texts)
    h2_metrics = baseline.metrics(gold_df, documents, gold, h2_raw, h2_outputs)
    h2_candidates = [item for item in h2_raw if item.candidate.rule == "decision_tribunal_relator_year"]
    h2_final = [item for item in h2_outputs if item.candidate.rule == "decision_tribunal_relator_year"]
    pairs = post.match_gold(gold, h2_outputs)
    pair_by_output = {output: gold[index] for index, output in pairs.items()}
    positives = [{"documento": item.document_id, "span": [item.candidate.start, item.candidate.end], "texto": item.candidate.text, "IoU": post.iou(pair_by_output[item.index].start, pair_by_output[item.index].end, item.candidate.start, item.candidate.end) if item.index in pair_by_output else 0.0, "gold": None if item.index not in pair_by_output else pair_by_output[item.index].gid, "resolver": post.result_dict(item.result), "classificacao": class_label(item)} for item in h2_final]
    incomplete = [item for item in gold if item.classification == "incompleta" and item.citation_type == "jurisprudencia"]
    strong = [item for item in incomplete if any(item.document_id == row["documento"] and item.start == row["span"][0] and item.end == row["span"][1] for row in positives)]
    covered_keys = {(item.document_id, item.start, item.end) for item in strong}
    tail = [{"gold": item.gid, "doc": item.document_id, "level": item.level, "text": item.text, "status": "deferred" if item.text != "reiterados\nprecedentes do Superior Tribunal de Justiça" else "rejected"} for item in incomplete if (item.document_id, item.start, item.end) not in covered_keys]
    old_gold = read_gold(OLD / "goldenset.csv")
    current_keys = {(item.document_id, item.gid) for item in gold}
    vague = [item for item in old_gold if item.citation_type == "jurisprudencia" and item.classification == "incompleta" and (item.document_id, item.gid) not in current_keys]
    overlap = lambda values: [{"gold": item.gid, "doc": item.document_id, "text": item.text, "h2": [(candidate.candidate.start, candidate.candidate.end) for candidate in h2_raw if candidate.document_id == item.document_id and candidate.candidate.rule == "decision_tribunal_relator_year" and post.iou(item.start, item.end, candidate.candidate.start, candidate.candidate.end) >= .5]} for item in values]
    vague_audit, real_audit, invented_audit = overlap(vague), overlap([item for item in gold if item.classification == "real"]), overlap([item for item in gold if item.classification == "inventada"])
    h2_spans = {(item.document_id, item.candidate.start, item.candidate.end) for item in h2_raw}
    corpus_negatives = [{"doc": doc, "span": [item.start, item.end], "text": item.text} for doc, text in texts.items() for item in detect_h2(text) if not any(item.start < gold_item.end and gold_item.start < item.end for gold_item in gold if gold_item.document_id == doc)]
    base_non_h2 = {stable(output_fingerprint(item, base_raw)): output_fingerprint(item, base_raw) for item in base_outputs}
    h2_non_synthetic = {stable(output_fingerprint(item, h2_raw)): output_fingerprint(item, h2_raw) for item in h2_outputs if item.candidate.rule != "decision_tribunal_relator_year"}
    safety_keys = ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")
    times_base, times_h2, detector_base_times, detector_h2_times = [], [], [], []
    snapshots = []
    for _ in range(3):
        repeat_base_raw, repeat_base_out, elapsed_base, _ = current_pipeline(texts)
        repeat_h2_raw, repeat_h2_out, elapsed_h2, _ = run_h2_pipeline(texts)
        times_base.append(elapsed_base)
        times_h2.append(elapsed_h2)
        started = time.perf_counter()
        baseline_detected = sum(len(CitationDetector().detect(text)) for text in texts.values())
        detector_base_times.append(time.perf_counter() - started)
        started = time.perf_counter()
        h2_detected = sum(len(CitationDetector().detect(text)) + len(detect_h2(text)) for text in texts.values())
        detector_h2_times.append(time.perf_counter() - started)
        snapshots.append(stable({"h2": [output_fingerprint(item, repeat_h2_raw) for item in repeat_h2_out], "metrics": baseline.metrics(gold_df, documents, gold, repeat_h2_raw, repeat_h2_out)}))
    gates = {
        "baseline reproduced": all(base_metrics[key] == value for key, value in BASELINE.items()),
        "strong group covered": len(strong) == 27,
        "all H2 spans exact": all(item["IoU"] == 1.0 for item in positives),
        "vague negatives rejected": not any(row["h2"] for row in vague_audit),
        "real overlaps absent": not any(row["h2"] for row in real_audit),
        "inventada overlaps absent": not any(row["h2"] for row in invented_audit),
        "non-gold negatives absent": not corpus_negatives,
        "survivors preserved": base_non_h2 == h2_non_synthetic,
        "resolver stays insufficient": all(item["resolver"]["status"] == "insufficient" for item in positives),
        "safety preserved": all(h2_metrics["safety"][key] == 0 for key in safety_keys),
        "deterministic": len(set(snapshots)) == 1,
        "production unchanged": not __import__("subprocess").check_output(["git", "diff", "HEAD", "--", "src"], cwd=ROOT),
    }
    report = {
        "environment": {"gold_sha256": digest(DATA / "goldenset.csv"), "database_sha256": digest(get_database_path()), "pragma": h2_pragma},
        "baseline": {key: base_metrics[key] for key in (*BASELINE, "safety")},
        "grammar_inventory": {"decision": _DECISION.pattern, "tribunal": _COURT.pattern, "year": _YEAR.pattern, "relator_label": _RELATOR_LABEL.pattern, "relator_name": _RELATOR_NAME.pattern},
        "rule_contract": "bounded decision/class + explicit tribunal + contextual year + relator phrase; one clause, no paragraph traversal",
        "candidate_contract": {"family": "jurisprudencia_concrete_incomplete", "rule": "decision_tribunal_relator_year", "parser_payload_family": "jurisprudencia_tribunal_contextual"},
        "positive_detection": positives, "coverage": {"jurisprudencia_incompleta": len(incomplete), "detected": len(strong), "strong_27": len(strong), "tail": tail},
        "negative_detection": {"removed_vague": vague_audit, "real": real_audit, "inventada": invented_audit, "corpus": corpus_negatives},
        "metrics": {"baseline": {key: base_metrics[key] for key in (*BASELINE, "safety")}, "h2": {key: h2_metrics[key] for key in (*BASELINE, "safety")}, "delta": {key: h2_metrics[key] - base_metrics[key] for key in ("predictions", "matches", "FP", "FN", "exact", "correct_real_ids", "N1", "N2", "final")}},
        "preservation": {"non_synthetic_outputs_identical": base_non_h2 == h2_non_synthetic, "base_outputs": len(base_outputs), "h2_outputs": len(h2_outputs)},
        "performance": {"baseline_pipeline_median_seconds": statistics.median(times_base), "h2_pipeline_median_seconds": statistics.median(times_h2), "pipeline_delta_seconds": statistics.median(times_h2) - statistics.median(times_base), "baseline_detector_median_seconds": statistics.median(detector_base_times), "h2_detector_median_seconds": statistics.median(detector_h2_times), "detector_delta_seconds": statistics.median(detector_h2_times) - statistics.median(detector_base_times), "baseline_detected": baseline_detected, "h2_detected": h2_detected},
        "determinism": {"runs": 3, "identical": len(set(snapshots)) == 1}, "gates": gates,
        "verdict": "PROMOTE_TO_V8" if all(gates.values()) else "KEEP_EXPERIMENT", "next_step": "PROMOTE_H2_TO_V8" if all(gates.values()) else "REFINE_H2",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "next_step": report["next_step"], "failed_gates": [key for key, value in gates.items() if not value], "artifact": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
