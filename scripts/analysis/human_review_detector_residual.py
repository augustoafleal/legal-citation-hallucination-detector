"""Gera um pacote de revisão humana dirigida para os residuais do Detector V4.

Este módulo é somente análise. Ele recompõe a pipeline atual, usa gold apenas
depois de construir as evidências do runtime e não altera componentes de
produção, testes, documentação ou o banco.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DIR = Path(__file__).resolve().parent
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import post_resolver_v3_remaining_audit as post
from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import CitationResolver
from bracis_jusbrasil.database import connect_database, get_database_path


JSON_PATH = ROOT / "artifacts" / "human_review_detector_residual.json"
HTML_PATH = ROOT / "artifacts" / "human_review_detector_residual.html"
RESPONSE_PATH = ROOT / "artifacts" / "human_review_detector_residual_response.md"
EXPECTED_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
EXPECTED_DETECTOR = (211, 127, 84, 98, 73)
EXPECTED_POST = (56, 60, 206, 0, 0, 0)
STATUS_ORDER = ("resolved", "no_match", "ambiguous", "insufficient", "missing")

CNJ = re.compile(r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b")
UF = re.compile(r"(?:/|-|\()\s*[A-Z]{2}\b")
NUMBER = re.compile(r"\d+(?:[.]\d+)*")
# Only flag a look-alike letter when it is embedded in an otherwise numeric
# token. A token made exclusively of digits is not evidence of OCR.
OCR = re.compile(r"(?<!\w)[0-9]*[OolISs][0-9OolISs]*(?!\w)")


class AuditError(RuntimeError):
    pass


def stable(value: Any) -> Any:
    """Remove somente valores de execução que variam entre rodadas."""
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items() if key not in {"timing_seconds", "runtime_seconds", "pipeline_seconds", "output_tail"}}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def as_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_json(item) for item in value]
    if hasattr(value, "__dict__"):
        return {str(key): as_json(item) for key, item in value.__dict__.items()}
    return value


def result_dict(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "status": result.status,
        "id_canonico": result.id_canonico,
        "candidate_ids": list(result.candidate_ids),
        "strategy": result.strategy,
        "reason": result.reason,
        "record_type": result.record_type,
    }


def parsed_dict(parsed: Any) -> dict[str, Any] | None:
    if parsed is None:
        return None
    return {
        "family": parsed.family,
        "tipo": parsed.tipo,
        "tribunal": parsed.tribunal,
        "tribunal_raw": parsed.tribunal_raw,
        "tribunal_source": parsed.tribunal_source,
        "data": dict(parsed.data),
        "provenance": dict(parsed.provenance),
    }


def iou(left: Sequence[int], right: Sequence[int]) -> float:
    intersection = max(0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return intersection / union if union else 0.0


def context_slice(text: str, start: int, end: int, radius_before: int = 220, radius_after: int = 220) -> tuple[int, int]:
    return max(0, start - radius_before), min(len(text), end + radius_after)


def structural_features(text: str, family: str) -> dict[str, Any]:
    number_match = NUMBER.search(text)
    prefix = text[:number_match.start()] if number_match else text
    words = re.findall(r"[\wÀ-ÿ]+", prefix)
    return {
        "family": family,
        "has_cnj_shape": bool(CNJ.search(text)),
        "has_newline": "\n" in text,
        "has_uf_like_suffix": bool(UF.search(text)),
        "has_numeric_separator": bool(re.search(r"[-/.]", text)),
        "compound_prefix_word_count": len(words),
        "compound_prefix": len(words) >= 3,
        "possible_local_ocr": bool(OCR.search(text)),
        "connectors": [token.lower() for token in re.findall(r"\b(?:no|na|nos|nas|do|da|dos|das)\b", text, re.I)],
    }


def record_dict(connection: Any, record_id: int | None, resolver: Any = None) -> dict[str, Any] | None:
    if record_id is None:
        return None
    row = connection.execute(
        "SELECT documento_id, id, tribunal, ano, relator, natureza, tipo, texto, texto_len "
        "FROM documentos WHERE id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["text_excerpt"] = result["texto"][:360]
    del result["texto"]
    if resolver is not None:
        identity = resolver.primary_identities.get(record_id)
        if identity is not None:
            result["primary_identity"] = {
                "id_canonico": identity.id_canonico,
                "tribunal": identity.tribunal,
                "numero_raw": identity.numero_raw,
                "numero_normalizado": identity.numero_normalizado,
                "classe": identity.classe,
            }
    return result


def raw_prediction_dict(item: Any, gold_span: Sequence[int], gold_text: str) -> dict[str, Any]:
    span = [item.candidate.start, item.candidate.end]
    return {
        "index": item.index,
        "span": span,
        "text": item.candidate.text,
        "family": item.candidate.family,
        "rule": item.candidate.rule,
        "iou": round(iou(gold_span, span), 6),
        "parsed": parsed_dict(item.parsed),
        "resolution": result_dict(item.result),
        "distance_to_gold": min(abs(span[0] - gold_span[1]), abs(span[1] - gold_span[0])),
    }


def relevant_raw(raw: Sequence[Any], document: str, gold_span: Sequence[int], gold_text: str) -> list[dict[str, Any]]:
    candidates = [item for item in raw if item.document_id == document]
    candidates.sort(key=lambda item: (-iou(gold_span, [item.candidate.start, item.candidate.end]), min(abs(item.candidate.start - gold_span[1]), abs(item.candidate.end - gold_span[0])), item.index))
    overlapping = [item for item in candidates if iou(gold_span, [item.candidate.start, item.candidate.end]) > 0]
    selected = overlapping[:3]
    for item in candidates:
        if item not in selected:
            selected.append(item)
        if len(selected) >= 5:
            break
    return [raw_prediction_dict(item, gold_span, gold_text) for item in selected]


def best_candidate(candidates: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return candidates[0] if candidates else None


def error_type(item: Mapping[str, Any]) -> str:
    candidates = item["current_candidates"]
    best = best_candidate(candidates)
    if best is None or best["iou"] == 0:
        return "MISS_COMPLETO"
    gold_start, gold_end = item["gold_span"]
    start, end = best["span"]
    if best["family"] != item["oracle_parse"]["family"]:
        return "FAMILY_INCORRETA"
    missing_prefix = start > gold_start
    missing_suffix = end < gold_end
    if missing_prefix and missing_suffix:
        return "BOUNDARY_AMBOS"
    if missing_prefix:
        return "PREFIXO_FALTANDO"
    if missing_suffix:
        return "SUFIXO_FALTANDO"
    if item["structural_features"]["possible_local_ocr"]:
        return "OCR"
    return "OVERLAP/NESTING"


def missing_text(gold_text: str, gold_span: Sequence[int], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    best = best_candidate(candidates)
    if best is None:
        return {"prefix": gold_text, "suffix": "", "candidate_only_prefix": "", "candidate_only_suffix": "", "entire_gold_missing": True}
    start, end = best["span"]
    gs, ge = gold_span
    prefix = gold_text[: max(0, min(len(gold_text), start - gs))] if start >= gs else ""
    suffix = gold_text[max(0, min(len(gold_text), end - gs)) :] if end <= ge else ""
    candidate_only_prefix = "" if start >= gs else best["text"][: gs - start]
    candidate_only_suffix = "" if end <= ge else best["text"][ge - start :]
    return {
        "prefix": prefix,
        "suffix": suffix,
        "candidate_only_prefix": candidate_only_prefix,
        "candidate_only_suffix": candidate_only_suffix,
        "entire_gold_missing": best["iou"] == 0,
    }


def prompts_for(case: Mapping[str, Any], primary: bool = True) -> dict[str, Any]:
    features = case["structural_features"]
    kind = case.get("error_type", "UNSUPPORTED")
    look_for = [
        "continuidade sintática entre classe, conectores e número",
        "delimitador forte que poderia encerrar a referência",
        "se a mesma construção aparece em outros documentos",
    ]
    if features.get("has_cnj_shape"):
        look_for.append("cadeia tribunal + classe processual + número CNJ")
    if features.get("compound_prefix"):
        look_for.append("modificadores como AgInt, AgRg, EDcl ou recurso dentro de recurso")
    if features.get("has_newline"):
        look_for.append("se a quebra de linha é apenas formatação ou separação semântica")
    if features.get("has_uf_like_suffix"):
        look_for.append("UF, hífen, barra e parênteses pertencendo ao identificador")
    if features.get("possible_local_ocr"):
        look_for.append("corrupção OCR local dentro da parte numérica")
    objective = (
        "Verifique se o Detector perdeu uma parte da mesma referência e se uma regra local, "
        "generalizável e com guards poderia recuperá-la sem absorver narrativa."
        if primary else
        "Verifique se existe identidade estrutural explícita que a taxonomia automática não modelou, sem tentar resolver o caso à força."
    )
    return {
        "OBJETIVO": objective,
        "O_QUE_OBSERVAR": [
            "se o gold começa no início natural da referência e termina antes de narrativa",
            "se a parte perdida é semanticamente necessária para a identidade",
            "se o downstream oracle resolve de modo único e se isso depende do gold",
        ],
        "PROCURE_POR": look_for[:8],
        "TENTE_QUEBRAR_A_HIPOTESE": "Imagine aplicar a mesma expansão em outro ponto do documento: ela capturaria narrativa, datas, números comuns ou outra referência aninhada?",
        "SINAL_FORTE_SE": ["há pelo menos 2 casos e 2 documentos", "negativos semelhantes podem ser rejeitados por guard estrutural", "a regra não menciona ID, documento ou frase específica"],
        "SINAL_FRACO_SE": ["há um único caso", "a regra depende de palavra específica", "a expansão atravessa sentença ou não tem negativo comparável"],
        "QUESTOES": {
            "Q1_unique_reference": {"prompt": "Um humano lendo apenas o contexto identificaria claramente uma única citação?", "options": ["SIM", "NÃO", "INCERTO"]},
            "Q2_gold_span": {"prompt": "O gold span parece semanticamente correto?", "options": ["SIM", "PARCIALMENTE", "NÃO", "INCERTO"]},
            "Q3_error_type": {"prompt": "Qual é o erro do Detector?", "options": ["MISS_COMPLETO", "PREFIXO_FALTANDO", "SUFIXO_FALTANDO", "BOUNDARY_AMBOS", "FAMILY_INCORRETA", "OCR", "OVERLAP/NESTING", "OUTRO", "INCERTO"]},
            "Q4_same_reference": {"prompt": "A parte perdida pertence inequivocamente à mesma referência?", "options": ["SIM", "NÃO", "INCERTO"]},
            "Q5_pattern": {"prompt": "Há um padrão estrutural reconhecível? Descreva-o.", "options": ["SIM", "NÃO", "INCERTO"]},
            "Q6_generalization": {"prompt": "O padrão parece geral ou específico?", "options": ["GENERALIZÁVEL", "PROVAVELMENTE_GENERALIZÁVEL", "CASO_ESPECÍFICO", "INCERTO"]},
            "Q7_rule": {"prompt": "Uma regex/regra local sem IDs ou frases específicas parece apropriada?", "options": ["SIM", "TALVEZ", "NÃO"]},
            "Q8_risks": {"prompt": "Qual o risco principal?", "options": ["CAPTURAR_TEXTO_NARRATIVO", "CRIAR_NOVOS_FP", "CONFUNDIR_REFERENCIAS_NESTED", "OCR_ESPECIFICO_DEMAIS", "FAMILY_AMBIGUA", "ATRAVESSAR_QUEBRA_DE_LINHA", "OVERFIT_LEXICAL", "SEM_RISCO_EVIDENTE", "OUTRO"], "multiple": True},
            "Q9_promote": {"prompt": "Você promoveria este padrão para investigação automática?", "options": ["SIM", "NÃO", "SOMENTE_SE_APARECER_EM_OUTROS_CASOS", "INCERTO"]},
            "Q10_notes": {"prompt": "Observação livre.", "options": []},
        },
        "HUMAN_ANSWER": None,
        "error_type_hint": kind,
    }


def record_case(base: Mapping[str, Any], raw: Sequence[Any], texts: Mapping[str, str], connection: Any, resolver: Any, primary: bool = True) -> dict[str, Any]:
    item = dict(base)
    text = texts[item["documento"]]
    gold_span = item.get("gold_span", [0, 0])
    gold_text = item.get("gold_text", text[gold_span[0] : gold_span[1]])
    candidates = relevant_raw(raw, item["documento"], gold_span, gold_text)
    oracle = item.get("oracle", {})
    item["gold_span"] = list(gold_span)
    item["gold_text"] = gold_text
    item["current_candidates"] = candidates
    item["current_best_iou"] = max((candidate["iou"] for candidate in candidates), default=0.0)
    item["oracle_parse"] = oracle.get("parsed")
    item["oracle_resolution"] = oracle.get("resolution")
    best = best_candidate(candidates)
    item["current_parser"] = None if best is None else best.get("parsed")
    item["current_resolution"] = None if best is None else best.get("resolution")
    item["error_type"] = error_type(item)
    item["missing_text"] = missing_text(gold_text, gold_span, candidates)
    item["structural_features"] = structural_features(gold_text, item.get("gold_family", item.get("family", "unknown")))
    item["canonical_target"] = record_dict(connection, item.get("gold_id"), resolver)
    start, end = context_slice(text, gold_span[0], gold_span[1])
    item["context"] = {"start": start, "end": end, "text": text[start:end]}
    item["review_prompts"] = prompts_for(item, primary=primary)
    return item


def target_indexes(items: Iterable[Mapping[str, Any]]) -> set[int]:
    return {int(item["gold_index"]) for item in items}


def hypothesis_predicate(name: str, text: str, family: str) -> bool:
    features = structural_features(text, family)
    return {
        "H1_CNJ_PROCEDURAL_PREFIX": features["has_cnj_shape"],
        "H2_NEWLINE_BOUNDARY": features["has_newline"],
        "H3_COMPOUND_PROCEDURAL_CHAIN": features["compound_prefix"],
        "H4_UF_OR_JURISDICTION_SUFFIX": features["has_uf_like_suffix"],
        "H5_LOCAL_OCR_NUMERIC": features["possible_local_ocr"],
    }.get(name, False)


def negative_sample(case: Mapping[str, Any], gold_item: Any, texts: Mapping[str, str], raw: Sequence[Any], outputs: Sequence[Any]) -> dict[str, Any]:
    document = gold_item.document_id
    span = [gold_item.start, gold_item.end]
    nearby = relevant_raw(raw, document, span, gold_item.text)
    output_matches = [output for output in outputs if output.document_id == document and iou(span, [output.candidate.start, output.candidate.end]) >= 0.5]
    return {
        "is_not_target": True,
        "gold_index": gold_item.index,
        "document": document,
        "level": gold_item.level,
        "classification": gold_item.classification,
        "span": span,
        "text": gold_item.text,
        "context": texts[document][max(0, gold_item.start - 150) : min(len(texts[document]), gold_item.end + 180)],
        "candidate": nearby[0] if nearby else None,
        "downstream_status": None if not output_matches else result_dict(output_matches[0].result),
        "instructions": {
            "OBJECTIVE": "Este caso NÃO é um target. Use-o para tentar quebrar a hipótese.",
            "PROCURE_POR": ["se a regra candidata também capturaria este trecho", "se expandiria demais", "se criaria uma citação inexistente", "se o delimitador difere do target", "se inventada/incompleta ficaria perigosa"],
            "QUESTIONS": ["A regra capturaria?", "Se capturasse, seria correto?", "Qual pista separa este caso do target?"],
            "HUMAN_ANSWER": None,
        },
    }


def make_hypotheses(detector_cases: Sequence[Mapping[str, Any]], gold: Sequence[Any], texts: Mapping[str, str], raw: Sequence[Any], outputs: Sequence[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_ids = target_indexes(detector_cases)
    names = [name for name in ("H1_CNJ_PROCEDURAL_PREFIX", "H2_NEWLINE_BOUNDARY", "H3_COMPOUND_PROCEDURAL_CHAIN", "H4_UF_OR_JURISDICTION_SUFFIX", "H5_LOCAL_OCR_NUMERIC") if any(hypothesis_predicate(name, case["gold_text"], case["gold_family"]) for case in detector_cases)]
    hypotheses: list[dict[str, Any]] = []
    negative_cards: list[dict[str, Any]] = []
    for name in names:
        positives = [case for case in detector_cases if hypothesis_predicate(name, case["gold_text"], case["gold_family"])]
        negative_gold = [item for item in gold if item.index not in target_ids and hypothesis_predicate(name, item.text, post.family_for(item.text, item.citation_type))]
        negative_gold.sort(key=lambda item: (item.classification == "real", item.index))
        samples = [negative_sample(positives[0], item, texts, raw, outputs) for item in negative_gold[:5]]
        negative_cards.extend([{**sample, "hypothesis": name} for sample in samples])
        docs = sorted({case["documento"] for case in positives})
        evidence = "STRONG" if len(positives) >= 2 and len(docs) >= 2 and samples else "MODERATE" if len(positives) >= 2 else "WEAK"
        hypotheses.append({
            "hypothesis": name,
            "target_cases": [case["gold_index"] for case in positives],
            "documents": docs,
            "N1_N2": dict(Counter(case["nivel"] for case in positives)),
            "negative_examples": len(samples),
            "automated_risk": "HIGH" if not samples else "MEDIUM-HIGH" if evidence != "STRONG" else "MEDIUM",
            "evidence": evidence,
            "description": {
                "H1_CNJ_PROCEDURAL_PREFIX": "prefixo processual amplo antes de número CNJ",
                "H2_NEWLINE_BOUNDARY": "quebra de linha dentro ou imediatamente antes da referência",
                "H3_COMPOUND_PROCEDURAL_CHAIN": "cadeia de modificadores/classe composta antes do número",
                "H4_UF_OR_JURISDICTION_SUFFIX": "UF ou delimitador jurisdicional associado ao número",
                "H5_LOCAL_OCR_NUMERIC": "possível corrupção OCR local na estrutura numérica",
            }[name],
            "human_verdict": None,
        })
    return hypotheses, negative_cards


def primary_identity_conflict(case: Mapping[str, Any], resolver: Any, connection: Any) -> dict[str, Any]:
    parsed = case.get("oracle", {}).get("parsed", {})
    number = parsed.get("data", {}).get("numero_normalizado")
    candidates = [identity.id_canonico for identity in resolver.primary_identities.values() if identity.numero_normalizado == number]
    return {
        "citation": case.get("gold_text"),
        "gold_target": record_dict(connection, case.get("gold_id"), resolver),
        "primary_number_candidates": [record_dict(connection, candidate, resolver) for candidate in sorted(candidates)],
        "parsed": parsed,
        "current_resolution": case.get("oracle", {}).get("resolution"),
        "instruction": "Procure se a identidade primária do gold corresponde ao CNJ ou se o documento apenas menciona esse CNJ no corpo.",
        "questions": {
            "primary_matches_cnj": ["SIM", "NÃO", "INCERTO"],
            "class_matches_citation": ["SIM", "NÃO", "INCERTO"],
            "gold_is_source_or_citing_document": ["FONTE", "DOCUMENTO_QUE_CITA", "INCERTO"],
            "correct_action": ["ABSTENTION_CORRECT", "GOLD_CORPUS_CONFLICT_CONFIRMED", "SAFE_RESOLUTION_EXISTS", "UNCERTAIN"],
        },
        "human_answer": None,
    }


def build_once(run_tests: bool) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    database_path = get_database_path().resolve()
    if sha256(database_path.read_bytes()).hexdigest() != EXPECTED_HASH:
        raise AuditError("database hash diverged")
    texts = post.deep.load_texts()
    gold = post.deep.load_gold()
    with connect_database(database_path, read_only=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {row["natureza"]: row["count"] for row in connection.execute("SELECT natureza, COUNT(*) AS count FROM documentos GROUP BY natureza")}
        if integrity != "ok" or counts != {"acordao": 998, "dispositivo": 13, "sumula": 5}:
            raise AuditError(f"database guard diverged: {integrity}, {counts}")
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        raw, outputs, pipeline_seconds = post.run_current_pipeline(texts, resolver)
        deep_raw = [post.deep.Prediction(item.index, item.document_id, item.candidate.start, item.candidate.end, item.candidate.text, item.candidate.rule, item.candidate.family, item.parsed, item.result) for item in raw]
        detector_matches = post.deep.match_predictions(gold, deep_raw)
        detector = {
            "predictions": len(raw), "TP": len(detector_matches), "FP": len(raw) - len(detector_matches),
            "FN": len(gold) - len(detector_matches),
            "exact": sum(raw[detector_matches[item.index]].candidate.start == item.start and raw[detector_matches[item.index]].candidate.end == item.end for item in gold if item.index in detector_matches),
        }
        if tuple(detector[key] for key in ("predictions", "TP", "FP", "FN", "exact")) != EXPECTED_DETECTOR:
            raise AuditError(f"Detector V4 diverged: {detector}")
        # The pre-arbitration matching is against raw Detector V4 predictions;
        # post.match_gold indexes the merged output list and cannot be reused.
        before_correct = sum(
            item.classification == "real"
            and raw[detector_matches[item.index]].result.status == "resolved"
            and raw[detector_matches[item.index]].result.id_canonico == item.canonical_id
            for item in gold if item.index in detector_matches
        )
        post_metrics = post.output_metrics(gold, outputs)
        observed_post = (before_correct, post_metrics["correct_ids"], post_metrics["outputs"], post_metrics["wrong_unique_real"], post_metrics["false_real_inventada"], post_metrics["false_real_incompleta"])
        if observed_post != EXPECTED_POST:
            raise AuditError(f"Resolver/arbitration diverged: {observed_post}")
        remaining = post.remaining_inventory(gold, texts, raw, outputs, resolver)
        detector_raw = [item for item in remaining if item["root_cause"] == "detector_residual"]
        unsupported_raw = [item for item in remaining if item["root_cause"] == "unsupported_reference_family"]
        ambiguous_raw = [item for item in remaining if item["first_blocker"] == "resolver_ambiguous"]
        conflict_raw = [item for item in remaining if item["root_cause"] == "gold_corpus_inconsistency"]
        if (len(detector_raw), len(unsupported_raw), len(ambiguous_raw), len(conflict_raw)) != (7, 9, 2, 1):
            raise AuditError(f"review universe diverged: {len(detector_raw)}, {len(unsupported_raw)}, {len(ambiguous_raw)}, {len(conflict_raw)}")
        detector_cases = [record_case(item, raw, texts, connection, resolver, primary=True) for item in detector_raw]
        hypotheses, negative_cards = make_hypotheses(detector_cases, gold, texts, raw, outputs)
        unsupported_cases = [record_case(item, raw, texts, connection, resolver, primary=False) for item in unsupported_raw]
        for item in unsupported_cases:
            item["review_prompts"]["QUESTOES"] = {
                "identity_signal": {"prompt": "Existe alguma identidade estrutural explícita que a taxonomia ignorou?", "options": ["STRUCTURAL_SIGNAL_PRESENT", "PARTIAL_STRUCTURAL_SIGNAL", "CONTEXTUAL_ONLY", "NO_RUNTIME_IDENTITY", "GOLD_SPAN_QUESTIONABLE", "UNCERTAIN"]},
                "gold_free_sufficiency": {"prompt": "Sem conhecer o gold ID, o texto contém informação suficiente para um único registro?", "options": ["SIM", "TALVEZ", "NÃO", "INCERTO"]},
            }
        ambiguous_cases = [record_case(item, raw, texts, connection, resolver, primary=False) for item in ambiguous_raw]
        for item in ambiguous_cases:
            ids = [] if item["current_resolution"] is None else item["current_resolution"].get("candidate_ids", [])
            item["candidate_primary_metadata"] = [record_dict(connection, int(candidate), resolver) for candidate in ids]
            item["review_prompts"]["QUESTOES"] = {"runtime_evidence": {"prompt": "Existe evidência runtime para escolher um ID?", "options": ["SIM", "NÃO", "INCERTO"]}, "discriminating_field": {"prompt": "Se SIM, qual campo?", "options": []}}
        conflict_case = record_case(conflict_raw[0], raw, texts, connection, resolver, primary=False)
        conflict_case["gold_corpus_evidence"] = primary_identity_conflict(conflict_case, resolver, connection)
        conflict_case["review_prompts"]["QUESTOES"] = conflict_case["gold_corpus_evidence"]["questions"]
        tests = None
        if run_tests:
            command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
            process = subprocess.run(command, cwd=ROOT, env={**dict(__import__("os").environ), "PYTHONPATH": "src"}, text=True, capture_output=True, timeout=120)
            match = re.search(r"Ran (\d+) tests", process.stdout + process.stderr)
            tests = {"command": "PYTHONPATH=src venv/bin/python -m unittest discover -s tests -v", "returncode": process.returncode, "tests": int(match.group(1)) if match else None, "passed": process.returncode == 0, "output_tail": (process.stdout + process.stderr)[-500:]}
            if tests["returncode"] != 0 or tests["tests"] != 82:
                raise AuditError(f"tests diverged: {tests}")
        payload: dict[str, Any] = {
            "status": "PASS WITH WARNINGS",
            "baseline": {"detector": detector, "resolver_before_arbitration": before_correct, "post_arbitration": {key: value for key, value in post_metrics.items() if key != "matches"}, "safety": {key: post_metrics[key] for key in ("wrong_unique_real", "false_real_inventada", "false_real_incompleta")}, "database": {"path": str(database_path.relative_to(ROOT)), "sha256": EXPECTED_HASH, "integrity_check": integrity, "records": sum(counts.values()), "natureza": counts}, "tests": tests or {"executed": False}},
            "detector_residual_cases": detector_cases,
            "hypotheses": hypotheses,
            "negative_samples": negative_cards,
            "unsupported_cases": unsupported_cases,
            "ambiguous_cases": ambiguous_cases,
            "gold_corpus_case": conflict_case,
            "review_questions": {"human_decision": "PENDING_HUMAN_REVIEW", "options": ["A) HUMAN_EVIDENCE_SUPPORTS_DETECTOR_V5_HYPOTHESIS", "B) MIXED_EVIDENCE_NEEDS_TARGETED_EXPERIMENT", "C) HUMAN_EVIDENCE_SUPPORTS_FREEZING_DETECTOR_V4"]},
            "review_template": {"case_id": None, "unique_reference": None, "gold_span_quality": None, "error_type": None, "same_reference": None, "structural_pattern": None, "generalization": None, "rule_suitability": None, "risks": [], "promote_for_experiment": None, "notes": ""},
            "artifacts": {"html": str(HTML_PATH.relative_to(ROOT)), "json": str(JSON_PATH.relative_to(ROOT)), "response_sheet": str(RESPONSE_PATH.relative_to(ROOT))},
            "automated_summary": {"decision": "PENDING_HUMAN_REVIEW", "detector_residual": 7, "unsupported": 9, "tst_ambiguous": 2, "gold_corpus_conflict": 1, "review_cards": 7 + 9 + 2 + 1, "negative_cards": len(negative_cards), "hypotheses_without_human_verdict": sum(item["human_verdict"] is None for item in hypotheses), "note": "automated evidence is descriptive; it does not decide Detector V5"},
            "timing_seconds": time.perf_counter() - started,
            "pipeline_seconds": pipeline_seconds,
        }
        return payload, time.perf_counter() - started


def marked_context(case: Mapping[str, Any]) -> str:
    context = case["context"]
    text = context["text"]
    base = context["start"]
    gold_start, gold_end = case["gold_span"]
    gold_start -= base
    gold_end -= base
    best = best_candidate(case.get("current_candidates", []))
    candidate_span = None if best is None else [best["span"][0] - base, best["span"][1] - base]
    points = {0, len(text), max(0, gold_start), min(len(text), gold_end)}
    if candidate_span is not None:
        points.update((max(0, candidate_span[0]), min(len(text), candidate_span[1])))
    points = sorted(point for point in points if 0 <= point <= len(text))
    chunks: list[str] = []
    for left, right in zip(points, points[1:]):
        if left == right:
            continue
        in_gold = left >= gold_start and right <= gold_end
        in_candidate = candidate_span is not None and left >= candidate_span[0] and right <= candidate_span[1]
        klass = "overlap" if in_gold and in_candidate else "gold" if in_gold else "v4" if in_candidate else "plain"
        chunks.append(f'<span class="{klass}">{html.escape(text[left:right])}</span>')
    return "".join(chunks)


def pre(value: Any) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def html_prompts(prompts: Mapping[str, Any]) -> str:
    parts = [f"<h4>{html.escape(key.replace('_', ' '))}</h4>" for key in ("OBJETIVO", "O_QUE_OBSERVAR", "PROCURE_POR", "TENTE_QUEBRAR_A_HIPOTESE", "SINAL_FORTE_SE", "SINAL_FRACO_SE") if key in prompts]
    result = []
    for key in ("OBJETIVO", "O_QUE_OBSERVAR", "PROCURE_POR", "TENTE_QUEBRAR_A_HIPOTESE", "SINAL_FORTE_SE", "SINAL_FRACO_SE"):
        if key not in prompts:
            continue
        value = prompts[key]
        body = "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in value) + "</ul>" if isinstance(value, list) else f"<p>{html.escape(str(value))}</p>"
        result.append(f"<h4>{html.escape(key.replace('_', ' '))}</h4>{body}")
    return "".join(result)


def detector_card(case: Mapping[str, Any]) -> str:
    candidates = case["current_candidates"]
    comparison = {"gold": case["gold_text"], "V4_candidates": [{"text": item["text"], "span": item["span"], "iou": item["iou"]} for item in candidates], "missing_or_extra": case["missing_text"]}
    return f"""<article class="card"><h3>D{case['gold_index']} · {html.escape(case['documento'])} · {html.escape(case['nivel'])}</h3>
