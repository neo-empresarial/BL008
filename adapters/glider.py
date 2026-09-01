"""
Adapter para a Glider B2B API v2 (https://api.glider.fi/v2).

Interface "unificada" que este adapter expõe (pra bater com reserve.py e
quantamm.py no futuro, e permitir comparar as três plataformas lado a lado):

- get_target_allocation(strategy_id) -> list[{"asset": str, "weight": float}]
    weight em escala 0-100.
- get_version_history(strategy_id) -> list[dict] (ordem cronológica, mais antigo primeiro)
    cada item: {"version", "date", "change_log", "num_assets", "weight_changed", "assets"}
- get_performance(strategy_id) -> {"method": "TWR"|"MWR", "points": [{"date", "percent_change"}]}
- discover_strategies(collection) -> list[dict]
    cada item: {"strategy_id", "name", "tvl_usd", "portfolio_count", "assets"}

Todas as funções levantam GliderAPIError com uma mensagem legível em caso de
falha (rede, HTTP, ou envelope {"success": false, ...}). Nenhuma função aqui
usa Streamlit — cache fica por conta de quem chama (ver app.py).

Além disso, pra viabilizar a visão comparativa lado a lado (app.py alterna o
adapter conforme a plataforma escolhida), este módulo também expõe a
interface comum aos três adapters — get_current_allocation,
get_rebalance_history — como wrappers finos em cima das funções acima (ver
final do arquivo). adapters/reserve.py e adapters/quantamm.py implementam a
mesma interface diretamente.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from . import pricing

BASE_URL = "https://api.glider.fi/v2"
DEFAULT_TIMEOUT = 15  # segundos

# strategy_id de exemplo pro dropdown (mesmas duas do prompt original).
EXAMPLE_BASKETS = {
    "Mag7 (Ondo/Bitwise, equal weight)": "01KV68M2Y685X59DWAEVX5D5X3",
    "Nancy Pelosi Tracker": "01KWJ0Q9GXP1HBN2Z53RCHCHQW",
}

# Quem decide um rebalanceamento na Glider — pro card comparativo no app.py.
DECISION_MAKER = (
    "Unilateral pelo provider da estratégia via chave de API — sem "
    "governança on-chain nem sinal de ML público envolvido."
)


class GliderAPIError(Exception):
    """Erro legível pra mostrar na UI (rede, HTTP, ou success=false)."""


def _get_api_key() -> str:
    api_key = os.environ.get("GLIDER_API_KEY", "").strip()
    if not api_key:
        raise GliderAPIError(
            "GLIDER_API_KEY não configurada. Crie um arquivo .env (veja "
            ".env.example) com sua key da Glider — peça uma em "
            "https://console.glidercloud.dev/."
        )
    return api_key


def _to_float(value: Any) -> float | None:
    """Converte um decimal-string da API (ex: '60.00') pra float com cuidado."""
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _request(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET autenticado em BASE_URL + path. Retorna o corpo já parseado.

    Levanta GliderAPIError em qualquer falha de rede, HTTP, JSON inválido,
    ou envelope {"success": false, "error": {...}}.
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
        raise GliderAPIError(f"Falha de rede ao chamar {path}: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise GliderAPIError(
            f"Resposta inválida (não é JSON) de {path} — status {response.status_code}"
        ) from exc

    if not response.ok:
        error = body.get("error") if isinstance(body, dict) else None
        message = (error or {}).get("message") if error else None
        raise GliderAPIError(
            message or f"Erro HTTP {response.status_code} ao chamar {path}"
        )

    if not body.get("success"):
        error = body.get("error") or {}
        raise GliderAPIError(error.get("message", f"Erro desconhecido em {path}"))

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
    """GET /v2/discovery/strategies — pra popular um dropdown de exploração."""
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
    """GET /v2/strategies/{strategyId} — nome, descrição, alocação-alvo, schedule."""
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
    """Alocação-alvo atual: list[{"asset", "weight"}], weight em 0-100."""
    return get_strategy_detail(strategy_id)["assets"]


def get_version_history(strategy_id: str) -> list[dict[str, Any]]:
    """GET /v2/strategies/{strategyId}/versions, paginando via nextCursor.

    Retorna em ordem cronológica (mais antigo primeiro) e já calcula, pra
    cada versão, se o peso de algum ativo mudou em relação à versão anterior
    — esse é o "log de rebalanceamento" que o app mostra numa tabela.
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

    # ordena por número de versão crescente (mais antigo primeiro)
    raw_versions.sort(key=lambda v: v.get("version", 0))

    history: list[dict[str, Any]] = []
    previous_weights: dict[str, float] | None = None

    for version in raw_versions:
        assets = _parse_assets(version.get("allocation"))
        weights = {a["asset"]: a["weight"] for a in assets if a["asset"] is not None}

        if previous_weights is None:
            weight_changed = False  # primeira versão conhecida: não há "antes"
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
    """GET /v2/strategies/{strategyId}/performance — curva de retorno acumulado.

    method é "TWR" (time-weighted) ou "MWR" (money-weighted) — cálculos
    diferentes, por isso mostramos qual foi usado na legenda do gráfico.
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


# --- interface comum aos três adapters (ver docstring do módulo) -----------


def get_current_allocation(strategy_id: str) -> list[dict[str, Any]]:
    """Wrapper fino sobre get_target_allocation, no formato comum aos adapters.

    Inclui `price_ref` (pra simulação de "e se eu pesasse diferente" no
    app.py) quando o assetId CAIP-19 é um erc20 numa chain EVM conhecida —
    ver adapters/pricing.py. Ativos fora desse caso (ex: não-EVM) ficam com
    price_ref None e o app.py simplesmente os exclui da simulação.
    """
    return [
        {
            "asset": a["asset"],
            "weight_pct": a["weight"],
            "price_ref": pricing.caip19_to_price_ref(a["asset"]),
        }
        for a in get_target_allocation(strategy_id)
    ]


def get_rebalance_history(strategy_id: str) -> list[dict[str, Any]]:
    """Wrapper fino sobre get_version_history, no formato comum aos adapters."""
    history = []
    for version in get_version_history(strategy_id):
        if version["change_log"]:
            description = version["change_log"]
        else:
            description = f"Versão {version['version']}" + (
                " — peso alterado" if version["weight_changed"] else " — sem mudança de peso"
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
