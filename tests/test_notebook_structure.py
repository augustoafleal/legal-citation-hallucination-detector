import json
from pathlib import Path
import unittest


class DatabaseOverviewNotebookTests(unittest.TestCase):
    def test_notebook_has_required_sections_and_read_only_import(self) -> None:
        notebook = json.loads(Path("notebooks/01_database_overview.ipynb").read_text())
        source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])

        for heading in (
            "# 1. Setup",
            "# 2. SQLite schema",
            "# 3. Visão geral",
            "# 4. Exemplos de registros",
            "# 5. Cabeçalhos por tribunal",
            "# 6. Súmulas",
            "# 7. Dispositivos legais",
            "# 8. FTS5",
            "# 9. Duplicatas",
            "# 10. Findings",
        ):
            self.assertIn(heading, source)

        self.assertIn("connect_database", source)
        self.assertNotIn("sys.path.append", source)
