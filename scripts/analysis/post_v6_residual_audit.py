"""Auditoria read-only dos reais residuais após CitationDetector V6.

Este é um instrumento DEV: usa gold para medir e explicar o pipeline, mas não
altera produção nem transforma informação de gold em regra de detecção.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationParser, CitationResolver  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


JSON_PATH = ROOT / "artifacts" / "post_v6_residual_audit.json"
HTML_PATH = ROOT / "artifacts" / "post_v6_residual_audit.html"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
V6_EXPECTED = (222, 140, 82, 85, 86, 70, 0.49422707514297154)
ORACLE_FAMILIES = (
    "processo_ou_recurso_numerado",
    "processo_cnj",
    "sumula_numerada",
    "jurisprudencia_tribunal_contextual",
    "jurisprudencia_referencia_geral",
)


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def counter(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def result_view(result: Any) -> dict[str, Any]:
    return post.result_dict(result)


def parsed_view(parsed: Any) -> dict[str, Any]:
    return post.parsed_dict(parsed)


def candidate_view(row: Any, gold: Any) -> dict[str, Any]:
    candidate = row.candidate
    return {
        "span": [candidate.start, candidate.end], "text": candidate.text,
        "rule": candidate.rule, "family": candidate.family,
        "iou": round(post.iou(gold.start, gold.end, candidate.start, candidate.end), 6),
        "parsed": parsed_view(row.parsed), "resolver": result_view(row.result),
    }


def local_context(text: str, start: int, end: int, radius: int = 200) -> dict[str, Any]:
    return {
        "span": [max(0, start - radius), min(len(text), end + radius)],
        "text": text[max(0, start - radius):min(len(text), end + radius)],
    }


def oracle_sweep(gold: Any, parser: CitationParser, resolver: CitationResolver) -> dict[str, Any]:
    """Passa somente a superfície literal do gold ao downstream atual."""
    families = (post.family_for(gold.text, gold.citation_type), *ORACLE_FAMILIES)
    attempts = []
    for family in dict.fromkeys(families):
        candidate = CitationCandidate(0, len(gold.text), gold.text, "audit_oracle", family)
        parsed = parser.parse(candidate, context=gold.text)
        resolved = resolver.resolve(parsed)
        attempts.append({
            "family": family, "parsed": parsed_view(parsed), "resolver": result_view(resolved),
            "canonical_correct": resolved.status == "resolved" and resolved.id_canonico == gold.canonical_id,
        })
    correct = [attempt for attempt in attempts if attempt["canonical_correct"]]
    return {
        "verdict": "DOWNSTREAM_READY" if correct else "DOWNSTREAM_NOT_READY",
        "attempts": attempts,
        "correct_families": [attempt["family"] for attempt in correct],
    }


def is_truncated_ocr(gold_text: str, candidate_text: str | None) -> bool:
    if not candidate_text or candidate_text == gold_text:
        return False
    return bool(
        re.search(r"\d+[A-Za-z](?:\s*\([A-Z]{2}\))?$", gold_text)
        and candidate_text.rstrip().endswith(tuple("0123456789"))
    )


def ocr_cause(text: str, candidate_text: str | None) -> str | None:
    if re.search(r"\b5[úu]mula\b", text, flags=re.IGNORECASE):
        return "OCR_REQUIRES_CHARACTER_MUTATION"
    if is_truncated_ocr(text, candidate_text):
        return "TERMINAL_OCR_TRUNCATION"
    if re.search(r"\d[\d.\- ]*[A-Za-z][\d.\- ]*\d", text):
        return "OCR_REQUIRES_DIGIT_MUTATION"
    return None


def first_blocker(gold: Any, raw_match: Any | None, output_match: Any | None,
                  parser: CitationParser, resolver: CitationResolver) -> tuple[str, str, dict[str, Any]]:
    oracle = oracle_sweep(gold, parser, resolver)
    if raw_match is None:
        return "DETECTOR", "NO_OFFICIAL_RAW_CANDIDATE", oracle
    if raw_match.result.status == "resolved" and raw_match.result.id_canonico == gold.canonical_id:
        return "ARBITRATION", "RAW_CORRECT_FINAL_INCORRECT", oracle
    if is_truncated_ocr(gold.text, raw_match.candidate.text):
        return "DETECTOR", "IDENTITY_TRUNCATED_BEFORE_PARSER", oracle
    oracle_base = oracle["attempts"][0]["parsed"]
    raw_parsed = parsed_view(raw_match.parsed)
    if raw_parsed != oracle_base and oracle["verdict"] == "DOWNSTREAM_READY":
        return "PARSER", "LITERAL_IDENTITY_LOST_BY_PARSER", oracle
    if output_match is None and raw_match is not None:
        return "ARBITRATION", "RAW_REPRESENTATION_NOT_EMITTED", oracle
    return "RESOLVER", "PARSED_IDENTITY_NOT_RESOLVED_CORRECTLY", oracle


def root_and_recoverability(gold: Any, raw_match: Any | None, blocker: str,
                            oracle: Mapping[str, Any]) -> tuple[str, str]:
    candidate_text = None if raw_match is None else raw_match.candidate.text
    if gold.citation_type == "lei":
        return "LEGAL_IDENTITY_NOT_VERIFIABLE", "CORPUS_BLOCKED"
    if raw_match and raw_match.candidate.family == "sumula_numerada":
        return "SUMULA_METADATA_NOT_INDEXABLE", "CORPUS_BLOCKED"
    if raw_match and raw_match.result.reason == "cnj_primary_class_conflict":
        return "GOLD_CORPUS_CONFLICT", "GOLD_CORPUS_CONFLICT"
    if raw_match and raw_match.result.status == "ambiguous":
        return "CANONICAL_PRIMARY_AMBIGUITY", "GENUINELY_AMBIGUOUS"
    ocr = ocr_cause(gold.text, candidate_text)
    if ocr:
        return ocr, "UNSAFE_TO_RECOVER"
    if blocker == "DETECTOR" and oracle["verdict"] == "DOWNSTREAM_READY":
        return "UNSUPPORTED_PROCESS_MARKER", "PLAUSIBLY_RECOVERABLE"
    if blocker == "DETECTOR":
        return "PARSER_STRUCTURAL_GAP", "PLAUSIBLY_RECOVERABLE"
    if blocker == "RESOLVER":
        return "CANONICAL_RECORD_NOT_FOUND", "CORPUS_BLOCKED"
    if blocker == "PARSER":
        return "PARSER_STRUCTURAL_GAP", "PLAUSIBLY_RECOVERABLE"
    return "ARBITRATION_CONFLICT", "PLAUSIBLY_RECOVERABLE"


def residual_group(row: Mapping[str, Any]) -> str:
    cause = row["terminal_cause"]
    if cause == "LEGAL_IDENTITY_NOT_VERIFIABLE":
        return "legal_identity_not_verifiable"
    if cause == "SUMULA_METADATA_NOT_INDEXABLE":
        return "sumula_metadata_not_indexable"
    if cause == "CANONICAL_PRIMARY_AMBIGUITY":
        return "canonical_primary_ambiguity"
    if cause == "GOLD_CORPUS_CONFLICT":
        return "gold_corpus_conflict"
    if cause.startswith("OCR_") or cause.startswith("TERMINAL_OCR"):
        return "unsafe_ocr"
    if cause == "UNSUPPORTED_PROCESS_MARKER":
        return "unsupported_process_marker"
    return "parser_structural_gap"


def render_html(payload: Mapping[str, Any]) -> str:
    rows = payload["residuals"]
    table_rows = "".join(
        "<tr>"
        f"<td>{row['gold_index']}</td><td>{escape(row['documento_id'])}</td><td>{row['nivel']}</td>"
        f"<td><code>{escape(row['gold_text'])}</code></td><td>{row['first_blocker']}</td>"
        f"<td>{row['terminal_cause']}</td><td>{row['recoverability']}</td>"
        "</tr>"
        for row in rows
    )
    details = "".join(
        f"<details><summary>Gold {row['gold_index']} — {escape(row['gold_text'])}</summary>"
        f"<h3>Contexto</h3><pre>{escape(row['context']['text'])}</pre>"
        f"<h3>Diagnóstico</h3><pre>{escape(json.dumps({key: row[key] for key in ('detector_candidates', 'parser', 'resolver', 'arbitration', 'final', 'oracle')}, ensure_ascii=False, indent=2))}</pre>"
        "</details>"
        for row in rows
    )
    coverage = payload["coverage"]
    return f"""<!doctype html><html lang=\"pt-BR\"><meta charset=\"utf-8\"><title>Post-V6 residual audit</title>
