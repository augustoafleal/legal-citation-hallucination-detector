---
title: Legal Citation Hallucination Detector
description: Índice da documentação da solução offline para o desafio Jusbrasil na BRACIS 2026.
---

# Legal Citation Hallucination Detector

Solução offline que recebe um SQLite e documentos TXT e gera o CSV de submissão.
O entrypoint é:

```bash
bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
```

## Comece aqui

1. Leia [Uso](usage.md) para preparar a execução local ou Docker e conferir o CSV.
2. Consulte [Arquitetura](architecture.md) para entender o pipeline em produção.
3. Use [Scripts](scripts.md) para inspecionar o SQLite de forma somente leitura.

## Índice da documentação

| documento | conteúdo |
| --- | --- |
| [Uso](usage.md) | argumentos, contratos de entrada e saída, Docker e falhas de uso. |
| [Arquitetura](architecture.md) | componentes atuais, fluxo de dados e garantias do runtime. |
| [Scripts](scripts.md) | utilitário de inspeção do SQLite. |
| [Competição](competition.md) | regras e contrato público consolidados. |
| [Notebooks](notebooks.md) | análises exploratórias históricas e reproduzíveis. |

## Dados públicos e avaliação

`material_desafio_jusbrasil_bracis/` é uma fixture pública para desenvolvimento
e validação. A execução final usa os caminhos de SQLite e TXT fornecidos pela
organização e não depende do gold público, do scorer ou de uma conexão de rede.

Para identificar a revisão usada em uma entrega:

```bash
git rev-parse HEAD
```
