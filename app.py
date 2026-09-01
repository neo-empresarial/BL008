"""
On-chain basket rebalancing simulator — MVP.

Compares real rebalancing behavior across "index basket" platforms: Glider,
Reserve Protocol and QuantAMM/Balancer. A platform selector swaps which
adapter is used (same interface across all three — see each one's docstring
in adapters/). The main workspace pairs the allocation editor with a live
performance chart for the selected basket; rebalance history and protocol
details sit below as secondary, collapsed sections; a separate comparison
section overlays the real performance of multiple example strategies across
platforms.

The current allocation becomes a "what if I weighted it differently": each
asset has a slider (starting at its real weight) that, when moved,
proportionally rescales the other assets to keep the total at 100% and
recalculates the donut chart and a simulated performance curve —
recombining each asset's historical price (via `price_ref`, which each
adapter exposes when it can map the asset to a chain:address pair
recognized by DefiLlama) using the new weights. An asset with no available
historical price is excluded from the simulation and listed as such, never
invented. The performance chart (and the comparison overlay) can show
either an indexed value (base 100) or percent return, via a shared toggle.

Caveat (repeated from the adapter): the underlying assets differ across the
three platforms (tokenized stocks vs. crypto vs. crypto pools), and the
"weight" in each comes from a different approximation — you can't directly
compare the REAL performance curve across all three as if it were the same
thing. The metric that's genuinely comparable between them is "who decides"
and the rebalance frequency, shown in the protocol details section. The
comparison overlay plots real performance side by side anyway, with a
visible note that methodologies differ.
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


def to_display_series(df: pd.DataFrame, chart_mode: str) -> pd.DataFrame:
    """Converts a {"percent_change", ...} frame into the active chart mode:
    "Indexed value" (base 100 at each series' first point, `percent_change`
    already is relative to that same first point) or "Percent return"
    (`percent_change` as-is)."""
    df = df.copy()
    if chart_mode == "Indexed value":
        df["display_value"] = 100.0 + df["percent_change"]
    else:
        df["display_value"] = df["percent_change"]
    return df


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

    st.divider()
    st.caption("Compare strategies")
    comparison_options = {
        f"{p_name} / {b_label}": (p_name, b_id)
        for p_name, p_adapter in PLATFORMS.items()
        for b_label, b_id in p_adapter.EXAMPLE_BASKETS.items()
    }
    comparison_selection = st.multiselect(
        "Compare strategies",
        list(comparison_options.keys()),
        label_visibility="collapsed",
    )

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

# --- allocation editor ------------------------------------------------------

allocation, allocation_error = safe_call(cached_get_current_allocation, platform_name, basket_id)

edited_weights: dict[str, float] = {}
real_weights: dict[str, float] = {}
price_refs: dict[str, str | None] = {}
display_labels: dict[str, str] = {}

st.subheader("Allocation")
if allocation_error:
    st.error(allocation_error)
elif not allocation:
    st.info("No allocation returned by the data source.")
else:
    price_refs = {row["asset"]: row.get("price_ref") for row in allocation}
    display_labels = {row["asset"]: (row.get("display_asset") or row["asset"]) for row in allocation}

    def _label_markup(row: dict) -> str:
        """Human-readable label first; token address/explorer link as
        secondary metadata, per row's `display_asset`/`explorer_url`
        (see adapters/pricing.py)."""
        label = row.get("display_asset") or row["asset"]
        markup = f"**{label}**"
        if row.get("explorer_url"):
            markup += f" [↗]({row['explorer_url']})"
        return markup

    with st.expander("Methodology"):
        st.caption(
            "Adjust each asset's concentration — the sliders start at the real "
            "weights. Moving one asset proportionally rescales the others so "
            "the total always stays at 100%. The simulated performance curve "
            "below uses these weights."
        )

    def _slider_key(asset: str) -> str:
        return f"w::{platform_name}::{basket_id}::{asset}"

    def _history_key() -> str:
        return f"prev_w::{platform_name}::{basket_id}"

    real_weights = {row["asset"]: round(row["weight_pct"], 1) for row in allocation}
    history_key = _history_key()
    reset_clicked = st.button("Reset to real weights")

    if reset_clicked or history_key not in st.session_state:
        committed = dict(real_weights)
    else:
        prev_committed = st.session_state[history_key]
        pending = {
            asset: st.session_state.get(_slider_key(asset), prev_committed.get(asset, real))
            for asset, real in real_weights.items()
        }
        changed = [
            asset
            for asset, value in pending.items()
            if abs(value - prev_committed.get(asset, value)) > 1e-9
        ]
        if len(changed) == 1:
            # Proportional rebalance: the dragged asset keeps its new
            # value; every other asset is scaled to fill the remaining
            # percentage, preserving their relative proportions — so the
            # sum always stays at 100% without a separate normalization
            # step (see docs/rebalancing-simulator.md).
            changed_asset = changed[0]
            new_value = max(0.0, min(100.0, pending[changed_asset]))
            others = [a for a in pending if a != changed_asset]
            remaining = 100.0 - new_value
            prev_others_total = sum(prev_committed.get(a, 0.0) for a in others)
            committed = {changed_asset: new_value}
            if prev_others_total > 0:
                for a in others:
                    committed[a] = prev_committed.get(a, 0.0) / prev_others_total * remaining
            elif others:
                equal_share = remaining / len(others)
                for a in others:
                    committed[a] = equal_share
        else:
            # 0 or 2+ diffs: basket/platform just switched, or nothing
            # changed yet — use pending as-is rather than guessing intent.
            committed = pending

    for asset, weight in committed.items():
        st.session_state[_slider_key(asset)] = weight
    st.session_state[history_key] = dict(committed)

    for row in allocation:
        asset = row["asset"]
        label_col, value_col, slider_col = st.columns([2, 1, 5])
        with label_col:
            st.markdown(_label_markup(row))
        with slider_col:
            st.slider(
                str(asset),
                min_value=0.0,
                max_value=100.0,
                step=0.5,
                key=_slider_key(asset),
                label_visibility="collapsed",
            )
        with value_col:
            st.markdown(f"`{committed[asset]:.1f}%`")

    edited_weights = committed

    df_allocation = pd.DataFrame(
        [
            {"asset": display_labels.get(asset, asset), "weight_pct": w}
            for asset, w in edited_weights.items()
        ]
    )
    fig = px.pie(
        df_allocation,
        names="asset",
        values="weight_pct",
        title="Simulated weight per asset (%)",
        hole=0.55,
    )
    st.plotly_chart(theme.apply_chart_theme(fig), use_container_width=True)
    st.dataframe(
        df_allocation.rename(columns={"asset": "Asset", "weight_pct": "Simulated weight (%)"}),
        use_container_width=True,
        hide_index=True,
    )

# --- performance curve: real vs. simulated with adjusted weights -----------

perf_title_col, perf_control_col = st.columns([3, 2])
with perf_title_col:
    st.subheader("Performance")
with perf_control_col:
    if hasattr(st, "segmented_control"):
        chart_mode = st.segmented_control(
            "Chart mode",
            ["Indexed value", "Percent return"],
            default="Indexed value",
            label_visibility="collapsed",
        )
    else:
        chart_mode = st.radio(
            "Chart mode",
            ["Indexed value", "Percent return"],
            horizontal=True,
            label_visibility="collapsed",
        )
chart_mode = chart_mode or "Indexed value"

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
    df_perf_all = to_display_series(pd.concat(perf_frames, ignore_index=True), chart_mode)
    y_label = "Indexed value (base 100)" if chart_mode == "Indexed value" else "Accumulated return (%)"
    fig_perf = px.line(
        df_perf_all,
        x="date",
        y="display_value",
        color="series",
        title=f"Performance — real vs. simulated ({chart_mode.lower()})",
        labels={"date": "Date", "display_value": y_label},
    )
    st.plotly_chart(theme.apply_chart_theme(fig_perf), use_container_width=True)
    st.caption(
        "'Real' uses the platform's native method/source (see adapter). "
        "'Simulated' recombines each asset's historical price using the "
        "weights adjusted above — it only matches 'Real' if the adjusted "
        "weights equal the real ones."
    )

# --- strategy comparison overlay --------------------------------------------

st.subheader("Strategy comparison")

if not comparison_selection:
    st.caption("Select example strategies in the sidebar to overlay their real performance here.")
else:
    comparison_frames = []
    for option_label in comparison_selection:
        c_platform, c_basket_id = comparison_options[option_label]
        c_performance, c_error = safe_call(cached_get_performance, c_platform, c_basket_id)
        if c_error:
            st.warning(f"{option_label}: {c_error}")
            continue
        if not c_performance or not c_performance.get("points"):
            st.warning(f"{option_label}: no performance data available.")
            continue
        df_c = pd.DataFrame(c_performance["points"])
        df_c["series"] = option_label
        comparison_frames.append(df_c)

    if comparison_frames:
        df_comparison = to_display_series(pd.concat(comparison_frames, ignore_index=True), chart_mode)
        y_label = "Indexed value (base 100)" if chart_mode == "Indexed value" else "Accumulated return (%)"
        fig_comparison = px.line(
            df_comparison,
            x="date",
            y="display_value",
            color="series",
            title=f"Strategy comparison ({chart_mode.lower()})",
            labels={"date": "Date", "display_value": y_label},
        )
        st.plotly_chart(theme.apply_chart_theme(fig_comparison), use_container_width=True)
        st.caption(
            "Each series uses its own platform's native performance method/source — "
            "methodologies differ across platforms (see adapter docstrings), so this "
            "overlay compares shape and timing, not a strictly equivalent metric."
        )

# --- secondary sections: rebalance history + protocol details --------------

history, history_error = safe_call(cached_get_rebalance_history, platform_name, basket_id)

with st.expander("Rebalance history"):
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

with st.expander("Protocol details"):
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
