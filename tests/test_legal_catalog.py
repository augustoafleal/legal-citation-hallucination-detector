from __future__ import annotations

import sqlite3
from pathlib import Path
import unittest

from bracis_jusbrasil.citations import CitationCandidate, CitationDetector, CitationParser, CitationResolver
from bracis_jusbrasil.citations.legal_catalog import LegalCanonicalCatalog, normalize_diploma_alias
from bracis_jusbrasil.cases import build_case_index
from bracis_jusbrasil.database import connect_database, get_database_path


class LegalCanonicalCatalogTests(unittest.TestCase):
    def synthetic_connection(self, rows: list[tuple[int, str]]) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE documentos (id INTEGER, documento_id TEXT, natureza TEXT, tribunal TEXT, ano TEXT, relator TEXT, texto TEXT)"
        )
        connection.executemany(
            "INSERT INTO documentos VALUES (?, ?, 'dispositivo', NULL, NULL, NULL, ?)",
            [(identifier, f"d-{identifier}", text) for identifier, text in rows],
        )
        return connection

    def test_public_database_derives_the_13_observed_entries(self) -> None:
        with connect_database(get_database_path(), read_only=True) as connection:
            catalog = LegalCanonicalCatalog.from_database(connection)
        self.assertEqual(len(catalog.entries), 13)
        self.assertEqual(catalog.lookup("290", "CPM"), (10590194,))
        self.assertEqual(catalog.lookup("290", "CF"), ())
        self.assertEqual(catalog.lookup("290", ""), ())
        self.assertIn("CPC", catalog.covered_namespaces)

    def test_synthetic_database_uses_its_own_ids(self) -> None:
        connection = self.synthetic_connection([(901, "Artigo 373 da Lei nº 13.105, de 16 de março de 2015")])
        self.addCleanup(connection.close)
        catalog = LegalCanonicalCatalog.from_database(connection)
        self.assertEqual(catalog.lookup("373", "CPC"), (901,))

    def test_duplicate_key_is_preserved_as_ambiguous(self) -> None:
        connection = self.synthetic_connection([
            (901, "Artigo 373 da Lei nº 13.105, de 16 de março de 2015"),
            (902, "Artigo 373 da Lei nº 13.105, de 16 de março de 2015"),
        ])
        self.addCleanup(connection.close)
        catalog = LegalCanonicalCatalog.from_database(connection)
        self.assertEqual(catalog.lookup("373", "CPC"), (901, 902))
        resolver = CitationResolver(case_index=build_case_index(connection), connection=connection, legal_catalog=catalog)
        candidate = next(item for item in CitationDetector().detect("art. 373 do CPC") if item.family == "lei_dispositivo_com_diploma")
        result = resolver.resolve(CitationParser().parse(candidate))
        self.assertEqual((result.status, result.reason), ("ambiguous", "legal_catalog_key_ambiguous"))

    def test_uncertain_header_is_not_covered_or_resolved(self) -> None:
        connection = self.synthetic_connection([(901, "Artigo 10 de uma norma sem identidade")])
        self.addCleanup(connection.close)
        catalog = LegalCanonicalCatalog.from_database(connection)
        self.assertEqual(catalog.entries, ())
        self.assertFalse(catalog.is_covered_namespace("LEI:12345:2020"))

    def test_safe_and_guarded_alias_contract(self) -> None:
        self.assertEqual(normalize_diploma_alias("Lei n. 13.105/2015", strong_legal_context=True).normalized, "CPC")
        self.assertEqual(normalize_diploma_alias("Lei nº 4.737/1965", strong_legal_context=True).normalized, "CE")
        self.assertEqual(normalize_diploma_alias("CF/88", strong_legal_context=True).normalized, "CF")
        loose = normalize_diploma_alias("CF", strong_legal_context=False)
        self.assertIsNone(loose.normalized)
        self.assertTrue(loose.guarded)


if __name__ == "__main__":
    unittest.main()