<p><b>OBJETIVO DESTE CARD</b></p>{html_prompts(case['review_prompts'])}
<p><b>CONTEXTO ORIGINAL — legend:</b> <span class="gold">GOLD</span> <span class="v4">V4</span> <span class="overlap">OVERLAP</span></p><div class="context">{marked_context(case)}</div>
<p><b>GOLD:</b> <code>{html.escape(case['gold_text'])}</code></p><p><b>Erro sugerido:</b> {html.escape(case['error_type'])} · <b>IoU V4 máximo:</b> {case['current_best_iou']}</p>
<details open><summary>Gold vs V4 e texto faltante/extra</summary><pre>{html.escape(json.dumps(comparison, ensure_ascii=False, indent=2))}</pre></details>
<details><summary>Parser/Resolver atual versus oracle</summary><pre>{pre({'current_parser': case['current_parser'], 'current_resolution': case['current_resolution'], 'oracle_parse': case['oracle_parse'], 'oracle_resolution': case['oracle_resolution'], 'canonical_target': case['canonical_target']})}</pre></details>
<details><summary>Contexto completo deste card</summary><pre>{html.escape(case['context']['text'])}</pre></details>
<p><b>HUMAN ANSWER:</b> preencher somente na folha de respostas.</p></article>"""


def negative_card(item: Mapping[str, Any]) -> str:
    return f"""<article class="negative card"><h3>NEGATIVO · {html.escape(item['hypothesis'])} · gold {item['gold_index']}</h3>
