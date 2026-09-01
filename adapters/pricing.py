"""
Helper compartilhado pelos três adapters pra buscar preço histórico de um
ATIVO INDIVIDUAL (não do basket agregado) via DefiLlama (`coins.llama.fi`).

Isso alimenta o simulador de "e se eu pesasse diferente" do app.py: cada
adapter, em get_current_allocation, já devolve um `price_ref` por ativo
quando consegue mapear pra um par chain:endereço reconhecido pela DefiLlama
— o app.py usa esse price_ref pra recombinar os retornos históricos de cada
ativo com os pesos que o usuário ajustar nos sliders.

`get_performance` de cada adapter (retorno REAL do basket) não usa nada
daqui — é uma função separada, cada uma com sua própria fonte (ver
docstring de cada adapter).
"""

from __future__ import annotations

from typing import Any

import requests

DEFAULT_TIMEOUT = 15


class PricingError(Exception):
    """Erro legível — sem preço histórico disponível pra esse price_ref."""


def get_price_history(price_ref: str, days: int = 180) -> list[dict[str, Any]]:
    """price_ref no formato que a DefiLlama espera: "<chain>:<endereço>"
    (ex: "ethereum:0x...", "base:0x..."). Retorna [{"timestamp", "price"}]."""
    try:
        response = requests.get(
            f"https://coins.llama.fi/chart/{price_ref}",
            params={"span": days, "period": "1d"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PricingError(f"Falha ao buscar preço histórico de {price_ref}: {exc}") from exc

    prices = ((body.get("coins") or {}).get(price_ref) or {}).get("prices") or []
    if not prices:
        raise PricingError(f"DefiLlama não tem preço histórico pra {price_ref}.")
    return prices


# Chain id (EIP-155) -> slug de chain usado pela DefiLlama. Só as chains mais
# comuns — CAIP-19 de chain fora dessa lista simplesmente não vira price_ref
# (o ativo fica de fora da simulação, sem quebrar nada).
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
    """Converte um assetId CAIP-19 EVM (ex: 'eip155:1/erc20:0xabc...') em
    price_ref pra DefiLlama ('ethereum:0xabc...'). Retorna None pra qualquer
    formato não reconhecido (não-EVM, token nativo via slip44, chain fora do
    mapa acima) — nesses casos o ativo fica de fora da simulação de
    performance, sem tentar adivinhar um preço.
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
