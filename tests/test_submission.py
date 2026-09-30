from __future__ import annotations

import csv
from collections import defaultdict
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from bracis_jusbrasil.submission import DEFAULT_SUBMISSION_CONFIDENCE, build_submission_record
from bracis_jusbrasil.submission_csv import write_submission_csv


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "material_desafio_jusbrasil_bracis"
GOLD_PATH = next(
    path for path in (DATA / "goldenset_offsets.csv", DATA / "goldenset.csv")
    if path.is_file()
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def gold_rows():
    with GOLD_PATH.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def solution(rows):
    grouped = defaultdict(list)
    levels = {}
    for row in rows:
        canonical = row["id_canonico"] if row["classificacao"] == "real" else "-"
        grouped[row["documento_id"]].append(
            f"{row['inicio']},{row['fim']},{row['classificacao']},{canonical}"
        )
        levels[row["documento_id"]] = int(row["nivel"])
    documents = sorted(grouped)
    return pd.DataFrame({"documento_id": documents, "nivel": [levels[doc] for doc in documents],
                         "citacoes": ["|".join(grouped[doc]) for doc in documents]})


def label(status: str) -> str:
    return {"resolved": "real", "no_match": "inventada", "ambiguous": "incompleta", "insufficient": "incompleta"}[status]


def matched_pairs(metric, gold, predictions):
    gold_by_document = defaultdict(list)
    prediction_by_document = defaultdict(list)
    for row in gold:
        gold_by_document[row["documento_id"]].append(row)
    for prediction in predictions:
        prediction_by_document[prediction[0]].append(prediction)
    pairs = []
    for document_id, gold_rows_for_document in gold_by_document.items():
        prediction_rows = prediction_by_document[document_id]
        selected, _, _ = metric._casar(
            [{"inicio": int(row["inicio"]), "fim": int(row["fim"])} for row in gold_rows_for_document],
            [{"inicio": row[1].start, "fim": row[1].end} for row in prediction_rows],
        )
        pairs.extend((gold_rows_for_document[gold_index], prediction_rows[prediction_index])
                     for gold_index, prediction_index in selected)
    return pairs


class SubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = load_module("generate_submission_test", ROOT / "scripts" / "generate_submission.py")
        cls.metric = load_module("submission_metric_test", DATA / "kaggle_metric.py")
        cls.gold = gold_rows()
        cls.texts, cls.raw, cls.outputs = cls.generator.run_pipeline(DATA / "desafio1_bracis.db", DATA / "txt")
        cls.temporary = TemporaryDirectory(prefix="submission-test-")
        cls.csv_path = Path(cls.temporary.name) / "submission.csv"
        cls.generator.write_submission(cls.csv_path, cls.texts, cls.outputs)
        cls.csv = pd.read_csv(cls.csv_path, dtype=str, keep_default_na=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_final_public_gold_inventory(self) -> None:
        self.assertEqual(len(self.gold), 192)
        self.assertEqual({label: sum(row["classificacao"] == label for row in self.gold) for label in ("real", "inventada", "incompleta")},
                         {"real": 96, "inventada": 64, "incompleta": 32})
        self.assertEqual({kind: sum(row["tipo"] == kind for row in self.gold) for kind in ("jurisprudencia", "lei")},
                         {"jurisprudencia": 164, "lei": 28})
        self.assertEqual({level: sum(int(row["nivel"]) == level for row in self.gold) for level in (1, 2)},
                         {1: 99, 2: 93})

    def test_default_confidence_contract(self) -> None:
        self.assertEqual(DEFAULT_SUBMISSION_CONFIDENCE, 0.85)
        self.assertIsInstance(DEFAULT_SUBMISSION_CONFIDENCE, float)
        self.assertGreaterEqual(DEFAULT_SUBMISSION_CONFIDENCE, 0.0)
        self.assertLessEqual(DEFAULT_SUBMISSION_CONFIDENCE, 1.0)

    def test_records_apply_confidence_only_to_final_outputs(self) -> None:
        for document_id, outputs in self.outputs.items():
            record = build_submission_record(document_id, outputs)
            self.assertEqual(len(record["citacoes"]), len(outputs))
            for citation, output in zip(record["citacoes"], outputs):
                self.assertEqual(citation["inicio"], output.candidate.start)
                self.assertEqual(citation["fim"], output.candidate.end)
                self.assertEqual(citation["classificacao"], label(output.resolution.status))
                self.assertEqual(citation["resolucao"]["id_canonico"], output.resolution.id_canonico)
                self.assertEqual(citation["confianca"], 0.85)
                if citation["classificacao"] == "real":
                    self.assertIsNotNone(citation["resolucao"]["id_canonico"])
                else:
                    self.assertIsNone(citation["resolucao"]["id_canonico"])

    def test_empty_document_record_is_valid(self) -> None:
        self.assertEqual(build_submission_record("empty", ()), {"documento_id": "empty", "citacoes": []})

    def test_internal_converter_uses_official_confidence_precision(self) -> None:
        with TemporaryDirectory(prefix="csv-contract-") as temporary:
            destination = Path(temporary) / "submission.csv"
            write_submission_csv(destination, [{
                "documento_id": "document",
                "citacoes": [{
                    "inicio": 1, "fim": 2, "classificacao": "real",
                    "resolucao": {"id_canonico": 9}, "confianca": 0.85,
                }],
            }])
            self.assertEqual(destination.read_text(encoding="utf-8"), "documento_id,citacoes\ndocument,\"1,2,real,9,0.8500\"\n")

    def test_csv_and_official_score(self) -> None:
        self.assertEqual(len(self.csv), 26)
        parsed = [
            citation
            for row in self.csv.itertuples(index=False)
            for citation in self.metric._parse_submission_cell(row.citacoes, row.documento_id)
        ]
        self.assertEqual(len(parsed), 208)
        self.assertTrue(all(citation["confianca"] == 0.85 for citation in parsed))
        structural_csv = self.csv.copy()
        structural_csv["citacoes"] = [
            "|".join(
                ",".join(citation.split(",")[:4] + ["-"])
                for citation in row.citacoes.split("|")
            ) if row.citacoes != "-" else "-"
            for row in structural_csv.itertuples(index=False)
        ]
        structural_score = self.metric.score(solution(self.gold), structural_csv, "documento_id")
        # A Lei 9.504 namespace is no longer covered unless observed in the
        # input DB, so conservative runtime behavior changes this public-only
        # checkpoint without retaining public IDs as a fallback.
        self.assertLess(abs(structural_score - 0.8312336481425335), 1e-12)
        score = self.metric.score(solution(self.gold), self.csv, "documento_id")
        self.assertLess(abs(score - 0.9096014462275214), 1e-12)

    def test_v11_legal_resolution_preserves_v10_nonlegal_checkpoints(self) -> None:
        self.assertEqual(len(self.raw), 252)
        self.assertEqual(sum(len(outputs) for outputs in self.outputs.values()), 208)
        raw_predictions = [(document_id, candidate, result) for document_id, candidate, _, result in self.raw]
        output_predictions = [
            (document_id, output.candidate, output.resolution)
            for document_id, outputs in self.outputs.items()
            for output in outputs
        ]
        raw_pairs = matched_pairs(self.metric, self.gold, raw_predictions)
        output_pairs = matched_pairs(self.metric, self.gold, output_predictions)
        self.assertEqual(len(raw_pairs), 172)
        self.assertEqual(len(raw_predictions) - len(raw_pairs), 80)
        self.assertEqual(len(self.gold) - len(raw_pairs), 20)
        self.assertEqual(sum(
            int(gold["inicio"]) == prediction[1].start and int(gold["fim"]) == prediction[1].end
            for gold, prediction in raw_pairs
        ), 131)
        self.assertEqual(sum(
            gold["classificacao"] == "real" and prediction[2].status == "resolved"
            and str(prediction[2].id_canonico) == gold["id_canonico"]
            for gold, prediction in output_pairs
        ), 87)
        self.assertEqual(len(output_predictions), 208)
        self.assertEqual(len(output_pairs), 172)
        self.assertEqual(len(output_predictions) - len(output_pairs), 36)
        self.assertEqual(sum(
            int(gold["inicio"]) == prediction[1].start and int(gold["fim"]) == prediction[1].end
            for gold, prediction in output_pairs
        ), 132)
        self.assertEqual(sum(
            gold["classificacao"] != "real" and prediction[2].status == "resolved"
            for gold, prediction in output_pairs
        ), 0)
        self.assertEqual(sum(
            gold["classificacao"] == "real" and prediction[2].status == "resolved"
            and str(prediction[2].id_canonico) != gold["id_canonico"]
            for gold, prediction in output_pairs
        ), 0)
        rules = [candidate.rule for _, candidate, _, _ in self.raw]
        families = [candidate.family for _, candidate, _, _ in self.raw]
        output_rules = [candidate.rule for _, candidate, _ in output_predictions]
        output_families = [candidate.family for _, candidate, _ in output_predictions]
        self.assertEqual(rules.count("rcl_relator_year_no_tribunal"), 4)
        self.assertEqual(rules.count("decision_tribunal_relator_year"), 27)
        self.assertEqual(families.count("processo_cnj"), 46)
        self.assertEqual(output_rules.count("rcl_relator_year_no_tribunal"), 4)
        self.assertEqual(output_rules.count("decision_tribunal_relator_year"), 27)
        self.assertEqual(output_families.count("processo_cnj"), 31)


if __name__ == "__main__":
    unittest.main()
