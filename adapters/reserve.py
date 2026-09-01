"""
Adapter for Reserve Protocol (Index DTFs).

Interface common to the three adapters (see also adapters/glider.py and
adapters/quantamm.py — all return exactly these shapes):

- get_current_allocation(basket_id) -> list[{"asset": str, "weight_pct": float}]
- get_rebalance_history(basket_id) -> list[{"date": str ISO, "description": str,
    "weights_after": list[{"asset","weight_pct"}] | None}]
- get_performance(basket_id) -> {"method": str, "points": [{"date", "percent_change"}]}

No function here uses Streamlit — caching is the caller's responsibility (see
app.py, `st.cache_data`).

Research done (real sources, not formal documentation — Reserve doesn't
expose a B2B API like Glider's):

- `https://docs.reserve.org/sitemap.md` maps only conceptual documentation
  (rebalancing, governance, fees) — no public API/subgraph page.
- The official frontend (`reserve-protocol/register` on GitHub) consumes
  data from two public sources with no API key:
  1. Goldsky subgraphs (The Graph-compatible), one per chain, indexing
     the Folio contract of each Index DTF. Endpoint and network names
     confirmed in that repo's `e2e/helpers/registry.ts`:
     `https://api.goldsky.com/api/public/project_cmgzim3e100095np2gjnbh6ry/subgraphs/dtf-index-{mainnet,base,bsc}/prod/gn`.
     The schema (`schema.graphql` in `reserve-protocol/dtf-index-subgraph`)
     confirms the `Rebalance { nonce, dtf, tokens, weightSpotLimit,
     timestamp, transactionHash }` entity — this is the **real rebalance
     log** (one Dutch auction per rebalance, approved beforehand via
     governance).
  2. `https://api.llama.fi` (DefiLlama) — aggregated protocol TVL
     (`reserve-protocol`) and historical price per token via
     `https://coins.llama.fi/chart/{chain}:{address}`, used here as the
     performance source (see get_performance).

- **Yield DTFs** (e.g. eUSD) do NOT have composition/rebalancing in this
  same subgraph — Reserve's own e2e suite reads Yield DTF state via
  direct RPC calls to the contract (BackingManager), not via GraphQL.
  Implementing that would require decoding the on-chain ABI, out of scope
  for this MVP — so `get_current_allocation`/`get_rebalance_history` raise
  `ReserveAPIError` explaining the limitation for "yield"-type baskets,
  instead of inventing a response format.

- weightSpotLimit is the target quantity per basket unit (on-chain
  fixed-point BigInt), not a ready-made %. Here we normalize by the sum of
  weights per rebalance to approximate relative weight by **quantity**
  (not by USD value, which would require multiplying by each asset's
  price — we don't do that conversion, to avoid risking a wrong number).

- There's no direct link (foreign key) in this subgraph between a
  `Rebalance` and the governance proposal that approved it — only
  `DTF.ownerGovernance.proposals` with free-text. So `description` in the
  rebalance history doesn't cite the specific proposal; it's a documented
  TODO below.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import requests

GOLDSKY_BASE = (
    "https://api.goldsky.com/api/public/project_cmgzim3e100095np2gjnbh6ry/subgraphs"
)

# Index DTF subgraphs per chain — public, no API key.
INDEX_SUBGRAPH_URLS = {
    "mainnet": f"{GOLDSKY_BASE}/dtf-index-mainnet/prod/gn",
    "base": f"{GOLDSKY_BASE}/dtf-index-base/prod/gn",
    "bsc": f"{GOLDSKY_BASE}/dtf-index-bsc/prod/gn",
}

# DefiLlama uses these same chain names as a prefix in coins.llama.fi.
DEFILLAMA_CHAIN_PREFIX = {"mainnet": "ethereum", "base": "base", "bsc": "bsc"}

DEFILLAMA_PROTOCOL_SLUG = "reserve-protocol"
DEFAULT_TIMEOUT = 15

# basket_id = "<chain>:<DTF address>". Two real Index DTFs (composed via the
# same subgraph) + one real Yield DTF (eUSD) just to make the data
# limitation described above visible in the UI — see get_current_allocation.
EXAMPLE_BASKETS = {
    "OPEN — Index DTF (mainnet)": "mainnet:0x323c03c48660fe31186fa82c289b0766d331ce21",
    "LCAP — Index DTF (base)": "base:0x4dA9A0f397dB1397902070f93a4D6ddBC0E0E6e8",
    "eUSD — Yield DTF (mainnet, composition unavailable)": (
        "mainnet:0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F"
    ),
}

# Who decides a rebalance on Reserve — for the comparative card in app.py.
DECISION_MAKER = (
    "On-chain governance (stToken voting) approves each rebalance's limits; "
    "execution is via Dutch auction, not a provider API key like Glider's."
)


class ReserveAPIError(Exception):
    """Readable error to show in the UI (network, GraphQL, or a known data limitation)."""


def _parse_basket_id(basket_id: str) -> tuple[str, str]:
    if ":" not in basket_id:
        raise ReserveAPIError(
            f"Invalid basket_id: '{basket_id}' — expected format is '<chain>:<address>' "
            "(e.g. 'mainnet:0x...')."
        )
    chain_key, address = basket_id.split(":", 1)
    if chain_key not in INDEX_SUBGRAPH_URLS:
        raise ReserveAPIError(
            f"Chain '{chain_key}' not supported. Use one of: "
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
        raise ReserveAPIError(f"Network failure querying subgraph: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise ReserveAPIError(
            f"Invalid response (not JSON) from subgraph — status {response.status_code}"
        ) from exc

    if not response.ok or "errors" in body:
        message = body.get("errors") if isinstance(body, dict) else None
        raise ReserveAPIError(f"Error in Reserve subgraph: {message or response.status_code}")

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
    """Normalizes weightSpotLimit (raw target quantity) into a relative % per token.

    Approximation by QUANTITY, not by USD value — see module docstring.
    Includes `price_ref` (chain:address in DefiLlama format) to enable the
    "what if I weighted it differently" simulation in app.py — see
    adapters/pricing.py.
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
    """Current target allocation, approximated from the most recent rebalance.

    The public subgraph doesn't have a dedicated "current basket" endpoint
    — we use the target weights (weightSpotLimit) from the last `Rebalance`
    as a proxy, since that's the state the last auction converged to. For
    an exact value it would be necessary to read the on-chain contract
    (`Folio.basket()`).
    """
    chain_key, address = _parse_basket_id(basket_id)
    rebalances = _fetch_rebalances(chain_key, address)
    if not rebalances:
        raise ReserveAPIError(
            "No rebalance found for this DTF in this subgraph — if it's a "
            "Yield DTF, composition is not exposed through this public "
            "route (see module docstring)."
        )
    return _weights_from_rebalance(rebalances[-1], chain_key)