<p><b>OBJECTIVE:</b> Este caso NÃO é um dos targets. Use-o para tentar quebrar a hipótese.</p><div class="context">{html.escape(item['context'])}</div>
<p><b>Trecho:</b> <code>{html.escape(item['text'])}</code> · <b>classe:</b> {html.escape(item['classification'])} · <b>documento:</b> {html.escape(item['document'])}</p>
<h4>PROCURE POR</h4><ul>{''.join(f"<li>{html.escape(x)}</li>" for x in item['instructions']['PROCURE_POR'])}</ul>
<h4>TENTE QUEBRAR A HIPÓTESE</h4><p>Compare este delimitador e esta forma numérica com o target: a regra capturaria texto narrativo ou criaria uma citação inexistente?</p>
<details><summary>Candidate/downstream</summary><pre>{pre({'candidate': item['candidate'], 'downstream_status': item['downstream_status']})}</pre></details>
<p><b>HUMAN ANSWER:</b> preencher na folha.</p></article>"""


def unsupported_card(case: Mapping[str, Any]) -> str:
    return f"""<article class="card"><h3>U{case['gold_index']} · {html.escape(case['documento'])} · {html.escape(case['nivel'])}</h3>
<p><b>OBJETIVO DESTE CARD</b>: confirmar se a taxonomia automática ignorou identidade runtime explícita; não tentar resolver à força.</p><p><b>CONTEXTO:</b></p><div class="context">{html.escape(case['context']['text'])}</div><p><b>Trecho:</b> <code>{html.escape(case['gold_text'])}</code> · <b>family:</b> {html.escape(case['gold_family'])}</p>
<h4>O QUE OBSERVAR</h4><ul><li>tribunal, classe, número, UF, ano, relator ou súmula explícitos;</li><li>se o texto é apenas referência indireta ou narrativa;</li><li>se um único registro seria encontrável sem conhecer o gold ID.</li></ul><h4>PROCURE POR</h4><ul><li>chave estrutural completa</li><li>delimitadores e OCR local</li><li>pistas que diferenciem este caso de referência textual</li></ul><h4>TENTE QUEBRAR A HIPÓTESE</h4><p>Remova mentalmente o gold ID: ainda sobraria uma identidade runtime única?</p><details><summary>Detalhes técnicos</summary><pre>{pre({'current_candidates': case['current_candidates'], 'current_parser': case['current_parser'], 'current_resolution': case['current_resolution'], 'oracle_parse': case['oracle_parse'], 'oracle_resolution': case['oracle_resolution']})}</pre></details><p><b>HUMAN ANSWER:</b> preencher na folha.</p></article>"""


def ambiguous_card(case: Mapping[str, Any]) -> str:
    return f"""<article class="card"><h3>TST AMBIGUOUS · gold {case['gold_index']} · {html.escape(case['documento'])}</h3>
