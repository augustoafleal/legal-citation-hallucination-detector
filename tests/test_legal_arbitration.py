from __future__ import annotations

import unittest

from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import (
    ArbitrationResult,
    CitationCandidate,
    CitationDetector,
    CitationParser,
    CitationResolver,
    ResolutionResult,
    arbitrate_citations,
    legal_maximal_span_arbitration,
)
from bracis_jusbrasil.database import connect_database, get_database_path


class LegalArbitrationV11Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.detector = CitationDetector()
        cls.parser = CitationParser()
        with connect_database(get_database_path(), read_only=True) as connection:
            cls.resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)

    def test_complete_span_survives_and_contained_article_is_suppressed(self) -> None:
        text = "art. 373, I, do CPC"
        candidates = self.detector.detect(text)
        parsed = tuple(self.parser.parse(item) for item in candidates)
        resolutions = tuple(self.resolver.resolve(item) for item in parsed)
        outputs = arbitrate_citations(text, candidates, parsed, resolutions, primary_identities=self.resolver.primary_identities)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].candidate.text, text)
        self.assertEqual(outputs[0].reason, "legal_maximal_span")
        self.assertEqual(outputs[0].resolution.id_canonico, 28893055)
        self.assertEqual(len(outputs[0].suppressed), 1)

    def test_conflicting_diploma_prevents_suppression(self) -> None:
        long_candidate = CitationCandidate(0, 36, "art. 5 da Constituição Federal", "test", "lei_dispositivo_com_diploma")
        short_candidate = CitationCandidate(0, 15, "art. 5 do CPP", "test", "lei_dispositivo_com_diploma")
        long_parsed = self.parser.parse(long_candidate)
        short_parsed = self.parser.parse(short_candidate)
        outputs = legal_maximal_span_arbitration((
            ArbitrationResult(long_candidate, long_parsed, self.resolver.resolve(long_parsed), (long_candidate,)),
            ArbitrationResult(short_candidate, short_parsed, self.resolver.resolve(short_parsed), (short_candidate,)),
        ))
        self.assertEqual(len(outputs), 2)
        self.assertTrue(all(item.reason == "preserved" for item in outputs))

    def test_nonlegal_output_is_preserved_by_identity(self) -> None:
        candidate = CitationCandidate(0, 17, "REsp nº 1.597.443", "processo_ou_recurso", "processo_ou_recurso_numerado")
        parsed = self.parser.parse(candidate)
        output = ArbitrationResult(candidate, parsed, ResolutionResult("insufficient", None, (), None, "test", None), (candidate,))
        self.assertEqual(legal_maximal_span_arbitration((output,)), (output,))


if __name__ == "__main__":
    unittest.main()
