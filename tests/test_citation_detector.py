from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd

from bracis_jusbrasil.citations import CitationDetector


DATASET_DIR = Path("material_desafio_jusbrasil_bracis")
RULE_TYPES = {
    "cnj": "jurisprudencia",
    "processo_ou_recurso": "jurisprudencia",
    "degraded_compact_cnj": "jurisprudencia",
    "sumula_numerada": "jurisprudencia",
    "lei_com_diploma": "lei",
    "dispositivo_legal": "lei",
    "tribunal_contextual": "jurisprudencia",
    "jurisprudencia_geral": "jurisprudencia",
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

    def test_general_jurisprudence_branches_keep_offsets_and_family(self) -> None:
        examples = (
            ("A jurisprudência pacífica desta Corte orienta o caso.", "jurisprudência pacífica desta Corte"),
            ("Aplica-se a orientação jurisprudencial consolidada.", "orientação jurisprudencial consolidada"),
            ("Há entendimento sumulado sobre a matéria.", "entendimento sumulado sobre a matéria"),
            ("Confira os precedentes desta Casa em 2024.", "precedentes desta Casa em 2024"),
            ("Incide o verbete sumular aplicável.", "verbete sumular aplicável"),
        )
        for text, expected_text in examples:
            with self.subTest(expected_text=expected_text):
                candidates = self.detector.detect(text)
                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertEqual(candidate.rule, "jurisprudencia_geral")
                self.assertEqual(candidate.family, "jurisprudencia_referencia_geral")
                self.assertEqual(candidate.text, expected_text)
                self.assertEqual(candidate.start, text.index(expected_text))
                self.assertEqual(candidate.end, candidate.start + len(expected_text))
                self.assertEqual(candidate.text, text[candidate.start:candidate.end])

    def test_general_jurisprudence_does_not_match_isolated_terms(self) -> None:
        text = "A jurisprudência relevante e a orientação adotada foram discutidas."
        self.assertEqual(self.detector.detect(text), ())

    def test_overlaps_are_preserved_and_exact_spans_are_unique(self) -> None:
        text = "Nos termos do art. 5 da Constituição Federal."
        candidates = self.detector.detect(text)
        self.assertEqual({candidate.rule for candidate in candidates}, {"lei_com_diploma", "dispositivo_legal"})
        self.assertTrue(candidates[0].end > candidates[1].start)
        self.assertEqual(len({(candidate.start, candidate.end) for candidate in candidates}), len(candidates))

    def test_not_a_string_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            self.detector.detect(None)  # type: ignore[arg-type]

    def test_process_candidates_expand_to_adjacent_uf(self) -> None:
        examples = (
            ("REsp nº 1.597.443-PR", "1.597.443-PR"),
            ("REsp nº 1.597.443/PR", "1.597.443/PR"),
            ("REsp nº 1.597.443 PR", "1.597.443 PR"),
            ("REsp nº 1.597.443- PR", "1.597.443- PR"),
        )
        for text, expected_suffix in examples:
            with self.subTest(text=text):
                candidate = next(item for item in self.detector.detect(text) if item.family == "processo_ou_recurso_numerado")
                self.assertTrue(candidate.text.endswith(expected_suffix))
                self.assertEqual(candidate.text, text[candidate.start:candidate.end])
                self.assertEqual(candidate.end, len(text))

    def test_process_uf_expansion_does_not_consume_arbitrary_text(self) -> None:
        text = "REsp nº 1.597.443 processo posterior"
        candidate = next(item for item in self.detector.detect(text) if item.family == "processo_ou_recurso_numerado")
        self.assertEqual(candidate.text, "REsp nº 1.597.443")

    def test_uf_expansion_does_not_change_other_families(self) -> None:
        text = "art. 75 da Constituição Federal; Súmula 331; jurisprudência pacífica."
        candidates = self.detector.detect(text)
        self.assertEqual(
            {(item.text, item.rule, item.family) for item in candidates},
            {
                ("art. 75 da Constituição Federal", "lei_com_diploma", "lei_dispositivo_com_diploma"),
                ("art. 75", "dispositivo_legal", "lei_dispositivo_sem_diploma"),
                ("Súmula 331", "sumula_numerada", "sumula_numerada"),
                ("jurisprudência pacífica", "jurisprudencia_geral", "jurisprudencia_referencia_geral"),
            },
        )
        self.assertFalse(any(item.rule == "processo_ou_recurso" for item in candidates))

    def test_process_uf_expansion_is_deterministic(self) -> None:
        text = "REsp nº 1.597.443-PR e art. 75 da Constituição."
        self.assertEqual(self.detector.detect(text), self.detector.detect(text))

    def test_h1_expands_only_a_structural_procedural_prefix_chain(self) -> None:
        examples = (
            "Embargos de Declaração no Agravo Interno no Agravo em Recurso Especial nº 1904603/TO",
            "Terceiro AG.REG na Rcl nº 62.425/SP",
            "EDcl nos EDcl no AgInt no Agravo em Recurso Especial No  1145207",
        )
        for text in examples:
            with self.subTest(text=text):
                candidates = [item for item in self.detector.detect(text) if item.rule == "processo_ou_recurso"]
                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertEqual(candidate.text, text)
                self.assertEqual(candidate.family, "processo_ou_recurso_numerado")
                self.assertEqual(candidate.text, text[candidate.start:candidate.end])

    def test_h1_rejects_narrative_delimiters(self) -> None:
        examples = (
            ("A discussão sobre Agravo genérico antecede o REsp nº 123456.", False),
            ("Agravo em; REsp nº 123456.", False),
        )
        for text, no_candidate in examples:
            with self.subTest(text=text):
                process = [item for item in self.detector.detect(text) if item.family == "processo_ou_recurso_numerado"]
                if no_candidate:
                    self.assertEqual(process, [])
                    continue
                self.assertEqual(len(process), 1)
                self.assertNotIn("Agravo", process[0].text)
                self.assertNotIn("Suspensão", process[0].text)

    def test_h2_supports_only_approved_dotted_class_aliases_with_numbers(self) -> None:
        examples = (
            ("AgRg no Rec. Esp. n.\u00a01.522.200 (SC)", "Rec. Esp."),
            ("AgRg no H.C. Nº 891369 (RS)", "H.C."),
            ("AgInt nos EDcl no Rec. Esp. n°\n 2.050.950-RJ", "Rec. Esp."),
        )
        for text, alias in examples:
            with self.subTest(text=text):
                candidate = next(item for item in self.detector.detect(text) if alias in item.text)
                self.assertEqual(candidate.family, "processo_ou_recurso_numerado")
                self.assertIn(alias, candidate.text)
                self.assertRegex(candidate.text, r"\d")
                self.assertEqual(candidate.text, text[candidate.start:candidate.end])

    def test_h2_requires_identity_and_does_not_create_a_broad_alias_catalog(self) -> None:
        for text in ("Rec. Esp.", "H.C.", "Rec. Esp. citado no processo", "R.E. nº 123456/PR"):
            with self.subTest(text=text):
                self.assertFalse(any(item.family == "processo_ou_recurso_numerado" for item in self.detector.detect(text)))

    def test_h3_extends_only_adjacent_approved_ocr_numeric_tails(self) -> None:
        for ocr in "OIlS":
            text = f"REsp nº 1234{ocr}5/PR"
            with self.subTest(text=text):
                candidate = next(item for item in self.detector.detect(text) if item.family == "processo_ou_recurso_numerado")
                self.assertEqual(candidate.text, text)
                self.assertEqual(candidate.text, text[candidate.start:candidate.end])

    def test_h3_does_not_fuzzy_repair_unrelated_text(self) -> None:
        examples = (
            "REsp nº 1234X5/PR",
            "REsp nº 1234 O5/PR",
            "REsp nº 1234, referência O5/PR",
        )
        for text in examples:
            with self.subTest(text=text):
                candidate = next((item for item in self.detector.detect(text) if item.family == "processo_ou_recurso_numerado"), None)
                self.assertIsNotNone(candidate)
                assert candidate is not None
                self.assertNotIn("X5", candidate.text)
                self.assertNotIn("O5", candidate.text)

    def test_v5_promotes_human_reviewed_residual_structures(self) -> None:
        examples = (
            ("Recurso Especial Eleitoral nº 2137-73.2014.6.21.0000", "processo_cnj"),
            ("Agravo Regimental no Agravo de Instrumento nº 0606252-11.2018.6.26.0000", "processo_cnj"),
            ("Agravo Interno na Suspensão\nde Liminar e de Sentença nº 2.883/MA", "processo_ou_recurso_numerado"),
            ("RHC nº\n88.033/RS", "processo_ou_recurso_numerado"),
            ("AR\nn. 2785 (SP)", "processo_ou_recurso_numerado"),
            ("RMS Nº  67109-TO", "processo_ou_recurso_numerado"),
            ("AgInt No 7000553-0320217000000", "processo_ou_recurso_numerado"),
        )
        for text, family in examples:
            with self.subTest(text=text):
                candidates = [item for item in self.detector.detect(text) if item.family == family]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].text, text)
                self.assertEqual(candidates[0].text, text[candidates[0].start:candidates[0].end])

    def test_v5_formal_cnj_prefix_negative_guard(self) -> None:
        """NEGATIVE_GUARD: prefixo narrativo não atravessa sentença até o CNJ."""
        text = (
            "Recurso Especial Eleitoral nº foi mencionado. "
            "Autos 1234567-89.2020.1.23.4567"
        )
        candidates = self.detector.detect(text)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.text, "1234567-89.2020.1.23.4567")
        self.assertEqual(candidate.family, "processo_cnj")
        self.assertEqual(candidate.text, text[candidate.start:candidate.end])

    def test_v5_newline_boundary_regression(self) -> None:
        """BOUNDARY_REGRESSION: RHC não atravessa narrativa após newline."""
        text = "RHC\nfoi citado no relatório antes do nº 88.033/RS."
        self.assertEqual(self.detector.detect(text), ())

    def test_v5_modifier_direct_number_negative_guard(self) -> None:
        """NEGATIVE_GUARD: ordinal narrativo não é identificador processual."""
        text = "AgInt No 12ª sessão da pauta."
        self.assertEqual(self.detector.detect(text), ())

    def test_v5_modifier_cnj_interaction_regression(self) -> None:
        """PARSER_SCOPE_REGRESSION: CNJ formatado fica somente na família CNJ."""
        text = "AgInt No 1234567-89.2020.1.23.4567"
        candidates = self.detector.detect(text)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].family, "processo_cnj")
        self.assertEqual(candidates[0].text, "1234567-89.2020.1.23.4567")

    def test_v5_compound_title_negative_guard(self) -> None:
        """NEGATIVE_GUARD: cadeia parcial ou sem número não é título formal."""
        examples = (
            "O agravo interno debateu a suspensão de liminar e de sentença, sem número.",
            "Agravo Interno na Suspensão Liminar nº 12",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.detector.detect(text), ())

    def test_v5_header_distractor_regression(self) -> None:
        """HEADER_DISTRACTOR: números administrativos não ativam extensões V5."""
        text = (
            "Processo nº 1234567-89.2020.1.23.4567; protocolo 2024; "
            "OAB 12345; fls. 12; AgInt No 1º da pauta."
        )
        candidates = self.detector.detect(text)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].family, "processo_cnj")
        self.assertEqual(candidates[0].text, "1234567-89.2020.1.23.4567")

    def test_v6_degraded_compact_cnj_positive_grammar_and_raw_span(self) -> None:
        examples = (
            "REsp 12345678901234567890",
            "AgInt no REsp 12345678901234567890",
            "ED no AgR-REsp 12345678901234567890",
            "Ag. Int. No 12345678901234567890",
            "REspe. n° 1234567-89.0123-\n.4.56.7890/BA",
        )
        for text in examples:
            with self.subTest(text=text):
                # The M1 grammar accepts all examples. Some already have a
                # V5 process candidate and are suppressed only at detector
                # integration, preserving the validated 219 -> 222 delta.
                candidates = self.detector._detect_degraded_compact_cnj(text)
                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertEqual(candidate.family, "processo_ou_recurso_numerado")
                self.assertEqual(candidate.text, text[candidate.start:candidate.end])
                self.assertEqual("".join(char for char in candidate.text if char.isdigit()), "12345678901234567890")

    def test_v6_suppresses_only_overlapping_existing_v5_candidates(self) -> None:
        text = "REsp 12345678901234567890"
        candidates = self.detector.detect(text)
        self.assertTrue(any(item.rule == "processo_ou_recurso" for item in candidates))
        self.assertFalse(any(item.rule == "degraded_compact_cnj" for item in candidates))

        unique = self.detector.detect("Ag. Int. No 12345678901234567890")
        self.assertEqual([item.rule for item in unique], ["degraded_compact_cnj"])

    def test_v6_degraded_compact_cnj_rejects_administrative_chains(self) -> None:
        examples = (
            "REsp OAB/SP 12345678901234567890",
            "REsp OAB 12345678901234567890",
            "AgInt OAB/SP 12345678901234567890",
            "REsp CPF 12345678901234567890",
            "REsp CNPJ 12345678901234567890",
            "REsp protocolo 12345678901234567890",
            "Processo nº 1234567-89.2020.1.02.3456\nprotocolo 12345\nOAB/SP 999999\nREsp OAB/SP 12345678901234567890",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertFalse(any(item.rule == "degraded_compact_cnj" for item in self.detector.detect(text)))

    def test_v6_degraded_compact_cnj_enforces_closed_identity_boundaries(self) -> None:
        examples = (
            "REsp 1234567890123456789",
            "REsp 123456789012345678901",
            "REsp 1234567890g123456789",
            "REsp 1234567-89.0123-\n\n4.56.7890",
            "REsp 1234567-89.0123 texto 4.56.7890",
            "REsp " + ("narrativa " * 12) + "12345678901234567890",
            "REsp. 12345678901234567890",
            "REsp! 12345678901234567890",
            "APL 12345678901234567890",
            "RSE 12345678901234567890",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertFalse(any(item.rule == "degraded_compact_cnj" for item in self.detector.detect(text)))


class CitationDetectorV6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        gold = pd.read_csv(DATASET_DIR / "goldenset.csv").reset_index(names="gold_idx")
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

    def test_v5_baseline_is_preserved_by_v6(self) -> None:
        baseline = self.predictions.loc[~self.predictions["rule"].eq("degraded_compact_cnj")]
        matches = greedy_match(self.gold, baseline)
        self.assertEqual(len(baseline), 219)
        self.assertEqual(len(matches), 137)
        self.assertEqual(int(matches["exact"].sum()), 83)

    def test_v6_candidate_count_offsets_and_exact_matches(self) -> None:
        self.assertEqual(len(self.predictions), 222)
        self.assertTrue((self.predictions["text"] == self.predictions.apply(lambda row: (DATASET_DIR / "txt" / f"{row.documento_id}.txt").read_text(encoding="utf-8")[row.start:row.end], axis=1)).all())
        self.assertEqual(len(self.matches), 140)
        self.assertEqual(int(self.matches["exact"].sum()), 86)

    def test_v6_metrics_by_level_and_type(self) -> None:
        expected = {
            "global": (self.gold, self.predictions, (140, 82, 85)),
            "N1": (self.gold[self.gold["nivel"].eq("N1")], self.predictions[self.predictions["nivel"].eq("N1")], (83, 40, 33)),
            "N2": (self.gold[self.gold["nivel"].eq("N2")], self.predictions[self.predictions["nivel"].eq("N2")], (57, 42, 52)),
            "jurisprudencia": (self.gold[self.gold["tipo"].eq("jurisprudencia")], self.predictions[self.predictions["tipo"].eq("jurisprudencia")], (124, 55, 62)),
            "lei": (self.gold[self.gold["tipo"].eq("lei")], self.predictions[self.predictions["tipo"].eq("lei")], (16, 27, 23)),
        }
        for name, (gold, predictions, expected_metrics) in expected.items():
            with self.subTest(group=name):
                self.assertEqual(metrics(gold, predictions, self.matches), expected_metrics)

    def test_non_process_candidates_and_validated_candidates_are_preserved(self) -> None:
        v1_predictions = self.predictions.loc[~self.predictions["rule"].eq("jurisprudencia_geral")].copy()
        v1_matches = greedy_match(self.gold, v1_predictions)
        new_predictions = self.predictions.loc[self.predictions["rule"].eq("jurisprudencia_geral")]
        new_matches = self.matches.loc[self.matches["pred_idx"].isin(new_predictions["pred_idx"])]

        self.assertEqual(len(v1_predictions), 210)
        self.assertEqual(
            v1_predictions.groupby("rule").size().to_dict(),
            {
                "cnj": 51,
                "degraded_compact_cnj": 3,
                "dispositivo_legal": 28,
                "lei_com_diploma": 15,
                "processo_ou_recurso": 74,
                "sumula_numerada": 10,
                "tribunal_contextual": 29,
            },
        )
        self.assertEqual(metrics(self.gold, v1_predictions, v1_matches), (128, 82, 97))
        self.assertEqual(int(v1_matches["exact"].sum()), 80)
        self.assertEqual(len(new_predictions), 12)
        self.assertEqual(len(new_matches), 12)
        self.assertTrue(set(v1_matches["gold_idx"]).issubset(set(self.matches["gold_idx"])))
        self.assertEqual(set(new_predictions["pred_idx"]), set(new_matches["pred_idx"]))
        self.assertTrue(new_predictions["family"].eq("jurisprudencia_referencia_geral").all())


if __name__ == "__main__":
    unittest.main()
