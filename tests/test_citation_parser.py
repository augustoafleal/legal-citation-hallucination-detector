from __future__ import annotations

from pathlib import Path
import re
import unittest

import pandas as pd

from bracis_jusbrasil.citations import CitationCandidate, CitationParser, ParsedCitation


DATASET_DIR = Path("material_desafio_jusbrasil_bracis")
CNJ_PATTERN = re.compile(r"\b\d{3,7}\s*-\s*\d{2}\s*[.]\s*\d{4}\s*[.]\s*\d\s*[.]\s*\d{2}\s*[.]\s*\d{4}\b")
SUMULA_PATTERN = re.compile(r"\bS[ÚU]MULA(?:\s+VINCULANTE)?\s*(?:N[ºO.]?\s*)?\d+", re.IGNORECASE)
ARTICLE_PATTERN = re.compile(r"\b(?:art(?:igo)?s?[.]?\s*)\d+", re.IGNORECASE)
DIPLOMA_PATTERN = re.compile(r"\b(?:Constituiç[aã]o(?: Federal)?|C[oó]digo|Lei(?: Complementar)?\s*(?:n[ºo.]?\s*)?\d+|CLT|CPC|CPP|CC|CDC|CPM)\b", re.IGNORECASE)
PROCESS_CLASS_PATTERN = re.compile(r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|Agravo|Recurso Especial|Recurso Extraordinário|Habeas Corpus|Reclamação)\b", re.IGNORECASE)
TRIBUNAL_PATTERN = re.compile(r"\b(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justiça|Tribunal Superior Eleitoral|Tribunal Superior do Trabalho|Superior Tribunal Militar)\b", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
RELATOR_PATTERN = re.compile(r"\b(?:Rel\.?|Relator(?:a)?)\s*(?:Min\.?|Ministra|Ministro|Des\.?)?\s*[A-Z]", re.IGNORECASE)
UNSUPPORTED_COMPOUND = re.compile(r"\bED(?:-[A-Z]+){2,}-RR-\d", re.IGNORECASE)


def candidate(text: str, family: str) -> CitationCandidate:
    return CitationCandidate(start=0, end=len(text), text=text, rule="test", family=family)


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


class CitationParserUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = CitationParser()

    def test_public_result_is_frozen_and_has_expected_envelope(self) -> None:
        parsed = self.parser.parse(candidate("REsp nº 1.597.443-PR", "processo_ou_recurso_numerado"))
        self.assertIsInstance(parsed, ParsedCitation)
        self.assertEqual(parsed.family, "processo_ou_recurso_numerado")
        self.assertEqual(parsed.tipo, "jurisprudencia")
        with self.assertRaises(AttributeError):
            parsed.family = "other"  # type: ignore[misc]

    def test_payload_mappings_reject_direct_mutation_and_keep_read_compatibility(self) -> None:
        parsed = self.parser.parse(candidate("REsp nº 1.597.443-PR", "processo_ou_recurso_numerado"))
        self.assertEqual(parsed.data["numero_normalizado"], "1597443")
        self.assertEqual(parsed.data.get("uf"), "PR")
        self.assertEqual(parsed.provenance["numero"], "normalized")
        self.assertEqual(parsed.provenance.get("numero"), "normalized")
        with self.assertRaises(TypeError):
            parsed.data["numero_normalizado"] = "alterado"  # type: ignore[index]
        with self.assertRaises(TypeError):
            parsed.provenance["numero"] = "alterado"  # type: ignore[index]

    def test_payload_mappings_are_defensive_copies(self) -> None:
        data = {"numero_normalizado": "1597443"}
        provenance = {"numero": "structural_repair"}
        parsed = ParsedCitation(
            family="processo_ou_recurso_numerado",
            tipo="jurisprudencia",
            tribunal_raw=None,
            tribunal=None,
            tribunal_source="unknown",
            data=data,
            provenance=provenance,
        )
        data["numero_normalizado"] = "alterado"
        provenance["numero"] = "alterado"
        self.assertEqual(parsed.data["numero_normalizado"], "1597443")
        self.assertEqual(parsed.provenance["numero"], "structural_repair")

    def test_cnj_preserves_raw_number_and_does_not_infer_court(self) -> None:
        parsed = self.parser.parse(candidate("Autos 1234567-89.2020.1.23.4567", "processo_cnj"))
        self.assertEqual(parsed.data["numero_raw"], "1234567-89.2020.1.23.4567")
        self.assertEqual(parsed.data["numero_normalizado"], "12345678920201234567")
        self.assertEqual(parsed.data["numero_family"], "cnj")
        self.assertIsNone(parsed.tribunal)
        self.assertEqual(parsed.tribunal_source, "unknown")

    def test_cnj_keeps_explicit_court(self) -> None:
        parsed = self.parser.parse(candidate("STJ, Autos 1234567-89.2020.1.23.4567", "processo_cnj"))
        self.assertEqual((parsed.tribunal_raw, parsed.tribunal, parsed.tribunal_source), ("STJ", "STJ", "explicit"))

    def test_cnj_context_enrichment_extracts_adjacent_class(self) -> None:
        text = "TST-AgARR-12345-67.2015.5.24.0001"
        start = text.index("12345")
        parsed = self.parser.parse(
            CitationCandidate(start, len(text), text[start:], "cnj", "processo_cnj"),
            context=text,
        )
        self.assertEqual(parsed.tribunal, "TST")
        self.assertEqual(parsed.data["classe_raw"], "AgARR")
        self.assertEqual(parsed.data["numero_normalizado"], "123456720155240001")
        self.assertEqual(parsed.provenance["classe"], "contextual_structural")

    def test_cnj_context_does_not_cross_sentence_or_newline(self) -> None:
        text = "AgARR foi citado antes.\nTST-12345-67.2015.5.24.0001"
        start = text.index("12345")
        parsed = self.parser.parse(
            CitationCandidate(start, len(text), text[start:], "cnj", "processo_cnj"),
            context=text,
        )
        self.assertNotIn("classe_raw", parsed.data)
        self.assertIsNone(parsed.tribunal)

    def test_numbered_case_handles_validated_marker_and_alias_forms(self) -> None:
        parsed = self.parser.parse(candidate("AgRg no Rec. Esp. n. 1.522.200 (SC)", "processo_ou_recurso_numerado"))
        self.assertEqual(parsed.data["classe_raw"], "AgRg")
        self.assertEqual(parsed.data["numero_normalizado"], "1522200")
        self.assertEqual(parsed.data["uf"], "SC")
        self.assertEqual(parsed.provenance["numero"], "structural_repair")

    def test_numbered_case_preserves_newline_and_degraded_punctuation(self) -> None:
        text = "Reclamação n° 33.-\n474 (MA)"
        parsed = self.parser.parse(candidate(text, "processo_ou_recurso_numerado"))
        self.assertEqual(parsed.data["numero_raw"], "33.-\n474")
        self.assertEqual(parsed.data["numero_normalizado"], "33474")
        self.assertEqual(parsed.data["uf"], "MA")
        self.assertEqual(parsed.provenance["numero"], "structural_repair")

    def test_uf_is_local_to_identifier(self) -> None:
        for suffix in ("-PR", "/PR", " PR", "- PR"):
            with self.subTest(suffix=suffix):
                parsed = self.parser.parse(candidate(f"REsp nº 1.597.443{suffix}", "processo_ou_recurso_numerado"))
                self.assertEqual(parsed.data["uf"], "PR")
        unrelated = self.parser.parse(candidate("REsp nº 1.597.443, citado em SP", "processo_ou_recurso_numerado"))
        self.assertNotIn("uf", unrelated.data)

    def test_real_absence_of_number_is_valid(self) -> None:
        parsed = self.parser.parse(candidate("Rcl de 2024, Rel. Min. Flávio Dino", "processo_ou_recurso_numerado"))
        self.assertEqual(parsed.data, {"classe_raw": "Rcl"})
        self.assertNotIn("numero", parsed.provenance)

    def test_unsupported_compound_class_remains_unparsed(self) -> None:
        parsed = self.parser.parse(candidate("ED-E-ED-RR-65-63.2010.5.01.0075", "processo_ou_recurso_numerado"))
        self.assertEqual(parsed.data, {"classe_raw": "RR"})

    def test_contextual_ocr_repair_preserves_raw_text(self) -> None:
        text = "AgInt no RESP 21737l8 - SP"
        parsed = self.parser.parse(candidate(text, "processo_ou_recurso_numerado"))
        self.assertEqual(parsed.data["numero_raw"], "21737l8")
        self.assertEqual(parsed.data["numero_normalizado"], "2173718")
        self.assertEqual(parsed.provenance["numero"], "ocr_repair")
        clean = self.parser.parse(candidate("REsp nº 1.597.443-PR", "processo_ou_recurso_numerado"))
        self.assertNotEqual(clean.provenance["numero"], "ocr_repair")
        outside_number = self.parser.parse(candidate("Rcl nº 123; referência 9O8", "processo_ou_recurso_numerado"))
        self.assertEqual(outside_number.data["numero_normalizado"], "123")
        self.assertNotEqual(outside_number.provenance["numero"], "ocr_repair")

    def test_sumula_is_not_process_number(self) -> None:
        parsed = self.parser.parse(candidate("Súmula nº 7 do STJ", "sumula_numerada"))
        self.assertEqual(parsed.data, {"sumula_numero_raw": "7", "sumula_numero": "7"})
        self.assertEqual(parsed.tribunal, "STJ")

    def test_legal_payload_keeps_legal_numbers_separate(self) -> None:
        parsed = self.parser.parse(candidate("art. 5º, § 1º, da Lei nº 8.078/1990", "lei_dispositivo_com_diploma"))
        self.assertEqual(parsed.data["artigo"], "5")
        self.assertEqual(parsed.data["paragrafo"], "1")
        self.assertEqual(parsed.data["law_number"], "8078")
        self.assertEqual(parsed.data["law_year"], "1990")
        self.assertNotIn("numero_normalizado", parsed.data)

    def test_legal_families_and_contextual_fields(self) -> None:
        without_diploma = self.parser.parse(candidate("art. 5º", "lei_dispositivo_sem_diploma"))
        general_law = self.parser.parse(candidate("Constituição Federal", "lei_referencia_geral"))
        contextual = self.parser.parse(candidate("STJ, 2024, Rel. Min. Maria Silva", "jurisprudencia_tribunal_contextual"))
        self.assertEqual(without_diploma.data, {"artigo": "5"})
        self.assertEqual(general_law.data["diploma_normalizado"], "CONSTITUIÇÃO FEDERAL")
        self.assertEqual(contextual.data["ano"], "2024")
        self.assertIn("relator_raw", contextual.data)

    def test_general_reference_and_unknown_family(self) -> None:
        parsed = self.parser.parse(candidate("jurisprudência pacífica desta Corte", "jurisprudencia_referencia_geral"))
        self.assertEqual(parsed.data, {})
        with self.assertRaisesRegex(ValueError, "não suportada"):
            self.parser.parse(candidate("texto", "sumula_sem_numero"))


class CitationParserOracleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        gold = pd.read_csv(DATASET_DIR / "goldenset.csv")
        texts = {path.stem: path.read_text(encoding="utf-8") for path in (DATASET_DIR / "txt").glob("*.txt")}
        parser = CitationParser()
        cls.rows = []
        for row in gold.itertuples():
            span = texts[row.documento_id][int(row.inicio):int(row.fim)]
            family = oracle_family(span, row.tipo)
            parsed = parser.parse(candidate(span, family))
            cls.rows.append({"documento_id": row.documento_id, "tipo_gold": row.tipo, "family": family, "span": span, "parsed": parsed})

    def test_oracle_family_inventory_is_reproduced(self) -> None:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row["family"]] = counts.get(row["family"], 0) + 1
        self.assertEqual(counts, {
            "processo_ou_recurso_numerado": 85,
            "jurisprudencia_referencia_geral": 46,
            "lei_dispositivo_com_diploma": 27,
            "processo_cnj": 25,
            "jurisprudencia_tribunal_contextual": 20,
            "lei_referencia_geral": 11,
            "sumula_numerada": 10,
            "lei_dispositivo_sem_diploma": 1,
        })

    def test_oracle_process_coverage(self) -> None:
        rows = [row for row in self.rows if row["family"] == "processo_ou_recurso_numerado"]
        self.assertEqual(sum("classe_raw" in row["parsed"].data for row in rows), 85)
        self.assertEqual(sum("numero_normalizado" in row["parsed"].data for row in rows), 73)
        missing = [row for row in rows if "numero_normalizado" not in row["parsed"].data]
        self.assertEqual(len(missing), 12)
        self.assertEqual(sum(bool(UNSUPPORTED_COMPOUND.search(row["span"])) for row in missing), 1)
        self.assertEqual(sum(not UNSUPPORTED_COMPOUND.search(row["span"]) for row in missing), 11)
        self.assertEqual(sum("uf" in row["parsed"].data for row in rows), 66)

    def test_oracle_other_family_coverage(self) -> None:
        by_family: dict[str, list[dict[str, object]]] = {}
        for row in self.rows:
            by_family.setdefault(str(row["family"]), []).append(row)
        cnj = by_family["processo_cnj"]
        sumulas = by_family["sumula_numerada"]
        contextual = by_family["jurisprudencia_tribunal_contextual"]
        legal = by_family["lei_dispositivo_com_diploma"]
        self.assertEqual(sum("numero_normalizado" in row["parsed"].data for row in cnj), 25)
        self.assertEqual(sum(row["parsed"].tribunal_source == "explicit" for row in cnj), 7)
        self.assertEqual(sum("sumula_numero" in row["parsed"].data for row in sumulas), 10)
        self.assertEqual(sum(row["parsed"].tribunal_source == "explicit" for row in sumulas), 7)
        self.assertEqual(sum(row["parsed"].tribunal_source == "explicit" for row in contextual), 20)
        self.assertEqual(sum("relator_raw" in row["parsed"].data for row in contextual), 20)
        self.assertEqual(sum("ano" in row["parsed"].data for row in contextual), 20)
        self.assertEqual(sum("artigo" in row["parsed"].data for row in legal), 27)
        self.assertEqual(sum("diploma_normalizado" in row["parsed"].data for row in legal), 27)
        self.assertEqual(sum("law_number" in row["parsed"].data for row in legal), 6)
