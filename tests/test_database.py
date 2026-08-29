import sqlite3
import unittest

from bracis_jusbrasil.database import connect_database, get_database_path


class DatabaseTests(unittest.TestCase):
    def test_database_path_exists(self) -> None:
        self.assertTrue(get_database_path().is_file())

    def test_read_only_connection_returns_rows_and_rejects_writes(self) -> None:
        with connect_database(get_database_path()) as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM documentos").fetchone()
            self.assertIsInstance(row, sqlite3.Row)
            self.assertEqual(row["total"], 1016)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE bootstrap_must_not_write (id INTEGER)")

    def test_missing_database_does_not_create_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            connect_database("does-not-exist.db")
