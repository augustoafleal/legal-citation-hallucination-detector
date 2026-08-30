"""Focused, production-isolated audit of CNJ overlap arbitration.

This module does not change Detector V4, Parser V1 or Resolver V2.  It imports
the previous deep-audit harness only to reproduce its frozen data loading,
matching and experimental ``primary_class_guard`` exactly.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import html
import json
from pathlib import Path
import sys
import time
from typing import Iterable, Mapping, Sequence

import post_detector_v4_deep_audit as deep

from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import CitationCandidate, CitationParser, CitationResolver, ParsedCitation, ResolutionResult
from bracis_jusbrasil.database import connect_database, get_database_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = PROJECT_ROOT / "artifacts" / "cnj_overlap_arbitration_audit.json"
HTML_PATH = PROJECT_ROOT / "artifacts" / "cnj_overlap_arbitration_audit.html"
EXPECTED_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
EXPECTED_DETECTOR = (211, 127, 84, 98, 73)


class AuditError(RuntimeError):
    """Raised when the frozen state or a required audit invariant diverges."""


@dataclass(frozen=True)
class OutputCandidate:
    index: int
    source_indexes: tuple[int, ...]
    document_id: str
    start: int
    end: int
    text: str
    rule: str
    family: str
    parsed: ParsedCitation
    result: ResolutionResult


def as_output(prediction: deep.Prediction) -> OutputCandidate:
    return OutputCandidate(
        prediction.index,
        (prediction.index,),
        prediction.document_id,
        prediction.start,
        prediction.end,
        prediction.text,
        prediction.rule,
        prediction.family,
        prediction.parsed,
        prediction.result,
    )


def spatial_relation(left: OutputCandidate | deep.Prediction, right: OutputCandidate | deep.Prediction) -> str:
    if left.document_id != right.document_id:
        return "disjoint"
    if left.start == right.start and left.end == right.end:
        return "exact"
    if left.end < right.start or right.end < left.start:
        return "adjacent" if left.end == right.start or right.end == left.start else "disjoint"
    if left.start >= right.start and left.end <= right.end:
        return "nested_inside"
    if left.start <= right.start and left.end >= right.end:
        return "contains"
    return "partial_overlap"


def intersects(left: OutputCandidate | deep.Prediction, right: OutputCandidate | deep.Prediction) -> bool:
    return left.document_id == right.document_id and max(left.start, right.start) < min(left.end, right.end)


def context(texts: Mapping[str, str], candidate: OutputCandidate | deep.Prediction, radius: int = 180) -> str:
    text = texts[candidate.document_id]
    return text[max(0, candidate.start - radius) : min(len(text), candidate.end + radius)]


def prediction_dict(candidate: OutputCandidate | deep.Prediction) -> dict[str, object]:
    return {
        "index": candidate.index,
        "source_indexes": list(getattr(candidate, "source_indexes", (candidate.index,))),
        "documento": candidate.document_id,
        "span": [candidate.start, candidate.end],
        "trecho": candidate.text,
        "rule": candidate.rule,
        "family": candidate.family,
        "parsed": deep.parsed_dict(candidate.parsed),
        "resolution": deep.result_dict(candidate.result),
    }


def normalized_digits(value: str) -> str | None:
    tokens = deep.tolerant_numeric_tokens(value)
    return tokens[0] if tokens else None


def runtime_cnj_key(
    prediction: deep.Prediction,
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
) -> dict[str, object] | None:
    if prediction.family != "processo_cnj" or prediction.result.status != "resolved":
        return None
    query = deep.cnj_query_for_prediction(prediction, texts[prediction.document_id], number_index, records)
    record = records.get(prediction.result.id_canonico)
    if record is None or not query.number:
        return None
    return {
        "record_type": record.natureza,
        "primary_number": record.primary_number,
        "normalized_cnj": query.number,
        "tribunal": record.tribunal,
        "process_class": query.class_signature,
        "primary_process_class": record.class_signature,
        "candidate_ids": list(prediction.result.candidate_ids),
        "resolved_id": prediction.result.id_canonico,
    }


def parent_runtime_projection(
    parent: deep.Prediction,
    texts: Mapping[str, str],
) -> dict[str, object]:
    window = texts[parent.document_id][max(0, parent.start - 100) : parent.end]
    return {
        "digits": normalized_digits(parent.text),
        "process_class": deep.class_signature(window),
        "tribunal": deep.inferred_cnj_tribunal(normalized_digits(parent.text), window),
        "family": parent.family,
    }


def eligible_structural_parent(
    child: deep.Prediction,
    parent: deep.Prediction,
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
) -> tuple[bool, dict[str, object]]:
    """Gold-free equivalence test used by the proposed policy.

    It intentionally requires a full resolved primary CNJ in one candidate and
    the same long numeric prefix plus the same primary process class in an
    overlapping companion.  Same ID alone is deliberately insufficient.
    """
    child_key = runtime_cnj_key(child, texts, number_index, records)
    projection = parent_runtime_projection(parent, texts)
    if child_key is None or not intersects(child, parent):
        return False, {"reason": "not_a_resolved_cnj_overlap", "child": child_key, "parent": projection}
    partial = projection["digits"]
    compatible_family = projection["family"] in {
        "processo_ou_recurso_numerado",
        "jurisprudencia_tribunal_contextual",
    }
    same_prefix = bool(partial and len(str(partial)) >= 10 and str(child_key["normalized_cnj"]).startswith(str(partial)))
    same_class = bool(
        child_key["primary_process_class"]
        and projection["process_class"] == child_key["primary_process_class"]
    )
    same_tribunal = not projection["tribunal"] or projection["tribunal"] == child_key["tribunal"]
    eligible = compatible_family and same_prefix and same_class and same_tribunal
    return eligible, {
        "reason": "same_primary_cnj_prefix_and_class" if eligible else "identity_projection_incomplete_or_conflicting",
        "child": child_key,
        "parent": projection,
        "checks": {
            "compatible_family": compatible_family,
            "same_prefix": same_prefix,
            "same_class": same_class,
            "same_tribunal": same_tribunal,
        },
    }


def output_matching(gold: Sequence[deep.GoldRow], outputs: Sequence[OutputCandidate]) -> dict[int, int]:
    pairs: list[tuple[float, int, int]] = []
    by_document: dict[str, list[OutputCandidate]] = defaultdict(list)
    for output in outputs:
        by_document[output.document_id].append(output)
    for item in gold:
        for output in by_document[item.document_id]:
            overlap = deep.iou(item.start, item.end, output.start, output.end)
            if overlap >= 0.5:
                pairs.append((overlap, item.index, output.index))
    selected: dict[int, int] = {}
    used_gold: set[int] = set()
    used_outputs: set[int] = set()
    for overlap, gold_index, output_index in sorted(pairs, key=lambda value: (-value[0], value[1], value[2])):
        if gold_index not in used_gold and output_index not in used_outputs:
            selected[gold_index] = output_index
            used_gold.add(gold_index)
            used_outputs.add(output_index)
    return selected


def output_metrics(gold: Sequence[deep.GoldRow], outputs: Sequence[OutputCandidate]) -> dict[str, object]:
    matches = output_matching(gold, outputs)
    by_index = {output.index: output for output in outputs}
    tp = len(matches)
    resolved_correct = sum(
        item.classification == "real"
        and (output := by_index.get(matches.get(item.index))) is not None
        and output.result.status == "resolved"
        and output.result.id_canonico == item.canonical_id
        for item in gold
    )
    wrong_unique = sum(
        item.classification == "real"
        and (output := by_index.get(matches.get(item.index))) is not None
        and output.result.status == "resolved"
        and output.result.id_canonico != item.canonical_id
        for item in gold
    )
    false_invented = sum(
        item.classification == "inventada"
        and (output := by_index.get(matches.get(item.index))) is not None
        and output.result.status == "resolved"
        for item in gold
    )
    false_incomplete = sum(
        item.classification == "incompleta"
        and (output := by_index.get(matches.get(item.index))) is not None
        and output.result.status == "resolved"
        for item in gold
    )
    exact = sum(
        item.start == by_index[output_index].start and item.end == by_index[output_index].end
        for item, output_index in ((item, matches[item.index]) for item in gold if item.index in matches)
    )
    formal_resolved = sum(
        output.index not in set(matches.values()) and output.result.status == "resolved"
        for output in outputs
    )
    duplicate_resolved: list[int] = []
    for item in gold:
        resolved = [
            output
            for output in outputs
            if output.result.status == "resolved"
            and output.document_id == item.document_id
            and max(item.start, output.start) < min(item.end, output.end)
        ]
        if len(resolved) > 1:
            duplicate_resolved.append(item.index)
    return {
        "outputs": len(outputs),
        "detector_TP": tp,
        "detector_FP": len(outputs) - tp,
        "detector_FN": len(gold) - tp,
        "detector_exact": exact,
        "correct_ids": resolved_correct,
        "wrong_unique_real": wrong_unique,
        "false_real_inventada": false_invented,
        "false_real_incompleta": false_incomplete,
        "formal_fp_resolved": formal_resolved,
        "duplicate_resolved_outputs": len(duplicate_resolved),
        "duplicate_resolved_gold_indexes": duplicate_resolved,
        "matched_output_indexes": sorted(matches.values()),
        "matches": matches,
    }


def overlap_components(predictions: Sequence[deep.Prediction]) -> list[list[int]]:
    by_document: dict[str, list[deep.Prediction]] = defaultdict(list)
    for prediction in predictions:
        by_document[prediction.document_id].append(prediction)
    components: list[list[int]] = []
    for candidates in by_document.values():
        links: dict[int, set[int]] = {candidate.index: set() for candidate in candidates}
        for position, left in enumerate(candidates):
            for right in candidates[position + 1 :]:
                if intersects(left, right):
                    links[left.index].add(right.index)
                    links[right.index].add(left.index)
        visited: set[int] = set()
        for index in sorted(links):
            if index in visited or not links[index]:
                continue
            stack = [index]
            component: list[int] = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                stack.extend(sorted(links[current] - visited, reverse=True))
            components.append(sorted(component))
    return sorted(components, key=lambda component: (component[0], len(component)))


def policy_none(predictions: Sequence[deep.Prediction], parser: CitationParser) -> tuple[list[OutputCandidate], list[dict[str, object]]]:
    return [as_output(prediction) for prediction in predictions], []


def policy_longest(predictions: Sequence[deep.Prediction], parser: CitationParser) -> tuple[list[OutputCandidate], list[dict[str, object]]]:
    by_index = {prediction.index: prediction for prediction in predictions}
    suppressed: set[int] = set()
    operations: list[dict[str, object]] = []
    for component in overlap_components(predictions):
        winner = max(component, key=lambda index: (by_index[index].end - by_index[index].start, -index))
        losers = tuple(index for index in component if index != winner)
        suppressed.update(losers)
        operations.append({"survivor": winner, "suppressed": list(losers), "reason": "longest_span"})
    return [as_output(prediction) for prediction in predictions if prediction.index not in suppressed], operations


def policy_resolved_wins(predictions: Sequence[deep.Prediction], parser: CitationParser) -> tuple[list[OutputCandidate], list[dict[str, object]]]:
    by_index = {prediction.index: prediction for prediction in predictions}
    suppressed: set[int] = set()
    operations: list[dict[str, object]] = []
    for component in overlap_components(predictions):
        resolved = [index for index in component if by_index[index].result.status == "resolved"]
        if len(resolved) != 1:
            continue
        winner = resolved[0]
        losers = tuple(index for index in component if index != winner)
        suppressed.update(losers)
        operations.append({"survivor": winner, "suppressed": list(losers), "reason": "only_resolved_candidate_wins"})
    return [as_output(prediction) for prediction in predictions if prediction.index not in suppressed], operations


def policy_same_resolved_id(predictions: Sequence[deep.Prediction], parser: CitationParser) -> tuple[list[OutputCandidate], list[dict[str, object]]]:
    by_index = {prediction.index: prediction for prediction in predictions}
    suppressed: set[int] = set()
    operations: list[dict[str, object]] = []
    for component in overlap_components(predictions):
        resolved = [
            index for index in component if by_index[index].result.status == "resolved" and by_index[index].result.id_canonico is not None
        ]
        ids = {by_index[index].result.id_canonico for index in resolved}
        if len(resolved) < 2 or len(ids) != 1:
            continue
        winner = max(resolved, key=lambda index: (by_index[index].end - by_index[index].start, -index))
        losers = tuple(index for index in resolved if index != winner)
        suppressed.update(losers)
        operations.append({"survivor": winner, "suppressed": list(losers), "reason": "same_resolved_identity"})
    return [as_output(prediction) for prediction in predictions if prediction.index not in suppressed], operations


def policy_structural_cnj_merge(
    predictions: Sequence[deep.Prediction],
    parser: CitationParser,
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
) -> tuple[list[OutputCandidate], list[dict[str, object]]]:
    """Fuse one safe primary-CNJ child with one compatible overlapping surface."""
    by_index = {prediction.index: prediction for prediction in predictions}
    used: set[int] = set()
    fused: list[OutputCandidate] = []
    operations: list[dict[str, object]] = []
    next_index = len(predictions)
    for child in sorted(predictions, key=lambda prediction: prediction.index):
        if child.index in used or runtime_cnj_key(child, texts, number_index, records) is None:
            continue
        candidates: list[tuple[deep.Prediction, dict[str, object]]] = []
        for parent in predictions:
            if parent.index == child.index or parent.index in used:
                continue
            eligible, evidence = eligible_structural_parent(child, parent, texts, number_index, records)
            if eligible:
                candidates.append((parent, evidence))
        if len(candidates) != 1:
            if candidates:
                operations.append(
                    {
                        "survivor": child.index,
                        "suppressed": [],
                        "reason": "no_safe_arbitration",
                        "candidate_parents": [parent.index for parent, _ in candidates],
                    }
                )
            continue
        parent, evidence = candidates[0]
        start, end = min(child.start, parent.start), max(child.end, parent.end)
        text = texts[child.document_id][start:end]
        parsed = parser.parse(CitationCandidate(0, len(text), text, "arbitrated_structural_merge", "processo_cnj"))
        fused.append(
            OutputCandidate(
                next_index,
                tuple(sorted((child.index, parent.index))),
                child.document_id,
                start,
                end,
                text,
                "arbitrated_structural_merge",
                "processo_cnj",
                parsed,
                child.result,
            )
        )
        operations.append(
            {
                "survivor": next_index,
                "suppressed": [parent.index, child.index],
                "reason": "same_identity_more_complete",
                "evidence": evidence,
            }
        )
        next_index += 1
        used.update({parent.index, child.index})
    outputs = [as_output(prediction) for prediction in predictions if prediction.index not in used] + fused
    return sorted(outputs, key=lambda output: output.index), operations


def policy_runner(
    name: str,
    predictions: Sequence[deep.Prediction],
    parser: CitationParser,
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
) -> tuple[list[OutputCandidate], list[dict[str, object]]]:
    if name == "none":
        return policy_none(predictions, parser)
    if name == "longest_span":
        return policy_longest(predictions, parser)
    if name == "resolved_identity_wins":
        return policy_resolved_wins(predictions, parser)
    if name == "same_resolved_id":
        return policy_same_resolved_id(predictions, parser)
    if name == "structural_cnj_union_merge":
        return policy_structural_cnj_merge(predictions, parser, texts, number_index, records)
    raise ValueError(name)


def gold_overlap_diagnostic(
    candidate: deep.Prediction,
    gold: Sequence[deep.GoldRow],
) -> list[dict[str, object]]:
    return [
        {
            "gold_index": item.index,
            "classification": item.classification,
            "gold_id": item.canonical_id,
            "span": [item.start, item.end],
            "iou": round(deep.iou(item.start, item.end, candidate.start, candidate.end), 6),
        }
        for item in gold
        if item.document_id == candidate.document_id and max(item.start, candidate.start) < min(item.end, candidate.end)
    ]


def pair_semantics(
    left: deep.Prediction,
    right: deep.Prediction,
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
    gold: Sequence[deep.GoldRow],
) -> tuple[str, dict[str, object]]:
    left_parent, left_evidence = eligible_structural_parent(left, right, texts, number_index, records)
    right_parent, right_evidence = eligible_structural_parent(right, left, texts, number_index, records)
    if left_parent or right_parent:
        return "same_reference_different_surface", left_evidence if left_parent else right_evidence
    if (
        left.result.status == "resolved"
        and right.result.status == "resolved"
        and left.result.id_canonico == right.result.id_canonico
    ):
        return "same_reference_same_identity", {"same_resolved_id": left.result.id_canonico}
    left_gold = {item["gold_index"] for item in gold_overlap_diagnostic(left, gold)}
    right_gold = {item["gold_index"] for item in gold_overlap_diagnostic(right, gold)}
    if left_gold and right_gold and left_gold.isdisjoint(right_gold):
        return "genuinely_distinct_references", {"left_gold_indexes": sorted(left_gold), "right_gold_indexes": sorted(right_gold)}
    if spatial_relation(left, right) in {"nested_inside", "contains"}:
        return "parent_child_reference", {"relation": spatial_relation(left, right)}
    return "unclear", {"left_runtime": left_evidence, "right_runtime": right_evidence}


def all_overlap_inventory(
    predictions: Sequence[deep.Prediction],
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
    gold: Sequence[deep.GoldRow],
) -> list[dict[str, object]]:
    overlaps: list[dict[str, object]] = []
    for position, left in enumerate(predictions):
        for right in predictions[position + 1 :]:
            if not intersects(left, right):
                continue
            semantics, evidence = pair_semantics(left, right, texts, number_index, records, gold)
            overlaps.append(
                {
                    "left": prediction_dict(left),
                    "right": prediction_dict(right),
                    "relation_left_to_right": spatial_relation(left, right),
                    "relation_right_to_left": spatial_relation(right, left),
                    "same_family": left.family == right.family,
                    "semantic_relation_audit": semantics,
                    "runtime_equivalence_evidence": evidence,
                    "gold_only_left": gold_overlap_diagnostic(left, gold),
                    "gold_only_right": gold_overlap_diagnostic(right, gold),
                }
            )
    return sorted(overlaps, key=lambda row: (row["left"]["index"], row["right"]["index"]))


def target_cases(
    predictions: Sequence[deep.Prediction],
    gold: Sequence[deep.GoldRow],
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
) -> list[dict[str, object]]:
    matches = deep.match_predictions(gold, predictions)
    matched_predictions = set(matches.values())
    targets: list[dict[str, object]] = []
    for candidate in predictions:
        if (
            candidate.index in matched_predictions
            or candidate.family != "processo_cnj"
            or candidate.result.status != "resolved"
            or candidate.result.id_canonico is None
        ):
            continue
        nearby_gold = [
            item
            for item in gold
            if item.classification == "real"
            and item.canonical_id == candidate.result.id_canonico
            and item.document_id == candidate.document_id
            and max(item.start, candidate.start) < min(item.end, candidate.end)
        ]
        if not nearby_gold:
            continue
        item = max(nearby_gold, key=lambda row: deep.iou(row.start, row.end, candidate.start, candidate.end))
        peers = [other for other in predictions if other.index != candidate.index and intersects(candidate, other)]
        peer_rows = []
        semantic_duplicates = 0
        for peer in peers:
            semantic, evidence = pair_semantics(candidate, peer, texts, number_index, records, gold)
            semantic_duplicates += semantic == "same_reference_different_surface"
            peer_rows.append(
                {
                    "candidate": prediction_dict(peer),
                    "relation": spatial_relation(candidate, peer),
                    "semantic_relation_audit": semantic,
                    "runtime_evidence": evidence,
                }
            )
        nature = (
            "semantically_duplicate" if semantic_duplicates else "matching_artifact"
        )
        targets.append(
            {
                "documento": candidate.document_id,
                "nivel": item.level,
                "candidate": prediction_dict(candidate),
                "cnj_runtime_key": runtime_cnj_key(candidate, texts, number_index, records),
                "gold_only": {
                    "gold_index": item.index,
                    "gold_gid": item.gid,
                    "span": [item.start, item.end],
                    "trecho": item.text,
                    "family": deep.surface_family(item.text, item.citation_type),
                    "gold_id": item.canonical_id,
                    "iou": round(deep.iou(item.start, item.end, candidate.start, candidate.end), 6),
                    "spatial_relation": spatial_relation(candidate, item),
                },
                "overlapping_v4_candidates": peer_rows,
                "matching_artifact_classification": nature,
                "context_200": context(texts, candidate),
            }
        )
    return sorted(targets, key=lambda row: int(row["candidate"]["index"]))


def policy_audit(
    name: str,
    predictions: Sequence[deep.Prediction],
    gold: Sequence[deep.GoldRow],
    targets: Sequence[dict[str, object]],
    parser: CitationParser,
    texts: Mapping[str, str],
    number_index: Mapping[str, tuple[int, ...]],
    records: Mapping[int, deep.CorpusRecord],
) -> dict[str, object]:
    original_outputs = [as_output(prediction) for prediction in predictions]
    original_metrics = output_metrics(gold, original_outputs)
    outputs, operations = policy_runner(name, predictions, parser, texts, number_index, records)
    metrics = output_metrics(gold, outputs)
    original_matches = {int(key): int(value) for key, value in original_metrics["matches"].items()}
    new_matches = {int(key): int(value) for key, value in metrics["matches"].items()}
    lost_gold = sorted(index for index in original_matches if index not in new_matches)
    source_to_operation: dict[int, dict[str, object]] = {}
    for operation in operations:
        for source in operation.get("suppressed", []):
            source_to_operation[int(source)] = operation
    target_decisions: list[dict[str, object]] = []
    for target in targets:
        target_index = int(target["candidate"]["index"])
        operation = source_to_operation.get(target_index)
        peers = target["overlapping_v4_candidates"]
        if operation and operation["reason"] == "same_identity_more_complete":
            decision = "merged_into_structural_union"
            handled = True
        elif not peers:
            decision = "preserved_singleton_matching_artifact"
            handled = True
        elif operation:
            decision = str(operation["reason"])
            handled = False
        else:
            decision = "overlap_preserved_no_safe_arbitration"
            handled = False
        target_decisions.append(
            {
                "candidate_index": target_index,
                "decision": decision,
                "handled": handled,
                "operation": operation,
            }
        )
    ambiguous_suppressed = sorted(
        source
        for source in source_to_operation
        if predictions[source].result.status == "ambiguous"
    )
    no_match_changed = sorted(
        source
        for source in source_to_operation
        if predictions[source].family == "processo_cnj" and predictions[source].result.status == "no_match"
    )
    target_handled = sum(bool(row["handled"]) for row in target_decisions)
    no_regression = not lost_gold and not ambiguous_suppressed and not no_match_changed
    original_by_index = {output.index: output for output in original_outputs}
    output_by_index = {output.index: output for output in outputs}
    suppression_audit: list[dict[str, object]] = []
    for operation in operations:
        sources = [int(source) for source in operation.get("suppressed", [])]
        if not sources:
            continue
        source_gold = {
            source: gold_overlap_diagnostic(original_by_index[source], gold)
            for source in sources
        }
        survivor = output_by_index.get(int(operation["survivor"]))
        survivor_gold = [] if survivor is None else [
            {
                "gold_index": item.index,
                "classification": item.classification,
                "gold_id": item.canonical_id,
                "span": [item.start, item.end],
                "iou": round(deep.iou(item.start, item.end, survivor.start, survivor.end), 6),
            }
            for item in gold
            if item.document_id == survivor.document_id
            and max(item.start, survivor.start) < min(item.end, survivor.end)
        ]
        source_matched_gold = sorted(
            gold_index for gold_index, source in original_matches.items() if source in sources
        )
        suppression_audit.append(
            {
                "operation": operation,
                "source_gold_overlap": source_gold,
                "survivor_gold_overlap": survivor_gold,
                "source_formally_matched_gold_indexes": source_matched_gold,
                "unique_evidence_lost": any(index in lost_gold for index in source_matched_gold),
            }
        )
    safe = (
        target_handled == len(targets)
        and metrics["correct_ids"] >= 55
        and metrics["wrong_unique_real"] == 0
        and metrics["false_real_inventada"] == 0
        and metrics["false_real_incompleta"] == 0
        and metrics["duplicate_resolved_outputs"] == 0
        and no_regression
    )
    return {
        "policy": name,
        "operations": operations,
        "metrics": metrics,
        "target_decisions": target_decisions,
        "target_six_handled": target_handled,
        "regression_audit": {
            "lost_formally_matched_gold_indexes": lost_gold,
            "ambiguous_candidates_suppressed": ambiguous_suppressed,
            "cnj_no_match_candidates_changed": no_match_changed,
            "suppressed_candidate_audit": suppression_audit,
            "safe": no_regression,
        },
        "safe_by_required_criteria": safe,
        "output_delta": metrics["outputs"] - original_metrics["outputs"],
    }


def family_matrix(overlaps: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for overlap in overlaps:
        left = str(overlap["left"]["family"])
        right = str(overlap["right"]["family"])
        key = tuple(sorted((left, right)))
        counts[key]["overlaps"] += 1
        counts[key]["same_identity"] += overlap["semantic_relation_audit"] in {
            "same_reference_same_identity",
            "same_reference_different_surface",
        }
        counts[key]["distinct"] += overlap["semantic_relation_audit"] == "genuinely_distinct_references"
    return [
        {
            "family_a": key[0],
            "family_b": key[1],
            "overlaps": value["overlaps"],
            "same_identity": value["same_identity"],
            "distinct": value["distinct"],
        }
        for key, value in sorted(counts.items())
    ]


def overlap_gold_class_summary(overlaps: Sequence[dict[str, object]]) -> dict[str, int]:
    counts = Counter()
    for overlap in overlaps:
        classes = {
            str(item["classification"])
            for item in [*overlap["gold_only_left"], *overlap["gold_only_right"]]
        }
        for classification in classes:
            counts[classification] += 1
    return {classification: counts[classification] for classification in ("real", "inventada", "incompleta")}


def affected_human_cases(
    targets: Sequence[dict[str, object]],
    overlaps: Sequence[dict[str, object]],
    recommended: Mapping[str, object],
    predictions: Sequence[deep.Prediction],
    texts: Mapping[str, str],
) -> list[dict[str, object]]:
    affected_sources = {
        source
        for operation in recommended["operations"]
        for source in operation.get("suppressed", [])
    }
    cases: list[dict[str, object]] = [
        {"kind": "target", "data": target} for target in targets
    ]
    for overlap in overlaps:
        indexes = {int(overlap["left"]["index"]), int(overlap["right"]["index"])}
        if indexes & affected_sources:
            cases.append({"kind": "policy_affected_overlap", "data": overlap})
    for prediction in predictions:
        if prediction.family == "processo_cnj" and prediction.result.status == "ambiguous":
            cases.append(
                {
                    "kind": "ambiguous_preserved",
                    "data": {
                        "candidate": prediction_dict(prediction),
                        "context_200": context(texts, prediction),
                        "reason": "ambiguity_preserved",
                    },
                }
            )
    seen: set[str] = set()
    unique: list[dict[str, object]] = []
    for case in cases:
        key = json.dumps(case, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(case)
    return unique


def build_payload() -> tuple[dict[str, object], dict[str, float]]:
    started = time.perf_counter()
    texts = deep.load_texts()
    gold = deep.load_gold()
    if (len(texts), len(gold)) != (26, 225):
        raise AuditError("dataset divergence")
    database_path = get_database_path()
    digest = hashlib.sha256(database_path.read_bytes()).hexdigest()
    if digest != EXPECTED_HASH:
        raise AuditError(f"database hash divergence: {digest}")
    with connect_database(database_path, read_only=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise AuditError(f"database integrity divergence: {integrity}")
        case_index = build_case_index(connection)
        resolver = CitationResolver(case_index=case_index, connection=connection)
        records = deep.corpus_records(connection)
        parser = CitationParser()
        number_index = deep.primary_number_index(case_index)
        predictions, pipeline_seconds = deep.run_pipeline(texts, resolver)
        evaluations = deep.evaluate(gold, texts, predictions, resolver)
        detector = deep.detector_metrics(gold, predictions, evaluations)
        observed = tuple(int(detector[key]) for key in ("predictions", "TP", "FP", "FN", "exact"))
        if observed != EXPECTED_DETECTOR:
            raise AuditError(f"Detector V4 divergence: {observed}")
        baseline_safety = deep.safe_metrics(evaluations)
        if baseline_safety != {
            "correct": 41,
            "wrong_unique_real": 0,
            "false_real_inventada": 0,
            "false_real_incompleta": 0,
        }:
            raise AuditError(f"E2E baseline divergence: {baseline_safety}")
        guarded = deep.apply_cnj_candidate_strategy(
            "primary_class_guard", predictions, texts, number_index, records
        )
        guarded_evaluations = deep.evaluate(gold, texts, guarded, resolver)
        guarded_safety = deep.safe_metrics(guarded_evaluations)
        if guarded_safety != {
            "correct": 55,
            "wrong_unique_real": 0,
            "false_real_inventada": 0,
            "false_real_incompleta": 0,
        }:
            raise AuditError(f"primary_class_guard divergence: {guarded_safety}")

        strict_cnj = [
            item
            for item in gold
            if deep.surface_family(texts[item.document_id][item.start : item.end], item.citation_type) == "processo_cnj"
        ]
        _, strict_queries, strict_strategy_ids = deep.cnj_strategy_matrix(
            strict_cnj, texts, parser, number_index, records
        )
        exact_wrong = [
            item
            for item in strict_cnj
            if item.classification == "real"
            and len(strict_strategy_ids["cnj_exact"][item.index]) == 1
            and strict_strategy_ids["cnj_exact"][item.index][0] != item.canonical_id
        ]
        if len(exact_wrong) != 1:
            raise AuditError(f"CNJ class-conflict count divergence: {len(exact_wrong)}")
        conflict_item = exact_wrong[0]
        conflict_query = strict_queries[conflict_item.index]
        conflict_primary_id = strict_strategy_ids["cnj_exact"][conflict_item.index][0]
        conflict_primary = records[conflict_primary_id]
        if strict_strategy_ids["primary_class_guard"][conflict_item.index]:
            raise AuditError("primary class guard did not abstain in known class conflict")
        ambiguous_cnj = [
            item
            for item in strict_cnj
            if item.classification == "real" and len(strict_strategy_ids["primary_class_guard"][item.index]) > 1
        ]
        if len(ambiguous_cnj) != 2:
            raise AuditError(f"TST ambiguity count divergence: {len(ambiguous_cnj)}")

        targets = target_cases(guarded, gold, texts, number_index, records)
        if len(targets) != 6:
            raise AuditError(f"target formal CNJ FP count divergence: {len(targets)}")
        overlaps = all_overlap_inventory(guarded, texts, number_index, records, gold)
        policies = {
            name: policy_audit(name, guarded, gold, targets, parser, texts, number_index, records)
            for name in (
                "none",
                "longest_span",
                "resolved_identity_wins",
                "same_resolved_id",
                "structural_cnj_union_merge",
            )
        }
        recommended = policies["structural_cnj_union_merge"]
        decision = (
            "SAFE_ARBITRATION_EXISTS"
            if recommended["safe_by_required_criteria"]
            else "SAFE_ARBITRATION_NOT_ESTABLISHED"
        )
        human_cases = affected_human_cases(targets, overlaps, recommended, guarded, texts)
        overlap_counts = Counter(
            "nested"
            if row["relation_left_to_right"] in {"nested_inside", "contains"}
            else "partial_overlap"
            if row["relation_left_to_right"] == "partial_overlap"
            else row["relation_left_to_right"]
            for row in overlaps
        )
        all_cross_family = sum(not bool(row["same_family"]) for row in overlaps)
        cnj_cross_family = sum(
            "processo_cnj" in {str(row["left"]["family"]), str(row["right"]["family"])}
            and not bool(row["same_family"])
            for row in overlaps
        )
        current_metrics = output_metrics(gold, [as_output(prediction) for prediction in predictions])
        guard_metrics = output_metrics(gold, [as_output(prediction) for prediction in guarded])
        payload: dict[str, object] = {
            "status": (
                "PASS WITH WARNINGS"
                if decision == "SAFE_ARBITRATION_EXISTS" and recommended["metrics"]["formal_fp_resolved"]
                else "PASS"
                if decision == "SAFE_ARBITRATION_EXISTS"
                else "PASS WITH WARNINGS"
            ),
            "baseline": {
                "detector_v4": detector,
                "end_to_end": baseline_safety,
                "database_sha256": digest,
                "integrity_check": integrity,
            },
            "target_cases": targets,
            "all_overlaps": {
                "total_pairs": len(overlaps),
                "relation_counts": dict(sorted(overlap_counts.items())),
                "cross_family_pairs": cnj_cross_family,
                "all_cross_family_pairs": all_cross_family,
                "same_family_pairs": len(overlaps) - all_cross_family,
                "family_matrix": family_matrix(overlaps),
                "gold_class_pair_diagnostics": overlap_gold_class_summary(overlaps),
                "pairs": overlaps,
            },
            "cnj_strategy": {
                "name": "primary_class_guard",
                "correct_ids": guarded_safety["correct"],
                "gain": guarded_safety["correct"] - baseline_safety["correct"],
                "safety": guarded_safety,
                "body_fallback_used": False,
                "class_alias_expansion_used": False,
                "class_conflict": {
                    "gold_index": conflict_item.index,
                    "cited_class": conflict_query.class_signature,
                    "primary_class": conflict_primary.class_signature,
                    "primary_exact_id": conflict_primary_id,
                    "guarded_candidate_ids": list(strict_strategy_ids["primary_class_guard"][conflict_item.index]),
                    "behavior": "abstain",
                },
                "tst_ambiguous_preserved": [
                    {
                        "gold_index": item.index,
                        "candidate_ids": list(strict_strategy_ids["primary_class_guard"][item.index]),
                    }
                    for item in ambiguous_cnj
                ],
            },
            "policies": policies,
            "policy_metrics": [
                {
                    "policy": name,
                    "mechanism": {
                        "none": "no arbitration",
                        "longest_span": "one longest candidate per overlap component",
                        "resolved_identity_wins": "one resolved candidate suppresses all overlapping candidates",
                        "same_resolved_id": "deduplicate only already-equal resolved IDs",
                        "structural_cnj_union_merge": "fuse a resolved primary CNJ with one overlapping compatible partial surface",
                    }[name],
                    "target_six_handled": result["target_six_handled"],
                    "regressions": len(result["regression_audit"]["lost_formally_matched_gold_indexes"]),
                    "complexity": {
                        "none": "LOW",
                        "longest_span": "LOW",
                        "resolved_identity_wins": "LOW",
                        "same_resolved_id": "LOW",
                        "structural_cnj_union_merge": "MODERATE",
                    }[name],
                    "risk": "LOW-MODERATE" if result["safe_by_required_criteria"] else "HIGH",
                    "safe": result["safe_by_required_criteria"],
                }
                for name, result in policies.items()
            ],
            "recommended_policy": {
                "name": "structural_cnj_union_merge",
                "runtime_rule": (
                    "Only fuse exactly one resolved CNJ primary candidate with exactly one overlapping "
                    "process/context candidate when its longest numeric token is a >=10-digit prefix of the "
                    "CNJ, its runtime process class equals the primary-record class, and tribunal is compatible."
                ),
                "survivor": "synthetic union span carrying the resolved CNJ identity; both source surfaces are compacted",
                "abstention": "preserve all candidates whenever zero or multiple compatible parents exist, class conflicts, or numeric projection is incomplete",
                "decision": decision,
                "result": recommended,
                "e2e_minimum_preserved": recommended["metrics"]["correct_ids"] >= guarded_safety["correct"],
                "additional_gold_matched_ids_from_union_span": (
                    recommended["metrics"]["correct_ids"] - guarded_safety["correct"]
                ),
            },
            "end_to_end_simulation": {
                "current": current_metrics,
                "cnj_before_arbitration": guard_metrics,
                "cnj_plus_arbitration": recommended["metrics"],
            },
            "generalization": {
                "assessment": "MODERATE" if recommended["safe_by_required_criteria"] else "LOW",
                "does_not_use_gold": True,
                "does_not_use_document_or_id_hardcode": True,
                "does_not_use_body_fallback": True,
                "requires_no_new_class_alias": True,
                "why": [
                    "requires exact runtime primary identity rather than a score or threshold",
                    "requires a long observed numeric prefix and an equal primary process class",
                    "requires exactly one compatible companion; otherwise abstains",
                    "was tested on every V4 character-overlap pair, including invented and incomplete gold diagnostics",
                ],
            },
            "human_review_cases": human_cases,
            "recommendation": {
                "decision": decision,
                "now": "A future Resolver V3 task may implement the primary CNJ identity + class guard followed by structural CNJ union merge.",
                "invariants": [
                    "no body-mention fallback",
                    "no class-alias expansion",
                    "never collapse an ambiguous or no-match CNJ",
                    "merge only one-to-one runtime-equivalent structural pairs",
                    "abstain and preserve candidates on every conflict or multiplicity",
                    "preserve 55/96, safety 0/0/0, and zero duplicate resolved outputs",
                ],
            },
            "determinism": {"required_runs": 3, "verified": False},
        }
    return payload, {
        "audit_seconds": time.perf_counter() - started,
        "pipeline_seconds": pipeline_seconds,
    }


def html_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def write_html(payload: Mapping[str, object]) -> None:
    cases = payload["human_review_cases"]
    cards = []
    for position, case in enumerate(cases, 1):
        cards.append(
            "<article>"
            f"<h2>{position}. {html.escape(str(case['kind']))}</h2>"
            f"<pre>{html.escape(json.dumps(case['data'], ensure_ascii=False, indent=2))}</pre>"
            "</article>"
        )
    policy_rows = payload["policy_metrics"]
    document = f"""<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">
