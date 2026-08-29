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
    H[Texto original] --> I[CitationDetector V2]
    I --> J[CitationCandidate[]]
    J -.-> K[CitationParser futuro]
    K -.-> L[Resolver futuro]
    L -.-> D
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
`citations/` contém o `CitationDetector` V2: um detector determinístico que
recebe texto original e retorna candidatos com offsets, texto, regra e família
superficial. A V2 preserva a V1 e adiciona a detecção conservadora de
referências jurisprudenciais gerais (`jurisprudencia_geral`): V1 obteve
105 TP / 89 FP / 120 FN (F1 0,501); V2, 117 TP / 89 FP / 108 FN (F1 0,543).
Ele não consulta o banco ou `CaseIndex`, não classifica uma citação e não faz
parsing estruturado ou resolução. `CitationParser` e `Resolver` continuam
futuros; somente este último poderá decidir a relação com o `CaseIndex`.
