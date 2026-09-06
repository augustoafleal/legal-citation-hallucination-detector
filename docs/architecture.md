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
    H[Texto original] --> I[CitationDetector V7]
    I --> J[CitationCandidate[]]
    J --> K[CitationParser V1]
    K --> L[ParsedCitation[]]
    L --> M[CitationResolver V3]
    M --> N[ResolutionResult]
    M --> D
    N --> O[StructuralCNJArbitrator]
    O --> P[ArbitrationResult]
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
`citations/` contém o `CitationDetector` V7: um detector determinístico que
recebe texto original e retorna candidatos com offsets, texto, regra e família
superficial. A V5 preserva a V4 e promove quatro extensões estruturais
confirmadas pela revisão humana dos sete residuais:

- expansão de prefixos processuais formais imediatamente anteriores a CNJ;
- `RHC`, `RMS` e `AR` com marcador, quebra de linha local e UF opcional;
- modificador (`AgInt`, `AgRg`, `EDcl` ou `ED`) diretamente seguido de número
  não-CNJ;
- título composto de agravo interno e suspensão, com whitespace/newline local.

Além das extensões V5, a V6 reconhece CNJs não canônicos/degradados com
exatamente 20 dígitos quando há marcador processual positivo e local. A regra
`degraded_compact_cnj` preserva dígitos e offsets do texto original, aceita
somente separadores estruturais e no máximo uma quebra local dentro do número,
e não faz OCR. O marcador é composto por classes/modificadores processuais
controlados; cadeias administrativas intermediárias, como `REsp OAB/SP ...`,
não pertencem à gramática e são rejeitadas sem blacklist.

A V7 acrescenta somente a regra `compound_procedural_chain`: cadeias
procedurais formadas por `ED`, `E` e `RR`, com prefixo opcional `TST`,
separadas exclusivamente por hífen ASCII e seguidas imediatamente por número
CNJ/TST completo. A cadeia contém de dois a cinco tokens lexicais, permite no
máximo duas ocorrências do mesmo token processual e aceita de um a sete dígitos
no primeiro segmento numérico. Números parciais, tokens fora desse conjunto e
variantes com whitespace ao redor do hífen são rejeitados. O Parser valida a
mesma estrutura antes de extrair classe terminal, número literal e tribunal
explícito; a regra não consulta banco nem completa dígitos.

A V8 acrescenta `decision_tribunal_relator_year` para jurisprudência concreta
insuficiente: exige, na mesma cláusula local, anchor decisório/classe, tribunal
explícito, ano contextual e relatoria explícita. O span começa no anchor e
termina no nome literal do relator. A regra usa a família contextual já
suportada pelo Parser e, portanto, segue para `insufficient` no Resolver e
`incompleta` na classificação sem exceção específica. Quando seu span tem IoU
de pelo menos 0,5 somente com `tribunal_contextual`, a V8 preserva H2 e suprime
o contextual curto; abaixo desse limiar mantém ambos. Nunca interfere com CNJ,
processos numerados, súmulas ou lei. Não consulta Gold, banco, IDs ou nomes.

As extensões são exclusivamente textuais, determinísticas e guardadas; não
consultam IDs, documentos, gold, banco ou `CaseIndex`, e não fazem reparo fuzzy.
A V4 preserva a V3 e promoveu somente três extensões estruturais:
H1 expande localmente cadeias processuais adjacentes com guards; H2 reconhece
`Rec. Esp.` e `H.C.` somente quando associados a uma estrutura processual válida;
H3 estende localmente uma continuação numérica sob ruído OCR estrutural. H3 não
é fuzzy matching. As hipóteses H4/H5 originais foram reavaliadas com os sete
residuais e promovidas apenas nas formas guardadas acima. Historicamente, a V2
adicionou referências jurisprudenciais gerais (`jurisprudencia_geral`): V1
obteve 105 TP / 89 FP / 120 FN (F1 0,501); V2, 117 TP / 89 FP / 108 FN
(F1 0,543). Sob o Gold V2, referências vagas a jurisprudência ou orientação
sem fonte concreta ficaram fora do escopo de citação e essa emissão foi retirada
do Detector.
Na revisão atual, V3 obteve 119 TP / 87 FP / 106 FN (F1 0,552), V4 obteve
127 TP / 84 FP / 98 FN (F1 0,583), V5 obteve 137 TP / 82 FP / 88 FN
(F1 0,617; 83 matches exatos) e V6 obteve 140 TP / 82 FP / 85 FN
(86 matches exatos). O detector não consulta o banco ou
`CaseIndex` e não classifica uma citação.

