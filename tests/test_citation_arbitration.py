from __future__ import annotations

import inspect
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
from bracis_jusbrasil.citations.arbitration import (
    filter_non_precedential_cnj_references,
    is_cnj_preserved_by_jurisprudential_guard,
    is_non_precedential_cnj_reference,
)


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


class V10RefinedCNJFilterTests(unittest.TestCase):
    number = "1111111-11.1111.1.11.1111"

    def filter(self, text, status="no_match", candidates=None, resolutions=None):
        start = text.index(self.number)
        candidate = CitationCandidate(start, start + len(self.number), self.number, "cnj", "processo_cnj")
        candidates = candidates or [candidate]
        parser = CitationParser()
        parsed = [parser.parse(item, context=text) for item in candidates]
        candidate_ids = (1,) if status == "resolved" else (1, 2) if status == "ambiguous" else ()
        resolutions = resolutions or [
            ResolutionResult(status, 1 if status == "resolved" else None, candidate_ids, None, "test", None)
        ]
        return filter_non_precedential_cnj_references(text, candidates, parsed, resolutions), candidate

    def assert_suppressed(self, text):
        filtered, _ = self.filter(text)
        self.assertEqual(filtered, ())

    def assert_preserved(self, text, status="no_match"):
        filtered, candidate = self.filter(text, status=status)
        self.assertEqual(filtered, (candidate,))

    def g4_text(self, publication="DJe"):
        return (
            f'Poder Judiciário Processo nº {self.number}. No mesmo sentido, a jurisprudência registra '
            f'"Apelação e Recurso"; Relatora: exemplo, {publication}, EMENTA.'
        )

    def test_suppresses_header_own_process(self):
        self.assert_suppressed(f"Poder Judiciário Processo nº {self.number}")

    def test_suppresses_party_block(self):
        self.assert_suppressed(f"Processo nº {self.number} Recorrente: exemplo")

    def test_suppresses_procedural_history(self):
        self.assert_suppressed(f"Autos nº {self.number} remetidos à origem")

    def test_suppresses_non_precedential_process_marker(self):
        self.assert_suppressed(f"Ação Penal nº {self.number} já sentenciada")

    def test_g4_preserves_jurisprudential_sequence_with_dje(self):
        text = self.g4_text()
        candidate_start = text.index(self.number)
        candidate = CitationCandidate(candidate_start, candidate_start + len(self.number), self.number, "cnj", "processo_cnj")
        self.assertTrue(is_cnj_preserved_by_jurisprudential_guard(text, candidate))
        self.assert_preserved(text)

    def test_g4_preserves_relator_sequence(self):
        self.assert_preserved(self.g4_text("Diário de Justiça"))

    def test_g4_preserves_ementa_sequence(self):
        self.assert_preserved(self.g4_text("EMENTA"))

    def test_g4_preserves_general_quoted_precedent_block(self):
        self.assert_preserved(self.g4_text())

    def test_resolved_cnj_is_preserved(self):
        self.assert_preserved(f"Poder Judiciário Processo nº {self.number}", status="resolved")

    def test_ambiguous_cnj_is_preserved(self):
        self.assert_preserved(f"Poder Judiciário Processo nº {self.number}", status="ambiguous")

    def test_h2_and_h4_are_preserved(self):
        text = f"Poder Judiciário Processo nº {self.number}"
        start = text.index(self.number)
        cnj = CitationCandidate(start, start + len(self.number), self.number, "cnj", "processo_cnj")
        h2 = CitationCandidate(0, 16, text[:16], "decision_tribunal_relator_year", "jurisprudencia_tribunal_contextual")
        h4 = CitationCandidate(17, 30, text[17:30], "rcl_relator_year_no_tribunal", "jurisprudencia_tribunal_contextual")
        parser = CitationParser()
        filtered = filter_non_precedential_cnj_references(
            text,
            [cnj, h2, h4],
            [parser.parse(cnj, context=text), parser.parse(h2, context=text), parser.parse(h4, context=text)],
            [ResolutionResult("no_match", None, (), None, "test", None)] * 3,
        )
        self.assertEqual(filtered, (cnj, h2, h4))

    def test_non_cnj_is_preserved(self):
        text = "jurisprudência sem número"
        candidate = CitationCandidate(0, len(text), text, "tribunal_contextual", "jurisprudencia_tribunal_contextual")
        parser = CitationParser()
        filtered = filter_non_precedential_cnj_references(
            text, [candidate], [parser.parse(candidate, context=text)],
            [ResolutionResult("insufficient", None, (), None, "test", None)],
        )
        self.assertEqual(filtered, (candidate,))

    def test_no_match_without_h4_context_is_preserved(self):
        self.assert_preserved(self.number)

    def test_absence_of_process_marker_is_preserved(self):
        self.assert_preserved(f"Poder Judiciário {self.number}")

    def test_filter_has_no_external_identity_input(self):
        parameters = set(inspect.signature(is_non_precedential_cnj_reference).parameters)
        self.assertFalse(parameters & {"documento_id", "offset", "lookup", "gold", "human_label"})

    def test_protected_overlap_is_preserved(self):
        text = f"Poder Judiciário Processo nº {self.number}"
        start = text.index(self.number)
        cnj = CitationCandidate(start, start + len(self.number), self.number, "cnj", "processo_cnj")
        h2 = CitationCandidate(start + 1, start + 12, text[start + 1:start + 12], "decision_tribunal_relator_year", "jurisprudencia_tribunal_contextual")
        parser = CitationParser()
        filtered = filter_non_precedential_cnj_references(
            text,
            [cnj, h2],
            [parser.parse(cnj, context=text), parser.parse(h2, context=text)],
            [ResolutionResult("no_match", None, (), None, "test", None)] * 2,
        )
        self.assertEqual(filtered, (cnj, h2))


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
