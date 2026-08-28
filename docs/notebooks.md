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

## 03_canonical_identity_analysis.ipynb

O terceiro notebook refina, de modo estritamente exploratório, a extração de
número principal e classe associada por tribunal. Ele audita a qualidade das
classes e revisita falhas, sem introduzir componentes de identidade no pacote
de produção.

## 04_primary_case_number_and_collisions.ipynb

O quarto notebook prioriza a identidade formal de cada tribunal: inventaria os
blocos estruturais, extrai o número principal somente dentro desses blocos,
preserva a evidência e prepara uma amostra para auditoria manual externa ao
parser; o notebook apenas produz a amostra auditável, sem julgar sua qualidade.
As análises de `tribunal + número principal` são preliminares e condicionadas à
maturidade do parsing; colisões, classe e UF não são conclusões globais deste
notebook. O parser TSE também tolera OCR e separadores degradados dentro do
candidato processual, suporta número legado e registra a origem da recuperação;
os 25 casos auditados manualmente são usados apenas como evidência diagnóstica,
sem hardcodes por documento. O SQLite permanece read-only e não há código de
produção, índice ou resolver.

## 05_canonical_collision_analysis.ipynb

O quinto notebook analisa, exclusivamente no SQLite, os grupos multi-ID
formados por `(tribunal, numero_normalizado)`. Classe, UF, relator, ano e texto
são evidências auxiliares somente dentro das colisões; o notebook preserva
todos os candidatos, não escolhe uma identidade final e não implementa
`CaseIndex`. A análise final cobre 1000/1000 acórdãos: 912 grupos, 831
single-ID e 81 multi-ID, envolvendo 169 candidatos. O delta dos 25 TSE criou
21 chaves single-ID e 2 colisões novas; as duas foram auditadas como
`same_case`, somando-se aos 79 grupos antigos já auditados. A chave é adequada
para recuperação, mas não garante identidade única; múltiplos IDs devem ser
preservados por feito. Essa baseline alimenta o `CaseIndex` V1 em
`src/bracis_jusbrasil.cases`: súmulas, dispositivos, citações `.txt` e FTS
continuam fora do índice.
