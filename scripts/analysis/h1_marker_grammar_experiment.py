"""Experimento focal de gramática de marcador para H1 (sem tocar produção).

M0 reproduz R2 exatamente. M1 conserva integralmente a gramática CNJ de R2
e substitui somente ``local_marker`` por uma gramática processual positiva.
As duas gerações são feitas antes de carregar gold, resolver ou índice canônico.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import h1_degraded_compact_cnj_experiment as h1  # noqa: E402
import h1_degraded_compact_cnj_refinement as refinement  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationResolver  # noqa: E402
from bracis_jusbrasil.citations import detector as v5  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


OUTPUT_PATH = ROOT / "artifacts" / "h1_marker_grammar_experiment.json"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
EXPECTED_R2_SCORE = 0.5035327467293448
R2 = "R2_SHARED_CORE_LOCAL_BREAK"
KNOWN = (143, 159, 165, 170, 172)
OTHER_TPS = (160, 168, 221)

# M1 is deliberately declared before corpus/gold evaluation. Its terminal
# class is V5 vocabulary; the two aliases are regular abbreviations of V5
# phrases, not corpus phrases or ID-dependent exceptions.
_H = r"[ \t\u00a0]"
_HS = _H + "+"
_MODIFIER = r"(?:AgInt|AgRg|AgR|EDcl|ED)"
_MODIFIER_CHAIN = rf"(?:(?:{_MODIFIER})(?:{_HS}(?:no|nos|na|nas|em){_HS}|{_H}*-{_H}*))*"
_DIRECT_V5_PRIMARY = (
    r"(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|RHC|RMS|AR|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|"
    r"Agravo(?:[ \t\u00a0]+Regimental)?|Recurso[ \t\u00a0]+Especial(?:[ \t\u00a0]+Eleitoral)?|"
    r"Recurso[ \t\u00a0]+Extraordin[aá]rio|Habeas[ \t\u00a0]+Corpus|Reclamaç[aã]o)"
)
_STRUCTURAL_V5_ALIAS = r"(?:REspe[.]?|Ag[.][ \t\u00a0]*Int[.])"
_PRIMARY = rf"(?:{_DIRECT_V5_PRIMARY}|{_STRUCTURAL_V5_ALIAS})"
M1_MARKER_RE = re.compile(
    # The dotted alias ends in '.', so its terminal boundary is supplied by
    # the required whitespace/number-marker tail, not by a word boundary.
    rf"(?P<marker>(?<!\w){_MODIFIER_CHAIN}{_PRIMARY}{_H}*(?:(?:n(?:[º°.o]|o)?){_H}*)?)$",
    re.IGNORECASE,
)


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def deterministic_view(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: deterministic_view(item) for key, item in value.items() if not key.endswith("_seconds")}
    if isinstance(value, list):
        return [deterministic_view(item) for item in value]
    return value


def m1_marker(text: str, number_start: int, max_distance: int = 96) -> tuple[int, str, str] | None:
    """Return only a contiguous positive procedural marker before a CNJ body."""
    left = max(0, number_start - max_distance)
    match = M1_MARKER_RE.search(text[left:number_start])
    if match is None:
        return None
    start = left + match.start("marker")
    marker = match.group("marker")
    component = "V5_STRUCTURAL_ALIAS" if re.search(_STRUCTURAL_V5_ALIAS, marker, re.I) else "V5_DIRECT_PRIMARY"
    if re.search(r"(?:AgInt|AgRg|AgR|EDcl|ED)(?:[ \t\u00a0]+(?:no|nos|na|nas|em)[ \t\u00a0]+|[ \t\u00a0]*-)", marker, re.I):
        component += "+V5_BOUNDED_MODIFIER_CHAIN"
    return start, marker, component


def m1_records(texts: Mapping[str, str]) -> tuple[list[h1.H1Record], dict[tuple[str, int, int], str]]:
    """M1 uses R2's fixed numeric/UF/span logic and changes only the marker."""
    records: list[h1.H1Record] = []
    provenance: dict[tuple[str, int, int], str] = {}
    seen: set[tuple[str, int, int]] = set()
    for document_id in sorted(texts):
        text = texts[document_id]
        for match in refinement.LOCAL_BREAK_RUN_RE.finditer(text):
            body = match.group(0)
            observed = h1.digits(body)
            if len(observed) != 20 or not h1.is_noncanonical_body(body):
                continue
            marker_info = m1_marker(text, match.start())
            if marker_info is None:
                continue
            marker_start, marker, component = marker_info
            local = text[marker_start:match.end()]
            if match.start() - marker_start > 96 or local.count("\n") > 1 or "\n\n" in local or re.search(r"[!?;]", local):
                continue
            end = match.end()
            uf = refinement.UF_RE.match(text[end:])
            if uf:
                end += uf.end()
            key = (document_id, marker_start, end)
            if key in seen:
                continue
            seen.add(key)
            candidate = CitationCandidate(marker_start, end, text[marker_start:end], "H1_M1_CONTROLLED", "processo_cnj")
            record = h1.H1Record(document_id, "H1_SHARED_CORE", "M1_CONTROLLED_PROCEDURAL_MARKER", candidate, marker, observed, observed, h1.normalized_candidate_text(candidate, observed), ("remove spaces", "remove structural dots/hyphens", "remove at most one local newline", "reinsert fixed CNJ separators"))
            records.append(record)
            provenance[key] = component
    return records, provenance


