---
title: Uso
summary: Como gerar e conferir o CSV de submissão.
---

# Uso

O comando de entrega recebe três argumentos, nesta ordem: o SQLite recebido, a
pasta com os documentos TXT e o caminho do CSV de saída.

```bash
bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
```

Exemplo com o material público versionado:

```bash
bash run.sh \
  material_desafio_jusbrasil_bracis/desafio1_bracis.db \
  material_desafio_jusbrasil_bracis/txt \
  submissions/teste.csv
```

`run.sh` encontra o projeto a partir da sua própria localização, portanto pode
ser chamado de outro diretório. Os caminhos podem conter espaços.

## Requisitos locais

- Python 3.11 ou superior.
- SQLite compatível com a tabela `documentos`.
- permissão de leitura para o SQLite e os TXT, e de escrita para o CSV.

Para executar fora de Docker:

```bash
python -m venv venv
source venv/bin/activate
pip install .
```

## Contrato de entrada

| argumento | conteúdo esperado |
| --- | --- |
| `<caminho_db>` | arquivo SQLite. A aplicação o abre em modo somente leitura. |
| `<pasta_txt>` | diretório com arquivos `*.txt` em UTF-8. O nome sem `.txt` torna-se `documento_id`. |
| `<arquivo_saida>` | destino do CSV. O diretório pai é criado quando necessário. |

A aplicação usa o SQLite informado em cada execução para construir os índices
canônicos. O gold e o scorer públicos não participam do fluxo de entrega.

## Contrato de saída

O CSV é UTF-8 e contém exatamente:

```text
documento_id,citacoes
```

Há uma linha por TXT de entrada. `citacoes` é `-` quando não há citações. Caso
contrário, contém itens separados por `|`, cada um com:

```text
inicio,fim,classificacao,id_canonico,confianca
```

`classificacao` pode ser `real`, `inventada` ou `incompleta`. Uma citação
`real` inclui o identificador canônico. Nas demais, o campo é `-`. A confiança
emitida atualmente é `0.8500`.

## Conferência rápida

```bash
head -n 2 submissions/teste.csv
```

Para registrar a revisão exata usada na entrega:

```bash
git rev-parse HEAD
```

## Docker

```bash
docker build -t bracis-jusbrasil-final .
docker run --rm -v "$PWD/inputs:/data" bracis-jusbrasil-final \
  /data/desafio.db /data/txt /data/submission.csv
```

A imagem não inclui dados do desafio. Monte o SQLite e os TXT que devem ser
processados.

## Falhas de uso

O comando encerra com erro quando recebe uma quantidade diferente de argumentos,
o SQLite não existe ou a pasta de TXT não existe. Nessas situações, escolha os
caminhos corretos e execute novamente.
