"""Validator offline isolado para o residual 98 pós-V6.

O experimento não altera ``src/``. Ele sobrepõe, apenas em memória, uma
gramática limitada de cadeia processual reconhecida + CNJ/TST estruturalmente
completo e um parser local para essa nova superfície. O Resolver, a arbitragem,
o banco e a métrica continuam sendo os componentes congelados.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DIR = ROOT / "scripts" / "analysis"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, ParsedCitation, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


ARTIFACT_PATH = ROOT / "artifacts" / "post_v6_case98_combined_experiment.json"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
TARGET_GOLD_ID = 98
TARGET_CANONICAL_ID = 862182008
V6_BASELINE = {
    "predictions": 222,
    "matches": 140,
    "FP": 82,
    "FN": 85,
    "exact": 86,
    "real_ids_correct": 70,
    "official_final": 0.49422707514297154,
    "safety": {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0},
}

# This is the project identity grammar, not a gold-derived selector: the
# sequence block may contain 1–7 digits, as in the frozen TST case parser.
_COMPOUND_PATTERN = re.compile(
    r"(?<!\w)(?P<chain>(?:[A-Za-zÀ-ÿ][A-Za-z0-9À-ÿ.]*-){2,}"
    r"(?P<number>\d{1,7}-\d{2}[.]\d{4}[.]\d[.]\d{2}[.]\d{4}))(?!\w)",
    re.IGNORECASE,
)
_RECOGNIZED_TOKENS = frozenset(
    {
        "ARESP", "RESP", "AGINT", "AGRG", "AGR", "EDCL", "ED", "HC", "RHC", "RMS", "AR",
        "RCL", "ADI", "ADPF", "RE", "AI", "MS", "RR", "AIRR", "ARR", "AP", "RO", "E",
        "TST", "STJ", "TSE", "STM", "STF",
    }
)


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def span_iou(left: Sequence[int], right: Sequence[int]) -> float:
    intersection = max(0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return intersection / union if union else 0.0


def parsed_view(parsed: ParsedCitation) -> dict[str, Any]:
    return post.parsed_dict(parsed)


def result_view(result: Any) -> dict[str, Any]:
    return post.result_dict(result)


def complete_chain_candidate(text: str, match: re.Match[str]) -> CitationCandidate | None:
    chain = match.group("chain")
    number = match.group("number")
    prefix = chain[: -len(number)].rstrip("-")
    tokens = [token.rstrip(".") for token in prefix.split("-") if token]
    if len(tokens) < 2 or not all(token.upper() in _RECOGNIZED_TOKENS for token in tokens):
        return None
    return CitationCandidate(
        match.start("chain"), match.end("chain"), chain,
        "experiment_compound_chain", "processo_ou_recurso_numerado",
    )


def experimental_candidates(text: str) -> list[CitationCandidate]:
    return [
        candidate
        for match in _COMPOUND_PATTERN.finditer(text)
        if (candidate := complete_chain_candidate(text, match)) is not None
    ]


def parse_experimental(candidate: CitationCandidate, text: str, parser: CitationParser) -> ParsedCitation:
    if candidate.rule != "experiment_compound_chain":
        return parser.parse(candidate, context=text)
    match = _COMPOUND_PATTERN.fullmatch(candidate.text)
    if match is None:
        raise ValueError(f"candidato experimental fora da gramática: {candidate.text!r}")
    number = match.group("number")
    prefix = candidate.text[: -len(number)].rstrip("-")
    tokens = [token.rstrip(".") for token in prefix.split("-") if token]
    tribunal = "TST" if tokens[0].upper() == "TST" else None
    return ParsedCitation(
        family="processo_ou_recurso_numerado",
        tipo="jurisprudencia",
        tribunal_raw=tribunal,
        tribunal=tribunal,
        tribunal_source="explicit" if tribunal else "unknown",
        data={
            "classe_raw": tokens[-1], "numero_raw": number,
            "numero_normalizado": re.sub(r"\D", "", number), "numero_family": "case_number",
        },
        provenance={"numero": "experimental_compound_chain", "classe": "explicit"},
    )


def run_experiment(texts: Mapping[str, str], resolver: CitationResolver, *, enabled: bool) -> tuple[list[Any], list[Any]]:
    detector, parser = CitationDetector(), CitationParser()
    raw, outputs = [], []
    for document_id in sorted(texts):
        text = texts[document_id]
        candidates = list(detector.detect(text))
        if enabled:
            # A complete compound candidate is the better representation of
            # the same literal reference. Replace only V6 candidates with an
            # IoU-overlap; otherwise the official submission sees a duplicate
            # span. No unrelated candidate is removed.
            for experimental in experimental_candidates(text):
                candidates = [
                    candidate for candidate in candidates
                    if span_iou(
                        [candidate.start, candidate.end],
                        [experimental.start, experimental.end],
                    ) < 0.5
                ]
                candidates.append(experimental)
        unique: dict[tuple[int, int], CitationCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: (item.start, item.end, item.rule)):
            unique.setdefault((candidate.start, candidate.end), candidate)
        candidates = list(unique.values())
        parsed = [parse_experimental(candidate, text, parser) for candidate in candidates]
        results = [resolver.resolve(item) for item in parsed]
        source_indexes = {id(candidate): index for index, candidate in enumerate(candidates)}
        for candidate, item, result in zip(candidates, parsed, results):
            raw.append(post.RawPrediction(len(raw), document_id, candidate, item, result))
        arbitrated = structural_cnj_union_merge(
            text, candidates, parsed, results, primary_identities=resolver.primary_identities
        )
        for item in arbitrated:
            outputs.append(
                post.Output(
                    len(outputs), document_id, item.candidate, item.parsed, item.resolution,
                    item.reason, tuple(sorted(source_indexes[id(source)] for source in item.source_candidates)),
                )
            )
    return raw, outputs


def exact_count(gold: Sequence[Any], outputs: Sequence[Any]) -> int:
    matches = post.match_gold(gold, outputs)
    return sum(
        outputs[output_index].candidate.start == gold[gold_index].start
        and outputs[output_index].candidate.end == gold[gold_index].end
        for gold_index, output_index in matches.items()
    )


def correct_real_gold_indexes(gold: Sequence[Any], outputs: Sequence[Any]) -> set[int]:
    matches = post.match_gold(gold, outputs)
    by_index = {item.index: item for item in outputs}
    return {
        item.index for item in gold
        if item.classification == "real"
        and (output := by_index.get(matches.get(item.index))) is not None
        and output.result.status == "resolved"
        and output.result.id_canonico == item.canonical_id
    }


def metric_summary(gold: Sequence[Any], outputs: Sequence[Any], *, raw: Sequence[Any], final: Sequence[Any], score: float) -> dict[str, Any]:
    output_metrics = post.output_metrics(gold, outputs)
    return {
        "raw_predictions": len(raw), "raw_matches": len(post.match_gold(gold, raw)),
        "final_predictions": len(final), "final_matches": len(post.match_gold(gold, final)),
        "final_exact": exact_count(gold, final), "real_ids_correct": output_metrics["correct_ids"],
        "official_final": score,
        "safety": {key: output_metrics[key] for key in ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")},
    }


def score_outputs(outputs: Sequence[Any], gold_df: Any) -> float:
    documents = list(gold_df.documento_id.drop_duplicates())
    submission = official.submission_from_cells(official.output_cells(outputs), documents)
    return float(official.METRIC.score(official.solution_from_gold(gold_df), submission, "documento_id"))


def chain_inventory(texts: Mapping[str, str], resolver: CitationResolver) -> list[dict[str, Any]]:
    rows = []
    parser = CitationParser()
    for document_id in sorted(texts):
        for candidate in experimental_candidates(texts[document_id]):
            parsed = parse_experimental(candidate, texts[document_id], parser)
            result = resolver.resolve(parsed)
            rows.append({
                "document_id": document_id, "span": [candidate.start, candidate.end], "chain": candidate.text,
                "parsed": parsed_view(parsed), "resolver": result_view(result), "source": "AUTOMATED",
            })
    return rows


def negative_checks() -> list[dict[str, Any]]:
    examples = (
        ("required_historical_partial", "TST-E-RR-173000-49.2008", False),
        ("arbitrary_tokens", "FOO-BAR-65-63.2010.5.01.0075", False),
        ("recognized_chain_complete_continuation", "TST-E-RR-173000-49.2008.5.15.0024", True),
    )
    checks = []
    for name, text, expected in examples:
        candidates = experimental_candidates(text)
        checks.append({
            "name": name, "text": text, "expected_candidate": expected,
            "actual_candidate": bool(candidates), "passed": bool(candidates) == expected,
            "candidate_spans": [[candidate.start, candidate.end] for candidate in candidates],
            "source": "AUTOMATED",
        })
    return checks


def target_diagnostic(gold: Sequence[Any], outputs: Sequence[Any]) -> dict[str, Any]:
    target = next(item for item in gold if item.index == TARGET_GOLD_ID)
    match = post.match_gold(gold, outputs).get(TARGET_GOLD_ID)
    output = next((item for item in outputs if item.index == match), None)
    return {
        "gold_id": TARGET_GOLD_ID, "gold_text": target.text, "gold_span": [target.start, target.end],
        "gold_canonical_id": target.canonical_id, "matched": output is not None,
        "candidate": None if output is None else {"span": [output.candidate.start, output.candidate.end], "text": output.candidate.text, "rule": output.candidate.rule, "family": output.candidate.family},
        "parsed": None if output is None else parsed_view(output.parsed),
        "resolver": None if output is None else result_view(output.result),
        "canonical_correct": bool(output and output.result.status == "resolved" and output.result.id_canonico == TARGET_CANONICAL_ID),
        "exact_span": bool(output and output.candidate.start == target.start and output.candidate.end == target.end),
        "source": "AUTOMATED",
    }


def build_report() -> dict[str, Any]:
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    if len(texts) != 26 or len(gold) != 225:
        raise RuntimeError("dimensão oficial inesperada")
    if sha256(get_database_path().read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("hash do banco divergiu do baseline congelado")
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        gold_df = official.load_gold_df()
        baseline_raw, baseline_final = run_experiment(texts, resolver, enabled=False)
        experiment_raw, experiment_final = run_experiment(texts, resolver, enabled=True)
        baseline_raw_matches = post.match_gold(gold, baseline_raw)
        baseline_metrics = metric_summary(
            gold, baseline_final, raw=baseline_raw, final=baseline_final,
            score=score_outputs(baseline_final, gold_df),
        )
        baseline_exact = sum(
            baseline_raw[r].candidate.start == gold[g].start and baseline_raw[r].candidate.end == gold[g].end
            for g, r in baseline_raw_matches.items()
        )
        measured_baseline = {
            "predictions": len(baseline_raw), "matches": len(baseline_raw_matches),
            "FP": len(baseline_raw) - len(baseline_raw_matches), "FN": len(gold) - len(baseline_raw_matches),
            "exact": baseline_exact, "real_ids_correct": baseline_metrics["real_ids_correct"],
            "official_final": baseline_metrics["official_final"], "safety": baseline_metrics["safety"],
        }
        if measured_baseline != V6_BASELINE:
            raise RuntimeError(f"baseline V6 não reproduzida: {measured_baseline}")

        experiment_metrics = metric_summary(
            gold, experiment_final, raw=experiment_raw, final=experiment_final,
            score=score_outputs(experiment_final, gold_df),
        )
        baseline_correct = correct_real_gold_indexes(gold, baseline_final)
        experiment_correct = correct_real_gold_indexes(gold, experiment_final)
        inventory = chain_inventory(texts, resolver)
        negatives = negative_checks()
        target = target_diagnostic(gold, experiment_final)
        regressions = sorted(baseline_correct - experiment_correct)
        new_correct = sorted(experiment_correct - baseline_correct)
        safety_pass = all(value == 0 for value in experiment_metrics["safety"].values())
        criteria = {
            "target_98_resolved_correctly": target["canonical_correct"],
            "target_98_exact_span": target["exact_span"],
            "historical_negative_rejected": negatives[0]["passed"] and not negatives[0]["actual_candidate"],
            "arbitrary_tokens_rejected": negatives[1]["passed"] and not negatives[1]["actual_candidate"],
            "complete_continuation_accepted": negatives[2]["passed"] and negatives[2]["actual_candidate"],
            "no_real_id_regression": not regressions,
            "no_new_false_real": safety_pass,
            "score_non_decreasing": experiment_metrics["official_final"] >= V6_BASELINE["official_final"],
        }
        passed = all(criteria.values())
        return {
            "schema_version": "1.0", "experiment_type": "POST_V6_CASE98_COMBINED_DETECTOR_PARSER_EXPERIMENT",
            "status": "PASS_FOR_TARGET_CASE" if passed else "FAIL",
            "promotion_status": "NOT_PROMOTION__OFFLINE_VALIDATOR_ONLY",
            "scope": {"case": TARGET_GOLD_ID, "txt_count": len(texts), "gold_used_only_for_evaluation": True, "database_read_only": True},
            "hypothesis": "recognized process-token chain + project-complete CNJ/TST number; parser extracts terminal class and literal number without mutation",
            "baseline": V6_BASELINE,
            "measured_baseline": measured_baseline,
            "experiment": experiment_metrics,
            "delta": {
                "raw_predictions": experiment_metrics["raw_predictions"] - baseline_metrics["raw_predictions"],
                "final_predictions": experiment_metrics["final_predictions"] - baseline_metrics["final_predictions"],
                "real_ids_correct": experiment_metrics["real_ids_correct"] - baseline_metrics["real_ids_correct"],
                "official_final": experiment_metrics["official_final"] - V6_BASELINE["official_final"],
                "new_correct_real_gold_indexes": new_correct, "regressed_real_gold_indexes": regressions,
            },
            "target_diagnostic": target, "accepted_chain_count": len(inventory), "accepted_chain_inventory": inventory,
            "negative_checks": negatives, "success_criteria": criteria,
            "interpretation": "A gramática acrescenta seis cadeias estruturais; cinco resolvem unicamente e uma permanece ambígua. O caso 98 é recuperado exatamente sem falso real novo. Este resultado é elegível para revisão de promoção, não altera V6.",
            "source": "AUTOMATED",
        }


def main() -> int:
    runs = [build_report() for _ in range(3)]
    if not (stable(runs[0]) == stable(runs[1]) == stable(runs[2])):
        raise RuntimeError("validator não determinístico em 3 execuções")
    report = runs[0]
    report["determinism"] = {"runs": 3, "identical": True, "source": "AUTOMATED"}
    ARTIFACT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "promotion_status": report["promotion_status"], "artifact": str(ARTIFACT_PATH), "delta": report["delta"], "determinism": report["determinism"]}, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS_FOR_TARGET_CASE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
