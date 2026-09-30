---
title: Legal Citation Hallucination Detector
description: Solução offline e materiais públicos do desafio Jusbrasil para a BRACIS 2026.
---

# Legal Citation Hallucination Detector

Este repositório contém a solução offline para o desafio Jusbrasil na BRACIS 2026. Ela recebe um banco SQLite e uma pasta de documentos TXT e produz o CSV de submissão por meio de `run.sh`.

O material público é uma fixture de desenvolvimento e validação. A avaliação oculta fornece seus próprios caminhos de banco e documentos; a execução final não depende do gold público.

Leia a [documentação consolidada da competição](competition.md), que reúne as regras do Kaggle, o contrato de dados, a métrica, a submissão, o cronograma, o FAQ e as regras fundamentais da plataforma. A fonte principal é a [página oficial no Kaggle](https://www.kaggle.com/competitions/desafio-jusbrasil-bracis-2026).

## Documentação técnica

- [Arquitetura](architecture.md): organização atual do bootstrap e seus limites.
- [Notebooks](notebooks.md): convenções para exploração reprodutível.
- [Scripts](scripts.md): sanity check read-only disponível.
- [Competição](competition.md): regras e especificação oficial consolidada.
- [Dados e materiais](#arquivos-disponiveis): localização dos arquivos distribuídos, sem cópia do SQLite.
- [Repositório público](https://github.com/augustoafleal/legal-citation-hallucination-detector): código-fonte e histórico de revisões.

!!! tip "Por onde começar"

    1. Leia a [documentação consolidada](competition.md) para entender o contrato de entrada e saída.
    2. Execute `bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>` para gerar o CSV final.
    3. Explore os textos em `material_desafio_jusbrasil_bracis/txt/` e o `goldenset_offsets.csv` somente como amostra pública de desenvolvimento.
    4. Use o SQLite recebido em cada execução como a fonte de verdade para decidir e resolver citações.

## Resumo da tarefa

Cada arquivo de entrada é uma peça judicial em texto simples. Para cada citação presente, a solução deve produzir um JSON com:

- o span Unicode, por `inicio` e `fim` exclusivo;
- o trecho literal;
- o tipo: `lei` ou `jurisprudencia`;
- a classificação: `real`, `inventada` ou `incompleta`;
- opcionalmente, a confiança e, quando a citação for real, a resolução canônica.

| Classe | Significado |
| --- | --- |
| `real` | A citação resolve para exatamente um feito na base. A resposta precisa informar um `id_canonico` aceito. |
| `inventada` | Há informação para buscar, mas nenhum registro correspondente existe na cobertura congelada. |
| `incompleta` | Faltam dados para formular a consulta ou para distinguir um único feito entre vários candidatos. |

O pareamento com o gabarito usa a sobreposição dos spans (IoU ≥ 0,5), portanto identificar a posição da citação faz parte da tarefa. A confiança, entre 0 e 1, é opcional e alimenta o bônus de calibração.

!!! warning "Dois cuidados que evitam erros comuns"

    - Use o valor de `id` do banco — o doc_id do Jusbrasil — em `resolucao.id_canonico`; não use `documento_id`, que é a chave interna do acervo.
    - Números no cabeçalho da própria peça (autos, protocolo, OAB, folhas ou valor da causa) são distratores e não devem ser extraídos como citações.

## Arquivos disponíveis

```text
material_desafio_jusbrasil_bracis/
├── desafio1_bracis.db                             # base canônica SQLite (~89 MiB)
├── goldenset_offsets.csv                          # gabarito público final da amostra
├── json_to_submission.py                          # conversor de JSONs para CSV de submissão
├── kaggle_metric.py                               # métrica oficial
├── sample_submission.csv                          # modelo de submissão
└── txt/                                           # 26 documentos jurídicos sintéticos de entrada
    ├── gen_n1_001.txt … gen_n1_013.txt
    └── gen_n2_001.txt … gen_n2_013.txt
```

Há 31 arquivos na cópia local atual: cinco arquivos de nível superior e 26 textos. O PDF instrucional da versão anterior está preservado em `material_desafio_jusbrasil_bracis_old2/`, fora do pacote atual.

## Enunciado

O enunciado consolidado nesta documentação estabelece o contrato e a estratégia de consulta à base. Uma cópia local histórica de `Dados do Caça-Alucinações - BRACIS 2026.pdf` é um PDF A4 de seis páginas.

Os documentos estão divididos em dois níveis. O nível 1 tem apresentação regular e peso 1×. O nível 2, com peso 2×, altera a superfície das referências: abreviações, pontuação, espaçamento, UF, possíveis confusões de OCR e quebras de linha. Por construção, esse ruído não substitui um dígito por outro; a normalização deve recuperar a referência verdadeira.

Para jurisprudência, uma busca textual pode retornar decisões que apenas mencionam um processo. O sinal prático é localizar, nos primeiros caracteres do documento canônico, o cabeçalho com classe processual, número e UF. Em seguida, conte **feitos distintos**, não apenas as linhas retornadas: um mesmo feito pode ter mais de um registro.

Súmulas e dispositivos legais devem ser comparados aos seus registros próprios na base, não aos acórdãos que os mencionam.

## Textos de entrada

O diretório `txt/` contém 26 peças jurídicas sintéticas em UTF-8, totalizando 89.488 bytes (86.681 caracteres). Cada texto tem entre 2.880 e 3.887 caracteres, com média de 3.334. As peças assumem formas como petições, pareceres, decisões monocráticas e memoriais, em matérias coerentes com os tribunais e normas citados.

| Conjunto | Arquivos | Peso | O que avalia |
| --- | ---: | ---: | --- |
| Nível 1 | `gen_n1_001.txt` a `gen_n1_013.txt` | 1× | Extração e resolução de citações no formato padronizado. |
| Nível 2 | `gen_n2_001.txt` a `gen_n2_013.txt` | 2× | Normalização robusta antes da resolução. |

O nome do arquivo sem extensão é o `documento_id` utilizado pelo gabarito e pela submissão.

## Base canônica

`desafio1_bracis.db` é um banco SQLite 3 em UTF-8, com 93.298.688 bytes. Sua verificação de integridade retorna `ok`. Ele é a cobertura congelada que define se uma citação é real ou inventada no desafio; não se deve consultar uma base jurídica externa para tomar essa decisão.

A tabela principal é `documentos`. A tabela virtual `documentos_fts` usa FTS5, conteúdo externo por `rowid` e o tokenizador `unicode61 remove_diacritics 2`, permitindo pesquisa no inteiro teor sem duplicá-lo.

| Campo de `documentos` | Finalidade |
| --- | --- |
| `documento_id` | Chave interna e nome do registro no acervo. |
| `id` | Identificador canônico do Jusbrasil; é o valor exigido em `resolucao.id_canonico`. |
| `tribunal`, `ano`, `relator` | Metadados dos acórdãos; `tribunal` é nulo para dispositivos legais. |
| `natureza` | `acordao`, `sumula` ou `dispositivo`. |
| `tipo` | `jurisprudencia` ou `lei`. |
| `texto`, `texto_len` | Inteiro teor e seu comprimento. |

A base final contém 1.014 registros, que somam 67.737.021 caracteres indexados:

| Natureza | Tipo | Registros |
| --- | --- | ---: |
| `acordao` | `jurisprudencia` | 996 |
| `sumula` | `jurisprudencia` | 5 |
| `dispositivo` | `lei` | 13 |

Há 200 acórdãos de STF, TST e STM, 199 de STJ e 199 de TSE. As cinco súmulas se distribuem entre STF (1), STJ (3) e TST (1); os 13 dispositivos não têm tribunal. Os acórdãos cobrem de 2009 a 2026. Além das chaves, existem índices para `tribunal`, `ano` e `natureza`.

## Gabarito de desenvolvimento

`goldenset_offsets.csv` contém uma linha por citação esperada da amostra de desenvolvimento final. São 192 linhas distribuídas pelos 26 documentos (média de 7,38 por peça).

| Campo | Descrição |
| --- | --- |
| `nivel` | `1` ou `2`; determina o peso. |
| `documento_id`, `citacao_id` | Identificam o texto de origem e a citação, por exemplo `g3`. |
| `inicio`, `fim` | Span em codepoints Unicode; `fim` é exclusivo. |
| `trecho` | Trecho anotado; quebras de linha são serializadas como `\\n`. |
| `tipo` | `jurisprudencia` ou `lei`. |
| `classificacao` | `real`, `inventada` ou `incompleta`. |
| `id_canonico` | Identificador único aceito; preenchido apenas em citações reais. |

| Recorte | `real` | `inventada` | `incompleta` | Total |
| --- | ---: | ---: | ---: | ---: |
| Nível 1 | 52 | 32 | 15 | 99 |
| Nível 2 | 44 | 32 | 17 | 93 |
| Total | 96 | 64 | 32 | 192 |

Existem 164 citações de jurisprudência e 28 de lei. As 96 linhas classificadas como `real` possuem `id_canonico`; as demais o deixam vazio, como estabelece o enunciado.

!!! note "Observação de consistência"

    Comparando `texto[inicio:fim]` com o `trecho`, ao interpretar `\\n` como quebra de linha, as 192 linhas coincidem com os textos atuais. A atualização final removeu três citações incompletas, ajustou spans anotados em textos revisados e corrigiu o ID de `gen_n2_005/g6`. Veja `report.md` na raiz do repositório.

## Revisões do dataset

A revisão oficial removeu os acórdãos duplicados `doc_0227` e `doc_0461` do
SQLite e corrigiu `gen_n2_010.txt`. A cópia local anterior tinha o gabarito em
`goldenset.xlsx`; a versão intermediária usou `goldenset.csv`. O pacote final usa
`goldenset_offsets.csv` e um SQLite com 1.014 registros: 996 acórdãos, 13
dispositivos e 5 súmulas. A atualização final removeu dois acórdãos, revisou
textos de dispositivos e súmulas, alterou três textos de entrada e ajustou o
gold. No contrato, `id_canonico` é um único valor para cada citação real. As
versões anteriores permanecem nas cópias históricas locais.

## Execução final

O entrypoint de entrega gera diretamente o CSV exigido pela organização:

```bash
bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
```

O resultado contém `documento_id,citacoes`. Cada citação é codificada como `inicio,fim,classificacao,id_canonico,confianca`, separada por `|`; documentos sem citação recebem `-`. Registre o hash do commit usado com `git rev-parse HEAD` antes de enviar a entrega.

## Arquivo compactado

`material_desafio_jusbrasil_bracis.zip` (35.392.239 bytes) é o pacote de distribuição do mesmo material. Ele possui 64 entradas: 32 de conteúdo principal e, além de `.DS_Store`, 31 metadados em `__MACOSX/`. A cópia extraída já é suficiente para o trabalho; o arquivo não foi alterado.
