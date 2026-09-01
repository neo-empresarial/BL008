"""
Helper shared by the three adapters to fetch historical price for an
INDIVIDUAL ASSET (not the aggregated basket) via DefiLlama (`coins.llama.fi`).

This feeds the "what if I weighted it differently" simulator in app.py: each
adapter, in get_current_allocation, already returns a `price_ref` per asset
when it can map it to a chain:address pair recognized by DefiLlama — app.py
uses that price_ref to recombine each asset's historical return with the
weights the user adjusts on the sliders.

`get_performance` on each adapter (the basket's REAL return) doesn't use
anything from here — it's a separate function, each with its own source (see
each adapter's docstring).
"""

from __future__ import annotations

from typing import Any

import requests

DEFAULT_TIMEOUT = 15


class PricingError(Exception):
    """Readable error — no historical price available for this price_ref."""


def get_price_history(price_ref: str, days: int = 180) -> list[dict[str, Any]]:
    """price_ref in the format DefiLlama expects: "<chain>:<address>"
    (e.g. "ethereum:0x...", "base:0x..."). Returns [{"timestamp", "price"}]."""
    try:
        response = requests.get(
            f"https://coins.llama.fi/chart/{price_ref}",
            params={"span": days, "period": "1d"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PricingError(f"Failed to fetch historical price for {price_ref}: {exc}") from exc

    prices = ((body.get("coins") or {}).get(price_ref) or {}).get("prices") or []
    if not prices:
        raise PricingError(f"DefiLlama has no historical price for {price_ref}.")
    return prices


# Chain id (EIP-155) -> chain slug used by DefiLlama. Only the most common
# chains — a CAIP-19 chain outside this list simply doesn't become a
# price_ref (the asset is left out of the simulation, without breaking anything).
EIP155_TO_DEFILLAMA = {
    1: "ethereum",
    10: "optimism",
    56: "bsc",
    100: "xdai",
    137: "polygon",
    250: "fantom",
    8453: "base",
    42161: "arbitrum",
    43114: "avax",
    59144: "linea",
    81457: "blast",
}


def caip19_to_price_ref(asset_id: str | None) -> str | None:
    """Converts an EVM CAIP-19 assetId (e.g. 'eip155:1/erc20:0xabc...') into
    a DefiLlama price_ref ('ethereum:0xabc...'). Returns None for any
    unrecognized format (non-EVM, native token via slip44, chain outside the
    map above) — in those cases the asset is left out of the performance
    simulation, without trying to guess a price.
    """
    if not asset_id or "/" not in asset_id:
        return None
    try:
        chain_part, asset_part = asset_id.split("/", 1)
        namespace, chain_ref = chain_part.split(":", 1)
        asset_namespace, asset_reference = asset_part.split(":", 1)
    except ValueError:
        return None

    if namespace != "eip155" or asset_namespace != "erc20":
        return None

    try:
        chain_id = int(chain_ref)
    except ValueError:
        return None

    slug = EIP155_TO_DEFILLAMA.get(chain_id)
    return f"{slug}:{asset_reference}" if slug else None
