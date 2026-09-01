"""
Adapter para QuantAMM (pools "Safe Haven" e afins, implantados como pools
Balancer v3 com peso dinâmico guiado por sinal de ML).

Interface comum aos três adapters (ver também adapters/glider.py e
adapters/reserve.py — todos retornam exatamente estas formas):

- get_current_allocation(basket_id) -> list[{"asset": str, "weight_pct": float}]
- get_rebalance_history(basket_id) -> list[{"date": str ISO, "description": str,
    "weights_after": list[{"asset","weight_pct"}] | None}]
- get_performance(basket_id) -> {"method": str, "points": [{"date", "percent_change"}]}

Nenhuma função aqui usa Streamlit — cache fica por conta de quem chama (ver
app.py, `st.cache_data`).

Pesquisa feita:

- `docs.balancer.fi` confirma que pools QuantAMM são pools nativos do
  Balancer v3 (tipo `QUANT_AMM_WEIGHTED`) — não existe uma API própria do
  QuantAMM separada da Balancer.
- A Balancer expõe uma API GraphQL pública e sem key em
  `https://api-v3.balancer.fi/graphql` (confirmado por introspecção). Ela
  serve o site oficial da Balancer.
- `poolGetPool(id, chain)` com o fragment `GqlPoolQuantAmmWeighted` traz
  `poolTokens[].weight` (peso ATUAL, fração 0-1) e, o mais importante pra
  esse projeto, `weightSnapshots { timestamp, weights }` — uma série
  temporal real de pesos por token, amostrada de hora em hora
  (confirmado: pool Safe Haven-BTC:PAXG:USDC em mainnet tinha ~168
  snapshots pra ~7 dias). É esse campo que usamos como
  **get_rebalance_history**.
- `poolGetSnapshots(id, chain, range)` traz `sharePrice` (valor da cota do
  LP token) — usamos como proxy de performance (mesma lógica de "preço de
  cota" que o adapter do Reserve usa para o token do DTF).
- TVL: `dynamicData.totalLiquidity` da própria Balancer já é por-pool e mais
  preciso que qualquer fonte agregada. Documentamos também o TVL do
  protocolo Balancer v3 inteiro via DefiLlama (slug `balancer-v3`) como
  cruzamento — mas o DefiLlama NÃO quebra TVL por pool individual, então
  esse número é só uma referência de ordem de grandeza, não específico do
  Safe Haven.
- Diferente do Reserve (governança) e da Glider (API key de provider), o
  QuantAMM roda 100% programático — cada snapshot de peso é resultado de um
  sinal de ML aplicado automaticamente on-chain, sem aprovação humana por
  rebalance. Deixamos isso explícito em cada `description` do histórico
  (`"signal-driven"`), pra não passar a impressão de que houve uma decisão
  registrada como no Reserve.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

BALANCER_API_URL = "https://api-v3.balancer.fi/graphql"
DEFILLAMA_PROTOCOL_SLUG = "balancer-v3"  # TVL do protocolo inteiro, não por pool
DEFAULT_TIMEOUT = 15

# Peso mínimo de mudança (em pontos percentuais, no maior ativo da pool)
# pra considerar um snapshot como um "evento" de rebalanceamento visível.
# Sem esse filtro, toda hora vira uma linha na tabela (o peso muda um
# pouco sempre, é contínuo) — isso aqui é só uma escolha de exibição, os
# dados brutos (todos os snapshots) continuam vindo da API sem filtro.
MIN_WEIGHT_SHIFT_PCT = 1.0

# basket_id = "<chain>:<endereço da pool>" (chain no formato do enum GqlChain
# da Balancer, ex: MAINNET, BASE, SONIC).
EXAMPLE_BASKETS = {
    "Safe Haven — BTC:PAXG:USDC (mainnet)": "MAINNET:0x6b61d8680c4f9e560c8306807908553f95c749c5",
}

# Chain (enum GqlChain da Balancer) -> slug de chain usado pela DefiLlama.
# Só as chains onde os dois nomes divergem/são conhecidos — uma chain fora
# desse mapa só faz os ativos dela ficarem sem price_ref (excluídos da
# simulação de performance no app.py, sem quebrar nada).
CHAIN_TO_DEFILLAMA = {
    "MAINNET": "ethereum",
    "ARBITRUM": "arbitrum",
    "AVALANCHE": "avax",
    "BASE": "base",
    "FANTOM": "fantom",
    "GNOSIS": "xdai",
    "OPTIMISM": "optimism",
    "POLYGON": "polygon",
    "SONIC": "sonic",
    "ZKEVM": "polygon_zkevm",
}

# Quem decide um rebalanceamento no QuantAMM — pro card comparativo no app.py.
DECISION_MAKER = (
    "100% programático — sinal de ML aplicado automaticamente on-chain a "
    "cada bloco/época, sem aprovação de governança nem chave de provider."
)


class QuantAMMAPIError(Exception):
    """Erro legível pra mostrar na UI (rede, GraphQL, ou schema inesperado)."""


def _parse_basket_id(basket_id: str) -> tuple[str, str]:
    if ":" not in basket_id:
        raise QuantAMMAPIError(
            f"basket_id inválido: '{basket_id}' — formato esperado é '<chain>:<endereço>' "
            "(ex: 'MAINNET:0x...')."
        )
    chain, address = basket_id.split(":", 1)
    return chain.upper(), address


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.post(
            BALANCER_API_URL,
            json={"query": query, "variables": variables},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise QuantAMMAPIError(f"Falha de rede ao consultar a API da Balancer: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise QuantAMMAPIError(
            f"Resposta inválida (não é JSON) da Balancer — status {response.status_code}"
        ) from exc

    if not response.ok or "errors" in body:
        raise QuantAMMAPIError(f"Erro na API da Balancer: {body.get('errors', response.status_code)}")

    return body.get("data") or {}


_POOL_QUERY = """
query GetQuantAmmPool($id: String!, $chain: GqlChain!) {
  pool: poolGetPool(id: $id, chain: $chain) {
    name
    dynamicData { totalLiquidity }
    ... on GqlPoolQuantAmmWeighted {
      poolTokens { address symbol weight }
      weightSnapshots { timestamp weights }
    }
  }
}
"""


def _fetch_pool(chain: str, address: str) -> dict[str, Any]:
    data = _graphql(_POOL_QUERY, {"id": address, "chain": chain})
    pool = data.get("pool")
    if not pool:
        raise QuantAMMAPIError(f"Pool '{address}' não encontrada na chain {chain}.")
    return pool


def get_current_allocation(basket_id: str) -> list[dict[str, Any]]:
    """Peso atual de cada ativo na pool (poolTokens[].weight, fração 0-1).

    Inclui `price_ref` (pra simulação de "e se eu pesasse diferente" no
    app.py) quando a chain está em CHAIN_TO_DEFILLAMA — ver adapters/pricing.py.
    """
    chain, address = _parse_basket_id(basket_id)
    pool = _fetch_pool(chain, address)
    tokens = pool.get("poolTokens") or []
    if not tokens:
        raise QuantAMMAPIError("Pool sem poolTokens retornados pela API.")
    prefix = CHAIN_TO_DEFILLAMA.get(chain)
    return [
        {
            "asset": t.get("symbol"),
            "weight_pct": (float(t["weight"]) * 100.0),
            "price_ref": f"{prefix}:{t['address']}" if prefix and t.get("address") else None,
        }
        for t in tokens
        if t.get("weight") is not None
    ]


def get_rebalance_history(basket_id: str) -> list[dict[str, Any]]:
    """Histórico de mudanças de peso, a partir dos weightSnapshots da pool.

    Cada linha aqui é um snapshot em que o(s) peso(s) mudou(aram) mais do
    que MIN_WEIGHT_SHIFT_PCT em relação ao snapshot anterior — não é uma
    decisão registrada como no Reserve, é uma reamostragem de uma curva
    contínua guiada por sinal de ML. Isso fica explícito em `description`.
    """
    chain, address = _parse_basket_id(basket_id)
    pool = _fetch_pool(chain, address)
    tokens = pool.get("poolTokens") or []
    symbols = [t.get("symbol") for t in tokens]
    snapshots = pool.get("weightSnapshots") or []
    if not snapshots:
        raise QuantAMMAPIError("Pool sem weightSnapshots retornados pela API.")

    snapshots = sorted(snapshots, key=lambda s: s["timestamp"])

    history: list[dict[str, Any]] = []
    previous_weights: list[float] | None = None

    for snap in snapshots:
        weights = [w * 100.0 for w in (snap.get("weights") or [])]
        if previous_weights is not None:
            max_shift = max(abs(w - p) for w, p in zip(weights, previous_weights))
            if max_shift < MIN_WEIGHT_SHIFT_PCT:
                continue  # muda pouco demais pra virar uma linha no log

        weights_after = [
            {"asset": symbol, "weight_pct": weight} for symbol, weight in zip(symbols, weights)
        ]

        if previous_weights is None:
            description = "Composição inicial observada — signal-driven (QuantAMM)"
        else:
            shifts = ", ".join(
                f"{symbol} {p:.1f}%→{w:.1f}%"
                for symbol, p, w in zip(symbols, previous_weights, weights)
                if abs(w - p) >= MIN_WEIGHT_SHIFT_PCT
            )
            description = f"Weight shift: {shifts}, signal-driven"

        history.append(
            {
                "date": datetime.fromtimestamp(snap["timestamp"], tz=timezone.utc).isoformat(),
                "description": description,
                "weights_after": weights_after,
            }
        )
        previous_weights = weights

    return history


_SNAPSHOTS_QUERY = """
query GetQuantAmmSnapshots($id: String!, $chain: GqlChain!, $range: GqlPoolSnapshotDataRange!) {
  snapshots: poolGetSnapshots(id: $id, chain: $chain, range: $range) {
    timestamp
    sharePrice
  }
}
"""


def get_performance(basket_id: str) -> dict[str, Any]:
    """Curva de performance via preço de cota (sharePrice) da própria pool.

    A Balancer não expõe TWR/MWR prontos pra pools QuantAMM. `sharePrice`
    (valor do LP token em USD) já é, por natureza, uma métrica por-cota —
    metodologicamente parecida com TWR — mas não é rotulada oficialmente
    como tal, por isso o `method` abaixo descreve a fonte em vez de
    reivindicar "TWR"/"MWR".
    """
    chain, address = _parse_basket_id(basket_id)
    data = _graphql(
        _SNAPSHOTS_QUERY, {"id": address, "chain": chain, "range": "NINETY_DAYS"}
    )
    snapshots = data.get("snapshots") or []
    if not snapshots:
        raise QuantAMMAPIError("Sem snapshots de performance pra essa pool.")

    snapshots = sorted(snapshots, key=lambda s: s["timestamp"])
    base_price = float(snapshots[0]["sharePrice"])

    points = [
        {
            "date": datetime.fromtimestamp(s["timestamp"], tz=timezone.utc).isoformat(),
            "percent_change": ((float(s["sharePrice"]) / base_price) - 1) * 100.0
            if base_price
            else None,
        }
        for s in snapshots
    ]

    return {"method": "Preço de cota da pool (sharePrice, via Balancer)", "points": points}


def get_tvl_usd_defillama() -> float | None:
    """TVL do protocolo Balancer v3 inteiro, via DefiLlama — cruzamento de
    ordem de grandeza, NÃO é o TVL específico da pool (DefiLlama não separa
    por pool QuantAMM)."""
    try:
        response = requests.get(
            f"https://api.llama.fi/tvl/{DEFILLAMA_PROTOCOL_SLUG}", timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        return float(response.json())
    except (requests.RequestException, ValueError, TypeError):
        return None
