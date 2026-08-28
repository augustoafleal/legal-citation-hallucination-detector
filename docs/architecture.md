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
    H[citations futura] -.-> D
```

| Diretório | Responsabilidade atual |
| --- | --- |
| `src/` | Código reutilizável: conexão read-only, normalização e domínio `cases`. |
| `notebooks/` | Exploração visual e experimentos executáveis. |
| `scripts/` | Comandos utilitários e reproduzíveis. |
| `data/` | Documentação da localização dos arquivos distribuídos. |
| `docs/` | Decisões e documentação renderizada pelo MkDocs. |

## Domínio `cases`

O `CaseIndex` cobre somente os 1.000 acórdãos do SQLite. O parser estrutural
por tribunal passa pela normalização e forma 912 feitos: 831 single-ID e 81
multi-ID. Todos os IDs de um mesmo feito são preservados; nenhum é escolhido
arbitrariamente. Súmulas e dispositivos ainda não fazem parte do índice.

O índice é uma chave de recuperação, não um resolver de citações. O domínio
futuro `citations/` poderá consultar `CaseIndex`, mas parsing de `.txt`, spans,
classificações, FTS e resolução permanecem fora desta etapa.
