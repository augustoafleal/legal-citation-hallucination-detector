# Notebook 04 — número principal e colisões

## Objetivo

Determinar experimentalmente o número principal do acórdão por tribunal e
medir o que acontece ao agrupar registros por `tribunal + numero_normalizado`.
O resultado deve separar uma chave de recuperação de uma identidade final de
feito, sem implementar componentes de produção.

## Limites

O único artefato analítico novo será
`notebooks/04_primary_case_number_and_collisions.ipynb`. SQLite, arquivos de
amostra e dados de referência não serão lidos para construir regras, nem
alterados. Não haverá fontes externas, LLM, dependências novas, regras por
identificador, índice, resolvedor, classificador ou parser em `src/`.

## Método

O notebook carrega exclusivamente os 1.000 acórdãos via `connect_database()`.
Cada tribunal tem uma função local e exploratória que localiza uma âncora
formal do próprio acórdão antes de extrair o número: não há competição global
por pontuação de regex nem uma janela fixa comum. Cada resultado conserva
bloco, âncora, posição, forma bruta, forma normalizada e status (`extraído`,
`ambíguo`, `suspeito`, `não extraído`).

O notebook exibe exemplos variados por tribunal, amostras reprodutíveis e
todas as falhas. A taxonomia de falhas descreve padrões reutilizáveis, sem
exceções por documento.

## Colisões e evidência auxiliar

Somente linhas com número estruturalmente confiável participam da chave A:
`tribunal + numero_normalizado`. Grupos multi-ID são inspecionados com hashes,
cabeçalhos, ano, relator e, quando recuperáveis do mesmo bloco formal, classe
aparente e UF. Essas duas últimas informações servem somente para investigar
as colisões; não condicionam a cobertura global.

As chaves B–D com classe e/ou UF são comparações incrementais, sempre com
cobertura explicitamente separada. Uma queda de colisões não é tratada, por si
só, como melhoria.

## Visualizações

Cada gráfico responde a uma pergunta de inspeção humana e usa apenas
matplotlib: posição por tribunal, cobertura acumulada, status de parsing,
tamanho dos grupos, grupos multi-ID por tribunal, categorias de colisão e
cobertura versus colisões das chaves incrementais. Títulos, eixos e legendas
ficam em português; cada figura é seguida de uma interpretação curta.

## Conclusão esperada

O notebook deve decidir se `tribunal + número` é uma chave de recuperação
suficientemente forte para uma primeira versão que preserve múltiplos
candidatos. A identidade final pode continuar ambígua. A hipótese conceitual
mantém uma chave de recuperação e uma lista de registros com ID, documento,
classe/UF opcionais e qualidade de parsing; nenhum desses objetos é criado no
pacote.

## Validação

Teste estrutural exige as 22 seções, os sete gráficos e as restrições de
escopo. A validação executa os quatro notebooks em cópias em `/tmp`, testes,
inspeção do banco, build estrito do MkDocs e hash/integridade do SQLite antes e
depois.
