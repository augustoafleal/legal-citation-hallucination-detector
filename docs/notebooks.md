---
title: Notebooks
description: Convenções para notebooks exploratórios.
---

# Notebooks

Os notebooks usam a convenção `NN_nome_descritivo.ipynb`, com uma numeração de
duas posições que torna a ordem de exploração explícita.

Eles servem para exploração e experimentos; lógica reutilizável deve permanecer
em `src/`, não em células de notebook.

## 01_database_overview.ipynb

O primeiro notebook entende o conteúdo, a estrutura e as características do
SQLite canônico antes de qualquer implementação de resolução de citações. Ele
abre o banco apenas em modo read-only e mostra consultas resumidas, cabeçalhos,
súmulas, dispositivos, FTS5 e duplicatas de texto.

## 02_canonical_database_analysis.ipynb

O segundo notebook examina os 1.000 acórdãos como possíveis identidades
processuais estruturadas. Ele mede posições, padrões por tribunal, cobertura de
parsing experimental, falhas, duplicatas, múltiplos IDs e chaves candidatas de
feito. A análise continua read-only e não implementa índice canônico ou
resolver.

## 03_canonical_identity_analysis.ipynb

O terceiro notebook refina, de modo estritamente exploratório, a extração de
número principal e classe associada por tribunal. Ele audita a qualidade das
classes e revisita falhas, sem introduzir componentes de identidade no pacote
de produção.

## 04_primary_case_number_and_collisions.ipynb

O quarto notebook prioriza a identidade formal de cada tribunal: inventaria os
blocos estruturais, extrai o número principal somente dentro desses blocos,
preserva a evidência e prepara uma amostra para auditoria manual externa ao
parser; o notebook apenas produz a amostra auditável, sem julgar sua qualidade.
As análises de `tribunal + número principal` são preliminares e condicionadas à
maturidade do parsing; colisões, classe e UF não são conclusões globais deste
notebook. O parser TSE também tolera OCR e separadores degradados dentro do
candidato processual, suporta número legado e registra a origem da recuperação;
os 25 casos auditados manualmente são usados apenas como evidência diagnóstica,
sem hardcodes por documento. O SQLite permanece read-only e não há código de
produção, índice ou resolver.

## 05_canonical_collision_analysis.ipynb

O quinto notebook analisa, exclusivamente no SQLite, os grupos multi-ID
formados por `(tribunal, numero_normalizado)`. Classe, UF, relator, ano e texto
são evidências auxiliares somente dentro das colisões; o notebook preserva
todos os candidatos, não escolhe uma identidade final e não implementa
`CaseIndex`. A análise final cobre 1000/1000 acórdãos: 912 grupos, 831
single-ID e 81 multi-ID, envolvendo 169 candidatos. O delta dos 25 TSE criou
21 chaves single-ID e 2 colisões novas; as duas foram auditadas como
`same_case`, somando-se aos 79 grupos antigos já auditados. A chave é adequada
para recuperação, mas não garante identidade única; múltiplos IDs devem ser
preservados por feito. Essa baseline alimenta o `CaseIndex` V1 em
`src/bracis_jusbrasil.cases`: súmulas, dispositivos, citações `.txt` e FTS
continuam fora do índice.

Os números acima registram a investigação sobre uma versão anterior do dataset.
Em 28/08/2026, a baseline oficial foi atualizada: `doc_0227` e `doc_0461`
foram removidos por serem duplicatas exatas. Uma atualização posterior removeu
30 referências genéricas da classe `incompleta` do `goldenset.csv`; por isso,
as estatísticas históricas de 225 citações descritas neste documento não são
baseline do material público final atual de 192 citações. A organização também
revisou três textos e offsets anotados, removeu duas decisões da base e
atualizou textos legais; consulte a documentação da competição para o inventário
corrente. Consulte `report.md` na raiz antes
de reexecutar ou comparar esses notebooks.

## 06_citation_surface_analysis.ipynb

O sexto notebook é uma etapa exploratória sobre a superfície das citações da
revisão corrente do desafio. Ele lê somente os 26 arquivos `txt` e as 225 linhas
do gold histórico `goldenset.csv`, valida os 225 spans contra os offsets do texto e separa
explicitamente detecção, parsing e resolução; somente as duas primeiras são
estudadas. A taxonomia textual cobre processos CNJ, processos ou recursos
numerados, súmulas, referências jurisprudenciais contextuais ou gerais e três
formas de referência legal. Os rótulos do goldenset são usados apenas depois
para cruzamentos analíticos, nunca para definir regras ou famílias.

