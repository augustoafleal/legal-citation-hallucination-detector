from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd

from bracis_jusbrasil.citations import CitationDetector


DATASET_DIR = Path("material_desafio_jusbrasil_bracis")
RULE_TYPES = {
    "cnj": "jurisprudencia",
    "processo_ou_recurso": "jurisprudencia",
    "sumula_numerada": "jurisprudencia",
    "lei_com_diploma": "lei",
    "dispositivo_legal": "lei",
    "tribunal_contextual": "jurisprudencia",
}


def span_iou(left_start: int, left_end: int, right_start: int, right_end: int) -> float:
    intersection = max(0, min(left_end, right_end) - max(left_start, right_start))
    union = max(left_end, right_end) - min(left_start, right_start)
    return intersection / union if union else 0.0


def greedy_match(gold: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    pairs: list[dict[str, object]] = []
    for document_id, gold_group in gold.groupby("documento_id"):
        predicted_group = predictions.loc[predictions["documento_id"].eq(document_id)]
        for gold_row in gold_group.itertuples():
            for predicted_row in predicted_group.itertuples():
                iou = span_iou(gold_row.inicio, gold_row.fim, predicted_row.start, predicted_row.end)
                if iou >= 0.5:
                    pairs.append(
                        {
                            "gold_idx": gold_row.gold_idx,
                            "pred_idx": predicted_row.pred_idx,
                            "iou": iou,
                            "exact": gold_row.inicio == predicted_row.start and gold_row.fim == predicted_row.end,
                        }
                    )

    used_gold: set[int] = set()
    used_predictions: set[int] = set()
    matches: list[dict[str, object]] = []
    for pair in sorted(pairs, key=lambda item: (-float(item["iou"]), int(item["gold_idx"]), int(item["pred_idx"]))):
        if pair["gold_idx"] not in used_gold and pair["pred_idx"] not in used_predictions:
            matches.append(pair)
            used_gold.add(int(pair["gold_idx"]))
            used_predictions.add(int(pair["pred_idx"]))
    return pd.DataFrame(matches, columns=["gold_idx", "pred_idx", "iou", "exact"])


def metrics(gold: pd.DataFrame, predictions: pd.DataFrame, matches: pd.DataFrame) -> tuple[int, int, int]:
    selected = matches.loc[
        matches["gold_idx"].isin(gold["gold_idx"])
        & matches["pred_idx"].isin(predictions["pred_idx"])
    ]
    true_positive = len(selected)
    false_positive = len(predictions) - len(set(selected["pred_idx"]))
    false_negative = len(gold) - len(set(selected["gold_idx"]))
    return true_positive, false_positive, false_negative


class CitationDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = CitationDetector()

    def test_empty_text_has_no_candidates(self) -> None:
        self.assertEqual(self.detector.detect(""), ())

    def test_candidates_keep_original_offsets_and_are_deterministic(self) -> None:
        text = "REsp nº 1.597.443; Súmula 331; art. 5 da Constituição Federal."
        first = self.detector.detect(text)
        second = self.detector.detect(text)
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertTrue(all(candidate.text == text[candidate.start:candidate.end] for candidate in first))
        self.assertEqual(list(first), sorted(first, key=lambda item: (item.start, item.end, item.rule)))

    def test_main_rules_and_families(self) -> None:
        examples = {
            "cnj": ("Autos 1234567-89.2020.1.23.4567.", "processo_cnj"),
            "processo_ou_recurso": ("Conforme o REsp nº 1.597.443.", "processo_ou_recurso_numerado"),
            "sumula_numerada": ("Aplica-se a Súmula 331.", "sumula_numerada"),
            "lei_com_diploma": ("Nos termos do art. 5 da Constituição Federal.", "lei_dispositivo_com_diploma"),
            "tribunal_contextual": ("Precedente do STJ em 2024, Relator indicado.", "jurisprudencia_tribunal_contextual"),
        }
        for rule, (text, family) in examples.items():
            with self.subTest(rule=rule):
                candidate = next(item for item in self.detector.detect(text) if item.rule == rule)
                self.assertEqual(candidate.family, family)

    def test_overlaps_are_preserved_and_exact_spans_are_unique(self) -> None:
        text = "Nos termos do art. 5 da Constituição Federal."
        candidates = self.detector.detect(text)
        self.assertEqual({candidate.rule for candidate in candidates}, {"lei_com_diploma", "dispositivo_legal"})
        self.assertTrue(candidates[0].end > candidates[1].start)
        self.assertEqual(len({(candidate.start, candidate.end) for candidate in candidates}), len(candidates))

    def test_not_a_string_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            self.detector.detect(None)  # type: ignore[arg-type]


class CitationDetectorBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        gold = pd.read_excel(DATASET_DIR / "goldenset.xlsx", sheet_name="goldenset", engine="openpyxl").reset_index(names="gold_idx")
        gold["nivel"] = "N" + gold["nivel"].astype(str).str.removeprefix("N")
        texts = {path.stem: path.read_text(encoding="utf-8") for path in sorted((DATASET_DIR / "txt").glob("*.txt"))}
        detector = CitationDetector()
        records = [
            {"documento_id": document_id, "start": item.start, "end": item.end, "text": item.text, "rule": item.rule, "family": item.family, "tipo": RULE_TYPES[item.rule]}
            for document_id, text in texts.items()
            for item in detector.detect(text)
        ]
        cls.gold = gold
        cls.predictions = pd.DataFrame(records).reset_index(names="pred_idx")
        cls.predictions["nivel"] = cls.predictions["documento_id"].map(gold.drop_duplicates("documento_id").set_index("documento_id")["nivel"])
        cls.matches = greedy_match(gold, cls.predictions)

    def test_baseline_candidate_count_offsets_and_exact_matches(self) -> None:
        self.assertEqual(len(self.predictions), 194)
        self.assertTrue((self.predictions["text"] == self.predictions.apply(lambda row: (DATASET_DIR / "txt" / f"{row.documento_id}.txt").read_text(encoding="utf-8")[row.start:row.end], axis=1)).all())
        self.assertEqual(len(self.matches), 105)
        self.assertEqual(int(self.matches["exact"].sum()), 19)

    def test_baseline_metrics_by_level_and_type(self) -> None:
        expected = {
            "global": (self.gold, self.predictions, (105, 89, 120)),
            "N1": (self.gold[self.gold["nivel"].eq("N1")], self.predictions[self.predictions["nivel"].eq("N1")], (64, 46, 52)),
            "N2": (self.gold[self.gold["nivel"].eq("N2")], self.predictions[self.predictions["nivel"].eq("N2")], (41, 43, 68)),
            "jurisprudencia": (self.gold[self.gold["tipo"].eq("jurisprudencia")], self.predictions[self.predictions["tipo"].eq("jurisprudencia")], (89, 62, 97)),
            "lei": (self.gold[self.gold["tipo"].eq("lei")], self.predictions[self.predictions["tipo"].eq("lei")], (16, 27, 23)),
        }
        for name, (gold, predictions, expected_metrics) in expected.items():
            with self.subTest(group=name):
                self.assertEqual(metrics(gold, predictions, self.matches), expected_metrics)


if __name__ == "__main__":
    unittest.main()
