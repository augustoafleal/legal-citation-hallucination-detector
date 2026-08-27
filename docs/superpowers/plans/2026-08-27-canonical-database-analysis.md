# Canonical database analysis — Implementation Plan

**Goal:** Expandir a exploração read-only do acervo canônico e medir identidades processuais experimentais, sem criar índice, resolver ou classificador.

**Architecture:** O Notebook 01 receberá uma demonstração FTS de múltiplos resultados. O Notebook 02 conterá funções locais, pequenas e explicitamente experimentais para extrair identidade por tribunal, medir cobertura, falhas, duplicatas e chaves candidatas; `src/` não receberá parser de produção.

**Constraints:** SQLite via `connect_database()` em `mode=ro`; nenhum acesso a goldenset ou textos de entrada; sem dependências novas, LLM, CanonicalIndex, resolver, classificação ou detector.

### Task 1: Cobertura verificável de notebooks

- [ ] Criar teste estrutural para o Notebook 02 e ampliar o teste do Notebook 01 para exigir a consulta FTS de múltiplos resultados.
- [ ] Executar o teste e observar a falha por ausência das seções/consulta.

### Task 2: Ajustar o experimento FTS do Notebook 01

- [ ] Exibir para `1.276.977` os metadados, posição, contexto e início do acórdão, ao lado do exemplo simples já existente.
- [ ] Executar o Notebook 01 em `/tmp` e confirmar que a consulta apresenta múltiplos documentos sem resolver nenhum deles.

### Task 3: Construir o Notebook 02

- [ ] Consultar os 1.000 acórdãos com a API de banco existente e apresentar universo, campos, nulos, anos e tamanhos.
- [ ] Implementar parsers locais experimentais por tribunal, preservando valores brutos e normalizando somente formatação.
- [ ] Medir posição, cobertura, falhas, classes, famílias numéricas, UF, duplicatas, grupos multi-ID e chaves A/B/C.
- [ ] Documentar fatos, hipóteses e questões abertas sem criar componentes de produção.

### Task 4: Documentar e validar

- [ ] Atualizar `docs/notebooks.md` com o Notebook 02.
- [ ] Executar ambos os notebooks em `/tmp`, testes, script de inspeção, build MkDocs e hash/integrity check antes/depois.
