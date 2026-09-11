from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, ParsedLegalIdentity


class LegalParserV11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = CitationDetector()
        self.parser = CitationParser()

    def parse_complete(self, text: str) -> ParsedLegalIdentity:
        candidate = next(item for item in self.detector.detect(text) if item.family == "lei_dispositivo_com_diploma")
        identity = self.parser.parse(candidate).legal
        self.assertIsNotNone(identity)
        return identity  # type: ignore[return-value]

    def test_article_diploma_and_raw_span_are_explicit_and_immutable(self) -> None:
        identity = self.parse_complete("art. 1.134 da Lei nº 13.105/2015")
        self.assertEqual((identity.article_raw, identity.article_normalized), ("1.134", "1134"))
        self.assertEqual(identity.diploma_normalized, "CPC")
        self.assertEqual(identity.raw_text, "art. 1.134 da Lei nº 13.105/2015")
        self.assertTrue(identity.has_complete_identity)
        with self.assertRaises(FrozenInstanceError):
            identity.article_normalized = "1"  # type: ignore[misc]

    def test_complements_do_not_change_primary_identity(self) -> None:
        paragraph = self.parse_complete("art. 896, § 1º-A, da CLT")
        inciso_alinea = self.parse_complete("art. 1º, I, 'g', da LC nº 64/1990")
        self.assertEqual((paragraph.article_normalized, paragraph.paragraph_normalized), ("896", "1-A"))
        self.assertEqual((inciso_alinea.article_normalized, inciso_alinea.inciso_normalized, inciso_alinea.alinea_normalized), ("1", "I", "g"))

    def test_article_suffix_is_preserved_but_not_promoted(self) -> None:
        identity = self.parse_complete("art. 896-A da CLT")
        self.assertEqual((identity.article_normalized, identity.article_suffix), ("896", "A"))
        self.assertFalse(identity.has_complete_identity)
        self.assertIn("ARTICLE_SUFFIX_UNCOVERED", identity.parse_warnings)

    def test_full_diplomas_guarded_aliases_and_noncritical_ocr(self) -> None:
        self.assertEqual(self.parse_complete("art. 14 do Código de Defesa do Consumidor").diploma_normalized, "CDC")
        self.assertEqual(self.parse_complete("art. 5º, LV, da Constituição Federal").diploma_normalized, "CF")
        repaired = self.parse_complete("artigo 7º, XXIX, da Constituição Fedcral")
        self.assertEqual(repaired.diploma_normalized, "CF")
        self.assertIn("NONCRITICAL_OCR_REPAIR", repaired.parse_warnings)
        loose = self.parser.parse(CitationCandidate(0, 2, "CF", "test", "lei_referencia_geral")).legal
        self.assertIsNotNone(loose)
        self.assertTrue(loose.has_ambiguous_identity)  # type: ignore[union-attr]
        self.assertFalse(loose.has_complete_identity)  # type: ignore[union-attr]

    def test_boundaries_and_critical_ocr_fail_closed(self) -> None:
        single = self.parse_complete("art 312 do Código\nde Processo Penal")
        self.assertTrue(single.has_complete_identity)
        double = self.detector.detect("art. 312\n\ndo Código de Processo Penal")
        self.assertFalse(any(item.family == "lei_dispositivo_com_diploma" for item in double))
        truncated = self.parser.parse(
            CitationCandidate(0, 25, "art. 312 do Código de De", "test", "lei_dispositivo_com_diploma")
        ).legal
        self.assertIsNotNone(truncated)
        self.assertTrue(truncated.has_critical_ocr)  # type: ignore[union-attr]
        self.assertFalse(truncated.has_complete_identity)  # type: ignore[union-attr]

    def test_left_diploma_is_bounded_and_narrative_is_excluded(self) -> None:
        text = "nos termos do CPC, art. 373, I"
        candidate = next(item for item in self.detector.detect(text) if item.rule == "lei_com_diploma_esquerda")
        self.assertEqual(candidate.text, "CPC, art. 373, I")
        identity = self.parser.parse(candidate).legal
        self.assertEqual(identity.parse_source, "bounded_left_diploma")  # type: ignore[union-attr]
        self.assertEqual(identity.inciso_normalized, "I")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
