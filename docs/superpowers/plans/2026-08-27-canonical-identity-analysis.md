# Canonical identity analysis implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar o Notebook 03 para medir experimentalmente número e classe principais por tribunal e evidenciar a composição de uma futura identidade de feito.

**Architecture:** Um único notebook auto-contido consulta os acórdãos por `connect_database()` em modo read-only. Funções locais localizam um bloco de identidade específico por tribunal, extraem candidatos, auditam qualidade e calculam chaves apenas para linhas elegíveis; nenhum artefato de produção é criado em `src/`.

**Tech Stack:** Python 3.12, sqlite3, pandas, matplotlib, Jupyter, unittest.

**Spec:** `docs/superpowers/specs/2026-08-27-canonical-identity-analysis-design.md`

## Global Constraints

- Usar `bracis_jusbrasil.database.connect_database()` e consultar somente `natureza = 'acordao'`.
- SQLite, goldenset e `.txt` permanecem inalterados; não usar fontes externas, LLM ou dependências novas.
- Não criar `CanonicalIndex`, resolver, classificador, detector de citações, API de produção ou regras por identificador.
- Preservar texto, número e classe brutos antes de normalizar e qualificar todo parsing como experimental.
- Fechar a conexão read-only na última célula; executar o notebook integralmente em uma cópia em `/tmp`.

---

### Task 1: Cobertura verificável do Notebook 03

**Files:**
- Modify: `tests/test_notebook_structure.py`
- Create: `notebooks/03_canonical_identity_analysis.ipynb`

**Interfaces:**
- Consumes: a estrutura JSON de notebooks usada pelos Notebooks 01 e 02.
- Produces: teste que exige as 16 seções e títulos de visualizações, sem aprovar componentes de produção.

- [ ] **Step 1: Write the failing test**

Adicionar `CanonicalIdentityAnalysisNotebookTests` que leia o notebook e exija os títulos `# 1. Setup`, `# 4. Número principal`, `# 5. Classe associada ao número principal`, `# 10. FTS como fallback real`, `# 16. Findings` e os títulos dos sete gráficos definidos na especificação. Exigir `connect_database` e vedar `sys.path.append`, `CanonicalIndex`, `resolver`, `goldenset` e `openai`.

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m unittest tests/test_notebook_structure.py -v`

Expected: falha por ausência de `notebooks/03_canonical_identity_analysis.ipynb`.

- [ ] **Step 3: Create the notebook skeleton**

Criar células markdown para as 16 seções, uma célula setup com:

```python
from bracis_jusbrasil.database import connect_database, get_database_path

