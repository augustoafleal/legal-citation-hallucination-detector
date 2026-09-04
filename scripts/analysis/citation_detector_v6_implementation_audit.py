"""Self-check reproduzível da implementação V6 contra V5 e a M1 congelada."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
import types
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import h1_degraded_compact_cnj_experiment as h1  # noqa: E402
import h1_marker_grammar_experiment as m1  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import (  # noqa: E402
    CitationCandidate,
    CitationDetector,
    CitationParser,
    CitationResolver,
    structural_cnj_union_merge,
)
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


OUTPUT_PATH = ROOT / "artifacts" / "citation_detector_v6_implementation.json"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
EXPECTED = {
    "V5": {"predictions": 219, "matches": 137, "FP": 82, "FN": 88, "exact": 83, "real_ids": 67, "N1": 0.5985263939056619, "N2": 0.42621584402406315, "final": 0.48365269398459604},
    "V6": {"predictions": 222, "matches": 140, "FP": 82, "FN": 85, "exact": 86, "real_ids": 70, "N1": 0.5985263939056619, "N2": 0.4420774157616263, "final": 0.49422707514297154},
}
EXPECTED_GAINS = (143, 159, 165)
EXPECTED_OVERLAPS = (160, 221)


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def deterministic_view(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: deterministic_view(item) for key, item in value.items() if not key.endswith("_seconds")}
    if isinstance(value, list):
        return [deterministic_view(item) for item in value]
    return value


def snapshot(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "documento_id": item.document_id,
            "span": [item.candidate.start, item.candidate.end],
            "text": item.candidate.text,
            "rule": item.candidate.rule,
            "family": item.candidate.family,
            "parsed": post.parsed_dict(item.parsed),
            "resolution": post.result_dict(item.result),
        }
        for item in items
    ]


def baseline_detector() -> type:
    source = subprocess.check_output(
        ["git", "show", "HEAD:src/bracis_jusbrasil/citations/detector.py"], cwd=ROOT, text=True
    )
    module = types.ModuleType("citation_detector_v5_head")
    sys.modules[module.__name__] = module
    exec(compile(source, "citation_detector_v5_head.py", "exec"), module.__dict__)
    return module.CitationDetector


def run_with(detector_type: type, texts: dict[str, str], resolver: CitationResolver):
    if detector_type is not CitationDetector:
        detector, parser = detector_type(), CitationParser()
        raw, outputs = [], []
        started = time.perf_counter()
        for document_id, text in texts.items():
            candidates = [
                CitationCandidate(item.start, item.end, item.text, item.rule, item.family)
                for item in detector.detect(text)
            ]
            parsed = [parser.parse(candidate, context=text) for candidate in candidates]
            results = [resolver.resolve(item) for item in parsed]
            by_identity = {}
            for candidate, item, result in zip(candidates, parsed, results):
                index = len(raw)
                raw.append(post.RawPrediction(index, document_id, candidate, item, result))
                by_identity[id(candidate)] = index
            for item in structural_cnj_union_merge(text, candidates, parsed, results, primary_identities=resolver.primary_identities):
                outputs.append(post.Output(len(outputs), document_id, item.candidate, item.parsed, item.resolution, item.reason, tuple(sorted(by_identity[id(source)] for source in item.source_candidates))))
        return raw, outputs, time.perf_counter() - started
    original = post.CitationDetector
    try:
        post.CitationDetector = detector_type
        return post.run_current_pipeline(texts, resolver)
    finally:
        post.CitationDetector = original


def score(gold_df: Any, documents: list[str], gold: Sequence[Any], raw: list[Any], outputs: list[Any]) -> dict[str, Any]:
    result = h1.score_variant(gold_df, documents, gold, raw, outputs)
    return {
        "predictions": result["predictions"], "matches": result["official_matches"], "FP": result["FP"], "FN": result["FN"],
        "exact": result["exact"], "real_ids": result["real_ids_correct"], "N1": result["N1"], "N2": result["N2"], "final": result["official_final"], "safety": result["safety"],
    }


def gold_record(gold: Sequence[Any], index: int) -> Any:
    return next(item for item in gold if item.index == index)


def related(raw: Sequence[Any], gold: Sequence[Any], index: int) -> list[Any]:
    item = gold_record(gold, index)
    return [candidate for candidate in raw if candidate.document_id == item.document_id and post.iou(item.start, item.end, candidate.candidate.start, candidate.candidate.end) >= .5]


def build() -> dict[str, Any]:
    if sha256(get_database_path().read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("DB hash divergente")
    texts = post.deep.load_texts()
    expected_records, _ = m1.m1_records(texts)
    detector = CitationDetector()
    helper_candidates = [
        (document_id, candidate)
        for document_id, text in sorted(texts.items())
        for candidate in detector._detect_degraded_compact_cnj(text)
    ]
    expected_by_span = {(record.document_id, record.candidate.start, record.candidate.end): record for record in expected_records}
    helper_by_span = {(document_id, candidate.start, candidate.end): candidate for document_id, candidate in helper_candidates}
    if set(expected_by_span) != set(helper_by_span):
        raise RuntimeError("V6 helper diverge do ledger M1")
    equivalence = []
    for key in sorted(expected_by_span):
        record, candidate = expected_by_span[key], helper_by_span[key]
        raw_digits = "".join(char for char in candidate.text if char.isdigit())
        equivalence.append({"documento_id": key[0], "span": list(key[1:]), "text": candidate.text, "rule": candidate.rule, "family": candidate.family, "raw_digits": raw_digits, "normalized_digits": record.normalized_digits, "digit_invariant": raw_digits == record.normalized_digits and len(raw_digits) == 20, "matches_M1_source_text": candidate.text == record.candidate.text})
    if not all(row["digit_invariant"] and row["matches_M1_source_text"] for row in equivalence):
        raise RuntimeError("invariante/texto M1 divergente")

    synthetic_positive = [
        "REsp 12345678901234567890", "AgInt no REsp 12345678901234567890", "ED no AgR-REsp 12345678901234567890",
        "Ag. Int. No 12345678901234567890", "REspe. n° 1234567-89.0123-\n.4.56.7890/BA",
    ]
    synthetic_negative = [
        "REsp OAB/SP 12345678901234567890", "REsp OAB 12345678901234567890", "AgInt OAB/SP 12345678901234567890",
        "REsp CPF 12345678901234567890", "REsp CNPJ 12345678901234567890", "REsp protocolo 12345678901234567890",
        "REsp 1234567890123456789", "REsp 123456789012345678901", "REsp 1234567890g123456789",
        "REsp 1234567-89.0123-\n\n4.56.7890", "REsp 1234567-89.0123 texto 4.56.7890",
        "REsp " + ("narrativa " * 12) + "12345678901234567890", "REsp! 12345678901234567890",
        "APL 12345678901234567890", "RSE 12345678901234567890",
    ]
    positives = [{"text": text, "M1_helper_candidates": len(detector._detect_degraded_compact_cnj(text))} for text in synthetic_positive]
    negatives = [{"text": text, "M1_helper_candidates": len(detector._detect_degraded_compact_cnj(text))} for text in synthetic_negative]
    if any(row["M1_helper_candidates"] != 1 for row in positives) or any(row["M1_helper_candidates"] for row in negatives):
        raise RuntimeError("contrato sintético divergente")

    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        gold = post.deep.load_gold()
        gold_df = h1.official.load_gold_df()
        documents = list(gold_df.documento_id.drop_duplicates())
        v5_raw, v5_outputs, v5_seconds = run_with(baseline_detector(), texts, resolver)
        v6_raw, v6_outputs, v6_seconds = run_with(CitationDetector, texts, resolver)
        v5_score, v6_score = score(gold_df, documents, gold, v5_raw, v5_outputs), score(gold_df, documents, gold, v6_raw, v6_outputs)
        for name, actual in (("V5", v5_score), ("V6", v6_score)):
            expected = EXPECTED[name]
            if any(actual[key] != expected[key] for key in ("predictions", "matches", "FP", "FN", "exact", "real_ids", "N1", "N2", "final")):
                raise RuntimeError(f"{name} métricas divergentes: {actual}")
        v5_signatures = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in v5_raw}
        v6_signatures = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in v6_raw}
        new_raw = [item for item in v6_raw if item.candidate.rule == "degraded_compact_cnj"]
        gains = []
        for index in EXPECTED_GAINS:
            item = gold_record(gold, index)
            candidates = related(v6_raw, gold, index)
            candidate = next(row for row in candidates if row.candidate.rule == "degraded_compact_cnj")
            gains.append({"case": index, "span": [candidate.candidate.start, candidate.candidate.end], "exact": (candidate.candidate.start, candidate.candidate.end) == (item.start, item.end), "rule": candidate.candidate.rule, "family": candidate.candidate.family, "parser": post.parsed_dict(candidate.parsed), "resolver": post.result_dict(candidate.result), "canonical_expected": item.canonical_id, "canonical_correct": candidate.result.id_canonico == item.canonical_id})
        unsupported = {str(index): any(row.candidate.rule == "degraded_compact_cnj" for row in related(v6_raw, gold, index)) for index in (170, 172)}
        overlaps = {}
        for index in EXPECTED_OVERLAPS:
            item = gold_record(gold, index)
            h1_candidate = helper_by_span.get(next(key for key in helper_by_span if key[0] == item.document_id and post.iou(item.start, item.end, key[1], key[2]) >= .5))
            v5_candidate = max(related(v5_raw, gold, index), key=lambda row: post.iou(item.start, item.end, row.candidate.start, row.candidate.end))
            overlaps[str(index)] = {"h1_span": [h1_candidate.start, h1_candidate.end], "v5_span": [v5_candidate.candidate.start, v5_candidate.candidate.end], "iou": round(post.iou(h1_candidate.start, h1_candidate.end, v5_candidate.candidate.start, v5_candidate.candidate.end), 6), "public_h1_suppressed": not any(row.document_id == item.document_id and row.candidate.rule == "degraded_compact_cnj" and post.iou(item.start, item.end, row.candidate.start, row.candidate.end) >= .5 for row in v6_raw), "result_preserved": any(row.result.status == "resolved" and row.result.id_canonico == item.canonical_id for row in related(v6_raw, gold, index))}
        baseline_matches = set(post.match_gold(gold, v5_raw))
        v6_matches = set(post.match_gold(gold, v6_raw))
        baseline_fp_indexes = set(range(len(v5_raw))) - set(post.match_gold(gold, v5_raw).values())
        fp_interaction = [item.index for item in v5_raw if item.index in baseline_fp_indexes and any(item.document_id == new.document_id and post.iou(item.candidate.start, item.candidate.end, new.candidate.start, new.candidate.end) >= .5 for new in new_raw)]
        runs = []
        for _ in range(3):
            raw, outputs, _ = run_with(CitationDetector, texts, resolver)
            runs.append({"raw": snapshot(raw), "outputs": snapshot(outputs), "score": score(gold_df, documents, gold, raw, outputs)})
        deterministic = all(stable(deterministic_view(runs[0])) == stable(deterministic_view(run) ) for run in runs[1:])
        if not deterministic:
            raise RuntimeError("V6 não determinística")
        payload = {
            "status": "V6_IMPLEMENTED_READY_FOR_INDEPENDENT_VALIDATION",
            "environment": {"python": sys.executable, "runner": "PYTHONPATH=src venv/bin/python -m unittest discover -s tests -v", "project_dependencies": "available in venv", "pytest": "not installed; not canonical runner"},
            "m1_equivalence": {"expected_helper_candidates": len(expected_records), "actual_helper_candidates": len(helper_candidates), "all_spans_text_digits_equal": True, "ledger": equivalence, "public_new_candidates": [{"documento_id": row.document_id, "span": [row.candidate.start, row.candidate.end], "text": row.candidate.text, "rule": row.candidate.rule, "family": row.candidate.family, "resolution": post.result_dict(row.result)} for row in new_raw]},
            "synthetic": {"positive": positives, "negative": negatives}, "scores": {"V5": v5_score, "V6": v6_score},
            "candidate_delta": {"V5": len(v5_raw), "V6": len(v6_raw), "added": len(v6_signatures - v5_signatures), "removed": len(v5_signatures - v6_signatures), "preserved_v5_candidates": len(v5_signatures & v6_signatures)},
            "v5_preservation": {"matches": len(baseline_matches), "preserved": len(baseline_matches & v6_matches), "all_137_preserved": baseline_matches <= v6_matches, "V4": "127/127", "V5_only": "10/10"},
            "dev_gains": gains, "unsupported": unsupported, "overlaps": overlaps, "fp_interaction": {"checked": len(baseline_fp_indexes), "aggravated": fp_interaction},
            "determinism": {"runs": 3, "identical": True}, "performance": {"V5_pipeline_seconds": v5_seconds, "V6_pipeline_seconds": v6_seconds, "delta_percent": round(100 * (v6_seconds - v5_seconds) / v5_seconds, 2) if v5_seconds else None},
            "integrity": {"database_sha256": DB_HASH, "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0], "official_data_diff": subprocess.check_output(["git", "diff", "--name-only", "--", "material_desafio_jusbrasil_bracis"], cwd=ROOT, text=True).splitlines()},
            "gold_leakage": subprocess.run(["rg", "-n", "gen_n1_|gen_n2_|goldenset|citacao_id", "src/"], cwd=ROOT, text=True, capture_output=True).stdout.splitlines(),
            "diff_scope": subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines(),
        }
    return payload


def main() -> int:
    payload = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"status = {payload['status']}")
    print(f"V6 score = {payload['scores']['V6']['final']}")
    print(f"artifact = {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