A baseline determinística de regras interpretáveis obteve 105 TP, 89 FP e 120
FN (precisão 0,541, recall 0,467 e F1 0,501 com IoU >= 0,5), tornando visíveis
as lacunas de cobertura e de fronteira antes de qualquer evolução. O parsing é
medido exclusivamente com spans-oráculo e registra apenas tribunal, classe,
número e UF explicitamente presentes. O notebook não resolve citações, não usa
`CaseIndex`, não cria código de produção e não usa o diretório histórico
`_old`; os resultados servem para orientar um próximo experimento de detecção
geral, não uma arquitetura de resolução.

A baseline de detecção desse notebook foi portada sem alterações de métricas
para `bracis_jusbrasil.citations.CitationDetector` V1. O componente preserva os
spans, regras, famílias, deduplicação e overlaps observados, enquanto parsing e
resolução continuam fora do código de produção.

## 07_citation_detector_error_analysis.ipynb

O sétimo notebook é uma análise exploratória e reproduz exatamente a baseline
do `CitationDetector` V1 na revisão corrente: 105 TP, 89 FP e 120 FN. Ele
classifica os erros sem alterar o detector: os FN se dividem em 79 casos sem
candidato e 41 candidatos contidos no trecho gold; os FP em 63 subspans de
trechos gold e 26 casos espúrios. Também mede regras, famílias, níveis N1/N2 e
tipos, além de explicitar os deltas de fronteira.

As variantes permanecem exclusivamente no notebook. A melhor isolada é uma
regra lexical conservadora para referências jurisprudenciais gerais: 117 TP,
89 FP, 108 FN e F1 0,543, sem regressões dos matches V1. A extensão estrutural
de sufixo de processo e as hipóteses contextuais mais amplas elevam falsos
positivos, portanto não são proposta de V2. O notebook recomenda somente essa
adição mínima como candidata a V2, condicionada a nova validação; não modifica
`CitationDetector`, não implementa V2 e não faz parsing ou resolução.

## 07_1_citation_detector_generalization_check.ipynb

O Notebook 07.1 audita o risco de generalização da regra experimental
`jurisprudencia_geral`, mantendo produção e a regra congeladas. Os 12 TP novos
se distribuem por 8 documentos (top-1: 25%, top-3: 58,3%; HHI: 0,153), cinco
branches lexicais e ambos os níveis (8 em N1, 4 em N2), sem FP novo. O LOODO,
explicitamente usado apenas como medida de concentração e não como holdout,
mantém ganho de F1 em 26/26 exclusões; a remoção simultânea dos dois documentos
com maior ganho ainda preserva +7 TP. Nenhum branch pode ser removido sem perder
cobertura, portanto não há simplificação equivalente. A evidência foi
classificada como `LOW GENERALIZATION RISK` e a decisão analítica é promover a
regra para uma futura V2, ainda condicionada à avaliação no blind set dos
organizadores. O notebook não altera produção nem implementa essa decisão.

A regra foi posteriormente promovida, sem alterações, ao `CitationDetector`
V2. Os notebooks 07 e 07.1 continuam sendo a evidência histórica da seleção e
do teste de robustez, incluindo a baseline de 117 TP, 89 FP e 108 FN. Sob o
Gold V2, a regra foi retirada da produção: referências jurisprudenciais vagas,
sem fonte concreta, deixaram de estar no escopo de citação.

## 08_citation_parsing_analysis.ipynb

O oitavo notebook estuda, de forma exploratória e determinística, o desenho de
um futuro parser de citações usando exclusivamente os 225 spans-oráculo do
goldenset e os 26 textos da revisão corrente. Ele separa reconhecimento de
família, extração de campos, normalização segura e informação ausente, sem
consultar `CaseIndex`, banco, FTS ou `id_canonico`, e sem alterar componentes de
produção.

A análise cobre processos CNJ, processos ou recursos numerados, súmulas,
referências jurisprudenciais contextuais ou gerais e as três famílias legais.
Ela mantém tipos numéricos distintos — inclusive artigo, número de lei e número
processual — e preserva valores brutos, normalizados e a proveniência do campo.
Os CNJs foram extraídos em 25/25 spans; números de súmula em 10/10; campos
contextuais (tribunal, relator e ano) em 20/20; e artigo/diploma nas referências
legais com diploma em 27/27. Já a família de processos ou recursos numerados
exige nova investigação: 61/85 números explicitamente presentes e 53/63 UFs
explícitas foram extraídos, com pior cobertura de número em N2 (61,7% contra
90,5% em N1).

O notebook não executa reparos OCR: registra separadamente newline e confusões
`O/0`, `l/1` e `S/5` como hipóteses locais. A recomendação é uma segunda rodada
exploratória de parsing sobre as estruturas de processo/recurso numerado e sua
robustez N2, antes de implementar `CitationParser` em produção. A proposta
arquitetural permanece conceitual: reconhecimento de família, dispatch para
parsers específicos e um contrato comum com campos opcionais; existência,
desambiguação, `id_canonico` e classificações finais pertencem ao futuro
Resolver.

