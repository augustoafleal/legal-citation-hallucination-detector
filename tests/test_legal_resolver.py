from __future__ import annotations

import unittest

from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver
from bracis_jusbrasil.database import connect_database, get_database_path


POSITIVES = (
    ("art. 276 do Código Eleitoral", 10577194),
    ("art. 290 do Código Penal Militar", 10590194),
    ("art. 14 do CDC", 10606184),
    ("art. 93 da Constituição Federal", 10626510),
    ("art. 896 da CLT", 10637358),
    ("art. 7 da Constituição Federal", 10641213),
    ("art. 5 da Constituição Federal", 10641516),
    ("art. 818 da CLT", 10647746),
    ("art. 312 do CPP", 10652044),
    ("art. 477 da CLT", 10710324),
    ("art. 186 do Código Civil", 10718759),
    ("art. 1 da LC nº 64/1990", 11304039),
    ("art. 373 do CPC", 28893055),
)

DANGEROUS_NEGATIVES = (
    "art. 290 da Constituição Federal",
    "art. 5 do CPP",
    "art. 7 da CLT",
    "art. 93 do Código Civil",
    "art. 477 do CPC",
    "art. 818 do CPC",
    "art. 896 do CPC",
    "art. 1 da Constituição Federal",
    "art. 14 do Código Civil",
    "art. 186 do CPP",
    "art. 312 do Código Civil",
    "art. 373 da CLT",
    "art. 276 do Código Civil",
)


class LegalResolverV11Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.detector = CitationDetector()
        cls.parser = CitationParser()
        with connect_database(get_database_path(), read_only=True) as connection:
            cls.resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)

    def resolve_complete(self, text: str):
        candidate = next(item for item in self.detector.detect(text) if item.family == "lei_dispositivo_com_diploma")
        return self.resolver.resolve(self.parser.parse(candidate))

    def test_all_13_positive_composite_keys_resolve_exact_id(self) -> None:
        for text, expected_id in POSITIVES:
            with self.subTest(text=text):
                result = self.resolve_complete(text)
                self.assertEqual((result.status, result.id_canonico), ("resolved", expected_id))
                self.assertEqual(result.strategy, "legal_article_diploma_catalog")

    def test_all_13_cross_diploma_negatives_never_resolve_real(self) -> None:
        for text in DANGEROUS_NEGATIVES:
            with self.subTest(text=text):
                result = self.resolve_complete(text)
                self.assertEqual((result.status, result.id_canonico), ("no_match", None))

    def test_incomplete_unknown_and_article_suffix_abstain(self) -> None:
        article_only = next(item for item in self.detector.detect("art. 290") if item.family == "lei_dispositivo_sem_diploma")
        self.assertEqual(self.resolver.resolve(self.parser.parse(article_only)).status, "insufficient")
        unknown = self.resolve_complete("art. 5 do Código Penal")
        self.assertEqual((unknown.status, unknown.id_canonico), ("insufficient", None))
        unknown_law = self.resolve_complete("art. 5 da Lei nº 12.345/2020")
        self.assertEqual((unknown_law.status, unknown_law.id_canonico), ("insufficient", None))
        suffix = self.resolve_complete("art. 896-A da CLT")
        self.assertEqual((suffix.status, suffix.id_canonico), ("insufficient", None))

    def test_guarded_alias_and_source_covered_no_match(self) -> None:
        guarded = self.resolve_complete("art. 5 da CF")
        self.assertEqual((guarded.status, guarded.id_canonico), ("resolved", 10641516))
        covered_no_match = self.resolve_complete("art. 172 da Lei nº 9.504/1997")
        self.assertEqual((covered_no_match.status, covered_no_match.id_canonico), ("no_match", None))

    def test_critical_ocr_never_becomes_inventada(self) -> None:
        candidate = CitationCandidate(0, 25, "art. 312 do Código de De", "test", "lei_dispositivo_com_diploma")
        result = self.resolver.resolve(self.parser.parse(candidate))
        self.assertEqual((result.status, result.id_canonico, result.reason), ("insufficient", None, "legal_identity_critical_ocr"))


if __name__ == "__main__":
    unittest.main()
