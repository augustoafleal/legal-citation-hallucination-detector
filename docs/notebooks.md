---
title: Notebooks
description: Convenções para notebooks exploratórios.
---

# Notebooks

Os notebooks usam a convenção `NN_nome_descritivo.ipynb`, com uma numeração de
duas posições que torna a ordem de exploração explícita.

Eles servem para exploração e experimentos; lógica reutilizável deve permanecer
em `src/`, não em células de notebook.

## 01_database_overview.ipynb

O primeiro notebook entende o conteúdo, a estrutura e as características do
SQLite canônico antes de qualquer implementação de resolução de citações. Ele
abre o banco apenas em modo read-only e mostra consultas resumidas, cabeçalhos,
súmulas, dispositivos, FTS5 e duplicatas de texto.

## 02_canonical_database_analysis.ipynb

O segundo notebook examina os 1.000 acórdãos como possíveis identidades
processuais estruturadas. Ele mede posições, padrões por tribunal, cobertura de
parsing experimental, falhas, duplicatas, múltiplos IDs e chaves candidatas de
feito. A análise continua read-only e não implementa índice canônico ou
resolver.