O pipeline de produção é `Texto -> CitationDetector V7 -> CitationParser V1 ->
CitationResolver V3 -> StructuralCNJArbitrator`. Após a promoção de H1/H2/H3, novas expansões marginais do
Detector não devem ser adicionadas sem nova evidência independente de
generalização e segurança.

`CitationParser` V1 recebe uma `CitationCandidate` já delimitada e produz
`ParsedCitation`: família, tipo, tribunal explícito e um payload específico da
família com os campos textualmente presentes. Opcionalmente, a etapa de
enriquecimento recebe o texto original e associa ao CNJ apenas o prefixo
estrutural imediatamente adjacente (`tribunal-classe-CNJ`); não atravessa
frases ou linhas arbitrariamente. O parser não consulta banco, FTS ou
`CaseIndex`, não classifica a citação e não decide se há evidência suficiente.

`CitationResolver` V3 é o único componente que confronta a interpretação com o
corpus. Ele preserva `case_number_exact` para processo/recurso e, quando um
número numerado retorna múltiplas identidades, promove somente a classe
primária explicitamente citada que seleciona exatamente um ID. Sem classe,
com conflito ou com mais de um match, a ambiguidade é preservada. Também promove
`sumula_number_only`: para a família `sumula_numerada`, um número presente é
consultado globalmente no corpus de súmulas, sem exigir tribunal. Zero matches
retorna `no_match`, um match retorna `resolved` e múltiplos matches retornam
`ambiguous`; o dispatch explícito com tribunal preserva a estratégia V1
`sumula_number_tribunal`. Grupos canônicos com mais de um `doc_id` permanecem
ambíguos quando não há esse guard estrutural; não há escolha arbitrária, busca
aproximada ou fallback por texto.

O resultado é `ResolutionResult`, com os estados neutros `resolved`,
`no_match`, `ambiguous` e `insufficient`. `no_match` significa que uma consulta
segura foi possível, mas não encontrou candidato; `insufficient` significa que
faltou informação ou que a estratégia não é aprovada nesta V3. Para CNJ, a V3
consulta somente a identidade primária formal do acórdão. O tribunal explícito
(ou o segmento estrutural do número) e a classe textual, quando presentes,
funcionam como guards; conflito, ausência de identidade primária ou múltiplos
IDs resultam em abstinência/ambiguidade, sem escolha arbitrária. Dispositivos
legais e jurisprudência geral/contextual permanecem `insufficient`.

`StructuralCNJArbitrator` é separado do resolver e recebe candidatos, parses e
resultados já calculados. Ele só une um CNJ primário resolvido a exatamente um
companheiro sobreposto de processo numerado ou contexto tribunalício quando o
companheiro carrega um prefixo numérico de pelo menos dez dígitos, a classe e o
tribunal são compatíveis e a identidade resolvida é a mesma. O intervalo unido
é recortado literalmente do texto original; em qualquer dúvida, os candidatos
são preservados. A saída imutável `ArbitrationResult` registra as fontes e os
itens suprimidos, sem alterar `CitationCandidate`.

Por decisão de segurança, a implementação não usa fallback por menção no corpo,
nem as políticas `longest-span`, `resolved-wins` ou seleção automática de um ID
em grupos multi-ID.
