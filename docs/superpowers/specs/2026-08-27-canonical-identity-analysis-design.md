# Notebook 03 — identidade processual e classe canônica

## Objetivo

Produzir evidência reproduzível para decidir a composição de uma futura
identidade de feito no corpus canônico. O notebook analisa somente os 1.000
acórdãos, abre o SQLite em modo read-only e não cria uma API, índice,
resolvedor ou classificador.

## Escopo e limites

O artefato é `notebooks/03_canonical_identity_analysis.ipynb`. Todo parser
fica local ao notebook e é explicitamente experimental. O trabalho não lê ou
altera goldenset, arquivos `.txt` ou fontes externas; não usa LLM, regras por
`documento_id`/`id` nem dependências novas.

Valores brutos permanecem nos resultados antes de qualquer normalização. As
conclusões distinguem fatos observados, proposta para implementação futura e
incertezas; nenhuma decisão cria `CanonicalIdentity` em `src/`.

## Fluxo de análise

1. Carregar, por `connect_database()`, os acórdãos e confirmar 1.000 registros
   (200 por tribunal).
2. Inspecionar cabeçalhos por STF, STJ, TSE, TST e STM e documentar o bloco
   estrutural no qual o próprio feito é apresentado.
3. Aplicar estratégias locais por tribunal para obter candidatos de número
   principal. Cada resultado conserva posição, padrão que o encontrou,
   ambiguidade e qualidade estrutural simples.
4. Extrair classe somente do mesmo bloco validado do número principal. Uma
   auditoria marca sinais de contexto narrativo, extensão excessiva, datas,
   nomes, `ART`, distância e ausência de padrões recorrentes.
5. Medir classes confiáveis, investigar classe-base e modificadores sem
   presumir que essa decomposição seja a representação final.
6. Revisitar os 14 casos antes não parseados, agrupando causas reutilizáveis.
   FTS recebe somente consultas derivadas dos candidatos/formatos observados;
   seus retornos são candidatos que precisam de revalidação contextual.
7. Recalcular chaves A/B/C e condicionalmente D apenas no subconjunto elegível,
   inspecionar grupos multi-ID e propor a definição operacional de feito.

## Resultados e confiança

Cada registro experimental preserva `numero_principal_raw`,
`numero_principal_normalizado`, `numero_posicao`, `numero_source_pattern`,
`classe_raw`, `classe_normalizada`, `classe_posicao`, `uf` e indicadores de
ambiguidade/qualidade. Caso a análise justifique, `classe_base` e
`modificadores` permanecem colunas exploratórias.

Os níveis propostos são `high confidence`, `medium confidence`, `low
confidence` e `unparsed`: decorrem da presença e proximidade de número e
classe no bloco esperado, de ambiguidades e de recuperação exclusiva por FTS;
não são probabilidades calibradas.

## Visualizações

Os gráficos aparecem logo após as tabelas que sintetizam e não escondem os
valores tabulares:

- barras agrupadas para cobertura de número principal e classe confiável;
- barras empilhadas para qualidade e causas de falha por tribunal;
- barras de classes válidas versus suspeitas e taxonomia frequente;
- barras para classes simples/compostas e efeito de modificadores;
- comparação das colisões e grupos elegíveis das chaves A–D;
- distribuição de tamanho dos grupos multi-ID e classificação manual;
- comparação de cobertura do parser estrutural e de candidatos FTS
  revalidados.

Todos os títulos, eixos e legendas ficam em português e descrevem a métrica,
não uma conclusão jurídica.

## Estrutura do notebook

As seções seguem o enunciado: setup, conceito de identidade, anatomia por
tribunal, número principal, classe associada, auditoria de classe, taxonomia,
classe-base/modificadores, 14 casos, FTS, chaves, colisões, definição
operacional, hipótese conceitual, confiança e findings. A conexão é fechada
na última célula.

## Validação

Um teste estrutural exige as seções e os títulos de visualizações, além das
restrições de não haver `sys.path.append`, `CanonicalIndex` ou resolver. A
validação executa o notebook do início ao fim para uma cópia em `/tmp`, roda a
suíte de testes e confere integridade/hash do SQLite antes e depois. A execução
não grava resultados no banco.
