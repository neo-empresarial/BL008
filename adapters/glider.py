"""
Adapter for the Glider B2B API v2 (https://api.glider.fi/v2).

"Unified" interface this adapter exposes (to match reserve.py and
quantamm.py in the future, and allow comparing the three platforms side by
side):

- get_target_allocation(strategy_id) -> list[{"asset": str, "weight": float}]
    weight on a 0-100 scale.
- get_version_history(strategy_id) -> list[dict] (chronological order, oldest first)
    each item: {"version", "date", "change_log", "num_assets", "weight_changed", "assets"}
- get_performance(strategy_id) -> {"method": "TWR"|"MWR", "points": [{"date", "percent_change"}]}
- discover_strategies(collection) -> list[dict]
    each item: {"strategy_id", "name", "tvl_usd", "portfolio_count", "assets"}
- discover_baskets() -> list[dict]
    each item: {"basket_id", "name", "tvl_usd", "chain"} — shared shape with
    reserve.py/quantamm.py's own discover_baskets, thin wrapper over the above

All functions raise GliderAPIError with a readable message on failure
(network, HTTP, or {"success": false, ...} envelope). No function here uses
Streamlit — caching is the caller's responsibility (see app.py).

Also, to enable the side-by-side comparative view (app.py switches the
adapter based on the chosen platform), this module also exposes the
interface common to the three adapters — get_current_allocation,
get_rebalance_history — as thin wrappers on top of the functions above (see
end of file). adapters/reserve.py and adapters/quantamm.py implement the
same interface directly.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from . import pricing

BASE_URL = "https://api.glider.fi/v2"
DEFAULT_TIMEOUT = 15  # seconds

# example strategy_id for the dropdown (same two as the original prompt).
EXAMPLE_BASKETS = {
    "Mag7 (Ondo/Bitwise, equal weight)": "01KV68M2Y685X59DWAEVX5D5X3",
    "Nancy Pelosi Tracker": "01KWJ0Q9GXP1HBN2Z53RCHCHQW",
}

# Who decides a rebalance on Glider — for the comparative card in app.py.
DECISION_MAKER = (
    "Unilateral by the strategy provider via API key — no on-chain "
    "governance nor public ML signal involved."
)


class GliderAPIError(Exception):
    """Readable error to show in the UI (network, HTTP, or success=false)."""


def _get_api_key() -> str:
    api_key = os.environ.get("GLIDER_API_KEY", "").strip()
    if not api_key:
        raise GliderAPIError(
            "GLIDER_API_KEY not configured. Create a .env file (see "
            ".env.example) with your Glider key — request one at "
            "https://console.glidercloud.dev/."
        )
    return api_key


def _to_float(value: Any) -> float | None:
    """Carefully converts a decimal-string from the API (e.g. '60.00') to float."""
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _request(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Authenticated GET on BASE_URL + path. Returns the already-parsed body.

    Raises GliderAPIError on any network failure, HTTP failure, invalid
    JSON, or {"success": false, "error": {...}} envelope.
    """
    api_key = _get_api_key()
    url = f"{BASE_URL}{path}"

    try:
        response = requests.get(
            url,
            headers={"x-api-key": api_key},
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GliderAPIError(f"Network failure calling {path}: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise GliderAPIError(
            f"Invalid response (not JSON) from {path} — status {response.status_code}"
        ) from exc

    if not response.ok:
        error = body.get("error") if isinstance(body, dict) else None
        message = (error or {}).get("message") if error else None
        raise GliderAPIError(
            message or f"HTTP error {response.status_code} calling {path}"
        )

    if not body.get("success"):
        error = body.get("error") or {}
        raise GliderAPIError(error.get("message", f"Unknown error in {path}"))

    return body


def _parse_assets(allocation: dict[str, Any] | None) -> list[dict[str, Any]]:
    assets = (allocation or {}).get("assets") or []
    parsed = []
    for asset in assets:
        parsed.append(
            {
                "asset": asset.get("assetId"),
                "weight": _to_float(asset.get("weight")),
            }
        )
    return parsed


def discover_strategies(
    collection: str = "curated",
    sort: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """GET /v2/discovery/strategies — to populate an exploration dropdown."""
    params: dict[str, Any] = {"collection": collection, "limit": limit}
    if sort:
        params["sort"] = sort

    strategies: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        if cursor:
            params["cursor"] = cursor
        body = _request("/discovery/strategies", params=params)
        data = body.get("data") or {}

        for item in data.get("strategies") or []:
            metrics = item.get("metrics") or {}
            strategies.append(
                {
                    "strategy_id": item.get("strategyId"),
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "tvl_usd": _to_float(metrics.get("tvlUsd")),
                    "portfolio_count": metrics.get("portfolioCount"),
                    "assets": _parse_assets(item.get("allocation")),
                }
            )

        cursor = body.get("nextCursor")
        if not cursor:
            break

    return strategies


def get_strategy_detail(strategy_id: str) -> dict[str, Any]:
    """GET /v2/strategies/{strategyId} — name, description, target allocation, schedule."""
    body = _request(f"/strategies/{strategy_id}")
    data = body.get("data") or {}
    return {
        "strategy_id": data.get("strategyId"),
        "name": data.get("name"),
        "description": data.get("description"),
        "schedule": data.get("schedule"),
        "assets": _parse_assets(data.get("allocation")),
    }


def get_target_allocation(strategy_id: str) -> list[dict[str, Any]]:
    """Current target allocation: list[{"asset", "weight"}], weight in 0-100."""
    return get_strategy_detail(strategy_id)["assets"]


def get_version_history(strategy_id: str) -> list[dict[str, Any]]:
    """GET /v2/strategies/{strategyId}/versions, paginating via nextCursor.

    Returns in chronological order (oldest first) and already computes, for
    each version, whether any asset's weight changed relative to the
    previous version — this is the "rebalance log" the app shows in a table.
    """
    raw_versions: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        body = _request(f"/strategies/{strategy_id}/versions", params=params)
        data = body.get("data") or {}
        raw_versions.extend(data.get("versions") or [])
        cursor = body.get("nextCursor")
        if not cursor:
            break

    # sort by ascending version number (oldest first)
    raw_versions.sort(key=lambda v: v.get("version", 0))

    history: list[dict[str, Any]] = []
    previous_weights: dict[str, float] | None = None

    for version in raw_versions:
        assets = _parse_assets(version.get("allocation"))
        weights = {a["asset"]: a["weight"] for a in assets if a["asset"] is not None}

        if previous_weights is None:
            weight_changed = False  # first known version: there's no "before"
        else:
            weight_changed = weights != previous_weights

        history.append(
            {
                "version": version.get("version"),
                "date": version.get("createdAt"),
                "change_log": version.get("changeLog"),
                "is_head": version.get("isHead", False),
                "num_assets": len(assets),
                "weight_changed": weight_changed,
                "assets": assets,
            }
        )
        previous_weights = weights

    return history


def get_performance(strategy_id: str) -> dict[str, Any]:
    """GET /v2/strategies/{strategyId}/performance — accumulated return curve.

    method is "TWR" (time-weighted) or "MWR" (money-weighted) — different
    calculations, which is why we show which one was used in the chart legend.
    """
    body = _request(f"/strategies/{strategy_id}/performance")
    data = body.get("data") or {}
    meta = data.get("meta") or {}

    points = [
        {
            "date": point.get("date"),
            "percent_change": _to_float(point.get("percentChange")),
        }
        for point in data.get("points") or []
    ]

    return {
        "method": meta.get("method"),
        "currency": meta.get("currency"),
        "points": points,
    }


# --- interface common to the three adapters (see module docstring) --------


def discover_baskets() -> list[dict[str, Any]]:
    """Lists every strategy in the "curated" collection, in the format
    shared with reserve.py/quantamm.py's own `discover_baskets`. Thin
    wrapper over `discover_strategies`, which already paginates fully via
    `nextCursor` (see its own docstring) — the default page size (`limit`
    left unset) is used because the API rejects `limit` above 50 with a
    validation error (confirmed empirically); pagination still covers every
    page regardless of page size.

    Scope: `collection="curated"` only, same as the TVL lookup in app.py —
    Glider's API isn't documented to support other collection names, so we
    don't guess at one.

    Returns list[{"basket_id", "name", "tvl_usd", "chain"}], sorted by TVL
    descending (ties/missing TVL fall back to name). `chain` is always None
    — Glider strategies aren't chain-scoped the way Reserve/QuantAMM
    baskets are.
    """
    rows = [
        {
            "basket_id": s["strategy_id"],
            "name": s.get("name") or s["strategy_id"],
            "tvl_usd": s.get("tvl_usd"),
            "chain": None,
        }
        for s in discover_strategies(collection="curated")
        if s.get("strategy_id")
    ]
    return sorted(rows, key=lambda r: (-(r["tvl_usd"] or 0.0), r["name"]))


def _short_address(address: str) -> str:
    """Truncates a hex address to '0x1234…abcd' — used as a display fallback
    when no symbol is available, so the UI never shows a raw CAIP-19 string."""
    if len(address) <= 10:
        return address
    return f"{address[:6]}…{address[-4:]}"


def _display_asset(asset_id: str | None, price_ref: str | None) -> str:
    """Best-effort human-readable label for a CAIP-19 assetId.

    The Glider API doesn't return a token symbol alongside assetId, so we
    try resolving one from `price_ref` via `pricing.resolve_token_symbol`
    (DefiLlama) first. When that fails (no price_ref, network error, or the
    source has no symbol for it) we fall back to a truncated address rather
    than showing the full 'eip155:1/erc20:0x...' string as the primary
    label — never a guessed or invented name.
    """
    symbol = pricing.resolve_token_symbol(price_ref)
    if symbol:
        return symbol
    if not asset_id:
        return "—"
    if "/" in asset_id:
        _, _, reference = asset_id.rpartition(":")
        return _short_address(reference)
    return asset_id


def get_current_allocation(strategy_id: str) -> list[dict[str, Any]]:
    """Thin wrapper over get_target_allocation, in the format common to the adapters.

    Includes `price_ref` (for the "what if I weighted it differently"
    simulation in app.py) when the CAIP-19 assetId is an erc20 on a known
    EVM chain — see adapters/pricing.py. Assets outside that case (e.g.
    non-EVM) are left with price_ref None and app.py simply excludes them
    from the simulation.

    Also includes optional display metadata (`display_asset`, `token_address`,
    `chain`, `explorer_url`) derived from the same CAIP-19 assetId, so the UI
    can show something more readable than the raw identifier — `display_asset`
    prefers a resolved token symbol (see `_display_asset`) and only falls
    back to a truncated address when no symbol could be resolved. `asset`
    itself is unchanged (still the raw assetId) for backward compatibility
    with code that keys off it.
    """
    rows = []
    for a in get_target_allocation(strategy_id):
        price_ref = pricing.caip19_to_price_ref(a["asset"])
        chain, _, token_address = price_ref.partition(":") if price_ref else (None, None, None)
        rows.append(
            {
                "asset": a["asset"],
                "weight_pct": a["weight"],
                "price_ref": price_ref,
                "display_asset": _display_asset(a["asset"], price_ref),
                "token_address": token_address or None,
                "chain": chain,
                "explorer_url": pricing.price_ref_to_explorer_url(price_ref),
            }
        )
    return rows


def get_rebalance_history(strategy_id: str) -> list[dict[str, Any]]:
    """Thin wrapper over get_version_history, in the format common to the adapters."""
    history = []
    for version in get_version_history(strategy_id):
        if version["change_log"]:
            description = version["change_log"]
        else:
            description = f"Version {version['version']}" + (
                " — weight changed" if version["weight_changed"] else " — no weight change"
            )
        history.append(
            {
                "date": version["date"],
                "description": description,
                "weights_after": [
                    {"asset": a["asset"], "weight_pct": a["weight"]} for a in version["assets"]
                ],
            }
        )
    return history