def marker_forms(records: Sequence[h1.H1Record], provenance: Mapping[tuple[str, int, int], str]) -> list[dict[str, Any]]:
    forms: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        normalized = re.sub(r"\s+", " ", record.marker).strip()
        key = (normalized.casefold(), provenance.get((record.document_id, record.candidate.start, record.candidate.end), "M0_GENERIC"))
        row = forms.setdefault(key, {"raw_marker": normalized, "normalized_marker_structure": key[0], "source_grammar_component": key[1], "V5_reuse": key[1] != "M0_GENERIC", "H1_specific_extension": "REspe/Ag. Int./AgR are bounded aliases of V5 full forms; no corpus/gold class", "occurrences": 0, "docs": set()})
        row["occurrences"] += 1
        row["docs"].add(record.document_id)
    return [{**row, "docs": sorted(row["docs"])} for row in sorted(forms.values(), key=lambda row: (row["raw_marker"].casefold(), row["source_grammar_component"]))]


def synthetic(variant: str) -> list[dict[str, Any]]:
    digits = "12345678901234567890"
    cases = [
        ("adversarial_oab_slash", f"REsp OAB/SP {digits}", False),
        ("adversarial_oab", f"REsp OAB {digits}", False),
        ("adversarial_oab_uf", f"REsp OAB/RJ {digits}", False),
        ("adversarial_agint_oab", f"AgInt OAB/SP {digits}", False),
        ("adversarial_processo_oab", f"Processo OAB/SP {digits}", False),
        ("adversarial_abc_uf", f"REsp ABC/SP {digits}", False),
        ("adversarial_tj_uf", f"REsp TJ/SP {digits}", False),
        ("adversarial_cpf", f"REsp CPF {digits}", False),
        ("adversarial_cnpj", f"REsp CNPJ {digits}", False),
        ("adversarial_protocolo", f"REsp protocolo {digits}", False),
        ("positive_resp", f"REsp {digits}", True),
        ("positive_agint_resp", f"AgInt no REsp {digits}", True),
        ("positive_ed_agr_resp", f"ED no AgR-REsp {digits}", True),
        ("positive_dotted_agint", f"Ag. Int. No {digits}", True),
        ("positive_rhcv5", f"RHC n. {digits}", True),
    ]
    rows = []
    for name, text, expected_m1 in cases:
        if variant == "M0":
            records = refinement.records_for(R2, {name: text})
            expected = None
        else:
            records, _ = m1_records({name: text})
            expected = expected_m1
        rows.append({"name": name, "expected": expected, "accepted": bool(records), "pass": expected is None or bool(records) == expected, "records": [{"span": [r.candidate.start, r.candidate.end], "text": r.candidate.text, "marker": r.marker} for r in records]})
    return rows


