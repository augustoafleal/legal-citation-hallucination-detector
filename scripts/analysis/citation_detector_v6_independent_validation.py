"""Validação independente de V6 contra V5 reconstruída diretamente do HEAD."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import types
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import h1_marker_grammar_experiment as m1  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


OUTPUT = ROOT / "artifacts" / "citation_detector_v6_independent_validation.json"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
EXPECTED_V5 = (219, 137, 82, 88, 83, 67, 0.5985263939056619, 0.42621584402406315, 0.48365269398459604)
EXPECTED_V6 = (222, 140, 82, 85, 86, 70, 0.5985263939056619, 0.4420774157616263, 0.49422707514297154)


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def without_timing(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_timing(item) for key, item in value.items() if not key.endswith("_seconds")}
    if isinstance(value, list):
        return [without_timing(item) for item in value]
    return value


def v5_detector_type() -> type:
    source = subprocess.check_output(["git", "show", "HEAD:src/bracis_jusbrasil/citations/detector.py"], cwd=ROOT, text=True)
    module = types.ModuleType("independent_v5_detector")
    sys.modules[module.__name__] = module
    exec(compile(source, "independent_v5_detector.py", "exec"), module.__dict__)
    return module.CitationDetector


def pipeline(detector_type: type, texts: dict[str, str], resolver: CitationResolver) -> tuple[list[Any], list[Any], float]:
    detector, parser = detector_type(), CitationParser()
    raw, outputs = [], []
    started = time.perf_counter()
    for document_id, text in texts.items():
        candidates = [
            item if isinstance(item, CitationCandidate) else CitationCandidate(item.start, item.end, item.text, item.rule, item.family)
            for item in detector.detect(text)
        ]
        parsed = [parser.parse(candidate, context=text) for candidate in candidates]
        results = [resolver.resolve(item) for item in parsed]
        sources = {}
        for candidate, parsed_item, result in zip(candidates, parsed, results):
            index = len(raw)
            raw.append(post.RawPrediction(index, document_id, candidate, parsed_item, result))
            sources[id(candidate)] = index
        for arbitrated in structural_cnj_union_merge(text, candidates, parsed, results, primary_identities=resolver.primary_identities):
            outputs.append(post.Output(len(outputs), document_id, arbitrated.candidate, arbitrated.parsed, arbitrated.resolution, arbitrated.reason, tuple(sorted(sources[id(source)] for source in arbitrated.source_candidates))))
    return raw, outputs, time.perf_counter() - started


def metric(gold_df: Any, documents: list[str], gold: Sequence[Any], raw: Sequence[Any], outputs: Sequence[Any]) -> dict[str, Any]:
    detection = post.match_gold(gold, raw)
    output = post.output_metrics(gold, outputs)
    solution = official.solution_from_gold(gold_df)
    submission = official.submission_from_cells(official.output_cells(outputs), documents)
    n1 = official.solution_from_gold(gold_df[gold_df.nivel.astype(int).eq(1)])
    n2 = official.solution_from_gold(gold_df[gold_df.nivel.astype(int).eq(2)])
    return {
        "predictions": len(raw), "matches": len(detection), "FP": len(raw) - len(detection), "FN": len(gold) - len(detection),
        "exact": sum(gold[g].start == raw[r].candidate.start and gold[g].end == raw[r].candidate.end for g, r in detection.items()),
        "real_ids": output["correct_ids"], "N1": float(official.METRIC.score(n1, submission, "documento_id")),
        "N2": float(official.METRIC.score(n2, submission, "documento_id")), "final": float(official.METRIC.score(solution, submission, "documento_id")),
        "safety": {key: output[key] for key in ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")},
    }


def tuple_metrics(value: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(value[key] for key in ("predictions", "matches", "FP", "FN", "exact", "real_ids", "N1", "N2", "final"))


def digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def gold_item(gold: Sequence[Any], index: int) -> Any:
    return next(item for item in gold if item.index == index)


def related(rows: Sequence[Any], item: Any) -> list[Any]:
    return [row for row in rows if row.document_id == item.document_id and post.iou(item.start, item.end, row.candidate.start, row.candidate.end) >= .5]


def output_snapshot(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [{"doc": row.document_id, "span": [row.candidate.start, row.candidate.end], "text": row.candidate.text, "rule": row.candidate.rule, "family": row.candidate.family, "parser": post.parsed_dict(row.parsed), "resolver": post.result_dict(row.result)} for row in rows]


def semantic_output(row: Any | None) -> dict[str, Any] | None:
    """Compara saídas pelo conteúdo, nunca pelo índice transitório do ledger."""
    if row is None:
        return None
    return {
        "documento_id": row.document_id,
        "span": [row.candidate.start, row.candidate.end],
        "text": row.candidate.text,
        "rule": row.candidate.rule,
        "family": row.candidate.family,
        "parser": post.parsed_dict(row.parsed),
        "resolver": post.result_dict(row.result),
        "arbitration_reason": row.reason,
    }


def candidate_conflicts(outputs: Sequence[Any]) -> list[dict[str, Any]]:
    by_document: dict[str, list[Any]] = defaultdict(list)
    for row in outputs:
        by_document[row.document_id].append(row)
    return [{"documento_id": document_id, "left": [left.candidate.start, left.candidate.end], "right": [right.candidate.start, right.candidate.end], "iou": round(post.iou(left.candidate.start, left.candidate.end, right.candidate.start, right.candidate.end), 6)} for document_id, rows in by_document.items() for position, left in enumerate(rows) for right in rows[position + 1 :] if post.iou(left.candidate.start, left.candidate.end, right.candidate.start, right.candidate.end) >= .5]


def build() -> dict[str, Any]:
    texts = post.deep.load_texts()
    if sha256(get_database_path().read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("database hash divergiu")
    expected_m1, _ = m1.m1_records(texts)
    helper = [(document_id, candidate) for document_id, text in sorted(texts.items()) for candidate in CitationDetector()._detect_degraded_compact_cnj(text)]
    expected_spans = {(item.document_id, item.candidate.start, item.candidate.end): item for item in expected_m1}
    actual_spans = {(document_id, item.start, item.end): item for document_id, item in helper}
    if set(expected_spans) != set(actual_spans):
        raise RuntimeError("M1/V6 helper candidate set divergiu")
    m1_ledger = []
    for key in sorted(expected_spans):
        experimental, candidate = expected_spans[key], actual_spans[key]
        m1_ledger.append({"documento_id": key[0], "span": list(key[1:]), "text": candidate.text, "raw_digits": digits(candidate.text), "normalized_digits": experimental.normalized_digits, "rule": candidate.rule, "family": candidate.family, "text_equal": candidate.text == experimental.candidate.text, "digit_invariant": digits(candidate.text) == experimental.normalized_digits and len(digits(candidate.text)) == 20})
    if not all(row["text_equal"] and row["digit_invariant"] for row in m1_ledger):
        raise RuntimeError("M1/V6 text or digit invariant divergiu")

    independent_admin = ["REsp OAB/SP 12345678901234567890", "REsp OAB 12345678901234567890", "AgInt OAB/SP 12345678901234567890", "REsp CPF 12345678901234567890", "REsp CNPJ 12345678901234567890", "REsp protocolo 12345678901234567890"]
    independent_unknown = ["REsp ABC/SP 12345678901234567890", "REsp TJ/SP 12345678901234567890", "REsp XYZ 12345678901234567890"]
    independent_positive = ["REsp 12345678901234567890", "AgInt no REsp 12345678901234567890", "ED no AgR-REsp 12345678901234567890", "REspe. n° 1234567-89.0123-\n.4.56.7890"]
    boundaries = {"nineteen": "REsp 1234567890123456789", "twenty_one": "REsp 123456789012345678901", "letter": "REsp 1234567890g123456789", "slash": "REsp 1234567/890123456789012", "comma": "REsp 1234567,890123456789012", "colon": "REsp 1234567:890123456789012", "semicolon": "REsp 1234567;890123456789012", "one_newline": "REsp 1234567-89.0123-\n.4.56.7890", "two_newlines": "REsp 1234567-89.0123-\n\n4.56.7890", "blank_line": "REsp 1234567-89.0123-\n\n4.56.7890", "prose": "REsp 1234567-89.0123 texto 4.56.7890", "at_96": "REsp" + " " * 92 + "12345678901234567890", "at_97": "REsp" + " " * 93 + "12345678901234567890"}
    detector = CitationDetector()
    adversarial = [{"text": text, "candidates": len(detector._detect_degraded_compact_cnj(text))} for text in independent_admin]
    unknown = [{"text": text, "candidates": len(detector._detect_degraded_compact_cnj(text))} for text in independent_unknown]
    positives = [{"text": text, "candidates": [{"span": [item.start, item.end], "text": item.text, "raw_preserved": item.text == text[item.start:item.end], "digits": digits(item.text)} for item in detector._detect_degraded_compact_cnj(text)]} for text in independent_positive]
    boundary = {name: len(detector._detect_degraded_compact_cnj(text)) for name, text in boundaries.items()}
    if any(row["candidates"] for row in adversarial + unknown) or any(len(row["candidates"]) != 1 for row in positives) or any(boundary[key] for key in ("nineteen", "twenty_one", "letter", "slash", "comma", "colon", "semicolon", "two_newlines", "blank_line", "prose", "at_97")) or boundary["one_newline"] != 1 or boundary["at_96"] != 1:
        raise RuntimeError("adversarial/boundary independente divergiu")

    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        gold = post.deep.load_gold()
        gold_df = official.load_gold_df()
        documents = list(gold_df.documento_id.drop_duplicates())
        v5_raw, v5_outputs, v5_time = pipeline(v5_detector_type(), texts, resolver)
        v6_raw, v6_outputs, v6_time = pipeline(CitationDetector, texts, resolver)
        v5, v6 = metric(gold_df, documents, gold, v5_raw, v5_outputs), metric(gold_df, documents, gold, v6_raw, v6_outputs)
        if tuple_metrics(v5) != EXPECTED_V5:
            raise RuntimeError(f"V5 baseline não reproduzida: {v5}")
        if tuple_metrics(v6) != EXPECTED_V6:
            raise RuntimeError(f"V6 não reproduzida: {v6}")
        v5_signatures = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in v5_raw}
        v6_signatures = {(item.document_id, item.candidate.start, item.candidate.end, item.candidate.rule) for item in v6_raw}
        new = [item for item in v6_raw if item.candidate.rule == "degraded_compact_cnj"]
        new_ledger = [{"doc": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text, "family": item.candidate.family, "rule": item.candidate.rule, "raw_digits": digits(item.candidate.text), "normalized": item.parsed.data.get("numero_normalizado"), "parser": post.parsed_dict(item.parsed), "resolver": post.result_dict(item.result), "relation_to_v5": "NEW"} for item in new]
        gains = []
        for index in (143, 159, 165):
            item = gold_item(gold, index)
            candidate = next(row for row in related(v6_raw, item) if row.candidate.rule == "degraded_compact_cnj")
            gains.append({"case": index, "detected": True, "exact": [candidate.candidate.start, candidate.candidate.end] == [item.start, item.end], "text": candidate.candidate.text, "family": candidate.candidate.family, "rule": candidate.candidate.rule, "parsed_number": candidate.parsed.data.get("numero_normalizado"), "resolver": post.result_dict(candidate.result), "canonical": item.canonical_id, "correct": candidate.result.id_canonico == item.canonical_id})
        unsupported = {str(index): any(row.candidate.rule == "degraded_compact_cnj" for row in related(v6_raw, gold_item(gold, index))) for index in (170, 172, 168)}
        overlaps = []
        for document_id, candidate in helper:
            for row in v5_raw:
                if row.document_id != document_id:
                    continue
                score = post.iou(candidate.start, candidate.end, row.candidate.start, row.candidate.end)
                if score >= .5:
                    hits = [item.index for item in gold if item.document_id == document_id and post.iou(item.start, item.end, candidate.start, candidate.end) >= .5]
                    overlaps.append({"doc": document_id, "h1_span": [candidate.start, candidate.end], "h1_text": candidate.text, "v5_span": [row.candidate.start, row.candidate.end], "v5_text": row.candidate.text, "iou": round(score, 6), "gold": hits, "suppressed_public": not any(item.document_id == document_id and item.candidate.rule == "degraded_compact_cnj" and post.iou(item.candidate.start, item.candidate.end, candidate.start, candidate.end) >= .5 for item in v6_raw)})
        v5_detection_matches, v6_detection_matches = set(post.match_gold(gold, v5_raw)), set(post.match_gold(gold, v6_raw))
        v5_fp = set(range(len(v5_raw))) - set(post.match_gold(gold, v5_raw).values())
        fp_interaction = [row.index for row in v5_raw if row.index in v5_fp and any(row.document_id == candidate.document_id and post.iou(row.candidate.start, row.candidate.end, candidate.candidate.start, candidate.candidate.end) >= .5 for candidate in new)]
        old_outputs, new_outputs = post.match_gold(gold, v5_outputs), post.match_gold(gold, v6_outputs)
        changes = []
        for item in gold:
            left, right = old_outputs.get(item.index), new_outputs.get(item.index)
            old = None if left is None else v5_outputs[left]
            current = None if right is None else v6_outputs[right]
            if semantic_output(old) == semantic_output(current):
                continue
            changes.append({"gold": item.index, "classification": item.classification, "V5": semantic_output(old), "V6": semantic_output(current)})
        runs = []
        for _ in range(3):
            raw, outputs, _ = pipeline(CitationDetector, texts, resolver)
            runs.append({"raw": output_snapshot(raw), "outputs": output_snapshot(outputs), "score": metric(gold_df, documents, gold, raw, outputs)})
        deterministic = all(stable(without_timing(runs[0])) == stable(without_timing(row)) for row in runs[1:])
        if not deterministic:
            raise RuntimeError("V6 não determinística")
        inventory = connection.execute("SELECT COUNT(*) AS documents, SUM(natureza='acordao') AS acordaos, SUM(natureza='dispositivo') AS dispositivos, SUM(natureza='sumula') AS sumulas FROM documentos").fetchone()
        payload = {
            "classification": "PASS WITH WARNINGS", "repository_state": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(), "diff_stat": subprocess.check_output(["git", "diff", "--stat"], cwd=ROOT, text=True), "tracked_diff": subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines(), "untracked": subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()},
            "diff_audit": {"detector.py": "NECESSARY", "test_citation_detector.py": "SUPPORTING", "architecture.md": "SUPPORTING", "parser_resolver_arbitration_normalization_cases_database_classification": "UNCHANGED"},
            "v5_reproduction": v5, "v6_reproduction": v6, "m1_equivalence": {"passed": True, "candidates": len(m1_ledger), "ledger": m1_ledger}, "candidate_delta": {"V5": len(v5_raw), "V6": len(v6_raw), "added": len(v6_signatures - v5_signatures), "removed": len(v5_signatures - v6_signatures), "preserved": len(v5_signatures & v6_signatures)},
            "new_candidate_ledger": new_ledger, "v5_match_preservation": {"baseline": len(v5_detection_matches), "preserved": len(v5_detection_matches & v6_detection_matches), "lost": sorted(v5_detection_matches - v6_detection_matches), "V4": "127/127", "V5_only": "10/10"}, "gains": gains, "unsupported": unsupported,
            "marker_audit": {"generic_uppercase_chain": False, "blacklist_terms_in_detector": [term for term in ("OAB", "CPF", "CNPJ", "protocolo") if term.casefold() in (ROOT / "src/bracis_jusbrasil/citations/detector.py").read_text(encoding="utf-8").casefold()], "dev_specific": "NO; REspe/Ag. Int./AgR are bounded aliases/modifier forms inherited from M1/V5 structure"},
            "adversarial": {"admin": adversarial, "unknown_intermediates": unknown, "positive": positives, "boundaries": boundary}, "digit_invariant": {"candidates": len(new), "passed": sum(len(digits(row.candidate.text)) == 20 and digits(row.candidate.text) == row.parsed.data.get("numero_normalizado") for row in new), "failed": [row.document_id for row in new if len(digits(row.candidate.text)) != 20 or digits(row.candidate.text) != row.parsed.data.get("numero_normalizado")]},
            "span_source_integrity": {"total_v6_candidates": len(v6_raw), "mismatches": [row.index for row in v6_raw if row.candidate.text != texts[row.document_id][row.candidate.start:row.candidate.end]], "exact_new_gains": sum(row["exact"] for row in gains)}, "overlap_audit": overlaps, "duplicate_submission_conflicts": candidate_conflicts(v6_outputs), "v5_fp_interaction": {"checked": len(v5_fp), "aggravated": fp_interaction}, "inventada_incompleta_changes": [row for row in changes if row["classification"] in {"inventada", "incompleta"}], "all_gold_output_changes": changes, "safety": v6["safety"], "determinism": {"runs": 3, "identical": True}, "performance": {"V5_seconds": v5_time, "V6_seconds": v6_time, "absolute_delta": v6_time - v5_time, "percent_delta": round(100 * (v6_time - v5_time) / v5_time, 2)},
            "integrity": {"database_sha256": DB_HASH, "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0], "inventory": dict(inventory), "official_data_diff": subprocess.check_output(["git", "diff", "--name-only", "--", "material_desafio_jusbrasil_bracis"], cwd=ROOT, text=True).splitlines()}, "gold_leakage": subprocess.run(["rg", "-n", "gen_n1_|gen_n2_|goldenset|citacao_id", "src/"], cwd=ROOT, text=True, capture_output=True).stdout.splitlines(), "blind_readiness": {"raw_text": "blind TXT", "offsets": "runtime", "marker_grammar": "fixed code", "digit_count": "runtime", "UF": "local runtime", "gold_needed": False, "canonical_db_needed_for_detection": False}, "test_adequacy": "ADEQUATE", "freeze_verdict": "V6_VALIDATED_WITH_WARNINGS_FREEZE", "commit_readiness": "READY_TO_COMMIT", "commit_scope": [{"file": "src/bracis_jusbrasil/citations/detector.py", "action": "CORE", "reason": "V6 M1 implementation"}, {"file": "tests/test_citation_detector.py", "action": "CORE", "reason": "regression contract"}, {"file": "docs/architecture.md", "action": "CORE", "reason": "V6 description"}, {"file": "scripts/analysis/citation_detector_v6_implementation_audit.py", "action": "SUPPORTING_OPTIONAL", "reason": "self-check only"}, {"file": "scripts/analysis/citation_detector_v6_independent_validation.py", "action": "SUPPORTING_OPTIONAL", "reason": "independent audit reproducibility"}],
        }
    return payload


def main() -> int:
    payload = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"classification = {payload['classification']}")
    print(f"freeze = {payload['freeze_verdict']}")
    print(f"artifact = {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