<p><b>OBJETIVO DESTE CARD:</b> testar se alguma diferença presente na citação também existe na metadata canônica e escolhe exatamente um ID.</p><div class="context">{html.escape(case['context']['text'])}</div><p><b>Citação:</b> <code>{html.escape(case['gold_text'])}</code></p>
<h4>O QUE OBSERVAR</h4><ul><li>classe, tribunal, relator, ano, órgão, número, UF ou marcador procedural;</li><li>se a diferença é realmente disponível no runtime;</li><li>se os dois candidatos são near-duplicates e a ambiguidade é genuína.</li></ul><h4>PROCURE POR</h4><ul><li>qualquer campo discriminante presente na citação</li><li>diferença primária entre os IDs</li><li>risco de escolher pelo texto do corpo</li></ul><h4>TENTE QUEBRAR A HIPÓTESE</h4><p>Procure uma razão para que qualquer filtro proposto também escolha o ID errado em outra ocorrência.</p><details open><summary>Candidate IDs e metadata</summary><pre>{pre({'current_resolution': case['current_resolution'], 'candidate_primary_metadata': case.get('candidate_primary_metadata'), 'canonical_target': case['canonical_target']})}</pre></details><p><b>HUMAN ANSWER:</b> preencher na folha.</p></article>"""


def conflict_card(case: Mapping[str, Any]) -> str:
    return f"""<article class="card"><h3>GOLD/CORPUS CONFLICT · gold {case['gold_index']} · {html.escape(case['documento'])}</h3>
