"""
Adapter for Reserve Protocol (Index DTFs).

Interface common to the three adapters (see also adapters/glider.py and
adapters/quantamm.py — all return exactly these shapes):

- get_current_allocation(basket_id) -> list[{"asset": str, "weight_pct": float}]
- get_rebalance_history(basket_id) -> list[{"date": str ISO, "description": str,
    "weights_after": list[{"asset","weight_pct"}] | None}]
- get_performance(basket_id) -> {"method": str, "points": [{"date", "percent_change"}]}
- discover_baskets() -> list[{"basket_id", "name", "tvl_usd", "chain"}]

No function here uses Streamlit — caching is the caller's responsibility (see
app.py, `st.cache_data`).

Data sources (public, no API key):

1. `https://api.reserve.org` — official Reserve API:
   - `GET /discover/dtfs` for the active Index DTF catalog (`discover_baskets`)
     and as a basket-weight fallback when no rebalance events exist.
   - `GET /dtf/rebalance` for the rebalance event list (same path the
     official CLI/`fetchRebalanceHistory` use); optional `&nonce=N` for
     detail when Goldsky has no `weightSpotLimit` for that nonce.
   - `GET /historical/dtf` for a NAV-style price series when DefiLlama has
     no chart for the DTF token.

2. Goldsky Index DTF subgraphs (The Graph-compatible), one per chain —
   used as a best-effort weight source (`weightSpotLimit`) and as a
   fallback event list if the Reserve API returns no rebalances. Endpoints:
   `https://api.goldsky.com/api/public/project_cmgzim3e100095np2gjnbh6ry/subgraphs/dtf-index-{mainnet,base,bsc}/prod/gn`.

3. DefiLlama (`coins.llama.fi/chart`) — primary performance proxy via the
   DTF token's market price.

- **Yield DTFs** (e.g. eUSD) are not in the Index discovery catalog and have
  no Index rebalance/basket path here. Manual entry raises `ReserveAPIError`
  when composition cannot be resolved — we do not invent weights.

- Empty rebalance history is a valid Index DTF state (young baskets with no
  `startRebalance` yet). `get_rebalance_history` returns `[]` in that case;
  allocation then comes from discover `basket[].weight` (0–100).

- Goldsky `weightSpotLimit` is a target quantity per basket unit (on-chain
  fixed-point), not a ready-made %. We normalize by the sum to approximate
  relative weight by **quantity**. When weights come from rebalance detail
  (`basketUnits` × `prices`), they are approximated by **USD value** instead.

- There's no direct link between a rebalance and the governance proposal
  that approved it in the public surfaces used here — `description` does
  not cite a proposal id.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from . import pricing

GOLDSKY_BASE = (
    "https://api.goldsky.com/api/public/project_cmgzim3e100095np2gjnbh6ry/subgraphs"
)

# Index DTF subgraphs per chain — public, no API key.
INDEX_SUBGRAPH_URLS = {
    "mainnet": f"{GOLDSKY_BASE}/dtf-index-mainnet/prod/gn",
    "base": f"{GOLDSKY_BASE}/dtf-index-base/prod/gn",
    "bsc": f"{GOLDSKY_BASE}/dtf-index-bsc/prod/gn",
}

RESERVE_API_BASE = "https://api.reserve.org"

# Chain ids as returned by the Reserve discovery API, mapped to the chain
# keys the rest of this adapter (Goldsky subgraphs, DefiLlama) understands.
CHAIN_ID_TO_KEY = {1: "mainnet", 8453: "base", 56: "bsc"}
KEY_TO_CHAIN_ID = {v: k for k, v in CHAIN_ID_TO_KEY.items()}

# DefiLlama uses these same chain names as a prefix in coins.llama.fi.
DEFILLAMA_CHAIN_PREFIX = {"mainnet": "ethereum", "base": "base", "bsc": "bsc"}

DEFILLAMA_PROTOCOL_SLUG = "reserve-protocol"
DEFAULT_TIMEOUT = 15
PERFORMANCE_LOOKBACK_DAYS = 180

# basket_id = "<chain>:<DTF address>". Two real Index DTFs + one real Yield
# DTF (eUSD) to make the composition-unavailable limitation visible in the UI.
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


def _nonce_key(nonce: Any) -> str:
    if nonce is None:
        return ""
    try:
        return str(int(nonce))
    except (TypeError, ValueError):
        return str(nonce)


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


def _fetch_rebalances_goldsky(chain_key: str, address: str) -> list[dict[str, Any]]:
    """Best-effort Goldsky read — empty list on failure, never raises."""
    try:
        return _fetch_rebalances(chain_key, address)
    except ReserveAPIError:
        return []


def _reserve_get(path: str) -> Any:
    url = f"{RESERVE_API_BASE}{path}"
    try:
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise ReserveAPIError(f"Network failure querying Reserve API: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise ReserveAPIError(
            f"Invalid response (not JSON) from Reserve API — status {response.status_code}"
        ) from exc

    if not response.ok:
        raise ReserveAPIError(f"Error in Reserve API: {body if body else response.status_code}")

    return body


def _fetch_rebalances_api(chain_key: str, address: str) -> list[dict[str, Any]]:
    """Official rebalance list (`/dtf/rebalance`), ascending by nonce."""
    chain_id = KEY_TO_CHAIN_ID[chain_key]
    path = (
        f"/dtf/rebalance?address={address.lower()}"
        f"&chainId={chain_id}&skip=0&limit=200"
    )
    body = _reserve_get(path) or []
    if not isinstance(body, list):
        return []
    return sorted(body, key=lambda r: int(r.get("nonce") or 0))


def _fetch_rebalance_detail_api(
    chain_key: str, address: str, nonce: Any
) -> dict[str, Any] | None:
    chain_id = KEY_TO_CHAIN_ID[chain_key]
    path = (
        f"/dtf/rebalance?address={address.lower()}"
        f"&chainId={chain_id}&nonce={_nonce_key(nonce)}"
    )
    try:
        body = _reserve_get(path)
    except ReserveAPIError:
        return None
    if isinstance(body, list):
        return body[0] if body else None
    if isinstance(body, dict):
        return body
    return None


def discover_baskets() -> list[dict[str, Any]]:
    """Lists Reserve's official active Index DTFs, via
    `GET /discover/dtfs` — the same catalog the Reserve site itself shows.
    Only `type == "index"` and `status == "active"` rows are kept, and only
    on chains the rest of this adapter supports (mainnet, base, bsc); Yield
    DTFs like eUSD are excluded here even though `EXAMPLE_BASKETS` pins one
    for illustration.

    Returns list[{"basket_id", "name", "tvl_usd", "chain"}], sorted by
    descending `tvl_usd` then by `name` — matching the Reserve site
    ordering. `tvl_usd` comes from the API's `marketCap`.
    """
    dtfs = _reserve_get("/discover/dtfs") or []
    rows: list[dict[str, Any]] = []
    for dtf in dtfs:
        if dtf.get("type") != "index" or dtf.get("status") != "active":
            continue
        chain_key = CHAIN_ID_TO_KEY.get(dtf.get("chainId"))
        if chain_key is None:
            continue
        address = dtf.get("address")
        name = dtf.get("symbol") or dtf.get("name") or address
        rows.append(
            {
                "basket_id": f"{chain_key}:{address.lower()}",
                "name": name,
                "tvl_usd": _to_float(dtf.get("marketCap")),
                "chain": chain_key,
            }
        )
    return sorted(rows, key=lambda r: (-(r["tvl_usd"] or 0.0), r["name"]))


def _asset_row(token: dict[str, Any], weight_pct: float, chain_key: str) -> dict[str, Any]:
    """Builds one allocation row, including display metadata (`display_asset`,
    `token_address`, `chain`, `explorer_url`) derived from the token's
    address — see adapters/pricing.py. `asset` keeps its existing fallback
    (symbol, or raw address when no symbol) for backward compatibility.
    """
    address = token.get("address")
    prefix = DEFILLAMA_CHAIN_PREFIX.get(chain_key)
    price_ref = f"{prefix}:{address}" if prefix and address else None
    asset = token.get("symbol") or address
    return {
        "asset": asset,
        "weight_pct": weight_pct,
        "price_ref": price_ref,
        "display_asset": asset,
        "token_address": address,
        "chain": prefix,
        "explorer_url": pricing.price_ref_to_explorer_url(price_ref),
    }


def _weights_from_rebalance(rebalance: dict[str, Any], chain_key: str) -> list[dict[str, Any]]:
    """Normalizes weightSpotLimit (raw target quantity) into a relative % per token.

    Approximation by QUANTITY, not by USD value — see module docstring.
    Includes `price_ref` (chain:address in DefiLlama format) to enable the
    "what if I weighted it differently" simulation in app.py — see
    adapters/pricing.py.
    """
    tokens = rebalance.get("tokens") or []
    raw_weights = [_to_float(w) or 0.0 for w in (rebalance.get("weightSpotLimit") or [])]

    total = sum(raw_weights)
    if total <= 0:
        return [_asset_row(t, 0.0, chain_key) for t in tokens]
    return [
        _asset_row(token, (weight / total) * 100.0, chain_key)
        for token, weight in zip(tokens, raw_weights)
    ]


def _weights_from_detail(detail: dict[str, Any], chain_key: str) -> list[dict[str, Any]]:
    """USD-approximate weights from rebalance detail (units × proposal prices)."""
    tokens = detail.get("tokens") or []
    token_by_addr = {
        (t.get("address") or "").lower(): t for t in tokens if t.get("address")
    }
    units = ((detail.get("basketUnits") or {}).get("proposal") or {}).get("units") or []
    prices = ((detail.get("prices") or {}).get("proposal") or {}).get("prices") or {}
    prices_l = {str(k).lower(): _to_float(v) or 0.0 for k, v in prices.items()}

    valued: list[tuple[dict[str, Any], float]] = []
    for entry in units:
        addr = (entry.get("address") or "").lower()
        if not addr:
            continue
        token = token_by_addr.get(addr) or {"address": addr, "symbol": None, "decimals": 18}
        decimals = int(token.get("decimals") or 18)
        raw_units = _to_float(entry.get("units")) or 0.0
        amount = raw_units / (10**decimals)
        usd = amount * prices_l.get(addr, 0.0)
        valued.append((token, usd))

    total = sum(v for _, v in valued)
    if total <= 0:
        if tokens:
            return [_asset_row(t, 0.0, chain_key) for t in tokens]
        return []
    return [_asset_row(token, (usd / total) * 100.0, chain_key) for token, usd in valued]


def _weights_for_event(
    event: dict[str, Any],
    chain_key: str,
    address: str,
    gs_by_nonce: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    nk = _nonce_key(event.get("nonce"))
    gs = gs_by_nonce.get(nk)
    if gs and (gs.get("weightSpotLimit") or []):
        merged = {
            **event,
            "tokens": gs.get("tokens") or event.get("tokens") or [],
            "weightSpotLimit": gs.get("weightSpotLimit"),
        }
        return _weights_from_rebalance(merged, chain_key)

    detail = _fetch_rebalance_detail_api(chain_key, address, event.get("nonce"))
    if detail:
        weights = _weights_from_detail(detail, chain_key)
        if weights:
            return weights

    if gs:
        return _weights_from_rebalance(gs, chain_key)
    return []


def _fetch_rebalances_merged(chain_key: str, address: str) -> list[dict[str, Any]]:
    """Canonical rebalance events: Reserve API list, Goldsky weights by nonce.

    Prefer the API event list (can be ahead of Goldsky). If the API returns
    nothing but Goldsky has rows, use Goldsky as the event list. Each row
    carries `_weights` for callers.
    """
    api_list = _fetch_rebalances_api(chain_key, address)
    gs_list = _fetch_rebalances_goldsky(chain_key, address)
    gs_by_nonce = {_nonce_key(r.get("nonce")): r for r in gs_list}

    events = gs_list if (not api_list and gs_list) else api_list
    enriched: list[dict[str, Any]] = []
    for event in events:
        row = dict(event)
        row["_weights"] = _weights_for_event(event, chain_key, address, gs_by_nonce)
        enriched.append(row)
    return enriched


def _allocation_from_discover(chain_key: str, address: str) -> list[dict[str, Any]]:
    """Current basket weights from discover `basket[].weight` (already 0–100)."""
    chain_id = KEY_TO_CHAIN_ID[chain_key]
    dtfs = _reserve_get("/discover/dtfs") or []
    target = address.lower()
    match = None
    for dtf in dtfs:
        if dtf.get("type") != "index":
            continue
        if (dtf.get("address") or "").lower() != target:
            continue
        if dtf.get("chainId") != chain_id:
            continue
        match = dtf
        break
    if not match:
        return []

    rows: list[dict[str, Any]] = []
    for token in match.get("basket") or []:
        weight = _to_float(token.get("weight"))
        if weight is None:
            continue
        rows.append(_asset_row(token, weight, chain_key))
    return rows


def get_current_allocation(basket_id: str) -> list[dict[str, Any]]:
    """Current target allocation for an Index DTF.

    Prefer weights from the latest rebalance (Goldsky quantity weights or
    API detail USD weights). If there are no rebalances yet, fall back to
    discover `basket[].weight`. Yield DTFs / unknown addresses raise.
    """
    chain_key, address = _parse_basket_id(basket_id)
    rebalances = _fetch_rebalances_merged(chain_key, address)
    if rebalances:
        weights = rebalances[-1].get("_weights") or []
        if weights:
            return weights

    discover_weights = _allocation_from_discover(chain_key, address)
    if discover_weights:
        return discover_weights

    raise ReserveAPIError(
        "No Index DTF composition found for this basket — Yield DTFs and "
        "unlisted addresses are not exposed through the Index discovery / "
        "rebalance routes used here."
    )


def get_rebalance_history(basket_id: str) -> list[dict[str, Any]]:
    """Real rebalance history (one Dutch auction per rebalance).

    Returns an empty list when the DTF has never rebalanced — that is a
    valid Index DTF state, not an error. TODO: no public FK linking a
    rebalance to the governance proposal that approved it.
    """
    chain_key, address = _parse_basket_id(basket_id)
    rebalances = _fetch_rebalances_merged(chain_key, address)

    history = []
    for rebalance in rebalances:
        weights = rebalance.get("_weights") or []
        num_assets = len(rebalance.get("tokens") or []) or len(weights)
        tx = rebalance.get("transactionHash")
        history.append(
            {
                "date": _timestamp_to_iso(rebalance.get("timestamp")),
                "description": (
                    f"Rebalance nonce {rebalance.get('nonce')} — Dutch auction, "
                    f"{num_assets} assets"
                    + (f" (tx {tx[:10]}…)" if tx else "")
                ),
                "weights_after": weights,
            }
        )
    return history


def _timestamp_to_iso(timestamp: Any) -> str | None:
    from datetime import datetime, timezone

    ts = _to_float(timestamp)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _performance_from_defillama(chain_key: str, address: str) -> list[dict[str, Any]] | None:
    prefix = DEFILLAMA_CHAIN_PREFIX[chain_key]
    url = f"https://coins.llama.fi/chart/{prefix}:{address}"
    try:
        response = requests.get(
            url, params={"span": PERFORMANCE_LOOKBACK_DAYS, "period": "1d"}, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError):
        return None

    coin_key = f"{prefix}:{address}"
    prices = ((body.get("coins") or {}).get(coin_key) or {}).get("prices") or []
    if not prices:
        return None

    base_price = prices[0]["price"]
    return [
        {
            "date": _timestamp_to_iso(p["timestamp"]),
            "percent_change": ((p["price"] / base_price) - 1) * 100.0 if base_price else None,
        }
        for p in prices
    ]


def _performance_from_reserve_historical(
    chain_key: str, address: str
) -> list[dict[str, Any]] | None:
    chain_id = KEY_TO_CHAIN_ID[chain_key]
    to_ts = int(time.time())
    from_ts = to_ts - PERFORMANCE_LOOKBACK_DAYS * 86400
    path = (
        f"/historical/dtf?address={address.lower()}&chainId={chain_id}"
        f"&from={from_ts}&to={to_ts}&interval=1d"
    )
    try:
        body = _reserve_get(path)
    except ReserveAPIError:
        return None

    series = body.get("timeseries") if isinstance(body, dict) else None
    if not isinstance(series, list):
        return None

    priced = [p for p in series if _to_float(p.get("price")) is not None]
    if not priced:
        return None

    base_price = _to_float(priced[0].get("price")) or 0.0
    return [
        {
            "date": _timestamp_to_iso(p.get("timestamp")),
            "percent_change": (
                ((_to_float(p.get("price")) or 0.0) / base_price - 1) * 100.0
                if base_price
                else None
            ),
        }
        for p in priced
    ]


def get_performance(basket_id: str) -> dict[str, Any]:
    """Approximate performance via DTF token price (DefiLlama, then Reserve).

    Prefer DefiLlama's chart; if missing, use `GET /historical/dtf` price
    points. Both are NAV-style proxies, not formal TWR/MWR.
    """
    chain_key, address = _parse_basket_id(basket_id)

    points = _performance_from_defillama(chain_key, address)
    if points:
        return {"method": "Token price (NAV proxy, via DefiLlama)", "points": points}

    points = _performance_from_reserve_historical(chain_key, address)
    if points:
        return {
            "method": "Token price (NAV proxy, via Reserve historical API)",
            "points": points,
        }

    raise ReserveAPIError(
        "No historical price available for this DTF from DefiLlama or the "
        "Reserve historical API — no performance data."
    )


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