def get_rebalance_history(basket_id: str) -> list[dict[str, Any]]:
    """Real rebalance history (one Dutch auction per rebalance).

    TODO: there's no events endpoint that already links a rebalance to the
    governance proposal that approved it (no FK between `Rebalance` and
    `Proposal` in the public schema). To cross-reference manually you'd
    need to compare `Rebalance.timestamp` with
    `DTF.ownerGovernance.proposals[].executionTime` (query
    `getGovernanceStats`, not implemented here).
    """
    chain_key, address = _parse_basket_id(basket_id)
    rebalances = _fetch_rebalances(chain_key, address)
    if not rebalances:
        raise ReserveAPIError(
            "No rebalance found for this DTF in this subgraph — if it's a "
            "Yield DTF, rebalance history is not exposed through this "
            "public route (see module docstring)."
        )

    history = []
    for rebalance in rebalances:
        num_assets = len(rebalance.get("tokens") or [])
        tx = rebalance.get("transactionHash")
        history.append(
            {
                "date": _timestamp_to_iso(rebalance.get("timestamp")),
                "description": (
                    f"Rebalance nonce {rebalance.get('nonce')} — Dutch auction, "
                    f"{num_assets} assets"
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
    """Approximate performance curve via the token's historical price (DefiLlama).

    Reserve doesn't expose a ready-made performance endpoint like Glider's
    (TWR/MWR). We approximate using the DTF token's market price over time
    (`coins.llama.fi/chart`) — since the token represents a share of the
    basket, its price movement approximates a TWR return (not a formal
    calculation, and it doesn't try to capture per-account mint/redeem flows).
    """
    chain_key, address = _parse_basket_id(basket_id)
    prefix = DEFILLAMA_CHAIN_PREFIX[chain_key]
    url = f"https://coins.llama.fi/chart/{prefix}:{address}"

    try:
        response = requests.get(url, params={"span": 180, "period": "1d"}, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ReserveAPIError(f"Failed to fetch historical price from DefiLlama: {exc}") from exc

    coin_key = f"{prefix}:{address}"
    prices = ((body.get("coins") or {}).get(coin_key) or {}).get("prices") or []
    if not prices:
        raise ReserveAPIError(
            "DefiLlama has no historical price for this token — no "
            "performance data available for this DTF."
        )

    base_price = prices[0]["price"]
    points = [
        {
            "date": _timestamp_to_iso(p["timestamp"]),
            "percent_change": ((p["price"] / base_price) - 1) * 100.0 if base_price else None,
        }
        for p in prices
    ]

    return {"method": "Token price (NAV proxy, via DefiLlama)", "points": points}


def get_tvl_usd_defillama() -> float | None:
    """Aggregated TVL for the entire Reserve protocol, via DefiLlama — a
    cross-check, not TVL for an individual DTF (DefiLlama doesn't break it
    down by DTF)."""
    try:
        response = requests.get(
            f"https://api.llama.fi/tvl/{DEFILLAMA_PROTOCOL_SLUG}", timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        return _to_float(response.json())
    except (requests.RequestException, ValueError):
        return None
