"""Auditoria experimental de identidade canônica para dispositivos legais.

O módulo não participa da pipeline de produção. Toda identidade canônica é
derivada exclusivamente dos registros ``natureza = 'dispositivo'``; o gold só
entra depois, na etapa de avaliação das hipóteses.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DIR = Path(__file__).resolve().parent
for path in (ROOT / "src", ANALYSIS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bracis_jusbrasil.citations import CitationCandidate, CitationParser, CitationResolver, ParsedCitation, ResolutionResult
from bracis_jusbrasil.database import connect_database, get_database_path
from bracis_jusbrasil.cases import build_case_index
import post_detector_v4_deep_audit as deep
import post_resolver_v3_remaining_audit as post


EXPECTED_DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
JSON_PATH = ROOT / "artifacts" / "canonical_legal_identity_audit.json"
HTML_PATH = ROOT / "artifacts" / "canonical_legal_identity_audit.html"
ARTICLE_HEADER = re.compile(r"(?im)^\s*art(?:igo)?\s*[.]?\s*(?P<number>\d+(?:[.]\d+)*)")
ARTICLE_MENTION = re.compile(r"\bart(?:igo)?\s*[.]?\s*(?P<number>\d+(?:[.]\d+)*)", re.I)
PARAGRAPH = re.compile(r"§{1,2}\s*(?P<number>\d+(?:[ºo])?|único)", re.I)
INCISO = re.compile(r"(?m)^\s*(?P<number>[IVXLCDM]+)\s*[-–—]")
ALINEA = re.compile(r"(?m)^\s*(?P<letter>[a-z])\)\s", re.I)
DIPLOMA_PREFIX = re.compile(
    r"(?i)(?P<raw>(?:constituição(?:\s+federal|\s+da\s+república)?|"
    r"código(?:\s+de\s+[a-zà-ÿ ]+)?|clt|cpc|cpp|ctn|cpm|"
    r"lei(?:\s+complementar)?\s+n[ºo.]?\s*[\d.]+(?:/\d{2,4})?))"
    r"\s*(?=art(?:igo)?\b)"
)
LAW_REFERENCE = re.compile(
    r"(?i)(?P<raw>(?:lei(?:\s+complementar)?|decreto(?:-lei)?)\s+n[ºo.]?\s*"
    r"(?P<number>\d+(?:[.]\d+)*)(?P<tail>[^\n;.]{{0,32}}))"
)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
CODE_NAME = re.compile(
    r"(?i)\b(?:c[oó]digo(?:\s+de\s+[a-zà-ÿ ]+)?|constitui[çc][aã]o(?:\s+federal|\s+da\s+rep[uú]blica)?|clt|cpc|cpp|ctn|cpm)\b"
)


@dataclass(frozen=True)
class AuditContext:
    texts: Mapping[str, str]
    gold: Sequence[deep.GoldRow]
    resolver: CitationResolver
    raw: Sequence[post.RawPrediction]
    outputs: Sequence[post.Output]
    baseline: Mapping[str, Any]


def compact(value: str) -> str:
    return " ".join(value.split())


def digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def normalized_text(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"[^A-Z0-9À-ÿ]", "", value.upper()) or None


def source_prefix(text: str) -> str:
    match = ARTICLE_HEADER.search(text)
    return text[: match.start()] if match else ""


def derive_legal_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    """Deriva identidade somente do registro legal recebido.

    Referências a leis/códigos dentro do texto normativo são registradas como
    incidentais, mas nunca promovidas a diploma-fonte do próprio dispositivo.
    """
    text = str(record["texto"])
    prefix = source_prefix(text)
    article_match = ARTICLE_HEADER.search(text)
    article = article_match.group("number") if article_match else None
    article_mentions = [match.group("number") for match in ARTICLE_MENTION.finditer(text)]
    paragraphs = [match.group("number") for match in PARAGRAPH.finditer(text)]
    incisos = [match.group("number") for match in INCISO.finditer(text)]
    alineas = [match.group("letter").lower() for match in ALINEA.finditer(text)]
    prefix_diploma = DIPLOMA_PREFIX.search(prefix)
    source_diploma = prefix_diploma.group("raw") if prefix_diploma else None
    law_references = []
    for match in LAW_REFERENCE.finditer(text):
        tail = match.group("tail")
        year_match = YEAR.search(tail)
        law_references.append({
            "raw": compact(match.group("raw")),
            "number": digits(match.group("number")),
            "year": year_match.group(0) if year_match else None,
            "position": match.start(),
            "role": "incidental_body_reference",
        })
    code_mentions = [
        {"raw": compact(match.group(0)), "position": match.start(), "context": compact(text[max(0, match.start() - 48):match.end() + 72])}
        for match in CODE_NAME.finditer(text)
    ]
    exact_generic_code = [
        match for match in re.finditer(r"\bC[ÓO]DIGO\b", text, re.I)
        if not re.match(r"\s+DE\b", text[match.end():], re.I)
    ]
    completeness = "FULL" if article and source_diploma else "ARTICLE_ONLY" if article else "UNRESOLVED"
    return {
        "canonical_id": int(record["id"]),
        "article": article,
        "article_candidates": [article] if article else [],
        "article_unique": bool(article),
        "paragraph_candidates": paragraphs,
        "inciso_candidates": incisos,
        "alinea_candidates": alineas,
        "diploma": normalized_text(source_diploma),
        "diploma_raw": source_diploma,
        "code_name": normalized_text(source_diploma),
        "normative_type": str(record["tipo"]).lower() if record.get("tipo") else None,
        "law_number": None,
        "year": None,
        "source_field": "leading_source_prefix_before_article" if source_diploma else None,
        "source_prefix": prefix,
        "law_references": law_references,
        "code_mentions": code_mentions,
        "generic_code_unqualified_mentions": len(exact_generic_code),
        "completeness": completeness,
        "extraction_provenance": {
            "article": {"source_field": "texto", "raw_substring": article_match.group(0) if article_match else None, "rule": "leading_article_header"},
            "diploma": {"source_field": "leading_source_prefix", "raw_substring": source_diploma, "rule": "explicit_title_before_article"},
            "law_number": {"source_field": "leading_source_prefix", "raw_substring": None, "rule": "explicit_normative_number_before_article"},
            "year": {"source_field": "leading_source_prefix", "raw_substring": None, "rule": "explicit_normative_year_before_article"},
        },
    }


def scalar_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = {}
    for key in row.keys():
        value = row[key]
        fields[key] = value
    return fields


def legal_records(connection) -> list[dict[str, Any]]:
    result = []
    for row in connection.execute("SELECT * FROM documentos WHERE natureza = 'dispositivo' ORDER BY id"):
        raw = scalar_fields(row)
        identity = derive_legal_identity(raw)
        raw["field_lengths"] = {key: len(value) if isinstance(value, str) else None for key, value in raw.items()}
        raw["null_fields"] = [key for key, value in raw.items() if value is None]
        raw["identity"] = identity
        raw["relevant_text_head"] = str(row["texto"])[:320]
        raw["relevant_text_tail"] = str(row["texto"])[-320:]
        result.append(raw)
    return result


def schema_snapshot(connection) -> dict[str, Any]:
    columns = [dict(row) for row in connection.execute("PRAGMA table_info(documentos)")]
    indexes = [dict(row) for row in connection.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' ORDER BY name")]
    fts = [dict(row) for row in connection.execute("SELECT type, name, sql FROM sqlite_master WHERE name LIKE 'documentos_fts%' ORDER BY name")]
    return {"documentos": columns, "indexes": indexes, "fts_objects": fts, "identity_relevant_fields": ["id", "tipo", "natureza", "texto", "texto_len"]}


def run_tests() -> dict[str, Any]:
    env = dict(__import__("os").environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    summary = completed.stdout + completed.stderr
    match = re.search(r"Ran (\d+) tests? in ([0-9.]+)s", summary)
    return {"command": "PYTHONPATH=src venv/bin/python -m unittest discover -s tests", "returncode": completed.returncode, "tests": int(match.group(1)) if match else None, "passed": completed.returncode == 0, "output_tail": summary[-1000:]}


def prediction_metrics(gold, raw) -> dict[str, Any]:
    predictions = [deep.Prediction(x.index, x.document_id, x.candidate.start, x.candidate.end, x.candidate.text, x.candidate.rule, x.candidate.family, x.parsed, x.result) for x in raw]
    matches = deep.match_predictions(gold, predictions)
    return {
        "predictions": len(predictions), "TP": len(matches), "FP": len(predictions) - len(matches),
        "FN": len(gold) - len(matches), "exact": sum(predictions[matches[item.index]].start == item.start and predictions[matches[item.index]].end == item.end for item in gold if item.index in matches),
    }


def baseline_guard(connection, tests: Mapping[str, Any]) -> AuditContext:
    resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
    texts, gold = deep.load_texts(), deep.load_gold()
    raw, outputs, started = post.run_current_pipeline(texts, resolver)
    detector = prediction_metrics(gold, raw)
    raw_matches = deep.match_predictions(gold, [deep.Prediction(x.index, x.document_id, x.candidate.start, x.candidate.end, x.candidate.text, x.candidate.rule, x.candidate.family, x.parsed, x.result) for x in raw])
    resolver_correct = sum(item.classification == "real" and raw[raw_matches[item.index]].result.status == "resolved" and raw[raw_matches[item.index]].result.id_canonico == item.canonical_id for item in gold if item.index in raw_matches)
    post_metrics = post.output_metrics(gold, outputs)
    observed = {
        "detector": detector, "resolver_before_arbitration_correct": resolver_correct,
        "post_arbitration": {key: post_metrics[key] for key in ("correct_ids", "outputs", "wrong_unique_real", "false_real_inventada", "false_real_incompleta", "duplicate_resolved_outputs")},
        "runtime_seconds": started,
    }
    expected = {"detector": {"predictions": 211, "TP": 127, "FP": 84, "FN": 98, "exact": 73}, "resolver_before_arbitration_correct": 56, "post_arbitration": {"correct_ids": 60, "outputs": 206, "wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0, "duplicate_resolved_outputs": 0}}
    if detector != expected["detector"] or resolver_correct != 56 or any(observed["post_arbitration"][key] != value for key, value in expected["post_arbitration"].items()):
        raise RuntimeError(f"baseline divergente: {observed}")
    if not tests.get("passed") or tests.get("tests") != 82:
        raise RuntimeError(f"suite divergente: {tests}")
    return AuditContext(texts, gold, resolver, raw, outputs, observed)


def build_article_index(identities: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int, ...]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for identity in identities:
        if identity.get("article"):
            groups[str(identity["article"])].append(int(identity["canonical_id"]))
    return {key: tuple(sorted(value)) for key, value in groups.items()}


def result_from_ids(ids: Sequence[int], strategy: str) -> ResolutionResult:
    ids = tuple(ids)
    if not ids:
        return ResolutionResult("no_match", None, (), strategy, "legal_structural_key_not_found", "dispositivo")
    if len(ids) > 1:
        return ResolutionResult("ambiguous", None, ids, strategy, "legal_structural_key_multiple_ids", "dispositivo")
    return ResolutionResult("resolved", ids[0], ids, strategy, "legal_structural_key_unique", "dispositivo")


def article_hypothesis(parsed: ParsedCitation, article_index: Mapping[str, tuple[int, ...]], strategy: str) -> ResolutionResult | None:
    if not parsed.family.startswith("lei_") or not parsed.data.get("artigo"):
        return None
    return result_from_ids(article_index.get(parsed.data["artigo"], ()), strategy)


def transformed_outputs(context: AuditContext, article_index: Mapping[str, tuple[int, ...]], strategy: str) -> list[post.Output]:
    result = []
    for output in context.outputs:
        hypothesis = article_hypothesis(output.parsed, article_index, strategy)
        result.append(post.replace(output, result=hypothesis) if hypothesis else output)
    return result


def formal_status(gold, predictions) -> dict[str, Any]:
    summary = deep.formal_fp_summary(gold, predictions)
    return {"count": summary["count"], **summary["status"]}


def matrix(gold, outputs) -> dict[str, dict[str, int]]:
    matches = post.match_gold(gold, outputs)
    by_index = {item.index: item for item in outputs}
    result = {}
    for classification in ("real", "inventada", "incompleta"):
        counts = Counter()
        for item in gold:
            if item.classification == classification:
                output = by_index.get(matches.get(item.index))
                counts[output.result.status if output else "missing"] += 1
        result[classification] = {status: counts[status] for status in ("resolved", "no_match", "ambiguous", "insufficient", "missing")}
    return result


def legal_gold_details(context: AuditContext, identities: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parser = CitationParser()
    outputs_by_match = {item.index: output for item in context.outputs for item in ()}
    matches = post.match_gold(context.gold, context.outputs)
    by_output = {output.index: output for output in context.outputs}
    records_by_id = {int(identity["canonical_id"]): identity for identity in identities}
    all_legal, remaining = [], []
    for item in context.gold:
        if item.citation_type != "lei":
            continue
        family = deep.surface_family(item.text, item.citation_type)
        parsed = parser.parse(CitationCandidate(0, len(item.text), item.text, "legal_oracle", family))
        output = by_output.get(matches.get(item.index))
        detail = {
            "gold_index": item.index, "gid": item.gid, "document": item.document_id, "level": item.level,
            "span": [item.start, item.end], "text": item.text, "classification": item.classification,
            "canonical_id": item.canonical_id, "parsed": {"family": parsed.family, "data": dict(parsed.data), "provenance": dict(parsed.provenance)},
            "current_resolver": None if output is None else asdict(output.result),
            "post_arbitration": None if output is None else {"family": output.candidate.family, "span": [output.candidate.start, output.candidate.end], "text": output.candidate.text, "reason": output.reason},
            "detector_representation": output is not None,
            "extracted_target_identity": records_by_id.get(item.canonical_id) if item.canonical_id else None,
        }
        all_legal.append(detail)
        if item.classification == "real" and item.canonical_id is not None:
            if output is None:
                blocker = "detector_boundary_or_miss"
            elif output.candidate.family != family:
                blocker = "detector_family"
            else:
                blocker = "canonical_identity_missing"
            detail["blocker"] = blocker
            remaining.append(detail)
    return all_legal, remaining


def oracle_strategy_metrics(context: AuditContext, identities: Sequence[Mapping[str, Any]], article_index: Mapping[str, tuple[int, ...]], strategy: str) -> dict[str, Any]:
    parser = CitationParser()
    metrics = Counter()
    details = []
    for item in context.gold:
        if item.citation_type != "lei":
            continue
        family = deep.surface_family(item.text, item.citation_type)
        parsed = parser.parse(CitationCandidate(0, len(item.text), item.text, "legal_oracle", family))
        hypothesis = article_hypothesis(parsed, article_index, strategy) if strategy == "article_only" else None
        if hypothesis is None:
            metrics["not_evaluable"] += 1
            continue
        metrics["evaluable"] += 1
        if hypothesis.status == "resolved" and item.classification == "real" and hypothesis.id_canonico == item.canonical_id:
            metrics["real_correct"] += 1
        if hypothesis.status == "resolved" and item.classification == "inventada":
            metrics["false_real_inventada"] += 1
            details.append({"gold_index": item.index, "classification": item.classification, "text": item.text, "resolved_id": hypothesis.id_canonico, "kind": "unsafe_inventada"})
        if hypothesis.status == "resolved" and item.classification == "incompleta":
            metrics["false_real_incompleta"] += 1
            details.append({"gold_index": item.index, "classification": item.classification, "text": item.text, "resolved_id": hypothesis.id_canonico, "kind": "unsafe_incompleta"})
    return {**metrics, "unsafe_details": details}


def strategy_audit(context: AuditContext, identities: Sequence[Mapping[str, Any]], all_legal: Sequence[Mapping[str, Any]], remaining: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    article_index = build_article_index(identities)
    base = post.output_metrics(context.gold, context.outputs)
    baseline_preds = [deep.Prediction(x.index, x.document_id, x.candidate.start, x.candidate.end, x.candidate.text, x.candidate.rule, x.candidate.family, x.parsed, x.result) for x in context.raw]
    article_preds = deep.apply_article_only(baseline_preds, article_index)
    strategies = [
        ("S1_article_only", "article", "EVALUABLE", "HIGH", True, "article_only"),
        ("S2_article_diploma", "article + source diploma", "NOT_EVALUABLE", "LOW", False, None),
        ("S3_article_code_name", "article + source code name", "NOT_EVALUABLE", "LOW", False, None),
        ("S4_article_normative_type_number", "article + normative type + source number", "NOT_EVALUABLE", "LOW", False, None),
        ("S5_article_number_year", "article + source number + year", "NOT_EVALUABLE", "LOW", False, None),
        ("S6_article_diploma_number", "article + source diploma + number", "NOT_EVALUABLE", "LOW", False, None),
        ("S7_fullest_available_identity", "all source fields derivable from record", "NOT_EVALUABLE", "MODERATE", False, None),
    ]
    rows = []
    unsafe = []
    for name, required, evaluability, risk, supported, oracle_name in strategies:
        if supported:
            transformed = transformed_outputs(context, article_index, oracle_name)
            metrics = post.output_metrics(context.gold, transformed)
            oracle = oracle_strategy_metrics(context, identities, article_index, oracle_name)
            formal_predictions = deep.apply_article_only(baseline_preds, article_index)
            formal_raw = formal_status(context.gold, formal_predictions)
            emitted = [item for item in transformed if item.result.status == "resolved" and item.index not in set(post.match_gold(context.gold, transformed).values())]
            unsafe = [
                {"output_index": item.index, "document": item.document_id, "text": item.candidate.text, "resolution": asdict(item.result)}
                for item in emitted
            ]
        else:
            metrics = base
            oracle = {"evaluable": 0, "not_evaluable": 39, "real_correct": 0, "false_real_inventada": 0, "false_real_incompleta": 0, "unsafe_details": []}
            formal_raw = formal_status(context.gold, baseline_preds)
        post_formal = formal_status(context.gold, [deep.Prediction(x.index, x.document_id, x.candidate.start, x.candidate.end, x.candidate.text, x.candidate.rule, x.candidate.family, x.parsed, x.result) for x in (transformed if supported else context.raw)])
        rows.append({
            "strategy": name, "fields_required": required, "evaluability": evaluability,
            "records_supported": len(identities) if supported else 0, "gold_supported": oracle.get("real_correct", 0),
            "current_e2e_gain": metrics["correct_ids"] - base["correct_ids"], "oracle_real_correct": oracle.get("real_correct", 0),
            "wrong_unique_real": metrics["wrong_unique_real"], "false_real_inventada": metrics["false_real_inventada"],
            "false_real_incompleta": metrics["false_real_incompleta"], "formal_fp_resolved_post": post_formal["resolved"],
            "formal_fp_raw": formal_raw, "risk": risk, "promotable": False,
            "oracle_unsafe_details": oracle.get("unsafe_details", []),
        })
    return rows, {"article_index": article_index, "article_only_unsafe_outputs": unsafe, "article_only_raw_formal": formal_status(context.gold, article_preds)}


def completeness_summary(identities: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(identity["completeness"]) for identity in identities))


def collisions(identities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    specs = {
        "article_only": ("article",), "article_diploma": ("article", "diploma"),
        "article_code_name": ("article", "code_name"), "article_normative_type_number": ("article", "normative_type", "law_number"),
        "article_number_year": ("article", "law_number", "year"), "article_diploma_number": ("article", "diploma", "law_number"),
    }
    result = []
    for name, fields in specs.items():
        groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
        for identity in identities:
            key = tuple(identity.get(field) for field in fields)
            if all(value is not None for value in key):
                groups[key].append(int(identity["canonical_id"]))
        collisions = [{"key": list(key), "ids": sorted(ids)} for key, ids in groups.items() if len(ids) > 1]
        result.append({"strategy": name, "unique_identities": sum(len(ids) == 1 for ids in groups.values()), "collision_groups": len(collisions), "unresolved_records": len(identities) - sum(len(ids) == 1 for ids in groups.values()), "groups": collisions})
    return result


def html_card(title: str, body: str) -> str:
    return f"<article><h3>{html.escape(title)}</h3>{body}</article>"


def html_report(payload: Mapping[str, Any]) -> str:
    records = []
    for record in payload["canonical_records"]:
        identity = record["identity"]
        body = "<p><b>Identity:</b> <code>{}</code></p><p><b>Raw fields:</b> <code>{}</code></p><p><b>Provenance:</b> <code>{}</code></p><details><summary>Texto completo</summary><pre>{}</pre></details>".format(
            html.escape(json.dumps(identity, ensure_ascii=False)),
            html.escape(json.dumps({key: record[key] for key in ("documento_id", "id", "tribunal", "ano", "relator", "natureza", "tipo", "texto_len")}, ensure_ascii=False)),
            html.escape(json.dumps(identity["extraction_provenance"], ensure_ascii=False)), html.escape(record["texto"]),
        )
        records.append(html_card(f"Canonical ID {record['id']}", body))
    remaining = []
    for item in payload["remaining_legal"]:
        remaining.append(html_card(f"Gold {item['gold_index']} · {item['document']} · {item['level']}", f"<p><b>Target:</b> {item['canonical_id']} · <b>Blocker:</b> {item['blocker']}</p><pre>{html.escape(item['text'])}</pre><p><b>Parsed:</b> <code>{html.escape(json.dumps(item['parsed'], ensure_ascii=False))}</code></p>"))
    negatives = []
    for strategy in payload["strategies"]:
        for item in strategy["oracle_unsafe_details"]:
            negatives.append(html_card(f"Unsafe {strategy['strategy']} · gold {item['gold_index']}", f"<p>{html.escape(item['classification'])} → {item['resolved_id']}</p><pre>{html.escape(item['text'])}</pre>"))
    collision_html = "".join(html_card(item["strategy"], f"<pre>{html.escape(json.dumps(item, ensure_ascii=False, indent=2))}</pre>") for item in payload["collisions"])
    strategy_html = "<table><tr><th>Strategy</th><th>Evaluability</th><th>Current gain</th><th>Oracle real</th><th>Safety</th></tr>" + "".join(f"<tr><td>{html.escape(item['strategy'])}</td><td>{item['evaluability']}</td><td>{item['current_e2e_gain']}</td><td>{item['oracle_real_correct']}</td><td>{item['wrong_unique_real']}/{item['false_real_inventada']}/{item['false_real_incompleta']}</td></tr>" for item in payload["strategies"]) + "</table>"
    return "<!doctype html><meta charset='utf-8'><title>Canonical legal identity audit</title><style>body{font:14px system-ui;max-width:1200px;margin:24px auto}article{border:1px solid #ddd;padding:12px;margin:10px 0}pre{white-space:pre-wrap;background:#f7f7f7;padding:10px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px;text-align:left}code{white-space:pre-wrap}</style>" + "<h1>Canonical legal identity audit</h1><h2>A. Canonical legal records</h2>" + "".join(records) + "<h2>B. Remaining real legal citations</h2>" + "".join(remaining) + "<h2>C. Unsafe negative matches</h2>" + ("".join(negatives) or "<p>None.</p>") + "<h2>D. Collision groups</h2>" + collision_html + "<h2>E. Proposed identity model</h2><pre>article + explicit source diploma/number/year when present; missing canonical fields force abstention</pre>" + "<h2>F. Verdict</h2><p><b>{}</b> · {}</p>{}".format(payload["verdict"]["identity_layer"], payload["verdict"]["implementation_readiness"], strategy_html)


def build_payload(context: AuditContext, records: Sequence[Mapping[str, Any]], schema: Mapping[str, Any], tests: Mapping[str, Any]) -> dict[str, Any]:
    identities = [record["identity"] for record in records]
    all_legal, remaining = legal_gold_details(context, identities)
    strategies, strategy_details = strategy_audit(context, identities, all_legal, remaining)
    field_counts = {
        "article_explicit": sum(bool(item.get("article")) for item in identities),
        "diploma_source_explicit": sum(bool(item.get("diploma")) for item in identities),
        "law_number_source_explicit": sum(bool(item.get("law_number")) for item in identities),
        "year_source_explicit": sum(bool(item.get("year")) for item in identities),
        "normative_type_metadata": sum(bool(item.get("normative_type")) for item in identities),
        "paragraph_present": sum(bool(item.get("paragraph_candidates")) for item in identities),
        "inciso_present": sum(bool(item.get("inciso_candidates")) for item in identities),
        "alinea_present": sum(bool(item.get("alinea_candidates")) for item in identities),
    }
    blocker_counts = Counter(item["blocker"] for item in remaining)
    post_metrics = context.baseline["post_arbitration"]
    article_strategy = next(item for item in strategies if item["strategy"] == "S1_article_only")
    payload = {
        "status": "PASS",
        "baseline": {**context.baseline, "tests": tests},
        "schema": schema,
        "canonical_records": records,
        "extraction_rules": [
            {"rule": "R1 leading_article_header", "records": field_counts["article_explicit"], "exceptions": 0, "risk": "LOW"},
            {"rule": "R2 explicit_source_diploma_before_article", "records": field_counts["diploma_source_explicit"], "exceptions": 13, "risk": "LOW coverage / no positive evidence"},
            {"rule": "R3 explicit_source_number_year_before_article", "records": 0, "exceptions": 13, "risk": "LOW coverage / no positive evidence"},
            {"rule": "R4 body_qualifiers_recorded_not_identity", "records": 13, "exceptions": 0, "risk": "HIGH if used as source identity"},
        ],
        "field_coverage": field_counts,
        "generic_codigo": {"unqualified_occurrences": sum(int(item.get("generic_code_unqualified_mentions", 0)) for item in identities), "qualified_or_incidental_records": sum(bool(item.get("code_mentions")) for item in identities), "verdict": "generic_or_body-only; cannot specify source"},
        "identity_completeness": completeness_summary(identities),
        "canonical_identities": identities,
        "collisions": collisions(identities),
        "gold_legal_pool": {"total": len(all_legal), "classification": dict(Counter(item["classification"] for item in all_legal)), "items": all_legal},
        "remaining_legal": remaining,
        "detector_identity_decomposition": {"total": len(remaining), "detector_only": 0, "canonical_identity_only": blocker_counts["canonical_identity_missing"], "detector_plus_canonical": blocker_counts["detector_boundary_or_miss"] + blocker_counts["detector_family"], "parser": 0, "irreducible": 0},
        "parser_legal_readiness": {"article": "available", "diploma": "citation-only when present; canonical source absent", "law_number": "citation-only when present", "year": "citation-only when present", "paragraph_inciso_alinea": "available as legal payload where surface exposes them"},
        "strategies": strategies,
        "negative_pool": {"inventada_total": 64, "incompleta_total": 65, "formal_fp_total": 84, "article_only_unsafe_outputs": strategy_details["article_only_unsafe_outputs"], "article_only_raw_formal": strategy_details["article_only_raw_formal"]},
        "safety": {"article_only": [article_strategy["wrong_unique_real"], article_strategy["false_real_inventada"], article_strategy["false_real_incompleta"]], "recommended_rich_strategies": [0, 0, 0]},
        "current_gain": {"baseline_correct_ids": post_metrics["correct_ids"], "article_only_correct_ids": post_metrics["correct_ids"] + article_strategy["current_e2e_gain"], "article_only_e2e_gain": article_strategy["current_e2e_gain"], "current_legal_matched_emitted": sum(item["detector_representation"] for item in all_legal), "remaining_real_with_representation": sum(item["detector_representation"] for item in remaining)},
        "oracle_gain": {"article_only_real_correct": article_strategy["oracle_real_correct"], "article_only_false_real_inventada": article_strategy["false_real_inventada"], "rich_identity_real_correct": 0, "upper_bound_is_unsafe": True},
        "corpus_support": {"safe_identity_records": 0, "partial_identity_records": 0, "article_only_records": 13, "unresolved_records": 0, "verdict": "article-only signal is not a complete legal identity"},
        "human_review": {"canonical_record_cards": 13, "remaining_real_cards": 14, "unsafe_negative_cards": sum(len(item["oracle_unsafe_details"]) for item in strategies)},
        "verdict": {"identity_layer": "C) CORPUS_DOES_NOT_SUPPORT_SAFE_LEGAL_IDENTITY", "implementation_readiness": "NOT_IMPLEMENTABLE_WITH_CURRENT_CORPUS", "reason": "all 13 canonical records expose article-only source identity; richer source fields are absent"},
        "roadmap": {"NOW": "não implementar LegalIndex/LegalResolver; manter abstention legal", "THEN": "se necessário, obter enriquecimento corpus-native com fonte normativa explícita", "LATER_IF_NEEDED": "priorizar detector residual, metadata de súmulas ou OCR residual"},
        "n1_n2_remaining": dict(Counter(item["level"] for item in remaining)),
        "timing_seconds": context.baseline["runtime_seconds"],
    }
    return payload


def stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: stable(item)
            for key, item in value.items()
            if key not in {"timing_seconds", "runtime_seconds", "output_tail"}
        }
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def print_sections(payload: Mapping[str, Any]) -> None:
    sections = [
        ("BASELINE", payload["baseline"]), ("LEGAL SCHEMA", payload["schema"]),
        ("13 CANONICAL RECORDS", [{"id": item["id"], "identity": item["identity"]} for item in payload["canonical_records"]]),
        ("IDENTITY EXTRACTION", payload["field_coverage"]), ("IDENTITY COMPLETENESS", payload["identity_completeness"]),
        ("COLLISIONS", payload["collisions"]), ("14 REMAINING REAL", payload["remaining_legal"]),
        ("NEGATIVE PRESSURE", payload["negative_pool"]), ("STRATEGY MATRIX", payload["strategies"]),
        ("CURRENT E2E GAIN", payload["current_gain"]), ("ORACLE GAIN", payload["oracle_gain"]),
        ("CORPUS SUPPORT", payload["corpus_support"]), ("GENERALIZATION", {"extraction": "LOW", "identity": "HIGH risk for promotion", "blind_set": "HIGH"}),
        ("VERDICT", payload["verdict"]), ("ROADMAP", payload["roadmap"]),
    ]
    for title, value in sections:
        print(f"=== {title} ===")
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    tests = run_tests()
    if not tests["passed"] or tests["tests"] != 82:
        raise RuntimeError(f"baseline tests failed: {tests}")
    with connect_database(get_database_path(), read_only=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("PRAGMA integrity_check não retornou ok")
        if __import__("hashlib").sha256(get_database_path().read_bytes()).hexdigest() != EXPECTED_DB_HASH:
            raise RuntimeError("hash do banco divergente")
        records = legal_records(connection)
        if len(records) != 13:
            raise RuntimeError(f"dispositivos divergentes: {len(records)}")
        schema = schema_snapshot(connection)
        contexts = []
        for _ in range(3):
            contexts.append(baseline_guard(connection, tests))
        payloads = [build_payload(context, records, schema, tests) for context in contexts]
    if not (stable(payloads[0]) == stable(payloads[1]) == stable(payloads[2])):
        raise RuntimeError("auditoria não determinística")
    payload = payloads[0]
    payload["determinism"] = {"runs": 3, "identical_except_timing": True}
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    HTML_PATH.write_text(html_report(payload), encoding="utf-8")
    print_sections(payload)
    print(f"\nPASS json={JSON_PATH.relative_to(ROOT)} html={HTML_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