## 09_case_citation_parsing_robustness.ipynb

O nono notebook encerra a exploração de robustez do parsing sobre os 110
spans-oráculo de processos CNJ e processos/recursos numerados. Ele reproduz a
baseline do Notebook 08 (CNJ 25/25, número 61/85 e UF 53/63 pelo protocolo
original), audita as 24 ausências da baseline e não consulta SQLite,
`CaseIndex` ou `id_canonico`.

Onze dessas 24 citações não contêm número processual: são referências
contextuais com ano. Das 13 limitações estruturais restantes, oito são
recuperadas por tolerância local a marcadores e aliases de classe, e quatro por
newline/pontuação em posição plausível; uma cadeia trabalhista composta fica
deliberadamente fora da proposta por ter suporte único. A extração de UF passa
de 53/63 no protocolo anterior para 66/66 formas localmente visíveis ao número,
sem buscar siglas no span inteiro. As regras estruturais não produzem
regressões e mantêm CNJ em 25/25.

Dois identificadores N2 exigem reparo OCR contextual (`l/1` e `S/5`). O reparo
permanece apenas como fallback local dentro de um candidato numérico já
delimitado, preservando `numero_raw` e registrando proveniência. A conclusão
exploratória recomenda implementar o futuro `CitationParser` V1 com dispatch
por família, envelope comum e payload específico simples; existência,
desambiguação e classificação continuam sendo responsabilidade do Resolver.

As regras investigadas nesses dois notebooks foram posteriormente portadas sem
ampliação para `bracis_jusbrasil.citations.CitationParser` V1. A implementação
mantém dispatch por família, preserva valores brutos e provenance, suporta as
tolerâncias estruturais validadas para processos/recurso e limita OCR a dois
contextos numéricos locais. O parser descreve apenas a superfície textual;
resolução e classificação permanecem fora de produção nesta etapa.

## 10_citation_resolution_analysis.ipynb

O décimo notebook conecta, de forma exclusivamente exploratória, os 225
spans-oráculo ao `CitationParser` V1 e ao corpus canônico. Ele reutiliza o
`CaseIndex` para acórdãos, inspeciona os três tipos de registro (acórdão,
súmula e dispositivo) e cria somente índices auxiliares em memória. As decisões
neutras — `resolved_unique`, `no_match`, `ambiguous` e `insufficient` — são
tomadas antes da comparação posterior com `classificacao` e `id_canonico`.

O experimento mede suficiência estrutural, conjuntos de candidatos, candidate
recall, resolução única correta, grupos multi-ID, gaps do parser e risco de
generalização por estratégia. Ele não usa FTS como identidade, não escolhe
arbitrariamente IDs de grupos múltiplos e evidencia que o schema atual não
expõe diploma legal estruturado para uma resolução segura de dispositivos. Ao
fim, propõe apenas um contrato conceitual de `ResolutionResult` e um resolver
futuro com dispatch interno; nenhum `CitationResolver` é implementado neste
notebook ou em produção.

## 11_resolution_edge_cases_and_identity_audit.ipynb

O décimo primeiro notebook audita os casos que impediam uma resolução canônica
segura: o CNJ/TST que diverge do ID gold, os três grupos multi-ID presentes em
citações reais, a ausência de diploma verificável nos dispositivos e as
limitações de metadados das súmulas. A auditoria confirma que o conflito CNJ é
isolado e consistente com uma divergência de gold, mas ainda o exclui da V1
porque o contrato exige ID escalar exato. Multi-ID continua `ambiguous`, sem
escolha por ordenação.

O notebook separa os 14 gaps reais do parser dos gaps semânticos e mede um
conjunto mínimo de `SAFE_STRATEGIES`: número explícito para processos/recursos
e tribunal+número para súmulas. Esse subconjunto é reavaliado sobre os 225
spans-oráculo com zero ID real errado e zero false-real; as famílias restantes
permanecem `insufficient` ou `ambiguous`. A decisão exploratória foi promovida
para o `CitationResolver` V1, limitado a estratégias verificáveis: número
exato de processo/recurso e súmula com número e tribunal explícito. O resolver
preserva grupos canônicos multi-ID como ambíguos e não introduz Parser V2,
busca aproximada ou novos índices nesta etapa.

## 12_end_to_end_pipeline_baseline.ipynb

O décimo segundo notebook executa, pela primeira vez, o fluxo completo
`CitationDetector` V2 → `CitationParser` V1 → `CitationResolver` V1 sobre os
26 textos reais, sem substituir candidatos por spans-oráculo. O gold é usado
somente após a execução para matching determinístico por IoU, funis por classe,
atribuição da primeira falha e comparação entre o teto oracle e o resultado
end-to-end.

