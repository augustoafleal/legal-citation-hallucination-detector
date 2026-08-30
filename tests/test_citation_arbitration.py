from __future__ import annotations

import unittest

from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.citations import (
    ArbitrationResult,
    CitationCandidate,
    CitationParser,
    CitationResolver,
    ParsedCitation,
    PrimaryIdentity,
    ResolutionResult,
    structural_cnj_union_merge,
)
from bracis_jusbrasil.database import connect_database, get_database_path


class StructuralCNJArbitrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = "TST-ED-E-ED-RR-3400-05.2011.5.21.0009"
        self.cnj = CitationCandidate(18, len(self.text), self.text[18:], "cnj", "processo_cnj")
        self.companion = CitationCandidate(0, 30, self.text[:30], "processo_ou_recurso", "processo_ou_recurso_numerado")
        parser = CitationParser()
        self.parsed = [parser.parse(self.cnj), parser.parse(self.companion)]
        self.resolution = [
            ResolutionResult("resolved", 7, (7,), "cnj_primary_identity", "cnj_primary_unique", "acordao"),
            ResolutionResult("insufficient", None, (), None, "family_not_supported_in_v1", None),
        ]
        self.identity = {
            7: PrimaryIdentity(7, "TST", "TST-ED-E-ED-RR-3400-05.2011.5.21.0009", "34000520115210009", "TST:RR")
        }

    def arbitrate(self, candidates=None, parsed=None, resolutions=None):
        return structural_cnj_union_merge(
            self.text,
            candidates or [self.cnj, self.companion],
            parsed or self.parsed,
            resolutions or self.resolution,
            primary_identities=self.identity,
        )

    def test_merges_literal_span_and_preserves_resolution(self):
        output = self.arbitrate()
        self.assertEqual(len(output), 1)
        self.assertIsInstance(output[0], ArbitrationResult)
        self.assertEqual(output[0].reason, "structural_cnj_union_merge")
        self.assertEqual(output[0].candidate.text, self.text)
        self.assertEqual(output[0].resolution.id_canonico, 7)
        self.assertEqual(len(output[0].suppressed), 2)

    def test_isolated_cnj_is_preserved(self):
        output = self.arbitrate([self.cnj], [self.parsed[0]], [self.resolution[0]])
        self.assertEqual(output[0].candidate, self.cnj)
        self.assertEqual(output[0].reason, "preserved")

    def test_multiple_compatible_companions_abstain(self):
        second = CitationCandidate(0, 29, self.text[:29], "processo_ou_recurso", "processo_ou_recurso_numerado")
        parser = CitationParser()
        output = self.arbitrate([self.cnj, self.companion, second], [self.parsed[0], self.parsed[1], parser.parse(second)], self.resolution + [self.resolution[1]])
        self.assertTrue(any(item.reason == "no_safe_arbitration" for item in output))
        self.assertEqual(len(output), 3)

    def test_class_conflict_abstains(self):
        identity = {7: PrimaryIdentity(7, "TST", self.identity[7].numero_raw, self.identity[7].numero_normalizado, "TST:AIRR")}
        output = structural_cnj_union_merge(self.text, [self.cnj, self.companion], self.parsed, self.resolution, primary_identities=identity)
        self.assertEqual(len(output), 2)
        self.assertTrue(all(item.reason != "structural_cnj_union_merge" for item in output))

    def test_non_process_companion_is_never_merged(self):
        legal = CitationCandidate(0, 30, self.text[:30], "dispositivo_legal", "lei_dispositivo_sem_diploma")
        parser = CitationParser()
        output = self.arbitrate([self.cnj, legal], [self.parsed[0], parser.parse(legal)], self.resolution)
        self.assertEqual(len(output), 2)

    def test_tribunal_conflict_abstains(self):
        identity = {7: PrimaryIdentity(7, "STJ", self.identity[7].numero_raw, self.identity[7].numero_normalizado, "TST:RR")}
        output = structural_cnj_union_merge(self.text, [self.cnj, self.companion], self.parsed, self.resolution, primary_identities=identity)
        self.assertEqual(len(output), 2)


class CitationResolverV3GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect_database(get_database_path(), read_only=True) as connection:
            cls.resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)

    def test_cnj_missing_number_is_insufficient(self):
        parsed = ParsedCitation("processo_cnj", "jurisprudencia", None, None, "unknown", {}, {})
        self.assertEqual(self.resolver.resolve(parsed).reason, "cnj_number_missing")

    def test_cnj_unknown_number_is_no_match(self):
        parsed = ParsedCitation("processo_cnj", "jurisprudencia", None, None, "unknown", {"numero_normalizado": "12345678901234567890"}, {})
        self.assertEqual(self.resolver.resolve(parsed).status, "no_match")

    def test_tst_class_conflict_abstains(self):
        identity = next(item for item in self.resolver.primary_identities.values() if item.tribunal == "TST" and item.classe == "TST:AIRR")
        parsed = ParsedCitation("processo_cnj", "jurisprudencia", "TST", "TST", "explicit", {"numero_normalizado": identity.numero_normalizado, "numero_raw": identity.numero_raw, "classe_raw": "AgARR"}, {})
        result = self.resolver.resolve(parsed)
        self.assertEqual((result.status, result.reason), ("insufficient", "cnj_primary_class_conflict"))

    def test_explicit_wrong_tribunal_abstains(self):
        identity = next(item for item in self.resolver.primary_identities.values() if item.tribunal == "TST")
        parsed = ParsedCitation("processo_cnj", "jurisprudencia", "STJ", "STJ", "explicit", {"numero_normalizado": identity.numero_normalizado, "numero_raw": identity.numero_raw}, {})
        self.assertEqual(self.resolver.resolve(parsed).reason, "cnj_tribunal_conflict")

    def test_cnj_unique_without_class_remains_resolvable(self):
        identity = next(item for item in self.resolver.primary_identities.values() if len(item.numero_normalizado) >= 16)
        parsed = ParsedCitation("processo_cnj", "jurisprudencia", None, None, "unknown", {"numero_normalizado": identity.numero_normalizado, "numero_raw": identity.numero_raw}, {})
        result = self.resolver.resolve(parsed)
        self.assertEqual((result.status, result.id_canonico), ("resolved", identity.id_canonico))

    def test_cnj_multi_id_is_ambiguous(self):
        case = next(
            case for case in self.resolver._case_index.cases()
            if len(case.canonical_ids) > 1 and len(case.numero_normalizado) >= 15
        )
        number = case.numero_normalizado
        parsed = ParsedCitation("processo_cnj", "jurisprudencia", None, None, "unknown", {"numero_normalizado": number, "numero_raw": number}, {})
        result = self.resolver.resolve(parsed)
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.id_canonico)


if __name__ == "__main__":
    unittest.main()
