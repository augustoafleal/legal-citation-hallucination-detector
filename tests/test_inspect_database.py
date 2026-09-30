import subprocess
import sys
import unittest


class InspectDatabaseScriptTests(unittest.TestCase):
    def test_script_reports_database_summary(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/inspect_database.py"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in (
            "integrity_check: ok",
            "documentos: 1014",
            "acordao: 996",
            "sumula: 5",
            "dispositivo: 13",
            "STF: 200",
            "STJ: 199",
            "TSE: 199",
            "TST: 198",
            "FTS5: available",
        ):
            self.assertIn(expected, result.stdout)
