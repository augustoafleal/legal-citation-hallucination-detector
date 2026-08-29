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

Os números acima registram a investigação sobre a versão anterior do dataset.
Em 28/08/2026, a baseline oficial foi atualizada: `doc_0227` e `doc_0461`
foram removidos por serem duplicatas exatas.

## 06_citation_surface_analysis.ipynb

O sexto notebook é uma etapa exploratória sobre a superfície das citações da
revisão corrente do desafio. Ele lê somente os 26 arquivos `txt` e as 225 linhas
do `goldenset.xlsx`, valida os 225 spans contra os offsets do texto e separa
explicitamente detecção, parsing e resolução; somente as duas primeiras são
estudadas. A taxonomia textual cobre processos CNJ, processos ou recursos
numerados, súmulas, referências jurisprudenciais contextuais ou gerais e três
formas de referência legal. Os rótulos do goldenset são usados apenas depois
para cruzamentos analíticos, nunca para definir regras ou famílias.

A baseline determinística de regras interpretáveis obteve 105 TP, 89 FP e 120
FN (precisão 0,541, recall 0,467 e F1 0,501 com IoU >= 0,5), tornando visíveis
as lacunas de cobertura e de fronteira antes de qualquer evolução. O parsing é
medido exclusivamente com spans-oráculo e registra apenas tribunal, classe,
número e UF explicitamente presentes. O notebook não resolve citações, não usa
`CaseIndex`, não cria código de produção e não usa o diretório histórico
`_old`; os resultados servem para orientar um próximo experimento de detecção
geral, não uma arquitetura de resolução.

A baseline de detecção desse notebook foi portada sem alterações de métricas
para `bracis_jusbrasil.citations.CitationDetector` V1. O componente preserva os
spans, regras, famílias, deduplicação e overlaps observados, enquanto parsing e
resolução continuam fora do código de produção.
