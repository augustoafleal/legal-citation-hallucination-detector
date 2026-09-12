from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import re
import unittest

import pandas as pd

from bracis_jusbrasil.cases import Case, CaseIndex, build_case_index
from bracis_jusbrasil.citations import CitationCandidate, CitationParser, CitationResolver, ParsedCitation, ResolutionResult
from bracis_jusbrasil.database import connect_database, get_database_path


DATASET_DIR = Path("material_desafio_jusbrasil_bracis")
CNJ_PATTERN = re.compile(r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b")
SUMULA_PATTERN = re.compile(r"\bS[ÚU]MULA(?:\s+VINCULANTE)?\s*(?:N[ºO.]?\s*)?\d+", re.IGNORECASE)
ARTICLE_PATTERN = re.compile(r"\b(?:art(?:igo)?s?[.]?\s*)\d+", re.IGNORECASE)
DIPLOMA_PATTERN = re.compile(r"\b(?:Constituiç[aã]o(?: Federal)?|C[oó]digo|Lei(?: Complementar)?\s*(?:n[ºo.]?\s*)?\d+|CLT|CPC|CPP|CC|CDC|CPM)\b", re.IGNORECASE)
PROCESS_CLASS_PATTERN = re.compile(r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|Agravo|Recurso Especial|Recurso Extraordinário|Habeas Corpus|Reclamação)\b", re.IGNORECASE)
TRIBUNAL_PATTERN = re.compile(r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
RELATOR_PATTERN = re.compile(r"\b(?:Rel\.?|Relator(?:a)?)\s*(?:Min\.?|Ministra|Ministro|Des\.?)?\s*[A-Z]", re.IGNORECASE)


def parsed(family: str, data: dict[str, str], *, tribunal: str | None = None, tribunal_source: str = "unknown") -> ParsedCitation:
    return ParsedCitation(family, "jurisprudencia", tribunal, tribunal, tribunal_source, data, {})


def oracle_family(text: str, citation_type: str) -> str:
    if citation_type == "lei":
        if ARTICLE_PATTERN.search(text) and DIPLOMA_PATTERN.search(text):
            return "lei_dispositivo_com_diploma"
        if ARTICLE_PATTERN.search(text):
            return "lei_dispositivo_sem_diploma"
        return "lei_referencia_geral"
    if SUMULA_PATTERN.search(text):
        return "sumula_numerada"
    if CNJ_PATTERN.search(text):
        return "processo_cnj"
    if PROCESS_CLASS_PATTERN.search(text) and re.search(r"\d", text):
        return "processo_ou_recurso_numerado"
    if TRIBUNAL_PATTERN.search(text) and (YEAR_PATTERN.search(text) or RELATOR_PATTERN.search(text)):
        return "jurisprudencia_tribunal_contextual"
    return "jurisprudencia_referencia_geral"


class CitationResolverUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with connect_database(get_database_path(), read_only=True) as connection:
            cls.index = build_case_index(connection)
            cls.resolver = CitationResolver(case_index=cls.index, connection=connection)

    def test_single_case_no_match_and_missing_number(self) -> None:
        case = next(case for case in self.index.cases() if len(case.canonical_ids) == 1)
        resolved = self.resolver.resolve(parsed("processo_ou_recurso_numerado", {"numero_normalizado": case.numero_normalizado}))
        self.assertEqual((resolved.status, resolved.id_canonico, resolved.candidate_ids, resolved.record_type), ("resolved", case.canonical_ids[0], case.canonical_ids, "acordao"))
        missing = self.resolver.resolve(parsed("processo_ou_recurso_numerado", {}))
        no_match = self.resolver.resolve(parsed("processo_ou_recurso_numerado", {"numero_normalizado": "9999999999999999"}))
        self.assertEqual((missing.status, missing.strategy), ("insufficient", None))
        self.assertEqual((no_match.status, no_match.candidate_ids, no_match.strategy), ("no_match", (), "case_number_exact"))

    def test_multi_id_never_selects_an_id_even_if_case_order_changes(self) -> None:
        case = next(case for case in self.index.cases() if len(case.canonical_ids) > 1)
        result = self.resolver.resolve(parsed("processo_ou_recurso_numerado", {"numero_normalizado": case.numero_normalizado}))
        reversed_case = Case(case.tribunal, case.numero_normalizado, tuple(reversed(case.canonical_ids)), tuple(reversed(case.records)))
        with connect_database(get_database_path(), read_only=True) as connection:
            reversed_resolver = CitationResolver(case_index=CaseIndex({(case.tribunal, case.numero_normalizado): reversed_case}), connection=connection)
        reversed_result = reversed_resolver.resolve(parsed("processo_ou_recurso_numerado", {"numero_normalizado": case.numero_normalizado}))
        for value in (result, reversed_result):
            self.assertEqual(value.status, "ambiguous")
            self.assertIsNone(value.id_canonico)
            self.assertEqual(len(value.candidate_ids), 2)

    def test_numbered_case_primary_class_guard_resolves_one_compatible_id(self) -> None:
        parsed_citation = parsed(
            "processo_ou_recurso_numerado",
            {"numero_normalizado": "1597443", "classe_raw": "AgInt no REsp"},
        )
        result = self.resolver.resolve(parsed_citation)
        self.assertEqual(
            (result.status, result.id_canonico, result.candidate_ids, result.strategy, result.reason),
            ("resolved", 2684973273, (2684973273,), "case_number_primary_class", "case_number_primary_class_unique"),
        )

    def test_numbered_case_primary_class_guard_abstains_without_unique_match(self) -> None:
        number = "1597443"
        cases = {
            "without_class": parsed("processo_ou_recurso_numerado", {"numero_normalizado": number}),
            "class_conflict": parsed(
                "processo_ou_recurso_numerado",
                {"numero_normalizado": number, "classe_raw": "HC"},
            ),
        }
        for label, parsed_citation in cases.items():
            with self.subTest(label=label):
                result = self.resolver.resolve(parsed_citation)
                self.assertEqual(result.status, "ambiguous")
                self.assertIsNone(result.id_canonico)
                self.assertEqual(result.candidate_ids, (2684973273, 2679428592))

    def test_numbered_case_primary_class_guard_abstains_when_class_matches_multiple_ids(self) -> None:
        result = self.resolver.resolve(
            parsed(
                "processo_ou_recurso_numerado",
                {"numero_normalizado": "1605278", "classe_raw": "AgInt"},
            )
        )
        self.assertEqual((result.status, result.id_canonico), ("ambiguous", None))
        self.assertEqual(result.candidate_ids, (2848942660, 2939369305))

    def test_sumula_number_only_does_not_require_tribunal(self) -> None:
        resolved = self.resolver.resolve(parsed("sumula_numerada", {"sumula_numero": "83"}, tribunal="STJ", tribunal_source="explicit"))
        no_tribunal = self.resolver.resolve(parsed("sumula_numerada", {"sumula_numero": "83"}))
        no_match = self.resolver.resolve(parsed("sumula_numerada", {"sumula_numero": "999"}))
        self.assertEqual((resolved.status, resolved.record_type), ("resolved", "sumula"))
        self.assertEqual(resolved.strategy, "sumula_number_tribunal")
        self.assertEqual((no_tribunal.status, no_tribunal.strategy, no_tribunal.reason), ("resolved", "sumula_number_only", "sumula_unique"))
        self.assertEqual((no_match.status, no_match.strategy, no_match.reason), ("no_match", "sumula_number_only", "sumula_number_not_found"))

    def test_unsupported_families_are_insufficient(self) -> None:
        families = {
            "lei_dispositivo_com_diploma": ("legal_identity_missing", "dispositivo"),
            "lei_dispositivo_sem_diploma": ("legal_identity_missing", "dispositivo"),
            "lei_referencia_geral": ("legal_identity_missing", "dispositivo"),
            "jurisprudencia_referencia_geral": ("family_not_supported_in_v1", None),
            "jurisprudencia_tribunal_contextual": ("family_not_supported_in_v1", None),
        }
        for family, (reason, record_type) in families.items():
            with self.subTest(family=family):
                result = self.resolver.resolve(parsed(family, {"numero_normalizado": "258237820155240091", "artigo": "290"}))
                self.assertEqual((result.status, result.reason, result.record_type), ("insufficient", reason, record_type))

    def test_result_is_immutable_and_validates_invariants(self) -> None:
        result = ResolutionResult("resolved", 7, (7,), "test", "test", "acordao")
        self.assertIsInstance(result.candidate_ids, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.status = "no_match"  # type: ignore[misc]
        with self.assertRaises(ValueError):
            ResolutionResult("resolved", 7, (8,), "test", "test", "acordao")
        with self.assertRaises(ValueError):
            ResolutionResult("ambiguous", None, (7,), "test", "test", "acordao")
        with self.assertRaises(TypeError):
            ResolutionResult("no_match", None, [], "test", "test", "acordao")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ResolutionResult("unknown", None, (), None, "invalid", None)  # type: ignore[arg-type]


class CitationResolverOracleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        gold = pd.read_csv(DATASET_DIR / "goldenset.csv")
        texts = {path.stem: path.read_text(encoding="utf-8") for path in (DATASET_DIR / "txt").glob("*.txt")}
        with connect_database(get_database_path(), read_only=True) as connection:
            cls.resolver = CitationResolver(case_index=build_case_index(connection), connection=connection)
        parser = CitationParser()
        cls.rows = []
        for row in gold.itertuples():
            span = texts[row.documento_id][int(row.inicio):int(row.fim)]
            family = oracle_family(span, row.tipo)
            parsed_citation = parser.parse(CitationCandidate(0, len(span), span, "oracle", family), context=span)
            cls.rows.append({"gold": row.classificacao, "gold_id": None if pd.isna(row.id_canonico) else int(row.id_canonico), "parsed": parsed_citation})

    def test_oracle_safe_strategy_matrix_and_critical_errors(self) -> None:
        resolved_rows = [{**row, "result": self.resolver.resolve(row["parsed"])} for row in self.rows]
        counts: dict[tuple[str, str], int] = {}
        for row in resolved_rows:
            key = (row["gold"], row["result"].status)
            counts[key] = counts.get(key, 0) + 1
        # Resolver V11: CNJ/classe primária são preservados e a frente legal
        # promove somente identidades artigo+diploma verificáveis.
        self.assertEqual(counts, {
            ("real", "resolved"): 76, ("real", "no_match"): 3, ("real", "ambiguous"): 2, ("real", "insufficient"): 15,
            ("inventada", "no_match"): 54, ("inventada", "insufficient"): 10,
            ("incompleta", "insufficient"): 35,
        })
        self.assertEqual(sum(row["result"].status == "resolved" and row["result"].id_canonico != row["gold_id"] for row in resolved_rows if row["gold"] == "real"), 0)
        self.assertEqual(sum(row["result"].status == "resolved" for row in resolved_rows if row["gold"] != "real"), 0)
        classified = sum(
            (row["gold"] == "real" and row["result"].status == "resolved")
            or (row["gold"] == "inventada" and row["result"].status == "no_match")
            or (row["gold"] == "incompleta" and row["result"].status in {"ambiguous", "insufficient"})
            for row in resolved_rows
        )
        self.assertEqual(classified, 165)

    def test_oracle_resolution_is_deterministic(self) -> None:
        snapshots = []
        for _ in range(3):
            snapshots.append(tuple(self.resolver.resolve(row["parsed"]) for row in self.rows))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])


if __name__ == "__main__":
    unittest.main()
