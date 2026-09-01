"""
Adapter for QuantAMM ("Safe Haven" and related pools, deployed as Balancer
v3 pools with dynamic ML-signal-driven weights).

Interface common to the three adapters (see also adapters/glider.py and
adapters/reserve.py — all return exactly these shapes):

- get_current_allocation(basket_id) -> list[{"asset": str, "weight_pct": float}]
- get_rebalance_history(basket_id) -> list[{"date": str ISO, "description": str,
    "weights_after": list[{"asset","weight_pct"}] | None}]
- get_performance(basket_id) -> {"method": str, "points": [{"date", "percent_change"}]}

No function here uses Streamlit — caching is the caller's responsibility (see
app.py, `st.cache_data`).

Research done:

- `docs.balancer.fi` confirms QuantAMM pools are native Balancer v3 pools
  (type `QUANT_AMM_WEIGHTED`) — there's no separate QuantAMM API apart from
  Balancer's.
- Balancer exposes a public, no-key GraphQL API at
  `https://api-v3.balancer.fi/graphql` (confirmed by introspection). It
  serves Balancer's official site.
- `poolGetPool(id, chain)` with the `GqlPoolQuantAmmWeighted` fragment
  returns `poolTokens[].weight` (CURRENT weight, 0-1 fraction) and, most
  importantly for this project, `weightSnapshots { timestamp, weights }` —
  a real time series of weights per token, sampled hourly (confirmed: the
  Safe Haven-BTC:PAXG:USDC pool on mainnet had ~168 snapshots for ~7 days).
  This is the field we use as **get_rebalance_history**.
- `poolGetSnapshots(id, chain, range)` returns `sharePrice` (the LP token's
  value) — used as a performance proxy (same "share price" logic the
  Reserve adapter uses for the DTF token).
- TVL: Balancer's own `dynamicData.totalLiquidity` is already per-pool and
  more precise than any aggregated source. We also document the entire
  Balancer v3 protocol's TVL via DefiLlama (slug `balancer-v3`) as a
  cross-check — but DefiLlama does NOT break TVL down by individual pool,
  so that number is only an order-of-magnitude reference, not specific to
  Safe Haven.
- Unlike Reserve (governance) and Glider (provider API key), QuantAMM runs
  100% programmatically — each weight snapshot is the result of an ML
  signal applied automatically on-chain, with no human approval per
  rebalance. We make this explicit in each history `description`
  (`"signal-driven"`), so as not to give the impression a decision was
  recorded like on Reserve.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

BALANCER_API_URL = "https://api-v3.balancer.fi/graphql"
DEFILLAMA_PROTOCOL_SLUG = "balancer-v3"  # TVL for the entire protocol, not per pool
DEFAULT_TIMEOUT = 15

# Minimum weight change (in percentage points, on the pool's largest asset)
# to consider a snapshot a visible rebalance "event". Without this filter,
# every hour would become a table row (the weight always changes a bit,
# it's continuous) — this is purely a display choice, the raw data (every
# snapshot) still comes from the API unfiltered.
MIN_WEIGHT_SHIFT_PCT = 1.0

# basket_id = "<chain>:<pool address>" (chain in Balancer's GqlChain enum
# format, e.g. MAINNET, BASE, SONIC).
EXAMPLE_BASKETS = {
    "Safe Haven — BTC:PAXG:USDC (mainnet)": "MAINNET:0x6b61d8680c4f9e560c8306807908553f95c749c5",
}

# Chain (Balancer's GqlChain enum) -> chain slug used by DefiLlama. Only the
# chains where the two names diverge/are known — a chain outside this map
# just leaves its assets without a price_ref (excluded from the performance
# simulation in app.py, without breaking anything).
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

# Who decides a rebalance on QuantAMM — for the comparative card in app.py.
DECISION_MAKER = (
    "100% programmatic — an ML signal applied automatically on-chain every "
    "block/epoch, with no governance approval nor provider key."
)


class QuantAMMAPIError(Exception):
    """Readable error to show in the UI (network, GraphQL, or unexpected schema)."""


def _parse_basket_id(basket_id: str) -> tuple[str, str]:
    if ":" not in basket_id:
        raise QuantAMMAPIError(
            f"Invalid basket_id: '{basket_id}' — expected format is '<chain>:<address>' "
            "(e.g. 'MAINNET:0x...')."
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
        raise QuantAMMAPIError(f"Network failure querying the Balancer API: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise QuantAMMAPIError(
            f"Invalid response (not JSON) from Balancer — status {response.status_code}"
        ) from exc

    if not response.ok or "errors" in body:
        raise QuantAMMAPIError(f"Error in the Balancer API: {body.get('errors', response.status_code)}")

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
        raise QuantAMMAPIError(f"Pool '{address}' not found on chain {chain}.")
    return pool


def get_current_allocation(basket_id: str) -> list[dict[str, Any]]:
    """Current weight of each asset in the pool (poolTokens[].weight, 0-1 fraction).

    Includes `price_ref` (for the "what if I weighted it differently"
    simulation in app.py) when the chain is in CHAIN_TO_DEFILLAMA — see
    adapters/pricing.py.
    """
    chain, address = _parse_basket_id(basket_id)
    pool = _fetch_pool(chain, address)
    tokens = pool.get("poolTokens") or []
    if not tokens:
        raise QuantAMMAPIError("Pool returned no poolTokens from the API.")
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
    """Weight change history, from the pool's weightSnapshots.

    Each row here is a snapshot where the weight(s) changed by more than
    MIN_WEIGHT_SHIFT_PCT relative to the previous snapshot — it's not a
    recorded decision like on Reserve, it's a resampling of a continuous
    curve driven by an ML signal. This is made explicit in `description`.
    """
    chain, address = _parse_basket_id(basket_id)
    pool = _fetch_pool(chain, address)
    tokens = pool.get("poolTokens") or []
    symbols = [t.get("symbol") for t in tokens]
    snapshots = pool.get("weightSnapshots") or []
    if not snapshots:
        raise QuantAMMAPIError("Pool returned no weightSnapshots from the API.")

    snapshots = sorted(snapshots, key=lambda s: s["timestamp"])

    history: list[dict[str, Any]] = []
    previous_weights: list[float] | None = None

    for snap in snapshots:
        weights = [w * 100.0 for w in (snap.get("weights") or [])]
        if previous_weights is not None:
            max_shift = max(abs(w - p) for w, p in zip(weights, previous_weights))
            if max_shift < MIN_WEIGHT_SHIFT_PCT:
                continue  # changes too little to become a log row

        weights_after = [
            {"asset": symbol, "weight_pct": weight} for symbol, weight in zip(symbols, weights)
        ]

        if previous_weights is None:
            description = "Initial composition observed — signal-driven (QuantAMM)"
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
    """Performance curve via the pool's own share price (sharePrice).

    Balancer doesn't expose ready-made TWR/MWR for QuantAMM pools.
    `sharePrice` (the LP token's value in USD) is, by nature, already a
    per-share metric — methodologically similar to TWR — but it isn't
    officially labeled as such, which is why `method` below describes the
    source rather than claiming "TWR"/"MWR".
    """
    chain, address = _parse_basket_id(basket_id)
    data = _graphql(
        _SNAPSHOTS_QUERY, {"id": address, "chain": chain, "range": "NINETY_DAYS"}
    )
    snapshots = data.get("snapshots") or []
    if not snapshots:
        raise QuantAMMAPIError("No performance snapshots for this pool.")

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

    return {"method": "Pool share price (sharePrice, via Balancer)", "points": points}


def get_tvl_usd_defillama() -> float | None:
    """TVL for the entire Balancer v3 protocol, via DefiLlama — an
    order-of-magnitude cross-check, NOT the pool-specific TVL (DefiLlama
    doesn't break it down by QuantAMM pool)."""
    try:
        response = requests.get(
            f"https://api.llama.fi/tvl/{DEFILLAMA_PROTOCOL_SLUG}", timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        return float(response.json())
    except (requests.RequestException, ValueError, TypeError):
        return None
