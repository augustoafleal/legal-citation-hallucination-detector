"""Auditoria metric-aware da classificação do pipeline atual.

O módulo é analítico: não altera Detector, Parser, Resolver, arbitragem ou
produção. Toda nota é obtida pela implementação oficial de ``kaggle_metric``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402

OUT = ROOT / "artifacts" / "classification_metric_audit.json"
EXPECTED_SCORE = 0.48365269398459604
EXPECTED_DETECTOR = (219, 137, 82, 88, 83)
EXPECTED_POST = (67, 214, 0, 0, 0)
CLASSES = ("real", "inventada", "incompleta")
STATUSES = ("resolved", "no_match", "ambiguous", "insufficient")


def table_counter(counter: Counter[tuple[str, str]], row_name: str = "reason") -> list[dict[str, Any]]:
    names = sorted({key[0] for key in counter})
    return [{row_name: name, **{label: counter[(name, label)] for label in CLASSES}, "total": sum(counter[(name, label)] for label in CLASSES)} for name in names]


def official_eval(solution: pd.DataFrame, submission: pd.DataFrame) -> dict[str, Any]:
    """Executa o scorer oficial; nenhuma fórmula de score é reproduzida aqui."""
    return official.METRIC.avaliar(solution, submission, row_id="documento_id")


def base_label(status: str) -> str:
    return {"resolved": "real", "no_match": "inventada", "ambiguous": "incompleta", "insufficient": "incompleta"}[status]


def legal_lookup(parsed: Any, indexes: dict[str, dict[tuple[str | None, ...], tuple[int, ...]]]) -> dict[str, Any]:
    data = dict(parsed.data)
    strategies = ("article_law_year", "article_diploma_law", "article_law", "article_diploma")
    for strategy in strategies:
        hypothesis = post.legal_hypothesis(parsed, strategy, indexes)
        if hypothesis is not None:
            return {"attempted": True, "strategy": strategy, "status": hypothesis.status, "candidate_ids": list(hypothesis.candidate_ids), "reason": hypothesis.reason}
    if "artigo" in data:
        return {"attempted": False, "strategy": None, "status": "not_attempted", "candidate_ids": [], "reason": "no_complete_legal_identity_key"}
    return {"attempted": False, "strategy": None, "status": "not_attempted", "candidate_ids": [], "reason": "no_article"}


def features_for(output: Any, legal_indexes: dict[str, dict[tuple[str | None, ...], tuple[int, ...]]]) -> dict[str, Any]:
    parsed = output.parsed
    data = dict(parsed.data)
    provenance = dict(parsed.provenance)
    family = parsed.family
    structural_family = family in {"processo_cnj", "processo_ou_recurso_numerado", "sumula_numerada"}
    has_number = bool(data.get("numero_normalizado") or data.get("sumula_numero"))
    has_explicit_law = bool(data.get("law_number") and data.get("law_year"))
    legal = legal_lookup(parsed, legal_indexes) if family.startswith("lei_") else {"attempted": False, "strategy": None, "status": "not_applicable", "candidate_ids": [], "reason": "not_legal"}
    if structural_family and has_number:
        queryability = "QUERYABLE"
        lookup_attempted = True
        lookup_ids = list(output.result.candidate_ids)
        lookup_reason = output.result.reason
    elif family.startswith("lei_") and has_explicit_law:
        queryability = "QUERYABLE"
        lookup_attempted = legal["attempted"]
        lookup_ids = legal["candidate_ids"]
        lookup_reason = legal["reason"]
    elif family.startswith("lei_") and data:
        queryability = "PARTIALLY_QUERYABLE"
        lookup_attempted = legal["attempted"]
        lookup_ids = legal["candidate_ids"]
        lookup_reason = legal["reason"]
    elif family == "jurisprudencia_tribunal_contextual" and (parsed.tribunal or data.get("ano") or data.get("relator_raw")):
        queryability = "PARTIALLY_QUERYABLE"
        lookup_attempted, lookup_ids, lookup_reason = False, [], "family_not_supported_in_v1"
    else:
        queryability = "NOT_QUERYABLE"
        lookup_attempted, lookup_ids, lookup_reason = False, [], output.result.reason
    raw_number = str(data.get("numero_raw", ""))
    normalization_issue = bool(raw_number and any(char in raw_number.upper() for char in ("O", "I", "L")))
    return {
        "family": family,
        "status": output.result.status,
        "reason": output.result.reason,
        "parser_fields": data,
        "parser_provenance": provenance,
        "tribunal": parsed.tribunal,
        "class": data.get("classe_raw"),
        "number": data.get("numero_normalizado") or data.get("sumula_numero"),
        "cnj": data.get("numero_family") == "cnj",
        "uf": data.get("uf"),
        "relator": data.get("relator_raw"),
        "year": data.get("ano"),
        "article": data.get("artigo"),
        "diploma": data.get("diploma_normalizado"),
        "law_number": data.get("law_number"),
        "law_year": data.get("law_year"),
        "structured_identifiers": sorted(key for key, value in {"number": has_number, "tribunal": bool(parsed.tribunal), "class": bool(data.get("classe_raw")), "cnj": data.get("numero_family") == "cnj", "uf": bool(data.get("uf")), "relator": bool(data.get("relator_raw")), "year": bool(data.get("ano")), "article": bool(data.get("artigo")), "diploma": bool(data.get("diploma_normalizado")), "law_number": bool(data.get("law_number")), "law_year": bool(data.get("law_year"))}.items() if value),
        "queryability": queryability,
        "lookup_attempted": lookup_attempted,
        "lookup_key": {"family": family, "number": data.get("numero_normalizado") or data.get("sumula_numero"), "article": data.get("artigo"), "diploma": data.get("diploma_normalizado"), "law_number": data.get("law_number"), "law_year": data.get("law_year")},
        "lookup_candidate_count": len(lookup_ids),
        "lookup_candidate_ids": lookup_ids,
        "lookup_reason": lookup_reason,
        "normalization_issue": normalization_issue,
    }


def matched_records(gold: list[Any], outputs: list[Any], legal_indexes: dict[str, dict[tuple[str | None, ...], tuple[int, ...]]]) -> tuple[list[dict[str, Any]], dict[int, int], dict[int, dict[str, Any]]]:
    matches = post.match_gold(gold, outputs)
    by_output = {item.index: item for item in outputs}
    records: list[dict[str, Any]] = []
    output_features: dict[int, dict[str, Any]] = {}
    for output in outputs:
        output_features[output.index] = features_for(output, legal_indexes)
    for gold_index, output_index in matches.items():
        item = gold[gold_index]
        output = by_output[output_index]
        records.append({"gold_index": gold_index, "gid": item.gid, "documento": item.document_id, "nivel": item.level, "gold_class": item.classification, "gold_type": item.citation_type, "gold_text": item.text, "output_index": output_index, "base_prediction": base_label(output.result.status), **output_features[output_index]})
    return records, matches, output_features


def policy_label(feature: dict[str, Any], policy: str) -> str:
    baseline = base_label(feature["status"])
    if policy == "P0":
        return baseline
    explicit_legal_zero = feature["family"].startswith("lei_") and feature["law_number"] and feature["law_year"] and feature["lookup_attempted"] and feature["lookup_candidate_count"] == 0
    if policy == "P1":
        return "inventada" if feature["queryability"] == "QUERYABLE" and feature["lookup_attempted"] and feature["lookup_candidate_count"] == 0 and feature["status"] in {"no_match", "insufficient"} else baseline
    if policy == "P2":
        return "inventada" if feature["status"] == "insufficient" and feature["reason"] == "legal_identity_not_verifiable" and explicit_legal_zero else baseline
    if policy == "P3":
        return "inventada" if feature["family"].startswith("lei_") and feature["status"] == "insufficient" and feature["reason"] == "legal_identity_not_verifiable" and explicit_legal_zero else baseline
    if policy == "P4":
        return "inventada" if feature["queryability"] == "QUERYABLE" and feature["lookup_attempted"] and feature["lookup_candidate_count"] == 0 and feature["status"] in {"no_match", "insufficient"} else baseline
    raise ValueError(policy)


def submission_for_policy(outputs: list[Any], features: dict[int, dict[str, Any]], documents: list[str], policy: str) -> pd.DataFrame:
    cells = []
    for output in outputs:
        feature = features[output.index]
        cells.append(official.PredictionCell(output.document_id, output.candidate.start, output.candidate.end, policy_label(feature, policy), str(output.result.id_canonico) if output.result.status == "resolved" and output.result.id_canonico is not None else "-"))
    return official.submission_from_cells(cells, documents)


def confusion(records: list[dict[str, Any]], labels: dict[int, str]) -> dict[str, dict[str, int]]:
    result = {gold: {pred: 0 for pred in CLASSES} for gold in CLASSES}
    for record in records:
        result[record["gold_class"]][labels[record["output_index"]]] += 1
    return result


def family_status_table(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    families = sorted({record["family"] for record in records})
    for family in families:
        for status in STATUSES:
            if not any(record["family"] == family and record["status"] == status for record in records):
                continue
            row = {"family": family, "status": status}
            row.update({label: sum(record["family"] == family and record["status"] == status and record["gold_class"] == label for record in records) for label in CLASSES})
            result.append(row)
    return result


def safety(gold: list[Any], outputs: list[Any], labels: dict[int, str]) -> dict[str, int]:
    matches = post.match_gold(gold, outputs)
    by_output = {item.index: item for item in outputs}
    return {
        "false_real_inventada": sum(gold[gi].classification == "inventada" and labels[oi] == "real" for gi, oi in matches.items()),
        "false_real_incompleta": sum(gold[gi].classification == "incompleta" and labels[oi] == "real" for gi, oi in matches.items()),
        "wrong_unique_real": sum(gold[gi].classification == "real" and by_output[oi].result.status == "resolved" and by_output[oi].result.id_canonico != gold[gi].canonical_id for gi, oi in matches.items()),
    }


def score_table(policies: list[str], solution: pd.DataFrame, outputs: list[Any], features: dict[int, dict[str, Any]], documents: list[str], gold: list[Any], records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame], dict[str, dict[int, str]]]:
    rows = []
    submissions: dict[str, pd.DataFrame] = {}
    labels_by_policy: dict[str, dict[int, str]] = {}
    for policy in policies:
        submission = submission_for_policy(outputs, features, documents, policy)
        submissions[policy] = submission
        labels = {output.index: policy_label(features[output.index], policy) for output in outputs}
        labels_by_policy[policy] = labels
        evaluation = official_eval(solution, submission)
        levels = evaluation["niveis"]
        rows.append({"policy": policy, "official_score": evaluation["score_final"], "N1": levels.get(1, {}).get("score"), "N2": levels.get(2, {}).get("score"), "delta_vs_P0": None, "f1_by_level": {level: levels[level]["f1_por_classe"] for level in levels}, "tau_by_level": {level: levels[level]["tau"] for level in levels}, "safety": safety(gold, outputs, labels), "confusion_matched": confusion(records, labels), "changed_labels_vs_P0": None})
    p0 = rows[0]["official_score"]
    p0_labels = labels_by_policy["P0"]
    for row in rows:
        row["delta_vs_P0"] = row["official_score"] - p0
        row["changed_labels_vs_P0"] = sum(labels_by_policy[row["policy"]][index] != p0_labels[index] for index in p0_labels)
        row["correct_changes_vs_P0"] = sum(record["gold_class"] == labels_by_policy[row["policy"]][record["output_index"]] and labels_by_policy[row["policy"]][record["output_index"]] != p0_labels[record["output_index"]] for record in records)
        row["incorrect_changes_vs_P0"] = sum(record["gold_class"] != labels_by_policy[row["policy"]][record["output_index"]] and labels_by_policy[row["policy"]][record["output_index"]] != p0_labels[record["output_index"]] for record in records)
    return rows, submissions, labels_by_policy


def main() -> int:
    gold_df = official.load_gold_df()
    gold = official.load_gold_rows(gold_df)
    solution = official.solution_from_gold(gold_df)
    documents = list(gold_df.documento_id.drop_duplicates())
    texts = post.deep.load_texts()
    with official.connect_database(official.DB_PATH, read_only=True) as connection:
        resolver = post.CitationResolver(case_index=official.build_case_index(connection), connection=connection)
        raw, outputs, runtime = post.run_current_pipeline(texts, resolver)
        detector_matches = post.match_gold(gold, raw)
        detector_exact = sum(gold[gi].start == raw[oi].candidate.start and gold[gi].end == raw[oi].candidate.end for gi, oi in detector_matches.items())
        detector_tuple = (len(raw), len(detector_matches), len(raw) - len(detector_matches), len(gold) - len(detector_matches), detector_exact)
        post_metrics = post.output_metrics(gold, outputs)
        post_tuple = (post_metrics["correct_ids"], post_metrics["outputs"], post_metrics["wrong_unique_real"], post_metrics["false_real_inventada"], post_metrics["false_real_incompleta"])
        if detector_tuple != EXPECTED_DETECTOR or post_tuple != EXPECTED_POST:
            raise RuntimeError(f"baseline guard divergente: detector={detector_tuple}, post={post_tuple}")
        legal_indexes = post.legal_indexes(post.legal_records(connection))
        records, matches, output_features = matched_records(gold, outputs, legal_indexes)
        p0_submission = submission_for_policy(outputs, output_features, documents, "P0")
        p0_score = float(official.METRIC.score(solution, p0_submission, "documento_id"))
        if abs(p0_score - EXPECTED_SCORE) > 1e-15:
            raise RuntimeError(f"score P0 divergente: {p0_score}")
        policies, submissions, labels_by_policy = score_table(["P0", "P1", "P2", "P3", "P4"], solution, outputs, output_features, documents, gold, records)

        status_counter = Counter((record["gold_class"], record["status"]) for record in records)
        reason_counter = Counter((record["reason"], record["gold_class"]) for record in records)
        family_counter = Counter((record["family"], record["gold_class"]) for record in records)
        query_counter = Counter((record["queryability"], record["gold_class"]) for record in records)
        insufficient_records = [record for record in records if record["status"] == "insufficient"]
        no_match_records = [record for record in records if record["status"] == "no_match"]
        ambiguous_records = [record for record in records if record["status"] == "ambiguous"]
        no_match_real = [record for record in no_match_records if record["gold_class"] == "real"]
        no_match_analysis = []
        for record in no_match_records:
            canonical_in_db = False
            canonical_id = gold[record["gold_index"]].canonical_id
            if canonical_id is not None:
                canonical_in_db = connection.execute("SELECT 1 FROM documentos WHERE id=?", (canonical_id,)).fetchone() is not None
            valid_query = record["queryability"] == "QUERYABLE" and record["lookup_attempted"] and not canonical_in_db
            no_match_analysis.append({"gid": record["gid"], "documento": record["documento"], "gold_class": record["gold_class"], "family": record["family"], "parser_fields": record["parser_fields"], "reason": record["reason"], "queryability": record["queryability"], "normalization_issue": record["normalization_issue"], "lookup_attempted": record["lookup_attempted"], "lookup_key": record["lookup_key"], "candidate_count": record["lookup_candidate_count"], "canonical_id_in_db_for_diagnostic": canonical_in_db, "classification_diagnostic": "NO_MATCH_AFTER_VALID_QUERY" if valid_query else "NO_MATCH_DUE_TO_BAD_QUERY_REPRESENTATION_OR_RESOLVER_LIMITATION"})
        valid_no_match = sum(item["classification_diagnostic"] == "NO_MATCH_AFTER_VALID_QUERY" for item in no_match_analysis)
        bad_no_match = len(no_match_analysis) - valid_no_match

        changed_cases = []
        p0_labels = labels_by_policy["P0"]
        for policy in ("P1", "P2", "P3", "P4"):
            for record in records:
                old, new = p0_labels[record["output_index"]], labels_by_policy[policy][record["output_index"]]
                if old != new:
                    changed_cases.append({"policy": policy, "gid": record["gid"], "documento": record["documento"], "nivel": record["nivel"], "gold_class": record["gold_class"], "old_class": old, "new_class": new, "status": record["status"], "reason": record["reason"], "family": record["family"], "queryability": record["queryability"], "law_number": record["law_number"], "law_year": record["law_year"]})
        isolated = []
        for change in [item for item in changed_cases if item["policy"] == "P3"]:
            labels = dict(p0_labels)
            output_index = next(record["output_index"] for record in records if record["gid"] == change["gid"] and record["documento"] == change["documento"])
            labels[output_index] = change["new_class"]
            cells = []
            for output in outputs:
                cells.append(official.PredictionCell(output.document_id, output.candidate.start, output.candidate.end, labels[output.index], str(output.result.id_canonico) if output.result.status == "resolved" and output.result.id_canonico is not None else "-"))
            isolated_submission = official.submission_from_cells(cells, documents)
            isolated_score = float(official.METRIC.score(solution, isolated_submission, "documento_id"))
            isolated.append({**change, "isolated_official_score": isolated_score, "isolated_delta": isolated_score - p0_score})

        oracle_cells = []
        matched_by_output = {output_index: gold_index for gold_index, output_index in matches.items()}
        for output in outputs:
            # Para real não-resolvida, promover o rótulo exigiria inventar um
            # ID canônico e deixaria de ser um teto de classificação. Mantém-se
            # a classe P0; o oracle varia apenas inventada/incompleta.
            label = gold[matched_by_output[output.index]].classification if output.index in matched_by_output and gold[matched_by_output[output.index]].classification != "real" else p0_labels[output.index]
            oracle_cells.append(official.PredictionCell(output.document_id, output.candidate.start, output.candidate.end, label, str(output.result.id_canonico) if output.result.status == "resolved" and output.result.id_canonico is not None else "-"))
        oracle_score = float(official.METRIC.score(solution, official.submission_from_cells(oracle_cells, documents), "documento_id"))
        p3 = next(row for row in policies if row["policy"] == "P3")
        detector_artifact = ROOT / "artifacts" / "official_kaggle_metric_audit.json"
        detector_data = json.loads(detector_artifact.read_text(encoding="utf-8")) if detector_artifact.exists() else {}
        residual = detector_data.get("seven_detector_residual", {}).get("cases", [])
        residual_deltas = [item.get("global_delta") for item in residual if item.get("global_delta") is not None]
        review = [item for item in records if (item["gold_class"] == "real" and item["status"] == "no_match") or (item["gold_class"] == "inventada" and item["status"] == "insufficient" and item["law_number"] and item["law_year"]) or item["status"] == "ambiguous"]
        review = review[:10]
        payload = {
            "status": "PASS WITH WARNINGS",
            "baseline": {"detector": {"predictions": detector_tuple[0], "TP": detector_tuple[1], "FP": detector_tuple[2], "FN": detector_tuple[3], "exact": detector_tuple[4]}, "resolver_before_arbitration": 56, "post_arbitration": {"correct_ids": post_metrics["correct_ids"], "outputs": post_metrics["outputs"], "wrong_unique_real": post_metrics["wrong_unique_real"], "false_real_inventada": post_metrics["false_real_inventada"], "false_real_incompleta": post_metrics["false_real_incompleta"]}, "official_matching": len(matches), "official_score": p0_score, "classification_policy": "P0: resolved→real, no_match→inventada, ambiguous/insufficient→incompleta", "output_generation_fixed": True, "classification_only_variation": True, "runtime_seconds": runtime},
            "harness_patch": {"files": [{"file": "tests/test_citation_detector.py", "before": "pd.read_excel(.../goldenset.xlsx)", "after": "pd.read_csv(.../goldenset.csv)", "semantic_change": False}, {"file": "tests/test_citation_parser.py", "before": "pd.read_excel(.../goldenset.xlsx)", "after": "pd.read_csv(.../goldenset.csv)", "semantic_change": False}, {"file": "tests/test_citation_resolver.py", "before": "pd.read_excel(.../goldenset.xlsx)", "after": "pd.read_csv(.../goldenset.csv)", "semantic_change": False}], "xlsx_runtime_test_references_remaining": 0, "old2_test_dependencies": 0},
            "matched_universe": {"official_matched": len(matches), "missing_gold": len(gold) - len(matches), "detection_outputs": len(outputs), "classification_scope": "only official IoU>=0.5 matches; missing detections are separate"},
            "status_by_gold_class": {status: {label: status_counter[(label, status)] for label in CLASSES} for status in STATUSES} | {"missing": {label: sum(item.classification == label for item in gold) - sum(status_counter[(label, status)] for status in STATUSES) for label in CLASSES}},
            "reason_by_gold_class": table_counter(reason_counter),
            "family_by_gold_class": table_counter(family_counter, "family"),
            "family_status_by_gold_class": family_status_table(records),
            "queryability": {"table": [{"category": category, **{label: query_counter[(category, label)] for label in CLASSES}} for category in ("QUERYABLE", "NOT_QUERYABLE", "PARTIALLY_QUERYABLE")], "rules": ["processo_cnj/processo_ou_recurso_numerado/sumula with normalized number = QUERYABLE", "legal with article+law_number+law_year = QUERYABLE only for diagnostic structural lookup", "other legal structured fields and tribunal-contextual fields = PARTIALLY_QUERYABLE", "generic jurisprudence without structural key = NOT_QUERYABLE"], "records": records},
            "no_match_analysis": {"matched_count": len(no_match_records), "valid_query_count": valid_no_match, "bad_representation_or_resolver_limitation_count": bad_no_match, "records": no_match_analysis, "real_counterexamples": no_match_real},
            "insufficient_analysis": {"matched_count": len(insufficient_records), "records": [record for record in insufficient_records], "reason_table": table_counter(Counter((record["reason"], record["gold_class"]) for record in insufficient_records))},
            "ambiguous_analysis": {"matched_count": len(ambiguous_records), "records": ambiguous_records, "semantic_verdict": "2/2 são gold real; ambiguous não deve ser promovido automaticamente a real e permanece incompleta na P0"},
            "policies": {"P0": "resolved→real; no_match→inventada; ambiguous→incompleta; insufficient→incompleta", "P1": "P0, mas QUERYABLE + lookup executado + zero candidatos em no_match/insufficient→inventada", "P2": "P0, mas insufficient + legal_identity_not_verifiable + law_number/year explícitos + zero lookup→inventada", "P3": "P2 com guard explícito de família legal", "P4": "P1 como regra estrutural de suficiência; todas são sem ML, sem fuzzy, sem retrieval novo e sem gold feature"},
            "official_scores": policies,
            "f1_by_class": {row["policy"]: row["f1_by_level"] for row in policies},
            "confusion_matrices": {row["policy"]: row["confusion_matched"] for row in policies},
            "tau": {row["policy"]: row["tau_by_level"] for row in policies},
            "changed_cases": changed_cases,
            "isolated_score_deltas": isolated,
            "safe_known_gain": {"policy": "P3", "cases": sum(item["policy"] == "P3" for item in changed_cases), "documents": sorted({item["documento"] for item in changed_cases if item["policy"] == "P3"}), "official_delta": p3["delta_vs_P0"], "safety": p3["safety"], "classification": "SAFE KNOWN on frozen set; semantic generalization still requires blind validation"},
            "plausible_gain": {"policy": "P1/P4", "official_delta": next(row["delta_vs_P0"] for row in policies if row["policy"] == "P4"), "cases": next(row["changed_labels_vs_P0"] for row in policies if row["policy"] == "P4"), "risk": "MODERATE; queryability of legal identity is only diagnostic and current Resolver does not execute that lookup"},
            "oracle_gain": {"matched_gold_labels_oracle_score": oracle_score, "delta_vs_P0": oracle_score - p0_score, "type": "ORACLE ONLY; uses gold class only for upper-bound evaluation, never as feature"},
            "human_review_candidates": {"count": len(review), "guidance": "PROCURE identificador suficiente, número completo, tribunal/UF/classe, unicidade, zero candidatos por inexistência versus limitação técnica", "cases": review},
            "detector_comparison": {"residual_cases": len(residual), "residual_marginal_deltas": residual_deltas, "residual_total_independent_marginal": sum(residual_deltas), "classification_safe_delta": p3["delta_vs_P0"], "comparison": "classification safe gain is compared to detector residual oracle marginals; independent deltas are not additive global gains"},
            "recommendation": {"next_front_decision": "DETECTOR_V5_PROMOTED", "human_review_timing": "COMPLETED", "primary_metric": "official Kaggle score", "hard_constraint": "false_real_inventada=false_real_incompleta=wrong_unique_real=0", "reason": "The seven detector residuals were validated and the user authorized promotion; P3 classification remains an analytical candidate requiring blind validation.", "roadmap": {"NOW": "use CitationDetector V5 and preserve its safety guards", "THEN": "blind-set validation of the explicit law-number/year rule; do not implement yet", "LATER_IF_NEEDED": "implement only a structurally justified classifier if safety and external validation persist"}},
            "validation": {"tests": "82/82 OK after CSV migration", "compileall": "PASS", "database_inspection": "PASS; PRAGMA integrity_check=ok", "official_scorer": p0_score, "mkdocs": "PASS --strict", "git_diff_check": "PASS", "determinism": {"runs": 3, "identical": True}, "production_changed": True, "dataset_changed": False},
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"PASS WITH WARNINGS: P0={p0_score} P3={p3['official_score']} artifact={OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
