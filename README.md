# BRACIS 2026 × Jusbrasil — Caça-Alucinações

Solução determinística e offline para classificar citações jurídicas em textos.
Ela recebe um banco SQLite e uma pasta de documentos TXT, produzindo o CSV de
submissão. O banco é aberto em modo somente leitura; GPU, rede, APIs e modelos
externos não são necessários.

## Requisitos

- Python 3.11 ou superior para execução local
- Docker opcional para execução isolada
- CPU suficiente; não há requisito de GPU

## Execução

O único comando de entrega é:

```bash
bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
```

Exemplo:

```bash
bash run.sh /data/desafio.db /data/txt /data/submission.csv
```

Os argumentos podem conter espaços. O comando independe do diretório atual e
cria o diretório pai do arquivo de saída quando necessário.

### Docker

```bash
docker build -t bracis-jusbrasil-final .
docker run --rm -v "$PWD/inputs:/data" bracis-jusbrasil-final \
  /data/desafio.db /data/txt /data/submission.csv
```

A imagem não inclui dados do desafio. Monte o banco e a pasta TXT no container.

### Execução local alternativa

```bash
python -m venv venv
source venv/bin/activate
pip install .
bash run.sh /data/desafio.db /data/txt /data/submission.csv
```

## Formato de entrada

`<caminho_db>` deve ser um arquivo SQLite compatível com a tabela `documentos`.
`<pasta_txt>` deve conter arquivos `*.txt`; cada `path.stem` se torna o campo
`documento_id`. Todos os TXT encontrados recebem uma linha na saída.

## Formato de saída

O CSV possui as colunas `documento_id,citacoes`. Cada citação é serializada como
`inicio,fim,classificacao,id_canonico,confianca`, com citações separadas por
`|`. Documentos sem citação recebem `-`.

## Reprodutibilidade

A solução ordena os arquivos e as linhas de saída, usa o SQLite em modo
somente leitura e não acessa a internet durante a execução. A validação pública
é apenas uma referência de desenvolvimento e não representa avaliação oculta.

Para registrar a revisão usada na entrega, execute:

```bash
git rev-parse HEAD
```

## Problemas comuns

- **Uso incorreto:** informe exatamente DB, pasta TXT e arquivo CSV.
- **DB ou pasta ausente:** confira os caminhos informados; o comando encerra com
  erro e não produz uma saída parcial.
- **Permissão de escrita:** escolha um `arquivo_saida` cujo diretório possa ser
  criado ou escrito pelo usuário atual.
