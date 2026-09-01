"""
Adapter para o Reserve Protocol (Index DTFs).

Interface comum aos três adapters (ver também adapters/glider.py e
adapters/quantamm.py — todos retornam exatamente estas formas):

- get_current_allocation(basket_id) -> list[{"asset": str, "weight_pct": float}]
- get_rebalance_history(basket_id) -> list[{"date": str ISO, "description": str,
    "weights_after": list[{"asset","weight_pct"}] | None}]
- get_performance(basket_id) -> {"method": str, "points": [{"date", "percent_change"}]}

Nenhuma função aqui usa Streamlit — cache fica por conta de quem chama (ver
app.py, `st.cache_data`).

Pesquisa feita (fontes reais, não documentação formal — o Reserve não expõe
uma API B2B como a da Glider):

- `https://docs.reserve.org/sitemap.md` mapeia só documentação conceitual
  (rebalancing, governança, fees) — nenhuma página de API/subgraph pública.
- O frontend oficial (`reserve-protocol/register` no GitHub) consome dados
  de duas fontes públicas e sem API key:
  1. Subgraphs Goldsky (compatíveis com The Graph), um por chain, indexando
     o contrato Folio de cada Index DTF. Endpoint e nomes de rede
     confirmados em `e2e/helpers/registry.ts` desse repo:
     `https://api.goldsky.com/api/public/project_cmgzim3e100095np2gjnbh6ry/subgraphs/dtf-index-{mainnet,base,bsc}/prod/gn`.
     O schema (`schema.graphql` em `reserve-protocol/dtf-index-subgraph`)
     confirma a entidade `Rebalance { nonce, dtf, tokens, weightSpotLimit,
     timestamp, transactionHash }` — é o **log real de rebalanceamento**
     (um Dutch auction por rebalance, aprovado antes via governança).
  2. `https://api.llama.fi` (DefiLlama) — TVL do protocolo agregado
     (`reserve-protocol`) e preço histórico por token via
     `https://coins.llama.fi/chart/{chain}:{address}`, usado aqui como fonte
     de performance (ver get_performance).

- **Yield DTFs** (ex: eUSD) NÃO têm composição/rebalanceamento nesse mesmo
  subgraph — o próprio e2e suite do Reserve lê o estado de Yield DTFs via
  chamadas RPC diretas ao contrato (BackingManager), não via GraphQL.
  Implementar isso exigiria decodificar ABI on-chain, fora do escopo deste
  MVP — por isso `get_current_allocation`/`get_rebalance_history` levantam
  `ReserveAPIError` explicando a limitação para baskets do tipo "yield", em
  vez de inventar um formato de resposta.

- weightSpotLimit é a quantidade-alvo por unidade de basket (BigInt em
  ponto fixo on-chain), não uma % pronta. Aqui normalizamos pela soma dos
  pesos de cada rebalance pra aproximar peso relativo por **quantidade**
  (não por valor em USD, que exigiria multiplicar por preço de cada ativo —
  não fazemos essa conversão, para não arriscar produzir um número errado).

- Não existe, nesse subgraph, um vínculo direto (chave estrangeira) entre um
  `Rebalance` e a proposta de governança que o aprovou — só existe
  `DTF.ownerGovernance.proposals` com texto livre. Por isso `description` no
  histórico de rebalanceamento não cita a proposta específica; é um TODO
  documentado abaixo.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import requests

GOLDSKY_BASE = (
    "https://api.goldsky.com/api/public/project_cmgzim3e100095np2gjnbh6ry/subgraphs"
)

# Subgraphs de Index DTF por chain — públicos, sem API key.
INDEX_SUBGRAPH_URLS = {
    "mainnet": f"{GOLDSKY_BASE}/dtf-index-mainnet/prod/gn",
    "base": f"{GOLDSKY_BASE}/dtf-index-base/prod/gn",
    "bsc": f"{GOLDSKY_BASE}/dtf-index-bsc/prod/gn",
}

# DefiLlama usa esses mesmos nomes de chain como prefixo em coins.llama.fi.
DEFILLAMA_CHAIN_PREFIX = {"mainnet": "ethereum", "base": "base", "bsc": "bsc"}

DEFILLAMA_PROTOCOL_SLUG = "reserve-protocol"
DEFAULT_TIMEOUT = 15

# basket_id = "<chain>:<endereço do DTF>". Duas Index DTFs reais (compostas
# pelo mesmo subgraph) + uma Yield DTF real (eUSD) só pra deixar visível na
# UI a limitação de dado descrita acima — ver get_current_allocation.
EXAMPLE_BASKETS = {
    "OPEN — Index DTF (mainnet)": "mainnet:0x323c03c48660fe31186fa82c289b0766d331ce21",
    "LCAP — Index DTF (base)": "base:0x4dA9A0f397dB1397902070f93a4D6ddBC0E0E6e8",
    "eUSD — Yield DTF (mainnet, composição indisponível)": (
        "mainnet:0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F"
    ),
}

# Quem decide um rebalanceamento no Reserve — pro card comparativo no app.py.
DECISION_MAKER = (
    "Governança on-chain (votação com stToken) aprova os limites de cada "
    "rebalance; a execução é via leilão holandês (Dutch auction), não por "
    "uma API key de provider como na Glider."
)


class ReserveAPIError(Exception):
    """Erro legível pra mostrar na UI (rede, GraphQL, ou limitação de dado conhecida)."""


def _parse_basket_id(basket_id: str) -> tuple[str, str]:
    if ":" not in basket_id:
        raise ReserveAPIError(
            f"basket_id inválido: '{basket_id}' — formato esperado é '<chain>:<endereço>' "
            "(ex: 'mainnet:0x...')."
        )
    chain_key, address = basket_id.split(":", 1)
    if chain_key not in INDEX_SUBGRAPH_URLS:
        raise ReserveAPIError(
            f"Chain '{chain_key}' não suportada. Use uma de: "
            f"{', '.join(INDEX_SUBGRAPH_URLS)}."
        )
    return chain_key, address.lower()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _graphql(url: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.post(
            url, json={"query": query, "variables": variables}, timeout=DEFAULT_TIMEOUT
        )
    except requests.RequestException as exc:
        raise ReserveAPIError(f"Falha de rede ao consultar subgraph: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise ReserveAPIError(
            f"Resposta inválida (não é JSON) do subgraph — status {response.status_code}"
        ) from exc

    if not response.ok or "errors" in body:
        message = body.get("errors") if isinstance(body, dict) else None
        raise ReserveAPIError(f"Erro no subgraph do Reserve: {message or response.status_code}")

    return body.get("data") or {}


_REBALANCES_QUERY = """
query GetRebalances($id: String!, $first: Int!) {
  rebalances(where: {dtf: $id}, orderBy: nonce, orderDirection: asc, first: $first) {
    nonce
    timestamp
    transactionHash
    tokens { address symbol decimals }
    weightSpotLimit
  }
}
"""


def _fetch_rebalances(chain_key: str, address: str) -> list[dict[str, Any]]:
    url = INDEX_SUBGRAPH_URLS[chain_key]
    data = _graphql(url, _REBALANCES_QUERY, {"id": address, "first": 200})
    return data.get("rebalances") or []


def _weights_from_rebalance(rebalance: dict[str, Any], chain_key: str) -> list[dict[str, Any]]:
    """Normaliza weightSpotLimit (quantidade-alvo bruta) em % relativo por token.

    Aproximação por QUANTIDADE, não por valor em USD — ver docstring do módulo.
    Inclui `price_ref` (chain:endereço no formato DefiLlama) pra viabilizar a
    simulação de "e se eu pesasse diferente" no app.py — ver adapters/pricing.py.
    """
    tokens = rebalance.get("tokens") or []
    raw_weights = [_to_float(w) or 0.0 for w in (rebalance.get("weightSpotLimit") or [])]
    prefix = DEFILLAMA_CHAIN_PREFIX.get(chain_key)

    def price_ref(token: dict[str, Any]) -> str | None:
        address = token.get("address")
        return f"{prefix}:{address}" if prefix and address else None

    total = sum(raw_weights)
    if total <= 0:
        return [
            {"asset": t.get("symbol") or t.get("address"), "weight_pct": 0.0, "price_ref": price_ref(t)}
            for t in tokens
        ]
    return [
        {
            "asset": token.get("symbol") or token.get("address"),
            "weight_pct": (weight / total) * 100.0,
            "price_ref": price_ref(token),
        }
        for token, weight in zip(tokens, raw_weights)
    ]


def get_current_allocation(basket_id: str) -> list[dict[str, Any]]:
    """Alocação-alvo atual, aproximada pelo rebalance mais recente.

    Não existe, no subgraph público, um endpoint dedicado de "basket atual"
    — usamos os pesos-alvo (weightSpotLimit) do último `Rebalance` como
    proxy, já que é o estado que a última auction convergiu pra ele. Pra um
    valor exato seria necessário ler o contrato on-chain (`Folio.basket()`).
    """
    chain_key, address = _parse_basket_id(basket_id)
    rebalances = _fetch_rebalances(chain_key, address)
    if not rebalances:
        raise ReserveAPIError(
            "Nenhum rebalance encontrado pra esse DTF nesse subgraph — se for "
            "uma Yield DTF, a composição não é exposta por essa via pública "
            "(ver docstring do módulo)."
        )
    return _weights_from_rebalance(rebalances[-1], chain_key)


def get_rebalance_history(basket_id: str) -> list[dict[str, Any]]:
    """Histórico real de rebalanceamentos (um Dutch auction por rebalance).

    TODO: sem endpoint de eventos que já ligue rebalance -> proposta de
    governança que o aprovou (não há FK entre `Rebalance` e `Proposal` no
    schema público). Pra cruzar manualmente seria preciso comparar
    `Rebalance.timestamp` com `DTF.ownerGovernance.proposals[].executionTime`
    (query `getGovernanceStats`, não implementada aqui).
    """
    chain_key, address = _parse_basket_id(basket_id)
    rebalances = _fetch_rebalances(chain_key, address)
    if not rebalances:
        raise ReserveAPIError(
            "Nenhum rebalance encontrado pra esse DTF nesse subgraph — se for "
            "uma Yield DTF, o histórico de rebalanceamento não é exposto por "
            "essa via pública (ver docstring do módulo)."
        )

    history = []
    for rebalance in rebalances:
        num_assets = len(rebalance.get("tokens") or [])
        tx = rebalance.get("transactionHash")
        history.append(
            {
                "date": _timestamp_to_iso(rebalance.get("timestamp")),
                "description": (
                    f"Rebalance nonce {rebalance.get('nonce')} — leilão holandês, "
                    f"{num_assets} ativos"
                    + (f" (tx {tx[:10]}…)" if tx else "")
                ),
                "weights_after": _weights_from_rebalance(rebalance, chain_key),
            }
        )
    return history


def _timestamp_to_iso(timestamp: Any) -> str | None:
    from datetime import datetime, timezone

    ts = _to_float(timestamp)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def get_performance(basket_id: str) -> dict[str, Any]:
    """Curva de performance aproximada via preço histórico do token (DefiLlama).

    O Reserve não expõe um endpoint de performance como o da Glider (TWR/MWR
    prontos). Aproximamos usando o preço de mercado do token do DTF ao longo
    do tempo (`coins.llama.fi/chart`) — como o token representa uma cota do
    basket, sua variação de preço se aproxima de um retorno TWR (não é um
    cálculo formal, e não tenta capturar fluxos de mint/redeem por conta).
    """
    chain_key, address = _parse_basket_id(basket_id)
    prefix = DEFILLAMA_CHAIN_PREFIX[chain_key]
    url = f"https://coins.llama.fi/chart/{prefix}:{address}"

    try:
        response = requests.get(url, params={"span": 180, "period": "1d"}, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ReserveAPIError(f"Falha ao buscar preço histórico no DefiLlama: {exc}") from exc

    coin_key = f"{prefix}:{address}"
    prices = ((body.get("coins") or {}).get(coin_key) or {}).get("prices") or []
    if not prices:
        raise ReserveAPIError(
            "DefiLlama não tem preço histórico pra esse token — sem dado de "
            "performance disponível pra esse DTF."
        )

    base_price = prices[0]["price"]
    points = [
        {
            "date": _timestamp_to_iso(p["timestamp"]),
            "percent_change": ((p["price"] / base_price) - 1) * 100.0 if base_price else None,
        }
        for p in prices
    ]

    return {"method": "Preço do token (proxy de NAV, via DefiLlama)", "points": points}


def get_tvl_usd_defillama() -> float | None:
    """TVL agregado do protocolo Reserve inteiro, via DefiLlama — cruzamento,
    não TVL por DTF individual (DefiLlama não quebra por DTF)."""
    try:
        response = requests.get(
            f"https://api.llama.fi/tvl/{DEFILLAMA_PROTOCOL_SLUG}", timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        return _to_float(response.json())
    except (requests.RequestException, ValueError):
        return None