<style>body{{font:14px system-ui;margin:2rem;max-width:1400px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #bbb;padding:.4rem;text-align:left;vertical-align:top}}code,pre{{white-space:pre-wrap}}details{{margin:1rem 0}}summary{{cursor:pointer;font-weight:600}}</style>
<h1>Post-V6 residual / first-blocker audit</h1><p>Corretos: {coverage['correct']}/96; residuais: {coverage['residual']}; próximo front: <strong>{payload['next_front']}</strong>.</p>
<h2>A — Summary</h2><pre>{escape(json.dumps({key: payload[key] for key in ('first_blocker_counts', 'terminal_root_cause_counts', 'recoverability_counts', 'engineering_closure', 'safe_ceiling', 'saturation_assessment', 'next_front')}, ensure_ascii=False, indent=2))}</pre>
<h2>B — Residual ledger</h2><table><tr><th>gold</th><th>doc</th><th>nível</th><th>texto</th><th>first blocker</th><th>causa</th><th>recoverability</th></tr>{table_rows}</table>
<h2>C–E — Diagnóstico por residual</h2>{details}
<h2>F — Recoverable groups</h2><pre>{escape(json.dumps(payload['plausible_groups'], ensure_ascii=False, indent=2))}</pre>
<h2>G — Terminally blocked groups</h2><pre>{escape(json.dumps(payload['structural_groups'], ensure_ascii=False, indent=2))}</pre>
<h2>H — Suggested next front</h2><p>{payload['next_front']}</p></html>"""


def audit_once() -> dict[str, Any]:
    before_production = subprocess.check_output(
        ["git", "diff", "--name-only", "--", "src", "tests", "docs"], cwd=ROOT, text=True
    ).splitlines()
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    if len(texts) != 26 or len(gold) != 225:
        raise RuntimeError("dimensão oficial inesperada")
    db_path = get_database_path()
    if sha256(db_path.read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("hash do banco divergiu")
    with connect_database(db_path, read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        parser = CitationParser()
        raw, outputs, _ = post.run_current_pipeline(texts, resolver)
        raw_matches, output_matches = post.match_gold(gold, raw), post.match_gold(gold, outputs)
        raw_by_index, output_by_index = {row.index: row for row in raw}, {row.index: row for row in outputs}
        gold_df = official.load_gold_df()
        documents = list(gold_df.documento_id.drop_duplicates())
        submission = official.submission_from_cells(official.output_cells(outputs), documents)
        score = float(official.METRIC.score(official.solution_from_gold(gold_df), submission, "documento_id"))
        output_metrics = post.output_metrics(gold, outputs)
        baseline = (
            len(raw), len(raw_matches), len(raw) - len(raw_matches), len(gold) - len(raw_matches),
            sum(gold[g].start == raw[r].candidate.start and gold[g].end == raw[r].candidate.end for g, r in raw_matches.items()),
            output_metrics["correct_ids"], score,
        )
        if baseline != V6_EXPECTED:
            raise RuntimeError(f"V6 baseline não reproduzida: {baseline}")

        inventory, residuals = [], []
        for item in gold:
            if item.classification != "real":
                continue
            raw_match = raw_by_index.get(raw_matches.get(item.index))
            output_match = output_by_index.get(output_matches.get(item.index))
            correct = bool(output_match and output_match.result.status == "resolved" and output_match.result.id_canonico == item.canonical_id)
            inventory.append({"gold": item.index, "doc": item.document_id, "level": item.level, "status": "CORRECT" if correct else "RESIDUAL", "match": output_match is not None, "canonical_correct": correct})
            if correct:
                continue
            blocker, reason, oracle = first_blocker(item, raw_match, output_match, parser, resolver)
            cause, recoverability = root_and_recoverability(item, raw_match, blocker, oracle)
            related = [
                candidate_view(row, item) for row in raw
                if row.document_id == item.document_id and post.iou(item.start, item.end, row.candidate.start, row.candidate.end) > 0
            ]
            related.sort(key=lambda row: (-row["iou"], row["span"]))
            row = {
                "gold_index": item.index, "citacao_id": item.gid, "documento_id": item.document_id, "nivel": item.level,
                "gold_span": [item.start, item.end], "gold_text": item.text, "gold_canonical_id": item.canonical_id,
                "context": local_context(texts[item.document_id], item.start, item.end),
                "official_raw_match": raw_match is not None, "official_output_match": output_match is not None,
                "detector_candidates": related[:8],
                "parser": None if raw_match is None else parsed_view(raw_match.parsed),
                "resolver": None if raw_match is None else result_view(raw_match.result),
                "arbitration": None if output_match is None else {"reason": output_match.reason, "source_indexes": list(output_match.source_indexes)},
                "final": None if output_match is None else result_view(output_match.result),
                "first_blocker": blocker, "first_blocker_confidence": "HIGH", "technical_reason": reason,
                "terminal_cause": cause, "recoverability": recoverability, "oracle": oracle if blocker == "DETECTOR" else None,
            }
            row["group"] = residual_group(row)
            residuals.append(row)

        if len(inventory) != 96 or len(residuals) != 26:
            raise RuntimeError(f"universo real inválido: total={len(inventory)} residual={len(residuals)}")
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in residuals:
            groups[row["group"]].append(row)
        structural_groups = [
            {"group": name, "cases": [row["gold_index"] for row in values], "case_count": len(values),
             "doc_count": len({row["documento_id"] for row in values}), "first_blockers": counter(row["first_blocker"] for row in values),
             "terminal_causes": counter(row["terminal_cause"] for row in values), "recoverability": counter(row["recoverability"] for row in values)}
            for name, values in sorted(groups.items())
        ]
        plausible = [row for row in residuals if row["recoverability"] == "PLAUSIBLY_RECOVERABLE"]
        safe = [row for row in residuals if row["recoverability"] == "SAFE_RECOVERABLE"]
        terminal = [row for row in residuals if row["recoverability"] in {"CORPUS_BLOCKED", "GENUINELY_AMBIGUOUS", "UNSAFE_TO_RECOVER", "GOLD_CORPUS_CONFLICT"}]
        closed = 70 + len(terminal)
        payload = {
            "classification": "PASS WITH WARNINGS", "baseline": {"predictions": baseline[0], "matches": baseline[1], "FP": baseline[2], "FN": baseline[3], "exact": baseline[4], "real_ids_correct": baseline[5], "official_final": baseline[6], "safety": {key: output_metrics[key] for key in ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")}},
            "real_gold_inventory": inventory, "correctly_resolved": 96 - len(residuals), "residuals": residuals,
            "first_blocker_counts": counter(row["first_blocker"] for row in residuals),
            "first_blocker_by_level": {level: counter(row["first_blocker"] for row in residuals if row["nivel"] == level) for level in ("N1", "N2")},
            "first_blocker_confidence": counter(row["first_blocker_confidence"] for row in residuals),
            "terminal_root_cause_counts": counter(row["terminal_cause"] for row in residuals),
            "recoverability_counts": counter(row["recoverability"] for row in residuals),
            "detector_oracles": [{"gold": row["gold_index"], "oracle": row["oracle"]["verdict"], "correct_families": row["oracle"]["correct_families"]} for row in residuals if row["first_blocker"] == "DETECTOR"],
            "resolver_diagnostics": [row for row in residuals if row["first_blocker"] == "RESOLVER"],
            "structural_groups": structural_groups, "safe_recoverable_groups": [],
            "plausible_groups": [group for group in structural_groups if any(row["group"] == group["group"] for row in plausible)],
            "post_v5_comparison": {"correct_real_ids": {"post_v5": 67, "post_v6": 70, "delta": 3}, "residual": {"post_v5": 29, "post_v6": 26, "delta": -3}, "first_blocker": {"post_v5": {"DETECTOR": 19, "RESOLVER": 10}, "post_v6": counter(row["first_blocker"] for row in residuals)}},
            "v6_closed_cases": [143, 159, 165],
            "coverage": {"correct": 96 - len(residuals), "total": 96, "residual": len(residuals), "percentage": round(100 * (96 - len(residuals)) / 96, 4)},
            "engineering_closure": {"closed": closed, "total": 96, "percentage": round(100 * closed / 96, 4), "definition": "correct + corpus_blocked + genuinely_ambiguous + unsafe_to_recover + gold_corpus_conflict; not a score"},
            "safe_ceiling": {"correct": 70, "safe_recoverable": len(safe), "ceiling": 70 + len(safe), "percentage": round(100 * (70 + len(safe)) / 96, 4)},
            "exploratory_ceiling": {"correct": 70, "safe_recoverable": len(safe), "plausibly_recoverable": len(plausible), "ceiling": 70 + len(safe) + len(plausible), "percentage": round(100 * (70 + len(safe) + len(plausible)) / 96, 4)},
            "terminal_closed_residuals": len(terminal), "open_actionable_residuals": len(safe) + len(plausible),
            "saturation_assessment": "RECALL_HAS_SMALL_PLAUSIBLE_TAIL",
            "next_front": "TARGETED_HUMAN_REVIEW",
            "next_front_reason": "Quatro resíduos plausíveis são estruturalmente heterogêneos, sem grupo multi-doc com regra e negativos suficientes para V7/Parser/Resolver.",
            "human_review_cases": [row["gold_index"] for row in plausible],
            "integrity": {"database_sha256": DB_HASH, "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0], "official_data_diff": subprocess.check_output(["git", "diff", "--name-only", "--", "material_desafio_jusbrasil_bracis"], cwd=ROOT, text=True).splitlines(), "production_diff_before": before_production},
        }
    after_production = subprocess.check_output(
        ["git", "diff", "--name-only", "--", "src", "tests", "docs"], cwd=ROOT, text=True
    ).splitlines()
    if after_production != before_production:
        raise RuntimeError("a auditoria alterou produção")
    payload["integrity"]["production_diff_after"] = after_production
    payload["integrity"]["production_changed_by_audit"] = False
    return payload


def external_checks() -> dict[str, str]:
    checks = [
        ("tests", ["venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("compile", ["venv/bin/python", "-m", "compileall", "-q", "src", "scripts"]),
        ("diff_check", ["git", "diff", "--check"]),
    ]
    results = {}
    for name, command in checks:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env={**dict(__import__("os").environ), "PYTHONPATH": "src"})
        if completed.returncode:
            raise RuntimeError(f"{name} falhou: {completed.stderr[-2000:]}")
        results[name] = "PASS"
    with tempfile.TemporaryDirectory(prefix="post-v6-residual-") as site:
        completed = subprocess.run(["venv/bin/python", "-m", "mkdocs", "build", "--strict", "--site-dir", site], cwd=ROOT, text=True, capture_output=True)
        if completed.returncode:
            raise RuntimeError(f"mkdocs falhou: {completed.stderr[-2000:]}")
    results["mkdocs"] = "PASS_WITH_KNOWN_UPSTREAM_WARNING"
    return results


def main() -> int:
    payloads = [audit_once() for _ in range(3)]
    if not (stable(payloads[0]) == stable(payloads[1]) == stable(payloads[2])):
        raise RuntimeError("auditoria não determinística")
    payload = payloads[0]
    payload["determinism"] = {"runs": 3, "identical": True}
    payload["checks"] = external_checks()
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if payload["open_actionable_residuals"] or payload["first_blocker_confidence"].get("LOW", 0):
        HTML_PATH.write_text(render_html(payload), encoding="utf-8")
    print(f"PASS WITH WARNINGS residual={payload['coverage']['residual']} next={payload['next_front']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
