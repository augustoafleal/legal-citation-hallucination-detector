---
title: Arquitetura
description: Organização atual do bootstrap do projeto BRACIS Jusbrasil.
---

# Arquitetura

O bootstrap separa o código reutilizável, a exploração, os entrypoints e a
documentação. Os arquivos originais do desafio permanecem em
`material_desafio_jusbrasil_bracis/`; `data/` documenta essa decisão sem criar
uma cópia do SQLite.

```mermaid
flowchart LR
    A[SQLite read-only] --> B[cases.parser]
    B --> C[normalization]
    C --> D[CaseIndex]
    D --> E[(tribunal, numero_normalizado) -> Case]
    B --> F[notebooks/]
    D --> G[scripts/]
    H[Texto original] --> I[CitationDetector V3]
    I --> J[CitationCandidate[]]
    J --> K[CitationParser V1]
    K --> L[ParsedCitation[]]
    L --> M[CitationResolver V1]
    M --> N[ResolutionResult]
    M --> D
```

| Diretório | Responsabilidade atual |
| --- | --- |
| `src/` | Código reutilizável: conexão read-only, normalização e domínio `cases`. |
| `notebooks/` | Exploração visual e experimentos executáveis. |
| `scripts/` | Comandos utilitários e reproduzíveis. |
| `data/` | Documentação da localização dos arquivos distribuídos. |
| `docs/` | Decisões e documentação renderizada pelo MkDocs. |

## Domínio `cases`

O `CaseIndex` cobre somente os 998 acórdãos do SQLite. O parser estrutural
por tribunal passa pela normalização e forma 912 feitos: 833 single-ID e 79
multi-ID. Todos os IDs de um mesmo feito são preservados; nenhum é escolhido
arbitrariamente. Súmulas e dispositivos ainda não fazem parte do índice.

A revisão oficial de 28/08/2026 contém 1.016 registros no total, incluindo 13
dispositivos e 5 súmulas. Os acórdãos removidos nessa revisão eram duplicatas
exatas: `doc_0227` e `doc_0461`.

O índice é uma chave de recuperação, não um resolver de citações. O domínio
`citations/` contém o `CitationDetector` V3: um detector determinístico que
recebe texto original e retorna candidatos com offsets, texto, regra e família
superficial. A V3 preserva a V2 e adiciona somente a expansão estrutural local
de UF imediatamente adjacente a referências processuais. A V2 preserva a V1 e adiciona a detecção conservadora de
referências jurisprudenciais gerais (`jurisprudencia_geral`): V1 obteve
105 TP / 89 FP / 120 FN (F1 0,501); V2, 117 TP / 89 FP / 108 FN (F1 0,543).
Na revisão atual, V3 obteve 119 TP / 87 FP / 106 FN (F1 0,552). O detector não
consulta o banco ou `CaseIndex` e não classifica uma citação.

`CitationParser` V1 recebe exclusivamente uma `CitationCandidate` já
delimitada e produz `ParsedCitation`: família, tipo, tribunal explícito e um
payload específico da família com os campos textualmente presentes. O parser
não consulta banco, FTS ou `CaseIndex`, não infere tribunal, não classifica a
citação e não decide se há evidência suficiente.

`CitationResolver` V1 é o único componente que confronta a interpretação com o
corpus. Nesta primeira versão resolve somente número exato de
processo/recurso e súmula com número e tribunal explícito. Grupos canônicos
com mais de um `doc_id` permanecem ambíguos; não há escolha arbitrária, busca
aproximada ou fallback por texto.

O resultado é `ResolutionResult`, com os estados neutros `resolved`,
`no_match`, `ambiguous` e `insufficient`. `no_match` significa que uma consulta
segura foi possível, mas não encontrou candidato; `insufficient` significa que
faltou informação ou que a estratégia não é aprovada nesta V1. CNJ,
dispositivos legais e jurisprudência geral/contextual permanecem
`insufficient`.
