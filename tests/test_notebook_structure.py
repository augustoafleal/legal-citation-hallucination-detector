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
        self.assertIn('2.110', source)


class CanonicalDatabaseAnalysisNotebookTests(unittest.TestCase):
    def test_notebook_has_experimental_analysis_sections(self) -> None:
        notebook = json.loads(
            Path("notebooks/02_canonical_database_analysis.ipynb").read_text()
        )
        source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])

        for heading in (
            "# 1. Setup",
            "# 2. Universo analisado",
            "# 3. Anatomia por tribunal",
            "# 4. Posição da identidade",
            "# 5. Parsing exploratório",
            "# 6. Métricas de cobertura",
            "# 7. Inspeção das falhas",
            "# 8. Classes processuais",
            "# 9. Números processuais",
            "# 10. UF e estado",
            "# 11. Duplicatas e múltiplos registros",
            "# 12. Chaves candidatas de feito",
            "# 13. FTS como fallback",
            "# 14. Findings",
        ):
            self.assertIn(heading, source)

        self.assertIn("connect_database", source)
        self.assertNotIn("sys.path.append", source)
        self.assertNotIn("CanonicalIndex", source)