DATABASE_PATH = get_database_path()
connection = connect_database(DATABASE_PATH)
```

e uma célula final com `connection.close()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m unittest tests/test_notebook_structure.py -v`

Expected: PASS para o teste estrutural novo e os testes existentes.

- [ ] **Step 5: Commit**

```bash
git add tests/test_notebook_structure.py notebooks/03_canonical_identity_analysis.ipynb
git commit -m "test: cobrir estrutura do notebook de identidade"
```

### Task 2: Universo, anatomia e extração experimental principal

**Files:**
- Modify: `notebooks/03_canonical_identity_analysis.ipynb`

**Interfaces:**
- Consumes: `connection`, `acordaos`, `TRIBUNAIS` e funções locais do notebook.
- Produces: `identidades`, DataFrame com número, bloco, posição, padrão, UF e qualidade por acórdão.

- [ ] **Step 1: Add observational setup cells**

Carregar `documento_id`, `id`, `tribunal`, `ano`, `relator` e `texto`; afirmar 1.000 linhas e 200 por tribunal. Exibir amostras por tribunal e uma tabela `tribunal | bloco de identidade | número principal | classe | UF | particularidades` derivada de observação, sem regras por ID.

- [ ] **Step 2: Add local parser functions**

Definir expressões para número CNJ/decimal, UF e delimitadores de cabeçalho. Implementar `identity_block(text, tribunal) -> tuple[str, int, str]` para devolver bloco, deslocamento e padrão estrutural; implementar `extract_main_number(row) -> dict[str, object]` para escolher apenas candidatos no bloco, registrar múltiplos candidatos como ambíguos e preservar `numero_principal_raw` antes de `normalize_number()`.

- [ ] **Step 3: Measure and inspect number extraction**

Construir `identidades` e apresentar a tabela por tribunal `registros`, `número principal extraído`, `não extraído`, `ambíguo`, mais amostras determinísticas de cada estado. Criar barras agrupadas com título `Cobertura de número principal por tribunal` e barras empilhadas `Qualidade do número principal por tribunal`.

- [ ] **Step 4: Run notebook through the number section**

Run: `venv/bin/jupyter nbconvert --to notebook --execute notebooks/03_canonical_identity_analysis.ipynb --output /tmp/03-canonical-identity.ipynb`

Expected: execução sem exceção, 1.000 acórdãos e gráficos renderizados em `/tmp/03-canonical-identity.ipynb`.

- [ ] **Step 5: Commit**

```bash
git add notebooks/03_canonical_identity_analysis.ipynb
git commit -m "feat: extrair número principal experimental"
```

### Task 3: Classe confiável, auditoria e taxonomia

**Files:**
- Modify: `notebooks/03_canonical_identity_analysis.ipynb`

**Interfaces:**
- Consumes: `identidades` com bloco, posição e número principal.
- Produces: `classe_confiavel`, `classe_suspeita`, `classe_base` e `modificadores` exploratórios.

- [ ] **Step 1: Add class extraction tied to the identity block**

Implementar `extract_main_class(row) -> dict[str, object]` que procure formato de classe no mesmo bloco do número e registre `classe_raw`, `classe_normalizada`, `classe_posicao` e distância. Implementar `audit_class(row) -> tuple[bool, str]` que marque como suspeitos valores longos, com `ART`, data, conectores narrativos, múltiplas sentenças, distância excessiva ou ausência de forma recorrente.

- [ ] **Step 2: Report class quality**

Mostrar por tribunal `número principal`, `classe confiável`, `classe ausente`, `classe ambígua`, além de exemplos determinísticos dos dois grupos. Criar gráfico `Classes confiáveis e suspeitas por tribunal`.

- [ ] **Step 3: Derive taxonomic views without a production taxonomy**

Exibir formas brutas confiáveis, frequência, tribunal e exemplos; derivar experimentalmente classes simples/compostas e possíveis base/modificadores. Criar os gráficos `Taxonomia observada de classes confiáveis` e `Classes simples e compostas`.

- [ ] **Step 4: Re-run the notebook**

Run: `venv/bin/jupyter nbconvert --to notebook --execute notebooks/03_canonical_identity_analysis.ipynb --output /tmp/03-canonical-identity.ipynb`

Expected: classes contextuais não são contadas como confiáveis e as células de taxonomia executam.

- [ ] **Step 5: Commit**

```bash
git add notebooks/03_canonical_identity_analysis.ipynb
git commit -m "feat: auditar classe principal experimental"
```

### Task 4: Falhas, FTS e chaves candidatas

**Files:**
- Modify: `notebooks/03_canonical_identity_analysis.ipynb`

**Interfaces:**
- Consumes: `identidades` qualificadas e `documentos_fts`.
- Produces: tabelas de falhas, candidatos FTS revalidados e `resumo_chaves` para A/B/C/D.

- [ ] **Step 1: Revisit the prior failures**

Construir tabela para todos os registros sem número principal com `documento_id`, tribunal, causa estrutural, existência observável, estratégia reutilizável e indicação de FTS. Não introduzir exceção baseada no identificador.

- [ ] **Step 2: Demonstrate FTS as recovery only**

Implementar `fts_candidates(query, occurrence)` que retorne consulta, candidatos, posição, início e contexto. Para formatos observados nas falhas, separar candidatos revalidados pelo bloco de cabeçalho de meras citações no corpo. Criar gráfico `Cobertura estrutural e candidatos FTS revalidados`.

- [ ] **Step 3: Recalculate collision keys**

Para registros elegíveis, calcular A (`tribunal+numero`), B (classe completa), C (+UF) e D (+classe-base) somente quando classe-base estiver observada. Reportar elegíveis, grupos, multi-ID, maior grupo, colisões e sem chave; apresentar maiores grupos com hashes de texto e cabeçalhos. Criar gráfico `Colisões das chaves candidatas`.

- [ ] **Step 4: Inspect collision classification**

Classificar grupos selecionados como provável mesmo feito, distintos, decisões/recursos diferentes ou indeterminado, usando apenas texto, IDs, número, classe, UF e hash. Criar gráfico `Classificação exploratória das colisões` e separar explicitamente esta leitura manual das métricas automáticas.

- [ ] **Step 5: Run notebook and commit**

Run: `venv/bin/jupyter nbconvert --to notebook --execute notebooks/03_canonical_identity_analysis.ipynb --output /tmp/03-canonical-identity.ipynb`

```bash
git add notebooks/03_canonical_identity_analysis.ipynb
git commit -m "feat: analisar falhas fts e chaves candidatas"
```

### Task 5: Conclusões, documentação e validação final

**Files:**
- Modify: `notebooks/03_canonical_identity_analysis.ipynb`
- Modify: `docs/notebooks.md`

**Interfaces:**
- Consumes: todas as métricas experimentais do notebook.
- Produces: findings com fatos, hipótese conceitual, confiança e limites; documentação de execução do Notebook 03.

- [ ] **Step 1: Complete sections 13–16**

Escrever células markdown que respondam diretamente se número, classe completa/base, UF, modificadores e conjunto de IDs pertencem à identidade. Propor somente uma estrutura conceitual e os quatro níveis de confiança; declarar o papel de FTS como fallback.

- [ ] **Step 2: Update documentation**

Adicionar o Notebook 03 a `docs/notebooks.md`, descrevendo que ele é read-only, experimental e não implementa uma identidade em produção.

- [ ] **Step 3: Run full verification**

Run:

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python scripts/inspect_database.py
venv/bin/jupyter nbconvert --to notebook --execute notebooks/03_canonical_identity_analysis.ipynb --output /tmp/03-canonical-identity.ipynb
venv/bin/mkdocs build --strict
sha256sum material_desafio_jusbrasil_bracis/desafio1_bracis.db
```

Expected: testes e build passam, o notebook executa, `integrity_check` é `ok` e o hash do banco é `031c4401d741938248609bc15c837ef9164f8b1d056bd361ab81b4e5c9ea885c`.

- [ ] **Step 4: Commit**

```bash
git add notebooks/03_canonical_identity_analysis.ipynb docs/notebooks.md
git commit -m "docs: registrar notebook de identidade canônica"
```
