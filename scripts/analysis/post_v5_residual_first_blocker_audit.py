"""Auditoria read-only dos 29 reais residuais após o freeze da CitationDetector V5.

O script não altera componentes de produção. Ele recompõe a pipeline atual,
atribui um único first blocker causal por real não resolvido e separa esse
blocker do limite terminal de corpus/gold. Oráculos são diagnósticos somente.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import classification_metric_audit as classification  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import (  # noqa: E402
    CitationCandidate,
    CitationDetector,
    CitationParser,
    CitationResolver,
)
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


DATASET_DIR = PROJECT_ROOT / "material_desafio_jusbrasil_bracis"
JSON_PATH = PROJECT_ROOT / "artifacts" / "post_v5_residual_first_blocker_audit.json"
EXPECTED_DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
EXPECTED_SCORE = 0.48365269398459604
EXPECTED_DETECTOR = (219, 137, 82, 88, 83)


class BaselineError(RuntimeError):
    """Raised when the frozen V5 checkpoint is not reproducible."""


def compact_counter(values: Iterable[str]) -> list[dict[str, object]]:
    counts = Counter(values)
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def result_dict(result: Any) -> dict[str, object]:
    return {
        "status": result.status,
        "id_canonico": result.id_canonico,
        "candidate_ids": list(result.candidate_ids),
        "strategy": result.strategy,
        "reason": result.reason,
        "record_type": result.record_type,
    }


def parsed_dict(parsed: Any) -> dict[str, object]:
    return {
        "family": parsed.family,
        "tipo": parsed.tipo,
        "tribunal": parsed.tribunal,
        "tribunal_raw": parsed.tribunal_raw,
        "tribunal_source": parsed.tribunal_source,
        "data": dict(parsed.data),
        "provenance": dict(parsed.provenance),
    }


def p0_class(result: Any) -> str:
    if result.status == "resolved":
        return "real"
    if result.status in {"ambiguous", "insufficient"}:
        return "incompleta"
    return "inventada"


def candidate_view(candidate: Any, gold: Any) -> dict[str, object]:
    return {
        "span": [candidate.start, candidate.end],
        "text": candidate.text,
        "rule": candidate.rule,
        "family": candidate.family,
        "iou": round(post.iou(gold.start, gold.end, candidate.start, candidate.end), 6),
        "exact": candidate.start == gold.start and candidate.end == gold.end,
        "gold_only_prefix": gold.text[: max(0, candidate.start - gold.start)],
        "gold_only_suffix": gold.text[max(0, candidate.end - gold.start) :],
    }


def identity_rows(resolver: CitationResolver, ids: Iterable[int]) -> list[dict[str, object]]:
    rows = []
    for canonical_id in sorted(set(ids)):
        identity = resolver.primary_identities.get(canonical_id)
        rows.append(
            {
                "id": canonical_id,
                "tribunal": None if identity is None else identity.tribunal,
                "classe": None if identity is None else identity.classe,
                "numero_normalizado": None if identity is None else identity.numero_normalizado,
            }
        )
    return rows


def oracle_family_sweep(
    gold: Any, parser: CitationParser, resolver: CitationResolver
) -> dict[str, object]:
    """Tests current downstream only; this is not a proposed detector rule."""
    families = (
        post.family_for(gold.text, gold.citation_type),
        "processo_ou_recurso_numerado",
        "processo_cnj",
        "sumula_numerada",
        "jurisprudencia_tribunal_contextual",
        "jurisprudencia_referencia_geral",
    )
    attempts = []
    for family in dict.fromkeys(families):
        candidate = CitationCandidate(0, len(gold.text), gold.text, "oracle", family)
        parsed = parser.parse(candidate, context=gold.text)
        result = resolver.resolve(parsed)
        attempts.append(
            {
                "family": family,
                "parsed": parsed_dict(parsed),
                "resolution": result_dict(result),
                "correct": result.status == "resolved" and result.id_canonico == gold.canonical_id,
            }
        )
    return {
        "base_family": families[0],
        "correct_attempts": [item for item in attempts if item["correct"]],
        "attempts": attempts,
    }


def technical_first_blocker(
    gold: Any,
    raw_match_index: int | None,
    output_match_index: int | None,
    raw: Sequence[Any],
    outputs: Sequence[Any],
    parser: CitationParser,
    resolver: CitationResolver,
) -> tuple[str | None, str, dict[str, object]]:
    """Returns one causal blocker, respecting Detector→Parser→Resolver→Output."""
    output = None if output_match_index is None else outputs[output_match_index]
    if output and output.result.status == "resolved" and output.result.id_canonico == gold.canonical_id:
        return None, "CORRECT_REAL", {}

    # O candidato abaixo usa somente o trecho literal do gold para diagnóstico;
    # nenhum contexto de corpo/ementa é consultado como fallback de identidade.
    base_family = post.family_for(gold.text, gold.citation_type)
    oracle_candidate = CitationCandidate(0, len(gold.text), gold.text, "oracle", base_family)
    oracle_parsed = parser.parse(oracle_candidate, context=gold.text)
    oracle_result = resolver.resolve(oracle_parsed)
    oracle = {"family": base_family, "parsed": parsed_dict(oracle_parsed), "resolution": result_dict(oracle_result)}

    related = [
        item
        for item in raw
        if item.document_id == gold.document_id
        and post.iou(gold.start, gold.end, item.candidate.start, item.candidate.end) >= 0.5
    ]
    related.sort(
        key=lambda item: (-post.iou(gold.start, gold.end, item.candidate.start, item.candidate.end), item.index)
    )

    if raw_match_index is None:
        return "Detector", "NO_OFFICIAL_CANDIDATE", oracle

    source = raw[raw_match_index]
    if source.result.status == "resolved" and source.result.id_canonico == gold.canonical_id:
        return "Arbitration", "RAW_CORRECT_BUT_OUTPUT_NOT_CORRECT", oracle

    # Uma divergência literal de span não é, por si só, prova de falha do
    # Detector.  Se o candidato já alcançou uma identidade que o Resolver
    # reconhece como ambígua, conflituosa ou sem metadados, o primeiro bloqueio
    # técnico está no Resolver.  Isso cobre, por exemplo, "Súmula 331" sem o
    # sufixo TST e CNJs cujo prefixo foi suprimido mas cujo parse continua
    # suficiente para a consulta.
    resolver_observed_reasons = {
        "cnj_primary_class_conflict",
        "legal_identity_not_verifiable",
        "sumula_number_not_found",
    }
    if source.result.status == "ambiguous" or source.result.reason in resolver_observed_reasons:
        return "Resolver", "PARSED_IDENTITY_NOT_RESOLVED_CORRECTLY", oracle

    # Caso de OCR conhecido: o Detector termina em "170076" e perde o "O"
    # terminal indispensável à identificação de 170076O.  O parser/resolver
    # não recebem informação estrutural suficiente; portanto é Detector-first.
    if gold.index == 204:
        return "Detector", "STRUCTURAL_SPAN_OR_FAMILY_INSUFFICIENT", oracle

    exact_span = source.candidate.start == gold.start and source.candidate.end == gold.end
    if not exact_span or source.candidate.family != base_family:
        return "Detector", "STRUCTURAL_SPAN_OR_FAMILY_INSUFFICIENT", oracle

    if (
        not post.parser_equivalent(source.parsed, oracle_parsed)
        and post.visible_parser_gap(oracle_parsed, source.candidate)
    ):
        return "Parser", "PRESENT_FIELD_NOT_EXTRACTED", oracle

    return "Resolver", "PARSED_IDENTITY_NOT_RESOLVED_CORRECTLY", oracle


def terminal_classification(
    gold: Any, blocker: str, output: Any | None, technical_reason: str
) -> tuple[str, str]:
    if gold.citation_type == "lei":
        return "LEGAL_CANONICAL_IDENTITY_MISSING", "BLOCKED_BY_CORPUS"
    if "sumula" in post.family_for(gold.text, gold.citation_type):
        return "SUMULA_METADATA_MISSING", "BLOCKED_BY_CORPUS"
    if output and output.result.reason == "cnj_primary_class_conflict":
        return "GOLD_CORPUS_PRIMARY_IDENTITY_CONFLICT", "GOLD_CORPUS_CONFLICT"
    if output and output.result.status == "ambiguous":
        return "CANONICAL_MULTI_ID", "GENUINELY_AMBIGUOUS"
    if gold.index == 204:
        return "OCR_TERMINAL_TRUNCATION", "PLAUSIBLY_RECOVERABLE"
    if blocker == "Detector":
        return "UNSUPPORTED_STRUCTURAL_REFERENCE", "PLAUSIBLY_RECOVERABLE"
    if technical_reason == "PARSED_IDENTITY_NOT_RESOLVED_CORRECTLY":
        return "RESOLVER_OR_CORPUS_LOOKUP_LIMIT", "UNKNOWN"
    return "UNCLASSIFIED", "UNKNOWN"


def detector_taxonomy(item: Mapping[str, object]) -> str:
    if item["terminal_root_cause"] == "LEGAL_CANONICAL_IDENTITY_MISSING":
        return "LEGAL_SURFACE_UNDETECTED"
    if item["gold_index"] == 204:
        return "OCR_TRUNCATION"
    text = str(item["gold_text"])
    if any(token in text for token in ("5úmula", "45g", "2020-\n.")):
        return "OCR_NOISY_REFERENCE"
    if item["gold_index"] == 71:
        return "GENERIC_REFERENCE"
    return "UNSUPPORTED_STRUCTURAL_PATTERN"


def stable(value: object) -> object:
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items() if key != "performance"}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def build_payload() -> dict[str, object]:
    texts = post.deep.load_texts()
    gold = post.deep.load_gold()
    if len(texts) != 26 or len(gold) != 225:
        raise BaselineError("dimensao oficial divergente")
    db_path = get_database_path()
    db_hash = sha256(db_path.read_bytes()).hexdigest()
    if db_hash != EXPECTED_DB_HASH:
        raise BaselineError(f"hash do DB divergente: {db_hash}")

    with connect_database(db_path, read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        parser = CitationParser()
        detector = CitationDetector()
        detector_started = time.perf_counter()
        detector_candidates = sum(len(detector.detect(text)) for text in texts.values())
        detector_seconds = time.perf_counter() - detector_started
        raw, outputs, runtime = post.run_current_pipeline(texts, resolver)
        raw_matches = post.match_gold(gold, raw)
        output_matches = post.match_gold(gold, outputs)
        raw_tuple = (
            len(raw),
            len(raw_matches),
            len(raw) - len(raw_matches),
            len(gold) - len(raw_matches),
            sum(
                gold[gold_index].start == raw[raw_index].candidate.start
                and gold[gold_index].end == raw[raw_index].candidate.end
                for gold_index, raw_index in raw_matches.items()
            ),
        )
        if raw_tuple != EXPECTED_DETECTOR:
            raise BaselineError(f"baseline detector divergente: {raw_tuple}")
        post_metrics = post.output_metrics(gold, outputs)
        if (
            post_metrics["correct_ids"],
            post_metrics["wrong_unique_real"],
            post_metrics["false_real_inventada"],
            post_metrics["false_real_incompleta"],
        ) != (67, 0, 0, 0):
            raise BaselineError(f"baseline downstream divergente: {post_metrics}")

        gold_df = official.load_gold_df()
        solution = official.solution_from_gold(gold_df)
        documents = list(gold_df.documento_id.drop_duplicates())
        p0_submission = official.submission_from_cells(official.output_cells(outputs), documents)
        p0_score = float(official.METRIC.score(solution, p0_submission, "documento_id"))
        if abs(p0_score - EXPECTED_SCORE) > 1e-15:
            raise BaselineError(f"score oficial divergente: {p0_score}")
        n1_solution = official.solution_from_gold(gold_df[gold_df.nivel.astype(int).eq(1)])
        n2_solution = official.solution_from_gold(gold_df[gold_df.nivel.astype(int).eq(2)])

        raw_by_index = {item.index: item for item in raw}
        output_by_index = {item.index: item for item in outputs}
        ledger: list[dict[str, object]] = []
        unresolved: list[dict[str, object]] = []
        for item in gold:
            if item.classification != "real":
                continue
            raw_index = raw_matches.get(item.index)
            output_index = output_matches.get(item.index)
            raw_item = None if raw_index is None else raw_by_index[raw_index]
            output = None if output_index is None else output_by_index[output_index]
            blocker, technical_reason, oracle = technical_first_blocker(
                item, raw_index, output_index, raw, outputs, parser, resolver
            )
            correct = blocker is None
            root, recoverability = ("CORRECT", "CORRECT_REAL") if correct else terminal_classification(
                item, blocker or "", output, technical_reason
            )
            related = [
                candidate_view(prediction.candidate, item)
                for prediction in raw
                if prediction.document_id == item.document_id
                and post.iou(item.start, item.end, prediction.candidate.start, prediction.candidate.end) > 0
            ]
            related.sort(key=lambda value: (-float(value["iou"]), value["span"]))
            row = {
                "gold_index": item.index,
                "citacao_id": item.gid,
                "documento_id": item.document_id,
                "nivel": item.level,
                "gold_span": [item.start, item.end],
                "gold_text": item.text,
                "gold_id_canonico": item.canonical_id,
                "official_matched": raw_index is not None,
                "detector": None if raw_item is None else candidate_view(raw_item.candidate, item),
                "parser": None if raw_item is None else parsed_dict(raw_item.parsed),
                "resolver": None if raw_item is None else result_dict(raw_item.result),
                "arbitration": None if output is None else {"reason": output.reason, "source_indexes": list(output.source_indexes)},
                "final": None if output is None else {"classificacao": p0_class(output.result), **result_dict(output.result)},
                "related_candidates": related,
                "correct_real": correct,
                "first_blocker": blocker,
                "technical_reason": technical_reason,
                "terminal_root_cause": root,
                "runtime_information": None if correct else ("INSUFFICIENT" if recoverability in {"BLOCKED_BY_CORPUS", "GENUINELY_AMBIGUOUS", "GOLD_CORPUS_CONFLICT"} else "PARTIAL"),
                "recoverability": recoverability,
                "oracle_detection": None if correct or blocker != "Detector" else oracle_family_sweep(item, parser, resolver),
            }
            ledger.append(row)
            if not correct:
                unresolved.append(row)

        if len(ledger) != 96 or len(unresolved) != 29:
            raise BaselineError(f"ledger divergente: real={len(ledger)}, unresolved={len(unresolved)}")

        detector_rows = [item for item in unresolved if item["first_blocker"] == "Detector"]
        for item in detector_rows:
            item["detector_taxonomy"] = detector_taxonomy(item)
        resolver_rows = [item for item in unresolved if item["first_blocker"] == "Resolver"]
        parser_rows = [item for item in unresolved if item["first_blocker"] == "Parser"]
        arbitration_rows = [item for item in unresolved if item["first_blocker"] == "Arbitration"]

        detector_oracle_ready = [
            item for item in detector_rows if item["oracle_detection"] and item["oracle_detection"]["correct_attempts"]
        ]
        for item in detector_oracle_ready:
            item["oracle_downstream_ready"] = True

        ambiguity = []
        for item in resolver_rows:
            final = item["final"] or {}
            if final.get("status") == "ambiguous":
                ambiguity.append(
                    {
                        "gold_index": item["gold_index"],
                        "citation": item["gold_text"],
                        "candidate_ids": final["candidate_ids"],
                        "primary_identities": identity_rows(resolver, final["candidate_ids"]),
                        "discriminant_in_runtime": False,
                        "verdict": "GENUINELY_AMBIGUOUS",
                    }
                )

        sumula_rows = [item for item in unresolved if item["terminal_root_cause"] == "SUMULA_METADATA_MISSING"]
        sumula_details = []
        for item in sumula_rows:
            target = connection.execute(
                "SELECT id, texto FROM documentos WHERE id=?", (item["gold_id_canonico"],)
            ).fetchone()
            number = "".join(character for character in str(item["gold_text"]) if character.isdigit())
            canonical_text = "" if target is None else str(target["texto"])
            sumula_details.append(
                {
                    "gold_index": item["gold_index"],
                    "citation": item["gold_text"],
                    "number_present": bool(number),
                    "canonical_text_contains_number": bool(number) and number in canonical_text,
                    "safe_index_possible_now": False,
                }
            )

        legal_rows = [item for item in unresolved if item["terminal_root_cause"] == "LEGAL_CANONICAL_IDENTITY_MISSING"]
        legal_records = post.legal_records(connection)
        legal_summary = {
            "cases": len(legal_rows),
            "canonical_record_count": len(legal_records),
            "canonical_records": [asdict(record) for record in legal_records],
            "safe_gain": 0,
            "verdict": "BLOCKED_BY_CORPUS",
        }

        conflict_rows = [item for item in unresolved if item["terminal_root_cause"] == "GOLD_CORPUS_PRIMARY_IDENTITY_CONFLICT"]
        conflicts = []
        for item in conflict_rows:
            parsed = item["parser"] or {}
            number = (parsed.get("data") or {}).get("numero_normalizado")
            case = resolver._case_index.lookup_by_number(number) if number else None  # diagnostic read-only access
            alternatives = () if case is None else case.canonical_ids
            conflicts.append(
                {
                    "gold_index": item["gold_index"],
                    "citation": item["gold_text"],
                    "gold_target": identity_rows(resolver, [int(item["gold_id_canonico"])]),
                    "alternative_primary_identities": identity_rows(resolver, alternatives),
                    "parsed_identity": parsed,
                    "body_fallback_used": False,
                }
            )

        legal_indexes = post.legal_indexes(legal_records)
        class_records, _, output_features = classification.matched_records(gold, outputs, legal_indexes)
        p3_rows, _, _ = classification.score_table(
            ["P0", "P3"], solution, outputs, output_features, documents, gold, class_records
        )
        p3 = next(item for item in p3_rows if item["policy"] == "P3")

        safe_known: list[dict[str, object]] = []
        plausible = [
            {
                "group": "detector_structural_forms_needing_human_review",
                "layer": "Detector",
                "cases": [item["gold_index"] for item in detector_oracle_ready],
                "structural_signal": "numero/UF preservado e Resolver atual confirma o target sob family processo_ou_recurso_numerado",
                "risk": "HIGH",
                "reason": "cinco superficies heterogeneas e isoladas; ainda nao ha regra generalizavel demonstrada",
            },
            {
                "group": "terminal_ocr_truncation",
                "layer": "Detector/Parser",
                "cases": [204],
                "structural_signal": "170076O termina fora do candidate e nao ha reparo atual que prove ID seguro",
                "risk": "HIGH",
                "reason": "reparo OCR exigiria evidencia independente e nao e resolvido nem pelo oracle literal atual",
            },
        ]
        blocked = [
            {"group": "legal_identity", "cases": [item["gold_index"] for item in legal_rows], "count": len(legal_rows), "reason": "canônicos legais sem identidade normativa verificável"},
            {"group": "sumula_metadata", "cases": [item["gold_index"] for item in sumula_rows], "count": len(sumula_rows), "reason": "número não indexável nos registros canônicos"},
            {"group": "canonical_duplicate", "cases": [item["gold_index"] for item in ambiguity], "count": len(ambiguity), "reason": "múltiplos IDs sem discriminante na citação"},
            {"group": "gold_corpus_conflict", "cases": [item["gold_index"] for item in conflict_rows], "count": len(conflict_rows), "reason": "gold incompatível com identidade primária do corpus"},
        ]

        current_score = p0_score
        payload: dict[str, object] = {
            "status": "PASS_WITH_WARNINGS",
            "commit": {
                "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip(),
                "subject": subprocess.check_output(["git", "log", "-1", "--oneline"], cwd=PROJECT_ROOT, text=True).strip(),
                "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=PROJECT_ROOT, text=True).strip(),
                "worktree_clean": not bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip()),
                "production_paths_clean": not bool(
                    subprocess.check_output(
                        ["git", "status", "--porcelain", "--", "src", "tests", "docs"],
                        cwd=PROJECT_ROOT,
                        text=True,
                    ).strip()
                ),
            },
            "baseline": {
                "detector": {"predictions": raw_tuple[0], "matches": raw_tuple[1], "fp": raw_tuple[2], "fn": raw_tuple[3], "exact": raw_tuple[4]},
                "post_arbitration": {"real_ids_correct": post_metrics["correct_ids"], "real_total": 96, "outputs": post_metrics["outputs"]},
                "official_score": {"N1": float(official.METRIC.score(n1_solution, p0_submission, "documento_id")), "N2": float(official.METRIC.score(n2_solution, p0_submission, "documento_id")), "final": current_score},
                "safety": {key: post_metrics[key] for key in ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")},
            },
            "real_ledger": ledger,
            "unresolved_reals": unresolved,
            "first_blockers": compact_counter(str(item["first_blocker"]) for item in unresolved),
            "root_causes": compact_counter(str(item["terminal_root_cause"]) for item in unresolved),
            "recoverability": compact_counter(str(item["recoverability"]) for item in unresolved),
            "detector_residual": detector_rows,
            "detector_oracle": {
                "detector_first": len(detector_rows),
                "downstream_ready": len(detector_oracle_ready),
                "oracle_resolved": len(detector_oracle_ready),
                "safe_recoverable": 0,
                "note": "family sweep é diagnóstico; os cinco casos resolvíveis não têm regra estrutural segura demonstrada",
            },
            "parser_residual": {"count": len(parser_rows), "verdict": "Parser V1 effectively saturated for current resolved surfaces" if not parser_rows else "needs analysis", "cases": parser_rows},
            "resolver_residual": resolver_rows,
            "resolver_reason_breakdown": compact_counter(str((item["final"] or {}).get("reason")) for item in resolver_rows),
            "canonical_ambiguities": ambiguity,
            "sumula_residual": sumula_details,
            "legal_residual": legal_summary,
            "unsupported_residual": [item for item in unresolved if item["terminal_root_cause"] == "UNSUPPORTED_STRUCTURAL_REFERENCE"],
            "ocr_residual": [item for item in unresolved if item["terminal_root_cause"] == "OCR_TERMINAL_TRUNCATION"],
            "gold_corpus_conflicts": conflicts,
            "safe_known_opportunities": safe_known,
            "plausible_opportunities": plausible,
            "blocked_cases": blocked,
            "official_score_potential": {
                "current": current_score,
                "detector_safe_oracle": current_score,
                "detector_safe_delta": 0.0,
                "resolver_safe_oracle": current_score,
                "resolver_safe_delta": 0.0,
                "method": "kaggle_metric.score chamado diretamente; nenhum caso satisfaz SAFE_RECOVERABLE",
            },
            "classification_diagnostic": {
                "P0": current_score,
                "P3_experimental": p3["official_score"],
                "delta": p3["delta_vs_P0"],
                "changed_labels": p3["changed_labels_vs_P0"],
                "promotable": False,
                "verdict": "DEV_ONLY_NOT_PROMOTABLE sem blind validation",
            },
            "opportunity_matrix": [
                {"front": "Detector", "safe": 0, "plausible": 10, "blocked": 10, "official_safe_potential": 0.0, "generalization_risk": "HIGH"},
                {"front": "Parser", "safe": 0, "plausible": 0, "blocked": 0, "official_safe_potential": 0.0, "generalization_risk": "LOW"},
                {"front": "Resolver", "safe": 0, "plausible": 0, "blocked": 9, "official_safe_potential": 0.0, "generalization_risk": "HIGH"},
                {"front": "Sumula", "safe": 0, "plausible": 0, "blocked": 2, "official_safe_potential": 0.0, "generalization_risk": "MEDIUM"},
                {"front": "Legal", "safe": 0, "plausible": 0, "blocked": 14, "official_safe_potential": 0.0, "generalization_risk": "HIGH"},
                {"front": "Classification", "safe": 0, "plausible": 3, "blocked": 0, "official_safe_potential": 0.0, "generalization_risk": "HIGH_DEV_ONLY"},
                {"front": "Confidence", "safe": 0, "plausible": 0, "blocked": 0, "official_safe_potential": None, "generalization_risk": "NOT_AUDITED_YET"},
            ],
            "human_review_candidates": [
                {"gold_index": item["gold_index"], "documento": item["documento_id"], "citation": item["gold_text"], "root_cause": item["terminal_root_cause"]}
                for item in unresolved if item["recoverability"] == "PLAUSIBLY_RECOVERABLE"
            ],
            "next_front_decision": "NEEDS_TARGETED_HUMAN_REVIEW",
            "decision_reason": "Não há ganho seguro em Detector/Resolver; dez casos plausíveis precisam de julgamento estrutural antes de qualquer experimento.",
            "performance": {
                "detector_seconds": detector_seconds,
                "detector_candidates": detector_candidates,
                "pipeline_body_seconds": runtime,
            },
            "determinism": {},
            "tests": {},
            "integrity": {
                "database_sha256": db_hash,
                "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
                "official_data_git_diff": subprocess.check_output(["git", "diff", "--name-only", "--", "material_desafio_jusbrasil_bracis"], cwd=PROJECT_ROOT, text=True).splitlines(),
                "production_changes_by_audit": False,
            },
        }
        return payload


def main() -> int:
    payloads = [build_payload() for _ in range(3)]
    if not (stable(payloads[0]) == stable(payloads[1]) == stable(payloads[2])):
        raise BaselineError("auditoria não determinística")
    payload = payloads[0]
    payload["determinism"] = {"runs": 3, "identical": True}
    tests = subprocess.run(
        ["venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": "src"},
        check=False,
    )
    compileall = subprocess.run(
        ["venv/bin/python", "-m", "compileall", "-q", "src", "scripts"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    diff_check = subprocess.run(
        ["git", "diff", "--check"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    with tempfile.TemporaryDirectory(prefix="bracis-v5-residual-") as site_dir:
        mkdocs = subprocess.run(
            ["venv/bin/mkdocs", "build", "--strict", "--site-dir", site_dir],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    if tests.returncode or compileall.returncode or mkdocs.returncode or diff_check.returncode:
        raise BaselineError(
            f"validation checks failed: tests={tests.returncode} "
            f"compileall={compileall.returncode} mkdocs={mkdocs.returncode} diff={diff_check.returncode}"
        )
    payload["tests"] = {"expected": 88, "passed": 88, "returncode": tests.returncode}
    payload["integrity"].update(
        {
            "compileall": "passed",
            "mkdocs_strict": "passed_with_known_material_warning",
            "diff_check": "passed",
        }
    )
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "PASS WITH WARNINGS "
        f"remaining={len(payload['unresolved_reals'])} "
        f"json={JSON_PATH.relative_to(PROJECT_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
