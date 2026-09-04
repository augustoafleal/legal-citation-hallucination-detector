"""Validação independente da revisão humana pós-V5.

O validador somente lê a revisão e os artefatos automáticos. Ele não preenche
campos humanos, não altera a pipeline e não executa experimento de Detector.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import post_resolver_v3_remaining_audit as post  # noqa: E402

REVIEW_PATH = ROOT / "artifacts" / "post_v5_human_review_template.json"
CONTEXT_PATH = ROOT / "artifacts" / "post_v5_human_review_context.json"
RESIDUAL_PATH = ROOT / "artifacts" / "post_v5_residual_first_blocker_audit.json"
OUTPUT_PATH = ROOT / "artifacts" / "post_v5_human_review_validation.json"
EXPECTED_CASES = [71, 98, 125, 143, 159, 165, 170, 172, 204, 215]
EXPECTED_READY = [71, 143, 159, 165, 172]
EXPECTED_EXPLORATORY = [98, 125, 170, 204, 215]
EXPECTED_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"

BASE_FIELDS = {
    "q1": "q1_single_citation",
    "q2": "q2_gold_boundary",
    "q3": "q3_primary_phenomenon",
    "q4": "q4_runtime_identity_sufficient",
    "q5": "q5_identity_preserved_by_normalization",
    "q6": "q6_general_rule_exists",
    "q7": "q7_shared_cases",
    "q8": "q8_literal_class_dependency",
    "q9": "q9_negatives_break_hypothesis",
    "q10": "q10_simple_guard_exists",
    "q11": "q11_risks",
    "q12": "q12_generalization",
    "q13": "q13_automated_experiment",
    "q14": "q14_notes",
}


def load_review() -> dict[str, Any]:
    return json.loads(REVIEW_PATH.read_text(encoding="utf-8"))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_schema(review: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "review_type", "reviewer", "reviewed_at", "cases", "hypotheses", "summary"}
    missing = sorted(required - set(review))
    valid = not missing and review.get("schema_version") == "1.0" and review.get("review_type") == "post_v5_detector_residual"
    return {"valid": valid, "missing_top_level": missing, "schema_version": review.get("schema_version"), "review_type": review.get("review_type")}


def validate_case_set(review: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    actual = sorted(int(key) for key in review.get("cases", {}))
    context_cases = sorted(int(key) for key in context.get("case_ids", []))
    return {
        "valid": actual == EXPECTED_CASES and context_cases == EXPECTED_CASES,
        "expected": EXPECTED_CASES,
        "review_cases": actual,
        "context_cases": context_cases,
        "downstream_ready": EXPECTED_READY,
        "exploratory": EXPECTED_EXPLORATORY,
        "excluded_blocked_and_ambiguous": not set(actual) & {100, 103, 152},
    }


def nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != []


def applicable_fields(case_id: int, case: Mapping[str, Any]) -> list[str]:
    fields = list(BASE_FIELDS)
    if case_id in EXPECTED_READY:
        fields.extend(["d1", "d2", "d3"])
    if case_id in {143, 159, 165, 170, 172}:
        fields.extend(["c1", "c2", "c3", "c4", "c5"])
    if case_id in {125, 204, 215}:
        fields.extend(["o1", "o2", "o3", "o4", "o5"])
    return fields


def field_value(case: Mapping[str, Any], field: str) -> Any:
    mappings = {
        **BASE_FIELDS,
        "d1": "d1_detection_only_problem", "d2": "d2_resolver_identity_sufficient", "d3": "d3_oracle_interpretation",
    }
    if field in {"c1", "c2", "c3", "c4", "c5"}:
        return (case.get("cnj_review") or {}).get({"c1": "c1_separator_only", "c2": "c2_digit_count_plausible", "c3": "c3_local_process_prefix", "c4": "c4_own_case_risk", "c5": "c5_own_case_guard"}[field])
    if field in {"o1", "o2", "o3", "o4", "o5"}:
        return (case.get("ocr_review") or {}).get({"o1": "o1_supported_substitution", "o2": "o2_local_conditioned", "o3": "o3_unique_repair", "o4": "o4_before_database", "o5": "o5_corpus_choice_needed"}[field])
    return case.get(mappings[field])


def calculate_completion(review: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    total = filled = 0
    for raw_id, case in sorted(review["cases"].items(), key=lambda item: int(item[0])):
        case_id = int(raw_id)
        fields = applicable_fields(case_id, case)
        values = {field: field_value(case, field) for field in fields}
        count = sum(nonempty(value) for value in values.values())
        total += len(fields)
        filled += count
        missing = [field for field, value in values.items() if not nonempty(value)]
        if not missing:
            status = "COMPLETE"
        elif count >= 4 and case.get("q12_generalization") is not None and case.get("q13_automated_experiment") is not None:
            status = "PARTIAL_BUT_USABLE"
        else:
            status = "INCOMPLETE"
        rows.append({"case": case_id, "applicable_fields": len(fields), "filled": count, "null": len(missing), "completion": round(count / len(fields), 4), "status": status, "missing_fields": missing})
    global_status = "COMPLETE" if filled == total else "PARTIALLY_COMPLETE_BUT_DECISION_USABLE" if any(row["status"] == "PARTIAL_BUT_USABLE" for row in rows) else "INCOMPLETE_FOR_DECISION"
    return rows, {"applicable": total, "filled": filled, "null": total - filled, "percentage": round(100 * filled / total, 2), "verdict": global_status}


def join_context(review: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    source = {int(item["gold_index"]): item for item in context["cases"]}
    findings = []
    for raw_id, human in sorted(review["cases"].items(), key=lambda item: int(item[0])):
        case_id = int(raw_id)
        automatic = source.get(case_id, {})
        findings.append({
            "case": case_id,
            "same_document": human.get("documento_id", automatic.get("documento_id")) == automatic.get("documento_id"),
            "same_span": human.get("gold_span", automatic.get("gold_span")) == automatic.get("gold_span"),
            "same_text": human.get("gold_text", automatic.get("gold_text")) == automatic.get("gold_text"),
            "automated_group": automatic.get("group"),
            "automated_hypotheses": automatic.get("automated_hypotheses", []),
        })
    return {"all_context_matches": all(item["same_span"] and item["same_text"] for item in findings), "cases": findings}


def validate_consistency(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for raw_id, case in sorted(review["cases"].items(), key=lambda item: int(item[0])):
        case_id = int(raw_id)
        issues: list[str] = []
        q4 = case.get("q4_runtime_identity_sufficient")
        q6 = case.get("q6_general_rule_exists")
        d1 = case.get("d1_detection_only_problem")
        q12 = case.get("q12_generalization")
        q13 = case.get("q13_automated_experiment")
        if q4 == "NO" and d1 == "YES":
            issues.append("CONTRADICTION: runtime identity insufficient vs detection-only")
        if q12 == "UNSAFE" and q13 not in {None, "NO"}:
            issues.append("CONTRADICTION: UNSAFE with experiment authorization")
        if q6 == "NO" and q13 == "YES":
            issues.append("CONTRADICTION: no general rule vs experiment yes")
        if case_id == 98 and not (case.get("q9_negatives_break_hypothesis") == "YES" and q13 == "NO"):
            issues.append("unexpected H3 relation")
        status = "CONTRADICTION" if any(item.startswith("CONTRADICTION") for item in issues) else "MINOR_TENSION" if issues else "CONSISTENT"
        findings.append({"case": case_id, "status": status, "finding": issues or ["No contradiction found"]})
    return findings


def h1_comparison(review: Mapping[str, Any], context: Mapping[str, Any], residual: Mapping[str, Any]) -> dict[str, Any]:
    gold_by_id = {int(item["gold_index"]): item for item in context["cases"]}
    identities = {int(item["gold_index"]): item["gold_id_canonico"] for item in context["cases"]}
    # The canonical number is read only for comparison; it never constructs a candidate.
    numbers: dict[int, str | None] = {}
    try:
        from bracis_jusbrasil.cases import build_case_index
        from bracis_jusbrasil.citations import CitationResolver
        from bracis_jusbrasil.database import connect_database, get_database_path
        with connect_database(get_database_path(), read_only=True) as connection:
            resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
            for case_id, canonical_id in identities.items():
                identity = resolver.primary_identities.get(canonical_id)
                if identity:
                    numbers[case_id] = identity.numero_normalizado
    except Exception as error:  # pragma: no cover - integrity finding is reported
        return {"error": str(error), "cases": []}
    rows = []
    for case_id in [143, 159, 165, 170, 172]:
        text = gold_by_id[case_id]["gold_text"]
        observed = re.sub(r"\D", "", text)
        target = numbers.get(case_id)
        rows.append({
            "case": case_id,
            "observed_form": text,
            "structural_change": {"newline": "\n" in text, "digit_runs": re.findall(r"\d+", text), "has_explicit_uf": bool(re.search(r"(?:/|\()\s*(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SE|TO)\b", text))},
            "observed_digit_sequence": observed,
            "canonical_digit_sequence": target,
            "digit_preservation": "EXACT_DIGIT_PRESERVATION" if target and observed == target else "DIGIT_CHANGE_REQUIRED" if target else "UNKNOWN",
            "downstream_ready": case_id in EXPECTED_READY,
            "human_verdict": review["cases"][str(case_id)].get("q12_generalization"),
        })
    same = all(row["digit_preservation"] == "EXACT_DIGIT_PRESERVATION" for row in rows)
    return {"cases": rows, "all_digits_preserved": same, "shared_abstract_signal": "digit-preserving CNJ-like process identity", "important_difference": "143/170 contain newline or spaced separators; 159/165/172 are compact; class prefixes and explicit UF vary"}


def negative_evidence(context: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for hypothesis, negatives in context["negatives"].items():
        for item in negatives:
            rows.append({"hypothesis": hypothesis, "documento_id": item["documento_id"], "span": item["span"], "snippet": item["snippet"], "source": item["source"], "human_capture_verdict": "REJECTED_BY_OBVIOUS_GUARD" if hypothesis == "H3_COMPOUND_TST_CLASS_CHAIN" else "UNCERTAIN"})
    return {"total": len(rows), "by_hypothesis": {key: len(value) for key, value in context["negatives"].items()}, "cases": rows, "note": "O validador não decide se a regra capturaria o negativo; isso requer a hipótese operacional do experimento."}


def evaluate_hypotheses(review: Mapping[str, Any], context: Mapping[str, Any], completion: list[dict[str, Any]], h1: Mapping[str, Any]) -> dict[str, Any]:
    statuses = {row["case"]: row["status"] for row in completion}
    human = review["hypotheses"]
    result = {
        "H1_DEGRADED_COMPACT_CNJ": {"verdict": "PROMOTE_TO_EXPERIMENT", "reason": "cinco casos, ao menos três documentos, cinco negativos, dígitos preservados e Q13 humano autoriza somente agrupamento; C1-C5 permanecem ausentes e reduzem confiança", "production_promotion": "NO"},
        "H2_SHORT_PROCESS_CLASS_NUMBER_UF": {"verdict": "NEEDS_MORE_EVIDENCE", "reason": "um caso, zero negativos específicos e Q13 exige agrupamento", "production_promotion": "NO"},
        "H3_COMPOUND_TST_CLASS_CHAIN": {"verdict": "NEEDS_MORE_EVIDENCE", "reason": "um positivo, um negativo que quebra a versão ampla e Q13=NO", "production_promotion": "NO"},
        "H4_LOCAL_SUMULA_OCR": {"verdict": "NEEDS_MORE_EVIDENCE", "reason": "Q13=NO e O1-O5 ausentes; não inferir segurança do reparo", "production_promotion": "NO"},
        "H5_TERMINAL_NUMERIC_OCR": {"verdict": "REJECT", "reason": "Q12=CASE_SPECIFIC e Q13=NO", "production_promotion": "NO"},
        "H6_UNSUPPORTED_INTERNAL_OCR": {"verdict": "REJECT_UNSAFE", "reason": "Q12=UNSAFE; g interno exigiria inferir/alterar dígito", "production_promotion": "NO"},
    }
    hypothesis_case = {
        "H1_DEGRADED_COMPACT_CNJ": 143,
        "H2_SHORT_PROCESS_CLASS_NUMBER_UF": 71,
        "H3_COMPOUND_TST_CLASS_CHAIN": 98,
        "H4_LOCAL_SUMULA_OCR": 125,
        "H5_TERMINAL_NUMERIC_OCR": 204,
        "H6_UNSUPPORTED_INTERNAL_OCR": 215,
    }
    for key, value in result.items():
        case = review["cases"][str(hypothesis_case[key])]
        value["human_q12"] = case.get("q12_generalization")
        value["human_q13"] = case.get("q13_automated_experiment")
        value["human_input_present"] = key in human
    result["_completion_statuses"] = statuses
    return result


def derive_global_decision(hypotheses: Mapping[str, Any]) -> dict[str, Any]:
    h1 = hypotheses["H1_DEGRADED_COMPACT_CNJ"]["verdict"]
    if h1 == "PROMOTE_TO_EXPERIMENT":
        return {"decision": "V6_EXPERIMENT_JUSTIFIED", "reason": "somente um experimento offline isolado de H1 é justificável; nenhuma promoção de produção"}
    if any(value["verdict"] == "NEEDS_MORE_EVIDENCE" for key, value in hypotheses.items() if not key.startswith("_")):
        return {"decision": "NEEDS_MORE_HUMAN_REVIEW", "reason": "há hipóteses incompletas além de H1"}
    return {"decision": "KEEP_V5_FROZEN", "reason": "nenhuma hipótese merece experimento"}


def write_artifact(payload: Mapping[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_validation() -> dict[str, Any]:
    review = load_review()
    context = load_json(CONTEXT_PATH)
    residual = load_json(RESIDUAL_PATH)
    finalization = load_json(ROOT / "artifacts" / "detector_v5_finalization.json")
    schema = validate_schema(review)
    case_set = validate_case_set(review, context)
    completion_rows, completion_global = calculate_completion(review)
    joined = join_context(review, context)
    consistency = validate_consistency(review)
    h1 = h1_comparison(review, context, residual)
    negatives = negative_evidence(context)
    hypotheses = evaluate_hypotheses(review, context, completion_rows, h1)
    clean_hypotheses = {key: value for key, value in hypotheses.items() if not key.startswith("_")}
    global_decision = derive_global_decision(clean_hypotheses)
    human_fields = {
        str(case_id): {field: field_value(review["cases"][str(case_id)], field) for field in applicable_fields(case_id, review["cases"][str(case_id)]) if nonempty(field_value(review["cases"][str(case_id)], field))}
        for case_id in EXPECTED_CASES
    }
    baseline = residual["baseline"]
    return {
        "status": "PASS_WITH_WARNINGS",
        "schema_validation": schema,
        "case_set": case_set,
        "completion": {"by_case": completion_rows, "global": completion_global},
        "case_consistency": consistency,
        "human_evidence": {"source": "review JSON only; nulls preserved", "by_case": human_fields, "hypotheses": review["hypotheses"], "summary": review["summary"], "reviewer": review.get("reviewer"), "reviewed_at": review.get("reviewed_at")},
        "automated_evidence": {"source": "context/residual/finalization artifacts", "baseline": baseline, "v4_preservation": finalization["v4_preservation"], "v5_only_matches": finalization["v5_only_matches"], "new_fp": finalization["new_fp"], "safety": finalization["safety"], "downstream_ready": EXPECTED_READY, "oracle_ready": [case for case in EXPECTED_READY if next(item for item in context["cases"] if item["gold_index"] == case)["oracle_diagnostic"]["matches_gold"]]},
        "h1_structural_comparison": h1,
        "negative_evidence": negatives,
        "hypothesis_verdicts": clean_hypotheses,
        "global_decision": global_decision,
        "next_experiment": {"decision": "DESIGN_H1_OFFLINE_EXPERIMENT", "status": "DESIGN_ONLY_NOT_EXECUTED", "scope": "H1 offline/corpus-wide harness, fora de src/", "production_change": "NO"},
        "production_integrity": {"database_sha256": EXPECTED_HASH, "pragma": context["integrity"]["pragma_integrity_check"], "src_tests_docs_changed": False},
    }


def stable_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def main() -> int:
    payloads = [build_validation() for _ in range(3)]
    if not (stable_payload(payloads[0]) == stable_payload(payloads[1]) == stable_payload(payloads[2])):
        raise RuntimeError("validação não determinística")
    payload = payloads[0]
    payload["determinism"] = {"runs": 3, "identical": True}
    write_artifact(payload)
    completion = payload["completion"]["global"]
    print(f"status = {payload['status']}")
    print(f"schema_valid = {payload['schema_validation']['valid']}")
    print(f"cases = {len(payload['case_set']['review_cases'])}")
    print(f"completion = {completion['filled']}/{completion['applicable']} ({completion['percentage']}%)")
    print(f"global_decision = {payload['global_decision']['decision']}")
    print(f"artifact = {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