<p><b>OBJETIVO DESTE CARD:</b> verificar se o gold aponta para documento que menciona o CNJ, enquanto a identidade primária aponta para outro registro.</p><div class="context">{html.escape(case['context']['text'])}</div><p><b>Citação:</b> <code>{html.escape(case['gold_text'])}</code></p>
<h4>O QUE OBSERVAR</h4><ul><li>se o gold possui a referência como identidade primária ou apenas no corpo;</li><li>se classe primária e classe citada entram em conflito;</li><li>se abstention é o comportamento correto.</li></ul><h4>PROCURE POR</h4><ul><li>CNJ no corpo e primary identity do registro</li><li>class mismatch</li><li>diferença entre documento-fonte e documento-que-cita</li></ul><h4>TENTE QUEBRAR A HIPÓTESE</h4><p>Verifique se existe algum campo runtime que reconcilie os dois IDs sem usar o gold.</p><details open><summary>Evidência gold/corpus</summary><pre>{pre(case['gold_corpus_evidence'])}</pre></details><p><b>HUMAN ANSWER:</b> preencher na folha.</p></article>"""


def html_report(payload: Mapping[str, Any]) -> str:
    rows = "".join(f"<tr><td>{html.escape(item['hypothesis'])}</td><td>{', '.join(map(str, item['target_cases']))}</td><td>{len(item['documents'])}</td><td>{html.escape(str(item['N1_N2']))}</td><td>{item['negative_examples']}</td><td>{item['evidence']}</td><td>HUMAN VERDICT: ______</td></tr>" for item in payload["hypotheses"])
    css = """body{font:15px/1.45 system-ui,sans-serif;max-width:1200px;margin:24px auto;padding:0 18px;color:#17202a}article{border:1px solid #cbd5e1;border-radius:9px;padding:16px;margin:18px 0}.warning{background:#fff4cc;border-left:5px solid #d97706;padding:14px}.context{white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;padding:12px;font-family:ui-monospace,monospace}.gold{background:#fecaca}.v4{background:#bfdbfe}.overlap{background:#c4b5fd}.negative{background:#fff7ed}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:10px}table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #cbd5e1;padding:7px;text-align:left;vertical-align:top}th{background:#e2e8f0}summary{cursor:pointer;font-weight:600}code{white-space:pre-wrap}"""
    detector = payload["detector_residual_cases"]
    unsupported = payload["unsupported_cases"]
    ambiguous = payload["ambiguous_cases"]
    return f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>Human review — Detector residual</title><style>{css}</style></head><body>
<h1>Revisão humana dirigida — Detector residual e casos estruturalmente irredutíveis</h1>
<div class='warning'><b>NÃO avalie uma regra apenas por recuperar estes exemplos.</b><br>Procure deliberadamente razões pelas quais a mesma regra poderia gerar falsos positivos em documentos novos. Respostas humanas não são preenchidas automaticamente.</div>
<h2>Baseline guard</h2><pre>{pre(payload['baseline'])}</pre>
<h2>Parte A — 7 detector residual</h2>{''.join(detector_card(item) for item in detector)}
<h2>Parte B — Negative samples associados</h2><p>Uma hipótese sem negative sampling relevante é evidência insuficiente, não segurança.</p>{''.join(negative_card(item) for item in payload['negative_samples']) or '<p>Nenhuma amostra negativa estrutural encontrada.</p>'}
<h2>Compare os 7 casos — hipóteses estruturais</h2><table><thead><tr><th>Hypothesis</th><th>Targets</th><th>Docs</th><th>N1/N2</th><th>Negatives</th><th>Automated evidence</th><th>Human verdict</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Parte C — 9 unsupported_reference_family</h2>{''.join(unsupported_card(item) for item in unsupported)}
<h2>Parte D — 2 ambiguidades TST</h2>{''.join(ambiguous_card(item) for item in ambiguous)}
<h2>Parte E — 1 gold/corpus conflict</h2>{conflict_card(payload['gold_corpus_case'])}
<h2>Parte F — resumo para decisão humana</h2><p><b>A. Existe hipótese para Detector V5?</b> [ ] SIM [ ] NÃO [ ] INCERTO</p><p><b>B. Hipóteses que merecem experimento:</b> ____________________</p><p><b>C. Caso específico demais:</b> ____________________</p><p><b>D. Unsupported com sinal estrutural:</b> ____________________</p><p><b>E. TST realmente ambíguos?</b> [ ] SIM [ ] NÃO [ ] INCERTO</p><p><b>F. Conflito gold/corpus confirmado?</b> [ ] SIM [ ] NÃO [ ] INCERTO</p><p><b>Automated recommendation:</b> PENDING_HUMAN_REVIEW</p>
</body></html>"""


def response_sheet(payload: Mapping[str, Any]) -> str:
    lines = ["# Human review response sheet", "", "Preencha sem alterar os dados automatizados. A decisão do Detector V5 permanece PENDING_HUMAN_REVIEW.", ""]
    for prefix, cases in (("D", payload["detector_residual_cases"]), ("U", payload["unsupported_cases"]), ("T", payload["ambiguous_cases"])):
        for case in cases:
            lines.extend([f"## {prefix}{case['gold_index']}", "", "- Referência única: [ ] Sim [ ] Não [ ] Incerto", "- Gold span correto: [ ] Sim [ ] Parcial [ ] Não [ ] Incerto", "- Tipo do erro:", "- Mesma referência: [ ] Sim [ ] Não [ ] Incerto", "- Padrão estrutural:", "- Generalizável: [ ] Sim [ ] Provavelmente [ ] Não [ ] Incerto", "- Regra merece experimento: [ ] Sim [ ] Não [ ] Só com mais casos", "- Riscos:", "- Observações:", ""])
    conflict = payload["gold_corpus_case"]
    lines.extend([f"## GOLD/CORPUS-{conflict['gold_index']}", "", "- Identidade primária corresponde ao CNJ: [ ] Sim [ ] Não [ ] Incerto", "- Classe corresponde: [ ] Sim [ ] Não [ ] Incerto", "- Gold é documento-fonte ou documento-que-cita:", "- Resposta: [ ] ABSTENTION_CORRECT [ ] GOLD_CORPUS_CONFLICT_CONFIRMED [ ] SAFE_RESOLUTION_EXISTS [ ] UNCERTAIN", "- Observações:", ""])
    lines.extend(["## Decisão final", "", "- [ ] HUMAN_EVIDENCE_SUPPORTS_DETECTOR_V5_HYPOTHESIS", "- [ ] MIXED_EVIDENCE_NEEDS_TARGETED_EXPERIMENT", "- [ ] HUMAN_EVIDENCE_SUPPORTS_FREEZING_DETECTOR_V4", "- Hipóteses selecionadas:", "- Justificativa:", ""])
    return "\n".join(lines)


def print_summary(payload: Mapping[str, Any]) -> None:
    baseline = payload["baseline"]
    detector = baseline["detector"]
    post_data = baseline["post_arbitration"]
    print("=== BASELINE ===")
    print(f"Detector={detector['predictions']}/{detector['TP']}/{detector['FP']}/{detector['FN']}; exact={detector['exact']}; Resolver={baseline['resolver_before_arbitration']}/96; post-arbitration={post_data['correct_ids']}/96; outputs={post_data['outputs']}; safety=0/0/0; tests={baseline['tests'].get('tests', 'not-run')}/{baseline['tests'].get('tests', 'not-run') if baseline['tests'].get('passed') else 0}")
    print("=== 7 DETECTOR RESIDUAL ===")
    print(json.dumps(payload["detector_residual_cases"], ensure_ascii=False, indent=2))
    print("=== STRUCTURAL HYPOTHESES ===")
    print(json.dumps(payload["hypotheses"], ensure_ascii=False, indent=2))
    print("=== NEGATIVE SAMPLES ===")
    print(json.dumps(payload["negative_samples"], ensure_ascii=False, indent=2))
    print("=== 9 UNSUPPORTED ===")
    print(json.dumps(payload["unsupported_cases"], ensure_ascii=False, indent=2))
    print("=== TST AMBIGUITIES ===")
    print(json.dumps(payload["ambiguous_cases"], ensure_ascii=False, indent=2))
    print("=== GOLD/CORPUS CONFLICT ===")
    print(json.dumps(payload["gold_corpus_case"], ensure_ascii=False, indent=2))
    print("=== HUMAN REVIEW ARTIFACTS ===")
    print(json.dumps({**payload["artifacts"], **payload["automated_summary"]}, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        first_payload, _ = build_once(run_tests=True)
        payloads = [first_payload]
        for _ in range(2):
            payload, _ = build_once(run_tests=False)
            # The guard is executed once before the three deterministic
            # evidence builds. Carry its stable result into the projections.
            payload["baseline"]["tests"] = first_payload["baseline"]["tests"]
            payloads.append(payload)
        reference = stable(payloads[0])
        if any(stable(payload) != reference for payload in payloads[1:]):
            raise AuditError("structured review output is not deterministic")
        payload = payloads[0]
        payload["determinism"] = {"runs": 3, "identical_except_timings": True}
        JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        HTML_PATH.write_text(html_report(payload), encoding="utf-8")
        RESPONSE_PATH.write_text(response_sheet(payload), encoding="utf-8")
        print_summary(payload)
        return 0
    except (AuditError, post.deep.BaselineError) as exc:
        print(f"AUDIT ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
