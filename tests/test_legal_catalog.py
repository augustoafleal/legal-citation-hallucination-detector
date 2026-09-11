from __future__ import annotations

from hashlib import sha256
import inspect
from pathlib import Path
import unittest

from bracis_jusbrasil.citations import (
    CANONICAL_DATABASE_SHA256,
    COVERED_DIPLOMA_NAMESPACES,
    DEFAULT_LEGAL_CATALOG,
    LEGAL_CATALOG_ENTRIES,
    SOURCE_PROVENANCE_HASHES,
)
from bracis_jusbrasil.citations.legal_catalog import normalize_diploma_alias


class LegalCanonicalCatalogTests(unittest.TestCase):
    def test_catalog_is_exactly_the_closed_13_entry_inventory(self) -> None:
        self.assertEqual(len(LEGAL_CATALOG_ENTRIES), 13)
        self.assertEqual(len(DEFAULT_LEGAL_CATALOG.entries), 13)
        keys = {(entry.normalized_article, entry.normalized_diploma) for entry in LEGAL_CATALOG_ENTRIES}
        ids = {entry.id_canonico for entry in LEGAL_CATALOG_ENTRIES}
        self.assertEqual(len(keys), 13)
        self.assertEqual(len(ids), 13)
        self.assertTrue(all(all(key) for key in keys))

    def test_catalog_is_bound_to_frozen_sqlite_hash(self) -> None:
        database = Path("material_desafio_jusbrasil_bracis/desafio1_bracis.db")
        self.assertEqual(sha256(database.read_bytes()).hexdigest(), CANONICAL_DATABASE_SHA256)

    def test_every_entry_has_auditable_source_metadata(self) -> None:
        self.assertTrue(all(entry.source_provenance_hash for entry in LEGAL_CATALOG_ENTRIES))
        self.assertTrue(all(len(value) == 64 for value in SOURCE_PROVENANCE_HASHES.values()))
        self.assertTrue({entry.normalized_diploma for entry in LEGAL_CATALOG_ENTRIES} <= set(SOURCE_PROVENANCE_HASHES))
        self.assertEqual(DEFAULT_LEGAL_CATALOG.covered_namespaces, COVERED_DIPLOMA_NAMESPACES)

    def test_lookup_requires_article_and_diploma(self) -> None:
        self.assertEqual(set(inspect.signature(DEFAULT_LEGAL_CATALOG.lookup).parameters), {"normalized_article", "normalized_diploma"})
        self.assertEqual(DEFAULT_LEGAL_CATALOG.lookup("290", "CPM"), (10590194,))
        self.assertEqual(DEFAULT_LEGAL_CATALOG.lookup("290", "CF"), ())
        self.assertEqual(DEFAULT_LEGAL_CATALOG.lookup("290", ""), ())

    def test_safe_and_guarded_alias_contract(self) -> None:
        for entry in LEGAL_CATALOG_ENTRIES:
            for alias in entry.accepted_aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(
                        normalize_diploma_alias(alias, strong_legal_context=True).normalized,
                        entry.normalized_diploma,
                    )
        self.assertEqual(normalize_diploma_alias("Lei n. 13.105/2015", strong_legal_context=True).normalized, "CPC")
        self.assertEqual(normalize_diploma_alias("Lei nº 4.737/1965", strong_legal_context=True).normalized, "CE")
        self.assertEqual(normalize_diploma_alias("CF/88", strong_legal_context=True).normalized, "CF")
        loose = normalize_diploma_alias("CF", strong_legal_context=False)
        self.assertIsNone(loose.normalized)
        self.assertTrue(loose.guarded)


if __name__ == "__main__":
    unittest.main()