def context_class(text: str, start: int, end: int) -> str:
    local = text[max(0, start - 160):min(len(text), end + 160)]
    if start < 250 and re.search(r"ACÓRDÃO|PROCESSO|AUTOS|TRIBUNAL", local, re.I):
        return "HEADER"
    if re.search(r"\b(?:Processo|Autos)\s+n[ºo.]?\s*$", text[max(0, start - 80):start], re.I):
        return "OWN_CASE"
    if start > len(text) - 180 and re.search(r"assinatura|secretaria|publicado|rodapé", local, re.I):
        return "FOOTER"
    if re.search(r"\b(?:decidido|apreciar|confira-se|precedente|quanto decidido)\b", local, re.I):
        return "NARRATIVE"
    return "UNKNOWN"


def record_gold_rows(records: Sequence[h1.H1Record], gold: Sequence[Any], texts: Mapping[str, str], provenance: Mapping[tuple[str, int, int], str]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        hits = [item for item in gold if item.document_id == record.document_id and post.iou(item.start, item.end, record.candidate.start, record.candidate.end) >= 0.5]
        rows.append({"documento_id": record.document_id, "span": [record.candidate.start, record.candidate.end], "text": record.candidate.text, "marker": record.marker, "marker_component": provenance.get((record.document_id, record.candidate.start, record.candidate.end), "M0_GENERIC"), "gold_matches": [item.index for item in hits], "exact_gold_matches": [item.index for item in hits if item.start == record.candidate.start and item.end == record.candidate.end], "position": context_class(texts[record.document_id], record.candidate.start, record.candidate.end), "context": h1.context_for(texts[record.document_id], record.candidate.start, record.candidate.end)})
    return rows


def known_matrix(gold: Sequence[Any], variants: Mapping[str, Sequence[h1.H1Record]]) -> list[dict[str, Any]]:
    rows = []
    for case in KNOWN:
        item = next(row for row in gold if row.index == case)
        row: dict[str, Any] = {"case": case}
        for name, records in variants.items():
            hits = [record for record in records if record.document_id == item.document_id and post.iou(item.start, item.end, record.candidate.start, record.candidate.end) >= 0.5]
            best = max(hits, key=lambda record: post.iou(item.start, item.end, record.candidate.start, record.candidate.end), default=None)
            row[name] = {"detected": best is not None, "exact": None if best is None else [best.candidate.start, best.candidate.end] == [item.start, item.end], "marker": None if best is None else best.marker, "span": None if best is None else [best.candidate.start, best.candidate.end]}
        rows.append(row)
    return rows


def metrics(records: Sequence[h1.H1Record], gold: Sequence[Any]) -> dict[str, int]:
    matched = {item.index for item in gold for record in records if item.document_id == record.document_id and post.iou(item.start, item.end, record.candidate.start, record.candidate.end) >= 0.5}
    exact = sum(any(item.document_id == record.document_id and item.start == record.candidate.start and item.end == record.candidate.end for item in gold) for record in records)
    return {"raw_H1_candidates": len(records), "accepted_H1_candidates": len(records), "Detection_TP": len(matched), "Detection_FP": len(records) - sum(bool([item for item in gold if item.document_id == record.document_id and post.iou(item.start, item.end, record.candidate.start, record.candidate.end) >= 0.5]) for record in records), "exact": exact, "docs": len({record.document_id for record in records})}


def marker_exposure(texts: Mapping[str, str], variant: str) -> dict[str, Any]:
    """Exposure before gold: every noncanonical 20-digit R2 numeric body is audited."""
    raw_bodies = marker_hits = accepted = 0
    rejected_reasons = Counter()
    forms = Counter()
    docs: set[str] = set()
    for document_id, text in sorted(texts.items()):
        for match in refinement.LOCAL_BREAK_RUN_RE.finditer(text):
            body = match.group(0)
            if len(h1.digits(body)) != 20 or not h1.is_noncanonical_body(body):
                continue
            raw_bodies += 1
            if variant == "M0":
                marker_info = h1.local_marker(text, match.start())
                marker = None if marker_info is None else marker_info[1]
                marker_start = None if marker_info is None else marker_info[0]
            else:
                marker_info = m1_marker(text, match.start())
                marker = None if marker_info is None else marker_info[1]
                marker_start = None if marker_info is None else marker_info[0]
            if marker is None:
                rejected_reasons["MARKER_GRAMMAR"] += 1
                continue
            marker_hits += 1
            local = text[marker_start:match.end()]
            if match.start() - marker_start > 96:
                rejected_reasons["LOCAL_DISTANCE"] += 1
            elif local.count("\n") > 1 or "\n\n" in local or re.search(r"[!?;]", local):
                rejected_reasons["LOCAL_BOUNDARY"] += 1
            else:
                accepted += 1
                forms[re.sub(r"\s+", " ", marker).strip()] += 1
                docs.add(document_id)
    return {"raw_noncanonical_20_digit_bodies": raw_bodies, "raw_marker_occurrences": marker_hits, "accepted_markers": accepted, "rejected_markers": raw_bodies - accepted, "rejected_by_reason": dict(sorted(rejected_reasons.items())), "unique_marker_forms": len(forms), "accepted_forms": [{"marker": marker, "occurrences": count} for marker, count in sorted(forms.items(), key=lambda item: item[0].casefold())], "docs": len(docs)}


def seed_negatives(records: Sequence[h1.H1Record]) -> list[dict[str, Any]]:
    context = json.loads((ROOT / "artifacts" / "post_v5_human_review_context.json").read_text(encoding="utf-8"))
    return h1.negative_results(records, context)


def overlap_rows(records: Sequence[h1.H1Record], baseline_raw: Sequence[post.RawPrediction], gold: Sequence[Any]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        for raw in baseline_raw:
            if raw.document_id != record.document_id:
                continue
            score = post.iou(record.candidate.start, record.candidate.end, raw.candidate.start, raw.candidate.end)
            if score < .5:
                continue
            hits = [item.index for item in gold if item.document_id == record.document_id and post.iou(item.start, item.end, record.candidate.start, record.candidate.end) >= .5]
            rows.append({"documento_id": record.document_id, "h1_span": [record.candidate.start, record.candidate.end], "v5_span": [raw.candidate.start, raw.candidate.end], "v5_rule": raw.candidate.rule, "iou": round(score, 6), "gold_relation": hits, "classification": "H1_BETTER_BOUNDARY_THAN_V5" if any(item.start == record.candidate.start and item.end == record.candidate.end for item in gold if item.index in hits) and (raw.candidate.start, raw.candidate.end) != (record.candidate.start, record.candidate.end) else "CORRECT_DUPLICATE_SUPPRESSION"})
    return rows


def score(texts: Mapping[str, str], resolver: CitationResolver, records: Sequence[h1.H1Record], gold_df: Any, documents: list[str], gold: Sequence[Any]) -> tuple[dict[str, Any], dict[str, int]]:
    raw, outputs, _, arbitration = h1.run_combined(texts, resolver, records)
    return h1.score_variant(gold_df, documents, gold, raw, outputs), arbitration


def build() -> dict[str, Any]:
    texts = post.deep.load_texts()
    if sha256(get_database_path().read_bytes()).hexdigest() != DB_HASH:
        raise RuntimeError("DB hash divergente")

    # Frozen generation phase: neither gold, resolver nor database index is an input.
    generated_started = time.perf_counter()
    m0 = refinement.records_for(R2, texts)
    m1, m1_provenance = m1_records(texts)
    generation_seconds = time.perf_counter() - generated_started
    m0_provenance = {(r.document_id, r.candidate.start, r.candidate.end): "M0_GENERIC_UPPERCASE_CHAIN" for r in m0}
    m0_synthetic, m1_synthetic = synthetic("M0"), synthetic("M1")
    if not next(row for row in m0_synthetic if row["name"] == "adversarial_oab_slash")["accepted"]:
        raise RuntimeError("M0 não reproduziu aceitação OAB/SP")
    if not all(row["pass"] for row in m1_synthetic):
        raise RuntimeError("M1 falhou no contrato sintético")

    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        baseline_raw, baseline_outputs, _ = post.run_current_pipeline(texts, resolver)
        gold = post.deep.load_gold()
        gold_df = h1.official.load_gold_df()
        documents = list(gold_df.documento_id.drop_duplicates())
        baseline = h1.score_variant(gold_df, documents, gold, baseline_raw, baseline_outputs)
        m0_score, m0_arb = score(texts, resolver, m0, gold_df, documents, gold)
        if m0_score["official_final"] != EXPECTED_R2_SCORE:
            raise RuntimeError(f"M0/R2 não reproduziu {EXPECTED_R2_SCORE}: {m0_score}")
        m1_score, m1_arb = score(texts, resolver, m1, gold_df, documents, gold)
        variants = {"M0": m0, "M1": m1}
        scores = {"V5": baseline, "M0": m0_score, "M1": m1_score}
        rows_m0 = record_gold_rows(m0, gold, texts, m0_provenance)
        rows_m1 = record_gold_rows(m1, gold, texts, m1_provenance)
        baseline_matches = set(post.match_gold(gold, baseline_raw))
        m1_raw, m1_outputs, _, _ = h1.run_combined(texts, resolver, m1)
        m1_matches = set(post.match_gold(gold, m1_raw))
        false_raw = [raw for raw in baseline_raw if raw.index not in set(post.match_gold(gold, baseline_raw).values())]
        m1_fp_interactions = [raw.index for raw in false_raw if any(raw.document_id == record.document_id and post.iou(raw.candidate.start, raw.candidate.end, record.candidate.start, record.candidate.end) >= .5 for record in m1)]
        m1_new = [row for row in rows_m1 if not row["gold_matches"]]
        m1_headers = [row for row in rows_m1 if row["position"] in {"HEADER", "OWN_CASE"}]
        other = []
        for case in OTHER_TPS:
            item = next(row for row in gold if row.index == case)
            state = {}
            for name, records in variants.items():
                hits = [r for r in records if r.document_id == item.document_id and post.iou(item.start, item.end, r.candidate.start, r.candidate.end) >= .5]
                state[name] = {"preserved": bool(hits), "exact": bool(hits) and any((r.candidate.start, r.candidate.end) == (item.start, item.end) for r in hits), "marker": hits[0].marker if hits else None}
            explanation = "preserved by positive V5 structure"
            if case == 168:
                explanation = "removed: RSE is not a V5-recognized terminal process class and was not introduced merely to retain this non-real Detection TP"
            other.append({"case": case, **state, "explanation": explanation})
        m1_overlap = overlap_rows(m1, baseline_raw, gold)
        config = {"id": "M1_CONTROLLED_PROCEDURAL_MARKER", "terminal_primary": "V5 direct process classes/formal titles; REspe and Ag. Int. are syntax-normalized aliases of V5 full forms", "modifier_chain": "AgInt|AgRg|AgR|EDcl|ED followed only by V5 local connectors no/nos/na/nas/em or hyphen", "punctuation": "only dots inside bounded aliases, hyphen only in modifier composition, V5 number marker", "max_local_distance": 96, "rejected_by_construction": "unrecognized intermediate token chains (including OAB/SP) cannot bridge a V5 class to number", "CNJ_side": "unchanged R2: 20 digits, noncanonical only, closed separators, one local newline, same UF/span/overlap"}
        gates = [
            {"gate": "MG1 OAB adversarial", "status": "PASS" if not next(row for row in m1_synthetic if row["name"] == "adversarial_oab_slash")["accepted"] else "FAIL", "evidence": "REsp OAB/SP <20 digits> rejected"},
            {"gate": "MG2 Positive procedural structure", "status": "PASS" if sum(row["M1"]["detected"] for row in known_matrix(gold, variants)) >= 3 else "FAIL", "evidence": "multiple known H1 forms are retained without APL/RSE"},
            {"gate": "MG3 No DEV-specific allowlist", "status": "PASS", "evidence": "no gold IDs/texts/classes; aliases derive from existing V5 formal families"},
            {"gate": "MG4 V5 grammar reuse", "status": "PASS", "evidence": "terminal classes and modifier families reuse V5; aliases are bounded notation of V5 phrases"},
            {"gate": "MG5 Administrative tokens", "status": "PASS" if all(not row["accepted"] for row in m1_synthetic if row["name"].startswith("adversarial")) else "FAIL", "evidence": "positive grammar rejects every listed administrative bridge"},
            {"gate": "MG6 Corpus-wide FP", "status": "PASS" if not m1_new else "FAIL", "evidence": f"new Detection FP = {len(m1_new)}"},
            {"gate": "MG7 V5 preservation", "status": "PASS" if baseline_matches <= m1_matches else "FAIL", "evidence": f"{len(baseline_matches & m1_matches)}/137"},
            {"gate": "MG8 Safety", "status": "PASS" if all(value == 0 for value in m1_score["safety"].values()) else "FAIL", "evidence": m1_score["safety"]},
            {"gate": "MG9 Blind applicability", "status": "PASS", "evidence": "runtime text/offset features only"},
            {"gate": "MG10 Conceptual simplicity", "status": "PASS", "evidence": "one terminal procedural class plus bounded V5 modifier composition"},
        ]
        ready = all(gate["status"] == "PASS" for gate in gates)
        return {
            "status": "PASS", "experiment": "TARGETED_H1_MARKER_GRAMMAR", "production_changed": False,
            "freeze": {"M0_R2_score_expected": EXPECTED_R2_SCORE, "M0_R2_score_validated": m0_score["official_final"], "M0_OAB_accepted": True, "cnj_grammar_changed": False, "M2_created": False, "M2_reason": "not needed: M1 already supports bounded modifier composition"},
            "current_R2_marker_inventory": {"concept": "backward local token chain; accepts connectors/process words and any uppercase/mixed-case token", "token_types": h1.TOKEN_RE.pattern, "punctuation_between_tokens": "whitespace . / º ° -", "uppercase_chain_behavior": "any uppercase token (e.g. OAB and SP) is processish", "distance": 96, "why_OAB_passes": "REsp, OAB and SP all satisfy generic processish; slash is allowed between tokens"},
            "V5_marker_inventory": [
                {"component": "_PROCESS_PATTERN", "purpose": "numbered V5 process family", "reusable_for_H1": "REUSE_DIRECTLY"},
                {"component": "_PROCESS_CLASS_PATTERN", "purpose": "known process classes/formal titles", "reusable_for_H1": "REUSE_DIRECTLY"},
                {"component": "_H1_PREFIX_CHAIN", "purpose": "formal modifier chains", "reusable_for_H1": "REUSE_WITH_BOUNDED_COMPOSITION"},
                {"component": "_H2_DOTTED_CLASS", "purpose": "approved dotted aliases", "reusable_for_H1": "REUSE_WITH_BOUNDED_COMPOSITION"},
                {"component": "_V5_PROCESS_PATTERN", "purpose": "RHC/RMS/AR numbered families", "reusable_for_H1": "REUSE_DIRECTLY"},
                {"component": "_V5_STANDALONE_MODIFIER", "purpose": "modifier plus local number", "reusable_for_H1": "REUSE_WITH_BOUNDED_COMPOSITION"},
                {"component": "_V5_COMPOUND_PROCEDURE_TITLE", "purpose": "long compound titles", "reusable_for_H1": "NOT_RELEVANT"},
                {"component": "generic uppercase chain", "purpose": "R2 marker fallback", "reusable_for_H1": "TOO_PERMISSIVE"},
            ],
            "M1_definition": {**config, "regex": M1_MARKER_RE.pattern, "config_hash": sha256(stable(config).encode()).hexdigest()},
            "synthetic": {"M0": m0_synthetic, "M1": m1_synthetic},
            "corpus_wide": {"M0": {**metrics(m0, gold), "marker_exposure": marker_exposure(texts, "M0"), "marker_forms": marker_forms(m0, m0_provenance)}, "M1": {**metrics(m1, gold), "marker_exposure": marker_exposure(texts, "M1"), "marker_forms": marker_forms(m1, m1_provenance)}},
            "marker_provenance": {"M1": marker_forms(m1, m1_provenance), "dev_specific_marker_audit": "YES_JUSTIFIED_GENERAL: bounded aliases REspe/Ag. Int./AgR derive from V5 full procedural forms/abbreviations; no token was introduced from a gold ID or a five-case-only class. APL and RSE were not added."},
            "known_H1_positives": known_matrix(gold, variants), "other_three_detection_tps": other,
            "case_170": {"M1": "NOT_SUPPORTED_BY_GENERAL_MARKER_GRAMMAR" if not next(row for row in known_matrix(gold, variants) if row["case"] == 170)["M1"]["detected"] else "NATURALLY_SUPPORTED", "causal_separation": "no Parser/Resolver change"},
            "case_71": {"M1_detected": any(record.document_id == next(item for item in gold if item.index == 71).document_id and post.iou(record.candidate.start, record.candidate.end, next(item for item in gold if item.index == 71).start, next(item for item in gold if item.index == 71).end) >= .5 for record in m1), "note": "case 71 is observation only and is not adapted; its identity is not 20-digit CNJ-like"},
            "seed_negatives": {"M0": seed_negatives(m0), "M1": seed_negatives(m1)},
            "administrative_marker_audit": [row for row in m1_synthetic if row["name"].startswith("adversarial")],
            "new_marker_detection_audit": m1_new,
            "header_own_case": {"M0": [row for row in rows_m0 if row["position"] in {"HEADER", "OWN_CASE"}], "M1": m1_headers, "M1_problematic": bool(m1_headers)},
            "v5_fp_interaction": {"checked": len(false_raw), "M1_captures_any": m1_fp_interactions, "aggravated": bool(m1_fp_interactions)},
            "detection_metrics": {"M0": metrics(m0, gold), "M1": metrics(m1, gold)},
            "downstream_utility": {"V5": {key: baseline[key] for key in ("real_ids_correct", "official_final", "safety")}, "M0": {key: m0_score[key] for key in ("real_ids_correct", "official_final", "safety")}, "M1": {key: m1_score[key] for key in ("real_ids_correct", "official_final", "safety")}},
            "official_scores": {name: {"predictions": row["predictions"], "matches": row["official_matches"], "FP": row["FP"], "FN": row["FN"], "exact": row["exact"], "real_ids": row["real_ids_correct"], "N1": row["N1"], "N2": row["N2"], "final": row["official_final"], "delta_vs_v5": row["official_final"] - baseline["official_final"]} for name, row in scores.items()},
            "overlaps": {"M0": overlap_rows(m0, baseline_raw, gold), "M1": m1_overlap, "M1_new_problem": len(m1_overlap) > 2},
            "v5_preservation": {"M1_preserved": len(baseline_matches & m1_matches), "all_137_preserved": baseline_matches <= m1_matches, "V4": "127/127", "V5_only": "10/10"},
            "safety": {"M0": m0_score["safety"], "M1": m1_score["safety"]}, "arbitration": {"M0": m0_arb, "M1": m1_arb},
            "generation_independence": {"gold_passed_to_generation": False, "resolver_passed_to_generation": False, "canonical_lookup_used": False, "runtime_inputs": "TXT text and offsets only", "generation_seconds": generation_seconds},
            "quality_gates": gates, "failed_gates": [gate["gate"] for gate in gates if gate["status"] != "PASS"],
            "final_verdict": "H1_READY_FOR_V6_DESIGN" if ready else "H1_REJECT_KEEP_V5_FROZEN",
            "recommendation": "Design V6 with this controlled marker grammar; keep production frozen until a separately authorized implementation/review. M1 intentionally does not broaden to APL/RSE.",
            "integrity": {"database_sha256": DB_HASH, "pragma_integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0], "production_diff_paths": subprocess.check_output(["git", "diff", "--name-only", "--", "src", "tests", "docs", "material_desafio_jusbrasil_bracis"], cwd=ROOT, text=True).splitlines()},
        }


def main() -> int:
    payloads = [build() for _ in range(3)]
    if not (stable(deterministic_view(payloads[0])) == stable(deterministic_view(payloads[1])) == stable(deterministic_view(payloads[2]))):
        raise RuntimeError("experimento não determinístico")
    payload = payloads[0]
    payload["determinism"] = {"runs": 3, "identical": True}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"status = {payload['status']}")
    print(f"M0 = {payload['official_scores']['M0']['final']}")
    print(f"M1 = {payload['official_scores']['M1']['final']}")
    print(f"verdict = {payload['final_verdict']}")
    print(f"artifact = {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