Ele também audita os falsos positivos do detector depois de parser e resolver,
mede o efeito de boundaries não exatos, separa N1 de N2 e registra
contrafactuais diagnósticos sem alterar regras de produção. A conclusão aponta
o `CitationDetector` como próximo componente a estudar, com base em retenção,
segurança de IDs e gaps estruturais; não cria filtro de candidatos,
classificação de produção ou submissão.

## 13_citation_detector_identity_preservation.ipynb

O décimo terceiro notebook explorou uma possível V3 e registrou a promoção da
única mudança aprovada. Ele reproduz o baseline V2 e reexecuta, do zero, o fluxo
`Detector → CitationParser V1 → CitationResolver V1` para comparar boundaries,
famílias e misses pelos efeitos downstream. A métrica principal é a preservação
de identidade e de resolução segura, complementada por F1, exact, auditoria de
FPs, segurança de IDs, N1/N2, distribuição por documento, LOODO, determinismo
e performance.

As variantes originais foram regras sintáticas locais em memória: não consultam gold,
documento, banco, `CaseIndex`, Resolver, FTS ou modelo. O notebook classifica os
21 boundary failures, 5 family mismatches e 19 misses, separa ganhos imediatos
de identidade estrutural futura e só recomenda uma V3 mínima quando há ganho de
ID sem `wrong_unique_real` ou falsos-real. Parser e Resolver continuam
congelados.

A revisão humana confirmou dois ganhos reais de ID e identificou um artefato na
auditoria de spans: candidates legais distintos compartilhavam `start` e não
podiam ser associados por esse campo isolado. A associação corrigida exige
family, rule, start e prefixo textual compatível; as duas supostas regressões
legais (`gen_n1_006` e `gen_n2_007`) não são regressões da V3. A implementação
promovida em produção é somente V2 + expansão estrutural local de UF para
processo/recurso numerado.

## 14_post_v3_bottleneck_reassessment.ipynb

O décimo quarto notebook reavalia os gargalos após a promoção da V3, executando
somente `CitationDetector V3 → CitationParser V1 → CitationResolver V1`. Ele
compara a atribuição histórica V2 com a nova atribuição dos 96 casos `real`,
separa `first blocker` de retorno imediato e mede os upper bounds contrafactuais
de Detector V4 e Parser V2 sem implementar nenhuma nova regra.

A análise também decompõe os 67 `real` ainda não resolvidos em investimento no
Detector, Parser, Resolver estrutural e investigação semântica/contextual.
Destaca o pool estrutural do Resolver como potencial de pesquisa, sem tratá-lo
como ganho seguro, e registra a matriz de estados, famílias, N1/N2, FPs,
determinismo, performance e a decisão do próximo experimento. Não altera
produção, Notebook 12 ou o dataset.

## 15_citation_resolver_v2_structural_analysis.ipynb

O décimo quinto notebook investiga exclusivamente os 22 casos do pool
estrutural identificado após a V3. Ele mantém Detector V3, Parser V1 e Resolver
V1 como baseline experimental, constrói índices experimentais em memória para CNJ,
dispositivos legais e súmulas, e avalia candidate recall, unicidade, segurança,
impacto nos 87 FPs e ganho oracle versus end-to-end.

As estratégias são avaliadas individualmente e somente as que preservam zero
`wrong_unique_real` e zero falsos-real podem compor uma candidata conceitual de
Resolver V2. A revisão humana aprovou `sumula_number_only`, que foi promovida
ao `CitationResolver V2` como a única nova estratégia. O notebook também cobre o conflito CNJ/gold, a ausência de
metadata legal estruturada, o pool semântico/contextual, LOODO e uma lista
reduzida para revisão humana. O notebook permanece evidência histórica anterior
à promoção; CNJ, estratégias legais e resolução semântica continuam fora de
produção.

### Atualização posterior: CitationDetector V5

Após a revisão humana dos sete residuais do Detector, os padrões foram
promovidos com guards estruturais ao componente de produção. A V5 recupera
prefixos formais de CNJ, `RHC`/`RMS`/`AR` com marcador e quebra de linha local,
modificadores processuais seguidos diretamente de identificador não-CNJ e o
título composto de agravo interno/suspensão. No corpus congelado, o resultado
passou de 127 TP / 84 FP / 98 FN na V4 para 137 TP / 82 FP / 88 FN na V5,
sem `wrong_unique_real`, `false_real_inventada` ou `false_real_incompleta` no
pipeline completo. A decisão está registrada em
`artifacts/detector_residual_human_review.json`.
