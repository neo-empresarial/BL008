# BL008 — Simulador de Rebalanceamento (MVP)

MVP em Streamlit pra comparar o comportamento real de rebalanceamento entre
plataformas de cestas on-chain (index baskets): **Glider**, **Reserve
Protocol** e **QuantAMM/Balancer**. As três estão implementadas e plugadas
num seletor de plataforma que troca qual adapter é usado, mantendo o mesmo
layout pra facilitar a comparação lado a lado.

## O que o MVP faz

- Deixa escolher a plataforma (Glider / Reserve Protocol / QuantAMM-Balancer)
  e um basket de exemplo de cada uma (mais um campo pra colar outro
  `basket_id` manualmente).
- Mostra a alocação-alvo atual (pizza + tabela, peso por ativo) com um
  **slider por ativo pra simular "e se eu pesasse diferente"** — ex: mudar a
  Mag7 de equal-weight (100/7 cada) pra qualquer outra concentração. Os
  sliders começam nos pesos reais e são normalizados pra somar 100%; um
  botão reseta pros pesos reais.
- Mostra o **histórico de rebalanceamento** — o log real de quando e como
  cada basket mudou de peso. A natureza desse log é bem diferente em cada
  plataforma (ver "Sobre cada plataforma" abaixo):
  - Glider: `changeLog` textual por versão.
  - Reserve: um Dutch auction por `Rebalance` (nonce, tx hash), aprovado
    antes via governança.
  - QuantAMM: snapshots de peso amostrados de uma curva contínua guiada por
    sinal de ML — sem decisão humana registrada.
- Mostra a curva de performance (retorno acumulado x data): a curva **Real**
  (método/fonte nativo de cada plataforma, claramente rotulado — não são
  comparáveis diretamente entre plataformas) e uma curva **Simulada**, que
  recombina o preço histórico de cada ativo (via DefiLlama) pelos pesos que
  você ajustou nos sliders. Ativo sem preço histórico disponível é excluído
  da simulação e listado como tal — nunca inventado.
- Mostra TVL quando disponível: por-estratégia na Glider (via discovery);
  agregado por protocolo inteiro via DefiLlama no Reserve e no QuantAMM
  (essas duas não expõem TVL por basket individual nas fontes públicas
  usadas aqui).
- Card comparável entre as três plataformas: **quem decide** um
  rebalanceamento (governança / chave de API do provider / sinal de ML
  autônomo) e a **frequência de rebalanceamento** estimada (eventos/mês) —
  essa é a métrica que de fato dá pra comparar lado a lado, diferente da
  curva de preço ou dos ativos subjacentes (ações tokenizadas vs. cripto vs.
  pools de cripto).

## O que falta (fora do escopo deste MVP)

- Composição e histórico de **Yield DTFs** do Reserve (ex: eUSD) — o
  subgraph público só cobre Index DTFs; Yield DTFs exigiriam leitura RPC
  direta do contrato (`BackingManager`), não implementada aqui. O adapter
  levanta um erro claro em vez de simular esse dado (ver
  `adapters/reserve.py`).
- Vínculo direto entre um `Rebalance` do Reserve e a proposta de governança
  que o aprovou (não existe FK entre as duas entidades no subgraph público
  — ver TODO em `adapters/reserve.py`).
- TVL por basket individual no Reserve e no QuantAMM (só temos TVL agregado
  do protocolo via DefiLlama).
- Dropdown de exploração livre pelo discovery da Glider (`GET
  /discovery/strategies`) — o adapter já suporta (`glider.discover_strategies`),
  só não tem um seletor próprio na UI ainda.
- Qualquer operação de escrita (criar estratégia, enroll, withdraw, votar) —
  este app é só leitura, nas três plataformas.

## Como rodar

1. Python 3.10+ recomendado.
2. Crie um virtualenv e instale as dependências:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copie `.env.example` para `.env` e cole sua API key da Glider (só ela
   precisa de key — Reserve e QuantAMM usam fontes públicas sem autenticação):

   ```bash
   cp .env.example .env
   # edite .env e preencha GLIDER_API_KEY=...
   ```

   Peça uma key em <https://console.glidercloud.dev/>. Sem a key, a
   plataforma Glider mostra um `st.error` claro explicando o que falta —
   não quebra com traceback, e as outras duas plataformas continuam
   funcionando normalmente.

4. Rode:

   ```bash
   streamlit run app.py
   ```

## Baskets de exemplo