<title>CNJ overlap arbitration audit</title><style>
body{{font:15px/1.45 system-ui,sans-serif;margin:2rem auto;max-width:1180px;padding:0 1rem;color:#18202a}}
article{{border:1px solid #ccd4df;border-radius:9px;padding:1rem;margin:1rem 0}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:.75rem;border-radius:6px}}
table{{border-collapse:collapse}}th,td{{border:1px solid #ccd4df;padding:.45rem .65rem;text-align:left}}th{{background:#edf2f7}}
</style></head><body><h1>CNJ overlap arbitration audit</h1>
<p>Decisão: <strong>{html.escape(str(payload['recommended_policy']['decision']))}</strong>. Gold aparece apenas como diagnóstico.</p>
{html_table(['policy', 'six handled', 'regressions', 'complexity', 'risk', 'safe'], ((row['policy'], row['target_six_handled'], row['regressions'], row['complexity'], row['risk'], row['safe']) for row in policy_rows))}
{''.join(cards)}</body></html>"""
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(document, encoding="utf-8")


def print_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    values = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in values:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in values:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_summary(payload: Mapping[str, object], performance: Mapping[str, float]) -> None:
    baseline = payload["baseline"]
    detector = baseline["detector_v4"]
    safety = baseline["end_to_end"]
    print("=== BASELINE ===")
    print(f"V4={detector['predictions']}/{detector['TP']}/{detector['FP']}/{detector['FN']}; exact={detector['exact']}")
    print(f"E2E={safety['correct']}/96; safety={safety['wrong_unique_real']}/{safety['false_real_inventada']}/{safety['false_real_incompleta']}")
    print("\n=== 6 TARGET CNJ OVERLAPS ===")
    print_table(
        ["doc", "level", "family", "gold relation", "resolved ID", "gold ID"],
        (
            (row["documento"], row["nivel"], row["candidate"]["family"], row["gold_only"]["spatial_relation"], row["candidate"]["resolution"]["id_canonico"], row["gold_only"]["gold_id"])
            for row in payload["target_cases"]
        ),
    )
    print("\n=== ALL V4 OVERLAPS ===")
    overlaps = payload["all_overlaps"]
    print(f"pairs={overlaps['total_pairs']}; relations={overlaps['relation_counts']}; CNJ cross-family={overlaps['cross_family_pairs']}; all cross-family={overlaps['all_cross_family_pairs']}; same-family={overlaps['same_family_pairs']}")
    print_table(["family A", "family B", "overlaps", "same identity", "distinct"], ((row["family_a"], row["family_b"], row["overlaps"], row["same_identity"], row["distinct"]) for row in overlaps["family_matrix"]))
    print("\n=== CANDIDATE EQUIVALENCE ===")
    print("Runtime equivalence requires resolved primary CNJ + overlapping companion + >=10-digit CNJ prefix + equal primary process class + compatible tribunal; otherwise abstain.")
    print("\n=== ARBITRATION POLICIES ===")
    print_table(["policy", "target six", "regressions", "complexity", "risk", "safe"], ((row["policy"], row["target_six_handled"], row["regressions"], row["complexity"], row["risk"], row["safe"]) for row in payload["policy_metrics"]))
    print("\n=== COUNTEREXAMPLES ===")
    for row in payload["policy_metrics"]:
        if not row["safe"] and row["policy"] != "none":
            print(f"{row['policy']}: rejected (regressions={row['regressions']} or target coverage/safety insufficient)")
    print("\n=== E2E SIMULATION ===")
    simulation = payload["end_to_end_simulation"]
    print_table(["metric", "current", "CNJ before arbitration", "CNJ + arbitration"], ((key, simulation["current"][key], simulation["cnj_before_arbitration"][key], simulation["cnj_plus_arbitration"][key]) for key in ("correct_ids", "wrong_unique_real", "false_real_inventada", "false_real_incompleta", "duplicate_resolved_outputs", "formal_fp_resolved", "outputs")))
    print("\n=== SAFETY ===")
    result = payload["recommended_policy"]["result"]
    regression = result["regression_audit"]
    print(
        f"target handled={result['target_six_handled']}/6; "
        f"lost matched={len(regression['lost_formally_matched_gold_indexes'])}; "
        f"ambiguous suppressed={len(regression['ambiguous_candidates_suppressed'])}; "
        f"CNJ no-match changed={len(regression['cnj_no_match_candidates_changed'])}; "
        f"safe={result['safe_by_required_criteria']}"
    )
    print("\n=== GENERALIZATION ===")
    print(f"assessment={payload['generalization']['assessment']}; no-gold={payload['generalization']['does_not_use_gold']}; abstention required=true")
    print("\n=== RECOMMENDATION ===")
    print(payload["recommendation"]["decision"])
    print(payload["recommendation"]["now"])
    print(f"human review={HTML_PATH.relative_to(PROJECT_ROOT)} ({len(payload['human_review_cases'])} cases); determinism={payload['determinism']['runs_verified']}/{payload['determinism']['required_runs']}; audit={performance['audit_seconds']:.3f}s")


def main(argv: Sequence[str] | None = None) -> int:
    single_run = "--single-run" in (argv if argv is not None else sys.argv[1:])
    runs = 1 if single_run else 3
    try:
        payloads: list[dict[str, object]] = []
        performance: list[dict[str, float]] = []
        for _ in range(runs):
            payload, timing = build_payload()
            payloads.append(payload)
            performance.append(timing)
        reference = json.dumps(payloads[0], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for payload in payloads[1:]:
            if json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != reference:
                raise AuditError("structured payload is not deterministic")
        payload = payloads[0]
        payload["determinism"] = {
            "required_runs": 3,
            "runs_verified": runs,
            "verified": runs == 3,
            "stable_projection": "entire JSON payload excluding runtime timings",
        }
        JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_html(payload)
        print_summary(payload, performance[0])
        return 0
    except AuditError as exc:
        print(f"AUDIT ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
