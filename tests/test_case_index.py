import json
import sqlite3
import unittest
from pathlib import Path

from bracis_jusbrasil.cases import CaseIndex, build_case_index
from bracis_jusbrasil.cases.parser import parse_case_identity
from bracis_jusbrasil.database import connect_database, get_database_path
from bracis_jusbrasil.normalization import normalize_case_number, normalize_tribunal


class CaseIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with connect_database(get_database_path(), read_only=True) as connection:
            cls.index = build_case_index(connection)

    def test_normalization(self) -> None:
        self.assertEqual(normalize_tribunal(" tse "), "TSE")
        self.assertEqual(normalize_case_number("1.597.443"), "1597443")
        self.assertEqual(
            normalize_case_number("102-81.2012.6.20.0019"),
            "1028120126200019",
        )
        self.assertEqual(normalize_case_number("3.334"), "3334")
        with self.assertRaises(ValueError):
            normalize_tribunal("TRF1")
        with self.assertRaises(ValueError):
            normalize_case_number("sem número")

    def test_corpus_invariants(self) -> None:
        self.assertEqual(
            self.index.stats,
            {
                "records_total": 998,
                "case_keys": 912,
                "single_id_cases": 833,
                "multi_id_cases": 79,
                "max_ids_per_case": 4,
            },
        )
        self.assertEqual(len(self.index), 912)

    def test_single_id_case_exists_for_each_tribunal(self) -> None:
        for tribunal in ("STF", "STJ", "TSE", "TST", "STM"):
            case = next(
                case
                for case in self.index.cases()
                if case.tribunal == tribunal and len(case.canonical_ids) == 1
            )
            record = case.records[0]
            self.assertEqual(self.index.lookup(tribunal, record.numero_raw), case)

    def test_tse_multi_id_cases_preserve_all_ids(self) -> None:
        self.assertEqual(
            self.index.lookup("TSE", "1028120126200019").canonical_ids,
            (23127111, 23531614),
        )
        self.assertEqual(
            self.index.lookup("TSE", "4774520126260139").canonical_ids,
            (23149811, 23542259),
        )

    def test_lookup_missing_case_returns_none(self) -> None:
        self.assertIsNone(self.index.lookup("TSE", "999-99.2099.9.99.9999"))

    def test_parser_has_one_real_case_per_tribunal(self) -> None:
        with connect_database(get_database_path(), read_only=True) as connection:
            for tribunal in ("STF", "STJ", "TSE", "TST", "STM"):
                row = connection.execute(
                    """
                    SELECT tribunal, texto
                    FROM documentos
                    WHERE natureza = 'acordao' AND tribunal = ?
                    ORDER BY documento_id
                    LIMIT 1
                    """,
                    (tribunal,),
                ).fetchone()
                parsed = parse_case_identity(row["texto"], row["tribunal"])
                self.assertEqual(parsed.tribunal, tribunal)
                self.assertTrue(parsed.numero_normalizado)

    def test_tse_special_parser_mechanisms(self) -> None:
        with connect_database(get_database_path(), read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT documento_id, tribunal, texto
                FROM documentos
                WHERE natureza = 'acordao' AND tribunal = 'TSE'
                ORDER BY documento_id
                """
            ).fetchall()

        parsed = [parse_case_identity(row["texto"], row["tribunal"]) for row in rows]
        self.assertEqual(len(parsed), 199)
        self.assertEqual(sum(item.numero_family == "legacy" for item in parsed), 1)
        self.assertEqual(sum(item.parse_source == "intradocument_fallback" for item in parsed), 2)
        self.assertGreaterEqual(sum(item.parse_source == "normalized_header" for item in parsed), 1)
        self.assertGreaterEqual(sum(item.marker_source == "ocr_tolerant" for item in parsed), 1)

        legacy = next(item for item in parsed if item.numero_family == "legacy")
        self.assertEqual(legacy.numero_normalizado, "3334")
        fallback = next(item for item in parsed if item.parse_source == "intradocument_fallback")
        self.assertTrue(fallback.numero_normalizado)

    def test_tse_manual_cases_are_present(self) -> None:
        notes = json.loads(Path("data/audits/tse_25_anotacoes.json").read_text())
        records = {
            record.documento_id
            for case in self.index.cases()
            for record in case.records
        }
        self.assertEqual(len(notes), 25)
        self.assertTrue({item["documento_id"] for item in notes} <= records)

    def test_updated_dataset_removed_duplicates(self) -> None:
        with connect_database(get_database_path(), read_only=True) as connection:
            for document_id, canonical_id in (
                ("doc_0227", 2939403187),
                ("doc_0461", 1890912862),
            ):
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM documentos WHERE documento_id = ?",
                        (document_id,),
                    ).fetchone()
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM documentos WHERE id = ?",
                        (canonical_id,),
                    ).fetchone()
                )

            for document_id, canonical_id in (
                ("doc_0230", 2849473714),
                ("doc_0462", 1931806554),
            ):
                row = connection.execute(
                    "SELECT documento_id, id, tribunal, texto FROM documentos WHERE documento_id = ?",
                    (document_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["id"], canonical_id)
                parsed = parse_case_identity(row["texto"], row["tribunal"])
                case = self.index.lookup(row["tribunal"], parsed.numero_raw)
                self.assertIsNotNone(case)
                self.assertIn(canonical_id, case.canonical_ids)

    def test_build_is_read_only(self) -> None:
        with connect_database(get_database_path(), read_only=True) as connection:
            index = CaseIndex.from_database(connection)
            self.assertEqual(index.stats["records_total"], 998)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE case_index_must_not_write (id INTEGER)")
