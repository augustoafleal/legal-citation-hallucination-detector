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
    H[Texto original] --> I[CitationDetector V4]
    I --> J[CitationCandidate[]]
    J --> K[CitationParser V1]
    K --> L[ParsedCitation[]]
    L --> M[CitationResolver V2]
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
`citations/` contém o `CitationDetector` V4: um detector determinístico que
recebe texto original e retorna candidatos com offsets, texto, regra e família
superficial. A V4 preserva a V3 e promove somente três extensões estruturais:
H1 expande localmente cadeias processuais adjacentes com guards; H2 reconhece
`Rec. Esp.` e `H.C.` somente quando associados a uma estrutura processual válida;
H3 estende localmente uma continuação numérica sob ruído OCR estrutural. H3 não
é fuzzy matching. H4, modificador diretamente antes de número com risco de
interação com CNJ, e H5, título processual composto/newline de baixa
generalização, foram avaliadas e não promovidas. A V2 preserva a V1 e adiciona a detecção conservadora de
referências jurisprudenciais gerais (`jurisprudencia_geral`): V1 obteve
105 TP / 89 FP / 120 FN (F1 0,501); V2, 117 TP / 89 FP / 108 FN (F1 0,543).
Na revisão atual, V3 obteve 119 TP / 87 FP / 106 FN (F1 0,552) e V4 obteve
127 TP / 84 FP / 98 FN (F1 0,583). O detector não consulta o banco ou
`CaseIndex` e não classifica uma citação.

O pipeline de produção é `Texto -> CitationDetector V4 -> CitationParser V1 ->
CitationResolver V2`. Após a promoção de H1/H2/H3, novas expansões marginais do
Detector não devem ser adicionadas sem nova evidência independente de
generalização e segurança.

`CitationParser` V1 recebe exclusivamente uma `CitationCandidate` já
delimitada e produz `ParsedCitation`: família, tipo, tribunal explícito e um
payload específico da família com os campos textualmente presentes. O parser
não consulta banco, FTS ou `CaseIndex`, não infere tribunal, não classifica a
citação e não decide se há evidência suficiente.

`CitationResolver` V2 é o único componente que confronta a interpretação com o
corpus. Ele preserva `case_number_exact` para processo/recurso e promove
`sumula_number_only`: para a família `sumula_numerada`, um número presente é
consultado globalmente no corpus de súmulas, sem exigir tribunal. Zero matches
retorna `no_match`, um match retorna `resolved` e múltiplos matches retornam
`ambiguous`; o dispatch explícito com tribunal preserva a estratégia V1
`sumula_number_tribunal`. Grupos canônicos com mais de um `doc_id` permanecem
ambíguos; não há escolha arbitrária, busca aproximada ou fallback por texto.

O resultado é `ResolutionResult`, com os estados neutros `resolved`,
`no_match`, `ambiguous` e `insufficient`. `no_match` significa que uma consulta
segura foi possível, mas não encontrou candidato; `insufficient` significa que
faltou informação ou que a estratégia não é aprovada nesta V2. CNJ,
dispositivos legais e jurisprudência geral/contextual permanecem
`insufficient`.
