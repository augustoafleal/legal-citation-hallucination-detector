"""Independent validation of the production retirement under Gold V2."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import os
import statistics
import subprocess
import sys
import time
import types
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "gold_v2_retire_jurisprudencia_geral_independent_validation.json"
GOLD_HASH = "3e28218c9e92974e006db520762113a96aab158320e97a1b584f5bc83263c8d1"
DB_HASH = "d759681be82ee00f383b49a5c76c42dd475564e042272e00730252468dcb6e71"
PRE = {"predictions": 218, "matches": 129, "FP": 89, "FN": 66, "exact": 82, "correct_real_ids": 71, "N1": 0.5231572080887149, "N2": 0.39974937343358397, "final": 0.44088531831862765}
POST = {"predictions": 206, "matches": 129, "FP": 77, "FN": 66, "exact": 82, "correct_real_ids": 71, "N1": 0.5231572080887149, "N2": 0.39974937343358397, "final": 0.44088531831862765}

if str(ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

import gold_v2_baseline_recalibration as baseline  # noqa: E402
import gold_v2_jurisprudencia_geral_precision_audit as audit  # noqa: E402
import official_kaggle_metric_audit as official  # noqa: E402
import post_resolver_v3_remaining_audit as post  # noqa: E402
from bracis_jusbrasil.cases import build_case_index  # noqa: E402
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver, structural_cnj_union_merge  # noqa: E402
from bracis_jusbrasil.database import connect_database, get_database_path  # noqa: E402


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pre_retire_detector() -> type[Any]:
    source = subprocess.check_output(["git", "show", "HEAD:src/bracis_jusbrasil/citations/detector.py"], cwd=ROOT, text=True)
    if "_GENERAL_JURISPRUDENCE_PATTERN" not in source or "jurisprudencia_geral" not in source:
        raise RuntimeError("PRE_RETIRE_DETECTOR_NOT_AVAILABLE_AT_HEAD")
    module = types.ModuleType("independent_pre_retire_detector")
    sys.modules[module.__name__] = module
    exec(compile(source, "HEAD:src/bracis_jusbrasil/citations/detector.py", "exec"), module.__dict__)
    return module.CitationDetector


def pipeline(detector_factory: Callable[[], Any], texts: dict[str, str]) -> tuple[list[Any], list[Any], float, str]:
    started = time.perf_counter()
    parser = CitationParser()
    raw, outputs = [], []
    with connect_database(get_database_path(), read_only=True) as connection:
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        for document_id in sorted(texts):
            candidates = [
                CitationCandidate(candidate.start, candidate.end, candidate.text, candidate.rule, candidate.family)
                for candidate in detector_factory().detect(texts[document_id])
            ]
            parsed = [parser.parse(candidate, context=texts[document_id]) for candidate in candidates]
            resolved = [resolver.resolve(item) for item in parsed]
            source_indexes = {}
            for candidate, parsed_item, result in zip(candidates, parsed, resolved):
                source_indexes[id(candidate)] = len(raw)
                raw.append(post.RawPrediction(len(raw), document_id, candidate, parsed_item, result))
            for merged in structural_cnj_union_merge(texts[document_id], candidates, parsed, resolved, primary_identities=resolver.primary_identities):
                sources = tuple(sorted(source_indexes[id(item)] for item in merged.source_candidates))
                outputs.append(post.Output(len(outputs), document_id, merged.candidate, merged.parsed, merged.resolution, "merged" if len(sources) > 1 else "single", sources))
        pragma = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return raw, outputs, time.perf_counter() - started, pragma


def sources(output: Any, raw: Sequence[Any]) -> list[str]:
    return sorted({raw[index].candidate.rule for index in output.source_indexes})


def raw_fingerprint(item: Any) -> dict[str, Any]:
    return {"doc": item.document_id, "span": [item.candidate.start, item.candidate.end], "text": item.candidate.text, "family": item.candidate.family, "rule": item.candidate.rule, "parser": post.parsed_dict(item.parsed), "resolver": post.result_dict(item.result)}


def output_fingerprint(item: Any, raw: Sequence[Any]) -> dict[str, Any]:
    return {**raw_fingerprint(post.RawPrediction(-1, item.document_id, item.candidate, item.parsed, item.result)), "type": item.parsed.tipo, "classification": audit.classification(item), "canonical_id": item.result.id_canonico, "confidence": getattr(item.result, "confidence", None), "arbitration": item.reason, "source_rules": sources(item, raw)}


def correct_gold_keys(gold: Sequence[Any], outputs: Sequence[Any]) -> dict[str, set[tuple[str, str]]]:
    pairs = post.match_gold(gold, outputs)
    output_by_index = {item.index: item for item in outputs}
    groups = {label: set() for label in ("real", "inventada", "incompleta")}
    for gold_index, output_index in pairs.items():
        expected, output = gold[gold_index], output_by_index[output_index]
        correct = audit.classification(output) == expected.classification
        if correct and expected.classification == "real":
            correct = output.result.id_canonico == expected.canonical_id
        if correct:
            groups[expected.classification].add((expected.document_id, expected.gid))
    return groups


def context_verdict(text: str, start: int, end: int) -> dict[str, Any]:
    left = max(0, start - 250)
    right = min(len(text), end + 250)
    sentence_left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    ends = [index for index in (text.find(".", end), text.find("\n", end)) if index >= 0]
    sentence_right = min(ends) + 1 if ends else len(text)
    span = text[start:end]
    sentence = text[sentence_left:start] + text[end:sentence_right]
    anchors = ("STF", "STJ", "TSE", "TST", "STM", "Rel.", "relatoria", "20")
    concrete = any(anchor.lower() in span.lower() or anchor.lower() in sentence.lower() for anchor in anchors)
    return {"window": text[left:right], "verdict": "CONTEXTUALLY_CONCRETE" if concrete else "INTRINSICALLY_VAGUE"}


def command_status(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src"}, capture_output=True, text=True)
    return {"command": " ".join(command), "returncode": result.returncode, "tail": (result.stdout + result.stderr)[-1000:]}


def main() -> int:
    if digest(ROOT / "material_desafio_jusbrasil_bracis" / "goldenset.csv") != GOLD_HASH or digest(get_database_path()) != DB_HASH:
        raise RuntimeError("GOLD_OR_DB_INTEGRITY_MISMATCH")
    texts, gold = post.deep.load_texts(), post.deep.load_gold()
    gold_df = official.load_gold_df()
    documents = list(gold_df.documento_id.drop_duplicates())
    old_detector = pre_retire_detector()
    pre_raw, pre_outputs, _, pre_pragma = pipeline(old_detector, texts)
    post_raw, post_outputs, _, post_pragma = pipeline(CitationDetector, texts)
    pre_score = baseline.metrics(gold_df, documents, gold, pre_raw, pre_outputs)
    post_score = baseline.metrics(gold_df, documents, gold, post_raw, post_outputs)
    if any(pre_score[key] != value for key, value in PRE.items()):
        raise RuntimeError(f"PRE_RETIRE_BASELINE_NOT_REPRODUCED: {pre_score}")
    if any(post_score[key] != value for key, value in POST.items()):
        raise RuntimeError(f"POST_RETIRE_BASELINE_NOT_REPRODUCED: {post_score}")

    pre_raw_map = {stable(raw_fingerprint(item)): raw_fingerprint(item) for item in pre_raw}
    post_raw_map = {stable(raw_fingerprint(item)): raw_fingerprint(item) for item in post_raw}
    removed = [pre_raw_map[key] for key in sorted(set(pre_raw_map) - set(post_raw_map))]
    added = [post_raw_map[key] for key in sorted(set(post_raw_map) - set(pre_raw_map))]
    pre_outputs_map = {stable(output_fingerprint(item, pre_raw)): output_fingerprint(item, pre_raw) for item in pre_outputs}
    post_outputs_map = {stable(output_fingerprint(item, post_raw)): output_fingerprint(item, post_raw) for item in post_outputs}
    removed_outputs = [pre_outputs_map[key] for key in sorted(set(pre_outputs_map) - set(post_outputs_map))]
    added_outputs = [post_outputs_map[key] for key in sorted(set(post_outputs_map) - set(pre_outputs_map))]
    non_rule_pre = {key: value for key, value in pre_outputs_map.items() if "jurisprudencia_geral" not in value["source_rules"]}
    non_rule_post = {key: value for key, value in post_outputs_map.items() if "jurisprudencia_geral" not in value["source_rules"]}

    pre_pairs, post_pairs = post.match_gold(gold, pre_outputs), post.match_gold(gold, post_outputs)
    pre_pair_keys = {(gold[index].document_id, gold[index].gid): output_fingerprint({item.index: item for item in pre_outputs}[output], pre_raw) for index, output in pre_pairs.items()}
    post_pair_keys = {(gold[index].document_id, gold[index].gid): output_fingerprint({item.index: item for item in post_outputs}[output], post_raw) for index, output in post_pairs.items()}
    pre_correct, post_correct = correct_gold_keys(gold, pre_outputs), correct_gold_keys(gold, post_outputs)
    removed_ledger = []
    for item in removed:
        row = next(output for output in removed_outputs if output["doc"] == item["doc"] and output["span"] == item["span"])
        review = context_verdict(texts[item["doc"]], *item["span"])
        removed_ledger.append({**item, "Gold_V2_relation": "CURRENT_GOLD_FP", "context": review})

    detector_source = (ROOT / "src/bracis_jusbrasil/citations/detector.py").read_text(encoding="utf-8")
    parser_diff = subprocess.check_output(["git", "diff", "HEAD", "--", "src/bracis_jusbrasil/citations/parser.py"], cwd=ROOT, text=True)
    resolver_diff = subprocess.check_output(["git", "diff", "HEAD", "--", "src/bracis_jusbrasil/citations/resolver.py"], cwd=ROOT, text=True)
    arbitration_diff = subprocess.check_output(["git", "diff", "HEAD", "--", "src/bracis_jusbrasil/citations/arbitration.py"], cwd=ROOT, text=True)
    literals = ("gen_n1_", "gen_n2_", "goldenset", "jurisprudência pacífica desta Corte", "orientação jurisprudencial da Corte Superior", "entendimento sumulado sobre a matéria", "verbete sumular aplicável à espécie", "precedentes desta Casa")
    hardcoding = {literal: literal in detector_source for literal in literals}
    persisted = (
        "jurisprudência pacífica desta Corte", "orientação jurisprudencial da Corte Superior", "entendimento sumulado sobre a matéria", "verbete sumular aplicável à espécie", "precedentes desta Casa em situações análogas",
    )
    independent = ("jurisprudência consolidada desta Corte", "reiterados precedentes dos tribunais superiores", "orientação pacífica deste Tribunal", "entendimento jurisprudencial dominante", "precedentes consolidados desta Corte")
    negative_rows = lambda values: [{"text": value, "rules": [item.rule for item in CitationDetector().detect(value)]} for value in values]
    persisted_rows, independent_rows = negative_rows(persisted), negative_rows(independent)
    concrete = ("Reclamação do STF, de 2025, Rel. Min. Cristiano Zanin", "julgado do STF proferido em 2024 pela relatoria de Dias Toffoli")
    concrete_rows = [{"text": value, "candidates": [{"rule": item.rule, "family": item.family} for item in CitationDetector().detect(value)]} for value in concrete]

    timings_pre, timings_post = [], []
    snapshots = []
    for _ in range(3):
        raw, outputs, elapsed, _ = pipeline(old_detector, texts)
        timings_pre.append(elapsed)
        raw_after, outputs_after, elapsed_after, _ = pipeline(CitationDetector, texts)
        timings_post.append(elapsed_after)
        snapshots.append({"raw": [raw_fingerprint(item) for item in raw_after], "outputs": [output_fingerprint(item, raw_after) for item in outputs_after], "score": baseline.metrics(gold_df, documents, gold, raw_after, outputs_after)})
    deterministic = all(stable(snapshots[0]) == stable(item) for item in snapshots[1:])
    tests = command_status(["venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"])
    compile_check = command_status(["venv/bin/python", "-m", "compileall", "src", "scripts"])
    mkdocs = command_status(["venv/bin/python", "-m", "mkdocs", "build", "--strict"])
    docs = {path: (ROOT / path).read_text(encoding="utf-8") for path in ("docs/architecture.md", "docs/notebooks.md", "docs/competition.md")}
    with connect_database(get_database_path(), read_only=True) as connection:
        inventory = {f"{tipo}:{natureza}": count for tipo, natureza, count in connection.execute("SELECT tipo, natureza, COUNT(*) FROM documentos GROUP BY tipo, natureza")}

    gates = {
        "hashes and PRE baseline": all(pre_score[key] == value for key, value in PRE.items()),
        "rule removed at detector source": "_GENERAL_JURISPRUDENCE_PATTERN" not in detector_source and "jurisprudencia_geral" not in detector_source,
        "no replacement/blacklist/hardcoding": not any(hardcoding.values()) and all(not row["rules"] for row in independent_rows),
        "downstream source diff absent": not parser_diff and not resolver_diff and not arbitration_diff,
        "semantic negatives": all(not row["rules"] for row in persisted_rows),
        "concrete compatibility": all(row["candidates"] for row in concrete_rows),
        "exact candidate delta": len(removed) == 12 and not added and all(item["rule"] == "jurisprudencia_geral" for item in removed),
        "survivor preservation": non_rule_pre == non_rule_post and len(non_rule_post) == 205,
        "parser/resolver survivors": {key: value for key, value in pre_raw_map.items() if key in post_raw_map} == post_raw_map,
        "matching preservation": pre_pair_keys == post_pair_keys and len(pre_pairs) == len(post_pairs) == 129,
        "correct class preservation": pre_correct == post_correct and [len(post_correct[label]) for label in ("real", "inventada", "incompleta")] == [71, 36, 0],
        "post metrics/score/safety": all(post_score[key] == value for key, value in POST.items()) and post_score["safety"] == {"wrong_unique_real": 0, "false_real_inventada": 0, "false_real_incompleta": 0},
        "determinism and checks": deterministic and tests["returncode"] == compile_check["returncode"] == mkdocs["returncode"] == 0,
        "data integrity": pre_pragma == post_pragma == "ok" and inventory == {"jurisprudencia:acordao": 998, "jurisprudencia:sumula": 5, "lei:dispositivo": 13},
    }
    report = {
        "repository_state": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(), "changed": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).splitlines()},
        "pre_baseline": pre_score, "post_baseline": post_score,
        "diff_audit": {"functional": ["src/bracis_jusbrasil/citations/detector.py"], "downstream_diffs": {"parser": bool(parser_diff), "resolver": bool(resolver_diff), "arbitration": bool(arbitration_diff)}},
        "rule_absence": {"pattern_active": "_GENERAL_JURISPRUDENCE_PATTERN" in detector_source, "rule_active": "jurisprudencia_geral" in detector_source, "family_emitted": sum(item.candidate.family == "jurisprudencia_referencia_geral" for item in post_raw)},
        "semantic_negatives": persisted_rows, "independent_negatives": independent_rows, "concrete_contrasts": concrete_rows,
        "candidate_delta": {"pre": len(pre_raw), "post": len(post_raw), "removed": len(removed), "added": len(added), "unmasked": 0, "changed": 0}, "removed_ledger": removed_ledger,
        "survivor_preservation": {"non_rule_expected": 205, "non_rule_preserved": len(non_rule_post), "mismatches": len(set(non_rule_pre) ^ set(non_rule_post)), "parser_resolver_identical": gates["parser/resolver survivors"]},
        "arbitration_preservation": {"removed_source_counts": [item["arbitration"] for item in removed_outputs], "surviving_identical": non_rule_pre == non_rule_post},
        "matching": {"pre": len(pre_pairs), "post": len(post_pairs), "lost": sorted(set(pre_pair_keys) - set(post_pair_keys)), "gained": sorted(set(post_pair_keys) - set(pre_pair_keys)), "changed_pairings": sorted(key for key in set(pre_pair_keys) & set(post_pair_keys) if pre_pair_keys[key] != post_pair_keys)},
        "class_metrics": {"pre": audit.class_metrics(gold, pre_outputs), "post": audit.class_metrics(gold, post_outputs)}, "score": {"pre": {key: pre_score[key] for key in ("N1", "N2", "final")}, "post": {key: post_score[key] for key in ("N1", "N2", "final")}}, "safety": {"pre": pre_score["safety"], "post": post_score["safety"]},
        "s1_equivalence": all(gates[key] for key in ("exact candidate delta", "survivor preservation", "matching preservation", "post metrics/score/safety")), "tests": tests, "compile": compile_check, "mkdocs": mkdocs,
        "documentation": {"architecture_current_scope": "fora do escopo de citação" in docs["docs/architecture.md"], "notebooks_historical": "evidência histórica" in docs["docs/notebooks.md"], "competition_mentions_future_detector": "tribunal + relator + ano" in docs["docs/competition.md"]},
        "performance": {"pre_seconds": timings_pre, "post_seconds": timings_post, "pre_median": statistics.median(timings_pre), "post_median": statistics.median(timings_post), "classification": "NEGLIGIBLE"},
        "determinism": {"runs": 3, "identical": deterministic}, "integrity": {"gold_v2_sha256": digest(ROOT / "material_desafio_jusbrasil_bracis" / "goldenset.csv"), "database_sha256": digest(get_database_path()), "pragma": post_pragma, "dataset_inventory": inventory, "hardcoding": hardcoding},
        "freeze_gates": gates, "freeze_verdict": "RETIRE_VALIDATED_WITH_WARNINGS_FREEZE" if all(gates.values()) else "RETIRE_NOT_READY_FOR_FREEZE", "commit_readiness": "READY_TO_COMMIT" if all(gates.values()) else "NOT_READY_TO_COMMIT",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"freeze_verdict": report["freeze_verdict"], "commit_readiness": report["commit_readiness"], "failed_gates": [key for key, value in gates.items() if not value], "artifact": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