- **Glider** — Mag7 (`01KV68M2Y685X59DWAEVX5D5X3`), Nancy Pelosi Tracker
  (`01KWJ0Q9GXP1HBN2Z53RCHCHQW`).
- **Reserve Protocol** — OPEN, Index DTF em mainnet
  (`mainnet:0x323c03c48660fe31186fa82c289b0766d331ce21`); LCAP, Index DTF em
  base (`base:0x4dA9A0f397dB1397902070f93a4D6ddBC0E0E6e8`); eUSD, Yield DTF
  em mainnet (`mainnet:0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F` —
  composição indisponível neste MVP, ver acima).
- **QuantAMM/Balancer** — Safe Haven BTC:PAXG:USDC em mainnet
  (`MAINNET:0x6b61d8680c4f9e560c8306807908553f95c749c5`).

## Sobre cada plataforma

### Glider

- Base URL: `https://api.glider.fi/v2`. Doc:
  `https://api.glider.fi/v2/llms.txt` / `https://docs.glider.fi/llms.txt`.
  OpenAPI: `https://api.glider.fi/v2/openapi.json`.
- Autenticação via header `x-api-key` (env var `GLIDER_API_KEY`).
- Envelope `{"success": true, "data": {...}}` ou `{"success": false, "error": {...}}`
  — o adapter trata os dois casos e levanta `GliderAPIError` com mensagem
  legível.
- Valores monetários e pesos vêm como strings decimais (ex: `"60.00"`) —
  convertidos com `Decimal`/`float()`, nunca com aritmética direta em string.
- `allocation.weight` é uma escala 0–100.

### Reserve Protocol

- Não tem uma API B2B pronta como a da Glider. O frontend oficial
  (`reserve-protocol/register`) consome dois públicos e sem key:
  - Subgraphs Goldsky (compatíveis com The Graph), um por chain
    (mainnet/base/bsc), indexando o contrato Folio de cada Index DTF —
    entidade `Rebalance` é o log real de rebalanceamento (endpoint e schema
    confirmados nos repositórios `reserve-protocol/register` e
    `reserve-protocol/dtf-index-subgraph`).
  - `https://api.llama.fi` (DefiLlama) — TVL do protocolo e preço histórico
    por token (`coins.llama.fi/chart`), usado aqui como proxy de
    performance.
- Pesos vêm como quantidade-alvo bruta on-chain (`weightSpotLimit`), não uma
  % pronta — normalizamos pela soma pra aproximar peso relativo por
  quantidade (não por valor em USD). Ver caveats completos em
  `adapters/reserve.py`.
- Rebalanceamento é via leilão holandês, aprovado antes por governança —
  mas não há vínculo direto (FK) entre `Rebalance` e a proposta que o
  aprovou no schema público.

### QuantAMM / Balancer

- Pools QuantAMM são pools nativos do Balancer v3 (tipo
  `QUANT_AMM_WEIGHTED`) — sem API própria separada.
- Fonte: API GraphQL pública e sem key da Balancer
  (`https://api-v3.balancer.fi/graphql`, confirmada por introspecção).
  `poolTokens[].weight` dá o peso atual; `weightSnapshots` dá uma série
  temporal real de pesos (amostrada de hora em hora) — é o que vira o
  "histórico de rebalanceamento"; `poolGetSnapshots.sharePrice` vira a curva
  de performance.
- 100% programático: cada mudança de peso é resultado de um sinal de ML
  aplicado automaticamente on-chain, sem aprovação humana — por isso cada
  entrada do histórico é rotulada `"signal-driven"`, sem fingir que houve
  uma decisão registrada como no Reserve.

## Estrutura

```
BL008/
  app.py                  # Streamlit entrypoint — seletor de plataforma
  adapters/
    __init__.py
    glider.py              # API da Glider (implementado)
    reserve.py             # Subgraph Goldsky + DefiLlama (implementado)
    quantamm.py             # API GraphQL da Balancer (implementado)
    pricing.py             # preço histórico por ativo (DefiLlama), usado no simulador de pesos
  .env.example
  requirements.txt
  README.md
```

Cada adapter expõe a mesma interface comum — `get_current_allocation`,
`get_rebalance_history`, `get_performance`, além de `EXAMPLE_BASKETS` e
`DECISION_MAKER` — pra permitir o app.py tratar as três plataformas de
forma genérica (ver docstring de cada arquivo em `adapters/` pros detalhes e
as aproximações/limitações específicas de cada fonte de dado).
