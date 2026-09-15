# fiscal-pipeline

Pipeline de ingestão, validação e análise de documentos fiscais eletrônicos.
XML de NF-e → data lake → motor de regras configurável → achados priorizados
por impacto financeiro.

![CI](https://github.com/maraysamacedo/fiscal-pipeline/actions/workflows/ci.yml/badge.svg)

## O problema

Uma contabilidade que atende milhares de empresas processa centenas de milhares
de notas por mês. Erros de classificação se repetem em escala, mas a validação
costuma ser manual e por amostragem. Este projeto automatiza a detecção e —
o ponto principal — **prioriza os achados por impacto em reais**. Encontrar
400 mil inconsistências não ajuda ninguém; encontrar as 300 que valem R$ 2
milhões, sim.

## Rodar

```bash
pip install -r requirements.txt
make tudo      # gera, ingere, processa, audita
make test      # 57 testes
make painel    # dashboard em localhost:8501
```

## Arquitetura

| Camada | Ferramenta | Papel |
|---|---|---|
| Ingestão | Python + lxml | Parse de XML, dedup por SHA-256 |
| Storage | DuckDB + Parquet | Colunar, particionado por competência |
| Regras | YAML + motor próprio | Validação configurável, sem deploy |
| Interface | Streamlit | Achados priorizados |
| IA | scikit-learn | Classificação de NCM com human-in-the-loop |

## Decisões técnicas

**DuckDB em vez de Postgres ou BigQuery.** Sintaxe compatível com BigQuery
(`QUALIFY`, window frames, `MERGE`), o que torna a migração quase mecânica.
Roda embarcado, então o CI executa o pipeline completo sem credencial de nuvem
nem custo.

**Regras em YAML em vez de código.** Regra fiscal muda com legislação, e quem
conhece a regra é o analista fiscal. Adicionar uma validação é editar seis
linhas de configuração. O motor valida cada expressão com `sqlglot` antes de
executar.

**Pydantic no parser.** O XML da SEFAZ tem estrutura variável — o grupo de ICMS
muda de nome conforme a tributação. Validar na fronteira evita que dado
malformado se propague até a apuração.

**Confiança ponderada por similaridade no classificador.** O `predict_proba` do
KNN é relativo aos vizinhos: retorna 1.0 mesmo para entradas que não se parecem
com nada conhecido. A ponderação pela distância ao vizinho mais próximo evita
roteamento automático de casos fora da distribuição.

## Testes

`pytest` valida o **código**; a comparação com `gabarito.json` valida a
**detecção**. O gerador registra exatamente quais notas receberam cada erro, e
os testes verificam correspondência exata — zero falso negativo, zero falso
positivo.

## Dados

Todos os XMLs são **sintéticos**, gerados por `src/gerar_massa.py` com erros
propositais. Nenhum dado real de contribuinte é utilizado ou versionado.

## Licença

MIT
