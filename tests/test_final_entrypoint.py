from __future__ import annotations

import csv
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "material_desafio_jusbrasil_bracis"


class FinalEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="final-entrypoint-")
        self.root = Path(self.temporary.name)
        self.db = self.root / "inputs" / "input.db"
        self.txt = self.root / "inputs" / "renamed-txt"
        self.db.parent.mkdir()
        shutil.copy2(DATA / "desafio1_bracis.db", self.db)
        shutil.copytree(DATA / "txt", self.txt)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_run_sh_requires_exactly_three_arguments(self) -> None:
        completed = subprocess.run(["bash", str(ROOT / "run.sh")], text=True, capture_output=True, cwd=self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Uso:", completed.stderr)

    def test_run_sh_and_python_cli_use_temporary_inputs_deterministically(self) -> None:
        output = self.root / "nested" / "submission.csv"
        again = self.root / "again.csv"
        command = ["bash", str(ROOT / "run.sh"), str(self.db), str(self.txt), str(output)]
        subprocess.run(command, check=True, text=True, capture_output=True, cwd=self.root)
        subprocess.run(command[:-1] + [str(again)], check=True, text=True, capture_output=True, cwd=self.root)
        self.assertTrue(output.is_file())
        with output.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 26)
        self.assertEqual(set(rows[0]), {"documento_id", "citacoes"})
        self.assertEqual(sha256(output.read_bytes()).hexdigest(), sha256(again.read_bytes()).hexdigest())
        direct = self.root / "direct.csv"
        completed = subprocess.run(
            ["python", str(ROOT / "scripts" / "generate_submission.py"), str(self.db), str(self.txt), str(direct)],
            text=True, capture_output=True, cwd=self.root,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(output.read_bytes(), direct.read_bytes())

    def test_python_cli_rejects_legacy_single_argument(self) -> None:
        completed = subprocess.run(
            ["python", str(ROOT / "scripts" / "generate_submission.py"), "only-output.csv"],
            text=True, capture_output=True, cwd=self.root,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("usage:", completed.stderr)


if __name__ == "__main__":
    unittest.main()
