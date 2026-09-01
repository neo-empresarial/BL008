"""
Simulador de rebalanceamento de cestas on-chain — MVP.

Compara o comportamento real de rebalanceamento entre plataformas de
"index baskets": Glider, Reserve Protocol e QuantAMM/Balancer. Um seletor de
plataforma troca qual adapter é usado (mesma interface nos três — ver
docstring de cada um em adapters/), mostrando os mesmos três blocos
(alocação atual, histórico de rebalanceamento, performance) com o mesmo
layout, pra permitir side-by-side visual mesmo com dados de fontes bem
diferentes.

Além disso, a alocação atual vira um "e se eu pesasse diferente": cada ativo
tem um slider (começando no peso real) que, ao ser movido, recalcula a
pizza/tabela e uma curva de performance simulada — recombinando o preço
histórico de cada ativo (via `price_ref`, que cada adapter expõe quando
consegue mapear o ativo pra um par chain:endereço reconhecido pela
DefiLlama) pelos novos pesos. Ativo sem preço histórico disponível é
excluído da simulação e listado como tal, nunca inventado.

Atenção (repetindo do adapter): os ativos subjacentes são diferentes entre
as três plataformas (ações tokenizadas vs. cripto vs. pools de cripto), e o
"peso" em cada uma vem de uma aproximação diferente — não dá pra comparar a
curva de performance REAL das três diretamente como se fosse a mesma coisa.
A métrica realmente comparável entre elas é a de "quem decide" e a
frequência de rebalanceamento, mostrada num card à parte.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from adapters import glider, pricing, quantamm, reserve

load_dotenv()

CACHE_TTL_SECONDS = 300
SIMULATION_DAYS = 180

PLATFORMS = {
    "Glider": glider,
    "Reserve Protocol": reserve,
    "QuantAMM/Balancer": quantamm,
}


# --- wrappers com cache (mantém os adapters livres de Streamlit) -----------


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def cached_get_current_allocation(platform_name: str, basket_id: str):
    return PLATFORMS[platform_name].get_current_allocation(basket_id)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def cached_get_rebalance_history(platform_name: str, basket_id: str):
    return PLATFORMS[platform_name].get_rebalance_history(basket_id)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def cached_get_performance(platform_name: str, basket_id: str):
    return PLATFORMS[platform_name].get_performance(basket_id)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def cached_discover_glider_strategies(collection: str = "curated"):
    return glider.discover_strategies(collection=collection)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def cached_get_price_history(price_ref: str, days: int = SIMULATION_DAYS):
    return pricing.get_price_history(price_ref, days=days)


def safe_call(func, *args):
    """Chama func(*args) e devolve (resultado, mensagem_de_erro).

    Cada adapter tem sua própria exceção (GliderAPIError, ReserveAPIError,
    QuantAMMAPIError) — capturamos Exception de forma genérica aqui de
    propósito, pra nenhuma seção da tela quebrar com traceback cru não
    importa qual plataforma esteja selecionada.
    """
    try:
        return func(*args), None
    except Exception as exc:  # noqa: BLE001 — ver docstring acima
        return None, str(exc)


def estimate_monthly_frequency(history: list[dict]) -> float | None:
    """Nº de eventos de rebalanceamento por mês, com base no span de datas do histórico."""
    dates = []
    for item in history:
        if not item.get("date"):
            continue
        try:
            dates.append(datetime.fromisoformat(item["date"].replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(dates) < 2:
        return None
    dates.sort()
    span_days = (dates[-1] - dates[0]).total_seconds() / 86400
    if span_days <= 0:
        return None
    return len(dates) / (span_days / 30.0)


def simulate_weighted_performance(
    weighted_assets: list[dict], days: int = SIMULATION_DAYS
) -> tuple[list[dict] | None, list[str]]:
    """Recombina o retorno histórico de cada ativo (via price_ref) pelos pesos
    ajustados nos sliders. Cada item: {"asset", "weight_pct", "price_ref"}.

    Ativos sem price_ref, ou sem preço histórico disponível na DefiLlama, são
    excluídos (nunca simulados/inventados) — os pesos dos que sobraram são
    renormalizados entre si pra a soma continuar em 100%. Retorna
    (points, excluded); points é None se nenhum ativo tinha preço disponível.
    """
    series: dict[str, pd.Series] = {}
    excluded: list[str] = []

    for item in weighted_assets:
        price_ref = item.get("price_ref")
        if not price_ref:
            excluded.append(item["asset"])
            continue
        try:
            raw = cached_get_price_history(price_ref, days)
        except pricing.PricingError:
            excluded.append(item["asset"])
            continue
        df = pd.DataFrame(raw)
        df["date"] = pd.to_datetime(df["timestamp"], unit="s").dt.floor("D")
        series[item["asset"]] = df.groupby("date")["price"].last()

    included = [item for item in weighted_assets if item["asset"] in series]
    total_weight = sum(item["weight_pct"] for item in included)
    if not included or total_weight <= 0:
        return None, excluded

    combined = pd.DataFrame(series).sort_index().ffill().bfill()
    normalized = combined / combined.iloc[0]

    weights = pd.Series({item["asset"]: item["weight_pct"] / total_weight for item in included})
    weighted_index = (normalized[weights.index] * weights).sum(axis=1)
    base = weighted_index.iloc[0]

    points = [
        {"date": date.isoformat(), "percent_change": ((value / base) - 1) * 100.0}
        for date, value in weighted_index.items()
    ]
    return points, excluded


# --- UI ----------------------------------------------------------------


st.set_page_config(page_title="Simulador de Rebalanceamento", layout="wide")
st.title("Simulador de Rebalanceamento — Cestas On-Chain")
st.caption(
    "MVP comparando o rebalanceamento real de estratégias de índice on-chain "
    "entre Glider, Reserve Protocol e QuantAMM/Balancer."
)

with st.sidebar:
    st.header("Configuração")
    platform_name = st.selectbox("Plataforma", list(PLATFORMS.keys()))
    adapter = PLATFORMS[platform_name]

    example_label = st.selectbox(
        "Basket / estratégia (exemplo)",
        list(adapter.EXAMPLE_BASKETS.keys()) + ["Outro (colar manualmente)"],
    )
    if example_label == "Outro (colar manualmente)":
        placeholder = "strategyId" if platform_name == "Glider" else "<chain>:<endereço>"
        basket_id = st.text_input("basket_id", placeholder=placeholder).strip()
    else:
        basket_id = adapter.EXAMPLE_BASKETS[example_label]

if not basket_id:
    st.warning("Escolha um exemplo ou informe um basket_id na barra lateral.")
    st.stop()

st.subheader(f"{platform_name} — `{basket_id}`")

# tvlUsd só vem do discovery da Glider; Reserve e QuantAMM só têm TVL
# agregado por protocolo via DefiLlama (não por basket individual) — ver
# docstring de get_tvl_usd_defillama em cada adapter.
if platform_name == "Glider":
    discovery, discovery_error = safe_call(cached_discover_glider_strategies, "curated")
    metrics_match = None
    if discovery:
        metrics_match = next((s for s in discovery if s["strategy_id"] == basket_id), None)
    if metrics_match:
        col1, col2 = st.columns(2)
        col1.metric(
            "TVL (USD)",
            f"${metrics_match['tvl_usd']:,.2f}" if metrics_match["tvl_usd"] is not None else "—",
        )
        col2.metric(
            "Nº de carteiras",
            metrics_match["portfolio_count"] if metrics_match["portfolio_count"] is not None else "—",
        )
    elif discovery_error:
        st.caption(f"TVL/nº de carteiras indisponíveis: {discovery_error}")
else:
    tvl, _ = safe_call(adapter.get_tvl_usd_defillama)
    if tvl is not None:
        st.caption(
            f"TVL do protocolo inteiro (DefiLlama, cruzamento — não é TVL por basket): ${tvl:,.0f}"
        )

# --- alocação-alvo atual, com sliders de concentração ----------------------

st.markdown("### Alocação-alvo atual")
allocation, allocation_error = safe_call(cached_get_current_allocation, platform_name, basket_id)

edited_weights: dict[str, float] = {}
price_refs: dict[str, str | None] = {}

if allocation_error:
    st.error(allocation_error)
elif not allocation:
    st.info("Sem alocação retornada pela fonte de dados.")
else:
    price_refs = {row["asset"]: row.get("price_ref") for row in allocation}

    st.caption(
        "Ajuste a concentração de cada ativo — os sliders começam nos pesos "
        "reais e são normalizados pra somar 100%. A curva de performance "
        "simulada, mais abaixo, usa esses pesos."
    )

    def _slider_key(asset: str) -> str:
        return f"w::{platform_name}::{basket_id}::{asset}"

    if st.button("Resetar para os pesos reais"):
        for row in allocation:
            st.session_state[_slider_key(row["asset"])] = round(row["weight_pct"], 1)

    raw_weights: dict[str, float] = {}
    slider_cols = st.columns(3)
    for i, row in enumerate(allocation):
        key = _slider_key(row["asset"])
        if key not in st.session_state:
            st.session_state[key] = round(row["weight_pct"], 1)
        with slider_cols[i % 3]:
            raw_weights[row["asset"]] = st.slider(
                str(row["asset"]),
                min_value=0.0,
                max_value=100.0,
                step=0.5,
                key=key,
            )

    total_raw = sum(raw_weights.values())
    st.caption(f"Soma bruta dos sliders: {total_raw:.1f}% → normalizada pra 100% abaixo.")
    if total_raw > 0:
        edited_weights = {asset: (w / total_raw) * 100.0 for asset, w in raw_weights.items()}
    else:
        edited_weights = raw_weights  # todos em zero — não tem o que normalizar

    df_allocation = pd.DataFrame(
        [{"asset": asset, "weight_pct": w} for asset, w in edited_weights.items()]
    )
    chart_col, table_col = st.columns([2, 1])
    with chart_col:
        fig = px.pie(df_allocation, names="asset", values="weight_pct", title="Peso simulado por ativo (%)")
        st.plotly_chart(fig, use_container_width=True)
    with table_col:
        st.dataframe(
            df_allocation.rename(columns={"asset": "Ativo", "weight_pct": "Peso simulado (%)"}),
            use_container_width=True,
            hide_index=True,
        )

# --- histórico de rebalanceamento -----------------------------------------

st.markdown("### Histórico de rebalanceamento")
history, history_error = safe_call(cached_get_rebalance_history, platform_name, basket_id)

if history_error:
    st.error(history_error)
elif not history:
    st.info("Nenhum evento de rebalanceamento encontrado.")
else:
    df_history = pd.DataFrame(
        [
            {
                "Data": h["date"],
                "O que mudou": h["description"],
                "Nº de ativos": len(h["weights_after"]) if h["weights_after"] else None,
            }
            for h in reversed(history)  # mais recente primeiro
        ]
    )
    st.dataframe(df_history, use_container_width=True, hide_index=True)

# --- curva de performance: real x simulada com os pesos ajustados ----------

st.markdown("### Performance")
performance, performance_error = safe_call(cached_get_performance, platform_name, basket_id)

perf_frames = []

if performance_error:
    st.error(performance_error)
elif performance and performance.get("points"):
    df_real = pd.DataFrame(performance["points"])
    df_real["série"] = f"Real ({performance.get('method') or '?'})"
    perf_frames.append(df_real)
else:
    st.info("Sem dados de performance real para esse basket.")

if edited_weights:
    weighted_assets = [
        {"asset": asset, "weight_pct": weight, "price_ref": price_refs.get(asset)}
        for asset, weight in edited_weights.items()
    ]
    sim_points, excluded = simulate_weighted_performance(weighted_assets)
    if sim_points:
        df_sim = pd.DataFrame(sim_points)
        df_sim["série"] = "Simulado (pesos ajustados)"
        perf_frames.append(df_sim)
        if excluded:
            st.caption(
                "Excluídos da simulação por falta de preço histórico: "
                + ", ".join(str(a) for a in excluded)
            )
    else:
        st.caption(
            "Não foi possível simular performance com os pesos ajustados — "
            "nenhum dos ativos dessa alocação tem preço histórico disponível "
            "na fonte usada (DefiLlama)."
        )

if perf_frames:
    df_perf_all = pd.concat(perf_frames, ignore_index=True)
    fig_perf = px.line(
        df_perf_all,
        x="date",
        y="percent_change",
        color="série",
        title="Retorno acumulado (%) — real vs. simulado",
        labels={"date": "Data", "percent_change": "Retorno acumulado (%)"},
    )
    st.plotly_chart(fig_perf, use_container_width=True)
    st.caption(
        "'Real' usa o método/fonte nativo da plataforma (ver adapter). "
        "'Simulado' recombina o preço histórico de cada ativo pelos pesos "
        "ajustados acima — só bate com o 'Real' se os pesos ajustados forem "
        "iguais aos reais."
    )

# --- card comparativo: quem decide + frequência de rebalanceamento --------

st.markdown("### Comparável entre plataformas: quem decide e com que frequência")
st.caption(
    "Os ativos subjacentes e a curva de preço NÃO são comparáveis diretamente entre "
    "plataformas — isto aqui é."
)

col_decision, col_frequency = st.columns(2)
with col_decision:
    st.markdown(f"**Quem decide ({platform_name})**")
    st.write(adapter.DECISION_MAKER)
with col_frequency:
    st.markdown("**Frequência de rebalanceamento**")
    if history:
        freq = estimate_monthly_frequency(history)
        st.write(f"~{freq:.1f} eventos/mês" if freq is not None else "Dados insuficientes (histórico curto demais).")
    else:
        st.write("Sem histórico disponível pra estimar.")
