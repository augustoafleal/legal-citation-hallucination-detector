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
        self.assertIn("Distribuição das posições dos resultados FTS5", source)


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
        for chart_title in (
            "Cobertura das extrações experimentais",
            "Cobertura acumulada por posição no texto",
            "Categorias do parsing exploratório",
            "Colisões nas chaves candidatas",
            "Tamanho dos grupos de identidade experimental",
        ):
            self.assertIn(chart_title, source)


class CanonicalIdentityAnalysisNotebookTests(unittest.TestCase):
    def test_notebook_covers_main_identity_without_production_components(self) -> None:
        notebook = json.loads(
            Path("notebooks/03_canonical_identity_analysis.ipynb").read_text()
        )
        source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])

        for heading in (
            "# 1. Setup",
            "# 2. O conceito de identidade principal",
            "# 3. Anatomia da identidade por tribunal",
            "# 4. Número principal",
            "# 5. Classe associada ao número principal",
            "# 6. Auditoria de qualidade da classe",
            "# 7. Taxonomia observada de classes",
            "# 8. Classe-base e modificadores",
            "# 9. Os 14 casos sem número",
            "# 10. FTS como fallback real",
            "# 11. Recalcular as chaves candidatas",
            "# 12. Inspeção manual das colisões",
            "# 13. Definição operacional de feito",
            "# 14. Hipótese de estrutura canônica",
            "# 15. Critérios de confiança do parsing",
            "# 16. Findings",
        ):
            self.assertIn(heading, source)

        for chart_title in (
            "Cobertura de número principal por tribunal",
            "Qualidade do número principal por tribunal",
            "Classes confiáveis e suspeitas por tribunal",
            "Taxonomia observada de classes confiáveis",
            "Classes simples e compostas",
            "Cobertura estrutural e candidatos FTS revalidados",
            "Colisões das chaves candidatas",
            "Classificação exploratória das colisões",
        ):
            self.assertIn(chart_title, source)

        self.assertIn("connect_database", source)
        self.assertIn("pd.isna(value)", source)
        for prohibited in (
            "sys.path.append",
            "CanonicalIndex",
            "resolver",
            "goldenset",
            "openai",
        ):
            self.assertNotIn(prohibited, source)
