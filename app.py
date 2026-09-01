"""
On-chain basket rebalancing simulator — MVP.

Compares real rebalancing behavior across "index basket" platforms: Glider,
Reserve Protocol and QuantAMM/Balancer. A platform selector swaps which
adapter is used (same interface across all three — see each one's docstring
in adapters/), showing the same three blocks (current allocation, rebalance
history, performance) with the same layout, to allow a visual side-by-side
even with data from very different sources.

Beyond that, the current allocation becomes a "what if I weighted it
differently": each asset has a slider (starting at its real weight) that,
when moved, recalculates the pie/table and a simulated performance curve —
recombining each asset's historical price (via `price_ref`, which each
adapter exposes when it can map the asset to a chain:address pair
recognized by DefiLlama) using the new weights. An asset with no available
historical price is excluded from the simulation and listed as such, never
invented.

Caveat (repeated from the adapter): the underlying assets differ across the
three platforms (tokenized stocks vs. crypto vs. crypto pools), and the
"weight" in each comes from a different approximation — you can't directly
compare the REAL performance curve across all three as if it were the same
thing. The metric that's genuinely comparable between them is "who decides"
and the rebalance frequency, shown in a separate card.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from adapters import glider, pricing, quantamm, reserve
from ui import theme

load_dotenv()

CACHE_TTL_SECONDS = 300
SIMULATION_DAYS = 180

PLATFORMS = {
    "Glider": glider,
    "Reserve Protocol": reserve,
    "QuantAMM/Balancer": quantamm,
}


# --- cached wrappers (keeps adapters free of Streamlit) --------------------


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
    """Calls func(*args) and returns (result, error_message).

    Each adapter has its own exception (GliderAPIError, ReserveAPIError,
    QuantAMMAPIError) — we deliberately catch Exception generically here,
    so no section of the screen breaks with a raw traceback no matter which
    platform is selected.
    """
    try:
        return func(*args), None
    except Exception as exc:  # noqa: BLE001 — see docstring above
        return None, str(exc)


def estimate_monthly_frequency(history: list[dict]) -> float | None:
    """Number of rebalance events per month, based on the history's date span."""
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
    """Recombines each asset's historical return (via price_ref) using the
    weights adjusted on the sliders. Each item: {"asset", "weight_pct", "price_ref"}.

    Assets with no price_ref, or with no historical price available on
    DefiLlama, are excluded (never simulated/invented) — the remaining
    assets' weights are renormalized among themselves so the sum stays at
    100%. Returns (points, excluded); points is None if no asset had a
    price available.
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


st.set_page_config(page_title="Rebalancing Simulator", layout="wide")
theme.inject_css()

with st.sidebar:
    st.header("Control Panel")
    platform_name = st.selectbox("Platform", list(PLATFORMS.keys()))
    adapter = PLATFORMS[platform_name]

    example_label = st.selectbox(
        "Basket / strategy (example)",
        list(adapter.EXAMPLE_BASKETS.keys()) + ["Other (paste manually)"],
    )
    if example_label == "Other (paste manually)":
        placeholder = "strategyId" if platform_name == "Glider" else "<chain>:<address>"
        basket_id = st.text_input("basket_id", placeholder=placeholder).strip()
    else:
        basket_id = adapter.EXAMPLE_BASKETS[example_label]

if not basket_id:
    st.warning("Choose an example or enter a basket_id in the sidebar.")
    st.stop()

theme.render_header("REBALANCING CONSOLE", platform_name, basket_id)

# tvlUsd only comes from Glider's discovery; Reserve and QuantAMM only have
# aggregated TVL per protocol via DefiLlama (not per individual basket) —
# see docstring of get_tvl_usd_defillama in each adapter.
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
            "Nº of wallets",
            metrics_match["portfolio_count"] if metrics_match["portfolio_count"] is not None else "—",
        )
    elif discovery_error:
        st.caption(f"TVL/wallet count unavailable: {discovery_error}")
else:
    tvl, _ = safe_call(adapter.get_tvl_usd_defillama)
    if tvl is not None:
        st.caption(
            f"TVL for the entire protocol (DefiLlama, cross-check — not per-basket TVL): ${tvl:,.0f}"
        )

tab_allocation, tab_history, tab_performance, tab_protocol = st.tabs(
    ["Allocation", "History", "Performance", "Protocol"]
)

# --- current target allocation, with concentration sliders -----------------

with tab_allocation:
    allocation, allocation_error = safe_call(cached_get_current_allocation, platform_name, basket_id)

    edited_weights: dict[str, float] = {}
    price_refs: dict[str, str | None] = {}

    if allocation_error:
        st.error(allocation_error)
    elif not allocation:
        st.info("No allocation returned by the data source.")
    else:
        price_refs = {row["asset"]: row.get("price_ref") for row in allocation}

        with st.expander("Methodology"):
            st.caption(
                "Adjust each asset's concentration — the sliders start at the real "
                "weights and are normalized to sum to 100%. The simulated "
                "performance curve on the Performance tab uses these weights."
            )

        def _slider_key(asset: str) -> str:
            return f"w::{platform_name}::{basket_id}::{asset}"

        if st.button("Reset to real weights"):
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
        st.caption(f"Raw sum of sliders: {total_raw:.1f}% → normalized to 100% below.")
        if total_raw > 0:
            edited_weights = {asset: (w / total_raw) * 100.0 for asset, w in raw_weights.items()}
        else:
            edited_weights = raw_weights  # all at zero — nothing to normalize

        df_allocation = pd.DataFrame(
            [{"asset": asset, "weight_pct": w} for asset, w in edited_weights.items()]
        )
        chart_col, table_col = st.columns([2, 1])
        with chart_col:
            fig = px.pie(df_allocation, names="asset", values="weight_pct", title="Simulated weight per asset (%)")
            st.plotly_chart(fig, use_container_width=True)
        with table_col:
            st.dataframe(
                df_allocation.rename(columns={"asset": "Asset", "weight_pct": "Simulated weight (%)"}),
                use_container_width=True,
                hide_index=True,
            )

# --- rebalance history -------------------------------------------------

with tab_history:
    history, history_error = safe_call(cached_get_rebalance_history, platform_name, basket_id)

    if history_error:
        st.error(history_error)
    elif not history:
        st.info("No rebalance event found.")
    else:
        df_history = pd.DataFrame(
            [
                {
                    "Date": h["date"],
                    "What changed": h["description"],
                    "Nº of assets": len(h["weights_after"]) if h["weights_after"] else None,
                }
                for h in reversed(history)  # most recent first
            ]
        )
        st.dataframe(df_history, use_container_width=True, hide_index=True)

# --- performance curve: real vs. simulated with adjusted weights -----------

with tab_performance:
    performance, performance_error = safe_call(cached_get_performance, platform_name, basket_id)

    perf_frames = []

    if performance_error:
        st.error(performance_error)
    elif performance and performance.get("points"):
        df_real = pd.DataFrame(performance["points"])
        df_real["series"] = f"Real ({performance.get('method') or '?'})"
        perf_frames.append(df_real)
    else:
        st.info("No real performance data for this basket.")

    if edited_weights:
        weighted_assets = [
            {"asset": asset, "weight_pct": weight, "price_ref": price_refs.get(asset)}
            for asset, weight in edited_weights.items()
        ]
        sim_points, excluded = simulate_weighted_performance(weighted_assets)
        if sim_points:
            df_sim = pd.DataFrame(sim_points)
            df_sim["series"] = "Simulated (adjusted weights)"
            perf_frames.append(df_sim)
            if excluded:
                st.caption(
                    "Excluded from the simulation for lack of historical price: "
                    + ", ".join(str(a) for a in excluded)
                )
        else:
            st.caption(
                "Could not simulate performance with the adjusted weights — "
                "none of this allocation's assets has historical price "
                "available in the source used (DefiLlama)."
            )

    if perf_frames:
        df_perf_all = pd.concat(perf_frames, ignore_index=True)
        fig_perf = px.line(
            df_perf_all,
            x="date",
            y="percent_change",
            color="series",
            title="Accumulated return (%) — real vs. simulated",
            labels={"date": "Date", "percent_change": "Accumulated return (%)"},
        )
        st.plotly_chart(fig_perf, use_container_width=True)
        st.caption(
            "'Real' uses the platform's native method/source (see adapter). "
            "'Simulated' recombines each asset's historical price using the "
            "weights adjusted above — it only matches 'Real' if the adjusted "
            "weights equal the real ones."
        )

# --- comparative card: who decides + rebalance frequency -------------------

with tab_protocol:
    st.caption(
        "The underlying assets and price curve are NOT directly comparable "
        "across platforms — this is."
    )

    col_decision, col_frequency = st.columns(2)
    with col_decision:
        st.markdown(f"**Who decides ({platform_name})**")
        st.write(adapter.DECISION_MAKER)
    with col_frequency:
        st.markdown("**Rebalance frequency**")
        if history:
            freq = estimate_monthly_frequency(history)
            st.write(f"~{freq:.1f} events/month" if freq is not None else "Insufficient data (history too short).")
        else:
            st.write("No history available to estimate.")
