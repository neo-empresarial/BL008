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


# DefiLlama chain slug -> block explorer "token" page template. Shared by all
# three adapters to turn a price_ref into a link for the UI's asset metadata
# (see `display_asset`/`explorer_url` in each adapter's get_current_allocation).
DEFILLAMA_TO_EXPLORER = {
    "ethereum": "https://etherscan.io/token/{address}",
    "optimism": "https://optimistic.etherscan.io/token/{address}",
    "bsc": "https://bscscan.com/token/{address}",
    "xdai": "https://gnosisscan.io/token/{address}",
    "polygon": "https://polygonscan.com/token/{address}",
    "fantom": "https://ftmscan.com/token/{address}",
    "base": "https://basescan.org/token/{address}",
    "arbitrum": "https://arbiscan.io/token/{address}",
    "avax": "https://snowtrace.io/token/{address}",
    "linea": "https://lineascan.build/token/{address}",
    "blast": "https://blastscan.io/token/{address}",
}


def price_ref_to_explorer_url(price_ref: str | None) -> str | None:
    """Turns a "<chain>:<address>" price_ref into a block explorer URL for
    that token. Returns None when price_ref is None or its chain has no
    known explorer template above — the caller simply omits the link."""
    if not price_ref or ":" not in price_ref:
        return None
    chain, address = price_ref.split(":", 1)
    template = DEFILLAMA_TO_EXPLORER.get(chain)
    return template.format(address=address) if template else None


def resolve_token_symbol(price_ref: str | None) -> str | None:
    """Best-effort token symbol lookup for a "<chain>:<address>" price_ref,
    via DefiLlama's current-price endpoint (`coins.llama.fi/prices/current`
    — same source family as get_price_history, so no new provider is
    introduced). Returns None on any network failure, missing entry, or
    missing symbol — callers must fall back to a safe display value (e.g. a
    truncated address) rather than inventing a name.
    """
    if not price_ref:
        return None
    try:
        response = requests.get(
            f"https://coins.llama.fi/prices/current/{price_ref}",
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError):
        return None
    symbol = ((body.get("coins") or {}).get(price_ref) or {}).get("symbol")
    return symbol or None
