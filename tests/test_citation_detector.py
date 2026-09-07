from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd

from bracis_jusbrasil.citations import CitationCandidate, CitationDetector


DATASET_DIR = Path("material_desafio_jusbrasil_bracis")
RULE_TYPES = {
    "cnj": "jurisprudencia",
    "processo_ou_recurso": "jurisprudencia",
    "degraded_compact_cnj": "jurisprudencia",
    "compound_procedural_chain": "jurisprudencia",
    "sumula_numerada": "jurisprudencia",
    "lei_com_diploma": "lei",
    "dispositivo_legal": "lei",
    "tribunal_contextual": "jurisprudencia",
    "decision_tribunal_relator_year": "jurisprudencia",
    "rcl_relator_year_no_tribunal": "jurisprudencia",
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

    def test_vague_general_jurisprudence_references_are_not_candidates(self) -> None:
        examples = (
            "A jurisprudência pacífica desta Corte orienta o caso.",
            "Aplica-se a orientação jurisprudencial da Corte Superior.",
            "Há entendimento sumulado sobre a matéria.",
            "Incide o verbete sumular aplicável à espécie.",
            "Confira os precedentes desta Casa em situações análogas.",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.detector.detect(text), ())

    def test_v8_h2_detects_only_complete_bounded_structures(self) -> None:
        examples = (
            "julgado do STF proferido em 2024 pela relatoria de Maria Silva Pereira",
            "acórdão do STJ em 2023, Rel. Min. João Silva",
            "Reclamação do STF de 2025, Rel. Min. Maria Silva",
            "RHC do STJ em 2022, Rel. Ministro José Pereira",
            "Agravo em REsp do STJ proferido em 2021, Rel. Ministra Ana Souza",
            "julgado do STF profcrido em 2024 pela relatoria dc Maria Silva Pereira",
        )
        for text in examples:
            with self.subTest(text=text):
                candidate = next(item for item in self.detector.detect(text) if item.rule == "decision_tribunal_relator_year")
                self.assertEqual(candidate.family, "jurisprudencia_tribunal_contextual")
                self.assertEqual(candidate.text, text)

    def test_v8_h2_rejects_missing_or_nonlocal_signals(self) -> None:
        examples = (
            "julgado do STF proferido em 2024",
            "julgado do STF pela relatoria de Maria Silva Pereira",
            "julgado proferido em 2024 pela relatoria de Maria Silva Pereira",
            "O STF em 2024, pela relatoria de Maria Silva Pereira, decidiu a matéria.",
            "julgado do STF 2024 pela relatoria de Maria Silva Pereira",
            "julgado do STF em 2024. Pela relatoria de Maria Silva Pereira, decidiu-se.",
            "julgado do STF em 2024.\n\nPela relatoria de Maria Silva Pereira, decidiu-se.",
            "A jurisprudência pacífica desta Corte orienta o caso.",
            "Aplica-se a orientação jurisprudencial da Corte Superior.",
            "Há entendimento sumulado sobre a matéria.",
            "Precedente do STJ em 2024, Relator indicado.",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertFalse(any(item.rule == "decision_tribunal_relator_year" for item in self.detector.detect(text)))

    def test_v9_h4_rcl_accepts_only_local_class_year_relator_chains(self) -> None:
        examples = (
            "Conforme Rcl de 2024, Rel. Min. Maria Silva Pereira, decidiu-se a questão.",
            "Na Reclamação em 2025, Rel. Ministra Ana Souza, o tema foi examinado.",
            "Conforme Rcl de 2031, pela relatoria de Nome Novo de Teste, decidiu-se a questão.",
            "Em Reclamação de 2022, sob relatoria de João Silva Pereira, decidiu-se a matéria.",
            "Rcl de 2024, Rel.\nMin. Maria Silva Pereira.",
        )
        for text in examples:
            with self.subTest(text=text):
                candidates = [item for item in self.detector.detect(text) if item.rule == "rcl_relator_year_no_tribunal"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].family, "jurisprudencia_tribunal_contextual")
                self.assertEqual(candidates[0].text, text[candidates[0].start:candidates[0].end])
                self.assertNotIn(candidates[0].text[-1], ".,;:")

    def test_v9_h4_rcl_rejects_incomplete_broad_and_non_rcl_forms(self) -> None:
        examples = (
            "Conforme Rcl de 2024, decidiu-se a questão.",
            "Conforme Rcl, Rel. Min. Maria Silva Pereira, decidiu-se a questão.",
            "Conforme Rcl 2024, Rel. Min. Maria Silva Pereira, decidiu-se a questão.",
            "Conforme Rcl de 2024. Rel. Min. Maria Silva Pereira decidiu outro tema.",
            "Conforme Rcl de 2024.\n\nRel. Min. Maria Silva Pereira decidiu outro tema.",
            "Rcl de 2024, Rel.\n\nMin. Maria Silva Pereira.",
            "Rcl de 2024, Rel.\n \nMin. Maria Silva Pereira.",
            "Rcl de 2024, Rel.\n\t\nMin. Maria Silva Pereira.",
            "Rcl de 2024, Rel.\n   \nMin. Maria Silva Pereira.",
            "Rcl de 2024, Rel.\r\n \r\nMin. Maria Silva Pereira.",
            "APL de 2023, Rel. Min. Maria Silva Pereira.",
            "RHC de 2024, Rel. Min. Maria Silva Pereira.",
            "Precedente do STF de 2024, da relatoria de Cármen Lúcia.",
            "Jurisprudência pacífica desta Corte.",
            "Orientação jurisprudencial da Corte Superior.",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertFalse(any(item.rule == "rcl_relator_year_no_tribunal" for item in self.detector.detect(text)))

    def test_v9_h4_rcl_preserves_h2_cnj_and_legacy_contextual_candidates(self) -> None:
        text = (
            "Rcl de 2024, Rel. Min. Maria Silva Pereira; "
            "julgado do STF proferido em 2024 pela relatoria de Ana Souza Pereira; "
            "Autos 1234567-89.2020.1.23.4567; Precedente do STJ em 2024, Relator indicado."
        )
        rules = {item.rule for item in self.detector.detect(text)}
        self.assertTrue({"rcl_relator_year_no_tribunal", "decision_tribunal_relator_year", "cnj", "tribunal_contextual"} <= rules)

    def test_v8_h2_priority_preserves_low_overlap_and_suppresses_dangerous_context(self) -> None:
        text = "Conforme julgado do STF proferido em 2024 pela relatoria de Maria Silva Pereira."
        candidates = self.detector.detect(text)
        h2_candidate = next(item for item in candidates if item.rule == "decision_tribunal_relator_year")
        contextual = next(item for item in candidates if item.rule == "tribunal_contextual")
        self.assertLess(span_iou(h2_candidate.start, h2_candidate.end, contextual.start, contextual.end), .5)
        self.assertIn(contextual, CitationDetector._apply_concrete_incomplete_priority(list(candidates)))

        dangerous = CitationCandidate(
            h2_candidate.start + 1, h2_candidate.end - 1,
            text[h2_candidate.start + 1:h2_candidate.end - 1],
            "tribunal_contextual", "jurisprudencia_tribunal_contextual",
        )
        retained = CitationDetector._apply_concrete_incomplete_priority([h2_candidate, dangerous])
        self.assertEqual(retained, [h2_candidate])

    def test_v8_h2_priority_never_suppresses_other_families(self) -> None:
        text = (
            "Autos 1234567-89.2020.1.23.4567; REsp nº 1.597.443; "
            "art. 5 da Constituição Federal; julgado do STF proferido em 2024 "
            "pela relatoria de Maria Silva Pereira."
        )
        rules = {item.rule for item in self.detector.detect(text)}
        self.assertTrue({"cnj", "processo_ou_recurso", "lei_com_diploma", "decision_tribunal_relator_year"} <= rules)

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

    def test_v7_detects_only_approved_complete_compound_chains(self) -> None:
        examples = (
            "ED-E-ED-RR-65-63.2010.5.01.0075",
            "TST-E-RR-173000-49.2008.5.15.0024",
            "TST-ED-E-ED-RR-3400-05.2011.5.21.0009",
            "E-ED-RR-41200-79.2011.5.21.0005",
            "ED-E-ED-RR-160000-97.2010.5.21.0006",
        )
        for text in examples:
            with self.subTest(text=text):
                candidates = [item for item in self.detector.detect(text) if item.rule == "compound_procedural_chain"]
                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertEqual(candidate.text, text)
                self.assertEqual((candidate.start, candidate.end), (0, len(text)))
                self.assertEqual(candidate.family, "processo_ou_recurso_numerado")

    def test_v7_rejects_partial_and_arbitrary_compound_chains(self) -> None:
        examples = (
            "TST-E-RR-173000-49.2008",
            "TST-ED-E-ED-ARR-1099-66.2011.5.02.",
            "ED-E-ED-RR-65-63.2010.5.01",
            "ABC-E-RR-173000-49.2008.5.15.0024",
            "ED-XYZ-RR-173000-49.2008.5.15.0024",
            "ED-E-FOO-173000-49.2008.5.15.0024",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.detector._detect_compound_procedural_chain(text), [])
                self.assertFalse(any(item.rule == "compound_procedural_chain" for item in self.detector.detect(text)))

    def test_v7_enforces_boundaries_separator_and_repetition(self) -> None:
        examples = (
            "E-65-63.2010.5.01.0075",
            "ED-TST-RR-65-63.2010.5.01.0075",
            "ED - RR - 65-63.2010.5.01.0075",
            "ED-ED-ED-RR-65-63.2010.5.01.0075",
            "ED-E-ED-RR-E-RR-65-63.2010.5.01.0075",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.detector._detect_compound_procedural_chain(text), [])

    def test_v7_accepts_one_to_seven_digit_prefixes_only(self) -> None:
        for digits in range(1, 8):
            with self.subTest(digits=digits):
                text = f"ED-RR-{'1' * digits}-23.2020.5.01.0001"
                self.assertEqual(len(self.detector._detect_compound_procedural_chain(text)), 1)
        self.assertEqual(
            self.detector._detect_compound_procedural_chain("ED-RR-12345678-23.2020.5.01.0001"),
            [],
        )

    def test_v7_span_excludes_narrative_and_trailing_punctuation(self) -> None:
        chain = "ED-E-ED-RR-65-63.2010.5.01.0075"
        text = f"Conforme {chain}, precedente aplicável."
        candidate = self.detector._detect_compound_procedural_chain(text)[0]
        self.assertEqual(candidate.text, chain)
        self.assertEqual((candidate.start, candidate.end), (text.index(chain), text.index(chain) + len(chain)))


class CitationDetectorV9Tests(unittest.TestCase):
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

    def test_v7_compound_candidates_are_bounded_and_all_match_gold(self) -> None:
        compound = self.predictions.loc[self.predictions["rule"].eq("compound_procedural_chain")]
        compound_matches = self.matches.loc[self.matches["pred_idx"].isin(compound["pred_idx"])]
        self.assertEqual(len(compound), 6)
        self.assertEqual(len(compound_matches), 6)
        self.assertEqual(set(compound["family"]), {"processo_ou_recurso_numerado"})

    def test_v9_candidate_count_offsets_and_exact_matches(self) -> None:
        self.assertEqual(len(self.predictions), 237)
        self.assertTrue((self.predictions["text"] == self.predictions.apply(lambda row: (DATASET_DIR / "txt" / f"{row.documento_id}.txt").read_text(encoding="utf-8")[row.start:row.end], axis=1)).all())
        self.assertEqual(len(self.matches), 160)
        self.assertEqual(int(self.matches["exact"].sum()), 113)

    def test_v9_metrics_by_level_and_type(self) -> None:
        expected = {
            "global": (self.gold, self.predictions, (160, 77, 35)),
            "N1": (self.gold[self.gold["nivel"].eq("N1")], self.predictions[self.predictions["nivel"].eq("N1")], (91, 37, 10)),
            "N2": (self.gold[self.gold["nivel"].eq("N2")], self.predictions[self.predictions["nivel"].eq("N2")], (69, 40, 25)),
            "jurisprudencia": (self.gold[self.gold["tipo"].eq("jurisprudencia")], self.predictions[self.predictions["tipo"].eq("jurisprudencia")], (144, 50, 21)),
            "lei": (self.gold[self.gold["tipo"].eq("lei")], self.predictions[self.predictions["tipo"].eq("lei")], (16, 27, 14)),
        }
        for name, (gold, predictions, expected_metrics) in expected.items():
            with self.subTest(group=name):
                self.assertEqual(metrics(gold, predictions, self.matches), expected_metrics)

    def test_non_process_candidates_are_preserved_without_general_references(self) -> None:
        self.assertEqual(len(self.predictions), 237)
        self.assertEqual(
            self.predictions.groupby("rule").size().to_dict(),
            {
                "cnj": 46,
                "compound_procedural_chain": 6,
                "decision_tribunal_relator_year": 27,
                "degraded_compact_cnj": 3,
                "dispositivo_legal": 28,
                "lei_com_diploma": 15,
                "processo_ou_recurso": 74,
                "rcl_relator_year_no_tribunal": 4,
                "sumula_numerada": 10,
                "tribunal_contextual": 24,
            },
        )
        self.assertEqual(metrics(self.gold, self.predictions, self.matches), (160, 77, 35))
        self.assertEqual(int(self.matches["exact"].sum()), 113)
        self.assertFalse(self.predictions["rule"].eq("jurisprudencia_geral").any())
        self.assertFalse(self.predictions["family"].eq("jurisprudencia_referencia_geral").any())


if __name__ == "__main__":
    unittest.main()
