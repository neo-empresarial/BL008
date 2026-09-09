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
invented. Multiple independent weight scenarios ("tabs") can be opened for
the same basket — each keeps its own slider state, so different what-if
allocations can be tweaked side by side and are overlaid together on the
same performance chart to compare them. The performance chart (and the
comparison overlay) can show an indexed value (base 100), percent return,
or growth of a $10,000 stake, via a shared toggle.

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
from uuid import uuid4

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from adapters import glider, pricing, quantamm, reserve
from ui import theme

load_dotenv()

CACHE_TTL_SECONDS = 300
SIMULATION_DAYS = 180

# Chart series names — deliberately explicit about what each one is, since
# "real", "HODL" and "adjusted" can look confusingly similar otherwise (see
# the "How to read this chart" expander below and docs/rebalancing-simulator.md).
SERIES_REAL = "Real strategy (with rebalancing)"
SERIES_HODL = "HODL (real weights, no rebalancing)"


def adjusted_series_label(scenario_label: str) -> str:
    """Series name for one weight-scenario tab's simulated curve — each tab
    gets its own line on the shared performance chart (see
    `render_scenario_editor`), named after that tab so several what-if
    allocations can be told apart when compared."""
    return f"Adjusted buy-and-hold ({scenario_label})"

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


# Discovery hits a paginated API/subgraph per platform — cached for an hour
# (much longer than CACHE_TTL_SECONDS) so it isn't repeated on every rerun.
DISCOVERY_CACHE_TTL_SECONDS = 3600


@st.cache_data(ttl=DISCOVERY_CACHE_TTL_SECONDS)
def cached_discover_baskets(platform_name: str):
    return PLATFORMS[platform_name].discover_baskets()


def build_basket_options(platform_name: str) -> dict[str, str]:
    """label -> basket_id for the sidebar/comparison selects: pinned
    `EXAMPLE_BASKETS` first (their curated labels), then every other basket
    `discover_baskets()` returns for this platform. Falls back to
    `EXAMPLE_BASKETS` alone (with a warning) if discovery fails or returns
    nothing — never an empty select.
    """
    adapter = PLATFORMS[platform_name]
    pinned = dict(adapter.EXAMPLE_BASKETS)

    with st.spinner(f"Loading {platform_name} strategies…"):
        rows, discovery_error = safe_call(cached_discover_baskets, platform_name)

    if not rows:
        if discovery_error:
            st.sidebar.warning(
                f"Couldn't load the full {platform_name} strategy list "
                f"({discovery_error}) — showing pinned examples only."
            )
        return pinned

    pinned_ids = {basket_id.lower() for basket_id in pinned.values()}
    name_counts: dict[str, int] = {}
    for row in rows:
        name_counts[row["name"]] = name_counts.get(row["name"], 0) + 1

    options = dict(pinned)
    for row in rows:
        if row["basket_id"].lower() in pinned_ids:
            continue  # already covered by a pinned example with a nicer label
        label = row["name"]
        if name_counts[label] > 1 or label in options:
            suffix = f" — {row['chain']}" if row.get("chain") else ""
            label = f"{row['name']}{suffix} ({row['basket_id'][-8:]})"
        options[label] = row["basket_id"]
    return options


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


CHART_MODES = ["Indexed value", "Percent return", "Growth of $10k"]
GROWTH_BASE_USD = 10_000.0


def to_display_series(df: pd.DataFrame, chart_mode: str) -> pd.DataFrame:
    """Converts a {"percent_change", ...} frame into the active chart mode
    (one of CHART_MODES), all derived from `percent_change` — each series'
    return since its own first point, never a new data source:

    - "Indexed value": base 100 at each series' first point.
    - "Percent return": `percent_change` as-is.
    - "Growth of $10k": what a $10,000 stake would be worth, e.g. a 10%
      return renders as $11,000, a -10% return as $9,000.
    """
    df = df.copy()
    if chart_mode == "Indexed value":
        df["display_value"] = 100.0 + df["percent_change"]
    elif chart_mode == "Growth of $10k":
        df["display_value"] = GROWTH_BASE_USD * (1.0 + df["percent_change"] / 100.0)
    else:
        df["display_value"] = df["percent_change"]
    return df


def _label_markup(row: dict) -> str:
    """Human-readable label first; token address/explorer link as
    secondary metadata, per row's `display_asset`/`explorer_url`
    (see adapters/pricing.py)."""
    label = row.get("display_asset") or row["asset"]
    markup = f"**{label}**"
    if row.get("explorer_url"):
        markup += f" [↗]({row['explorer_url']})"
    return markup


def _scenarios_key(platform_name: str, basket_id: str) -> str:
    return f"scenarios::{platform_name}::{basket_id}"


def get_scenarios(platform_name: str, basket_id: str) -> list[dict]:
    """This basket's list of weight-scenario tabs, creating a single default
    one the first time a basket is opened. Each scenario is
    `{"id", "label"}`; slider/committed-weight state is keyed by scenario id
    (see `render_scenario_editor`), so tabs never share slider state and
    switching platform/basket starts fresh rather than reusing another
    basket's tabs."""
    key = _scenarios_key(platform_name, basket_id)
    if key not in st.session_state:
        st.session_state[key] = [{"id": "s1", "label": "Scenario 1"}]
    return st.session_state[key]


def _active_scenario_key(platform_name: str, basket_id: str) -> str:
    return f"active_scenario::{platform_name}::{basket_id}"


def _renaming_key(platform_name: str, basket_id: str) -> str:
    return f"renaming_scenario::{platform_name}::{basket_id}"


def _commit_rename(scenario: dict, widget_key: str, renaming_key: str) -> None:
    """`on_change` callback for a tab's rename box (see `render_tab_bar`):
    Streamlit runs this the moment the text_input's value changes (Enter or
    losing focus) — i.e. the instant the user commits — and reruns the
    script right after, so no separate confirm button is needed. Ignores a
    blanked-out value rather than leaving a tab unnamed."""
    new_label = st.session_state.get(widget_key, "").strip()
    if new_label:
        scenario["label"] = new_label
    st.session_state[renaming_key] = False


def render_tab_bar(platform_name: str, basket_id: str, scenarios: list[dict]) -> str:
    """Browser-style tab strip for this basket's weight scenarios — one row
    of compact buttons built from `st.columns` (`st.tabs` can't host a "+"
    or a per-tab close/rename control, so the whole strip is hand-rolled;
    see `ui/theme.py` for the CSS that makes it read as tabs rather than a
    row of plain buttons). Only the *active* scenario's editor is rendered
    below the strip — like a browser, every other tab's content stays
    hidden (but its weights are preserved in session state either way, see
    `render_scenario_editor`). Mutates `scenarios` in place (add/remove) and
    returns the id of whichever scenario ends up active after this run's
    clicks are applied.

    Per scenario: a select button (its own label; `type="primary"` when
    active, giving it Streamlit's accent color as the "active tab" look)
    plus a small "✕". Streamlit has no double-click event to hook a rename
    onto, so the nearest native equivalent stands in: clicking a tab that's
    already active swaps its select button for an inline `text_input`
    (pre-filled with the current label) instead of switching/doing nothing
    — i.e. "click the tab you're on to rename it". A trailing "＋" adds a
    new scenario and switches to it immediately.
    """
    active_key = _active_scenario_key(platform_name, basket_id)
    renaming_key = _renaming_key(platform_name, basket_id)

    valid_ids = {s["id"] for s in scenarios}
    if st.session_state.get(active_key) not in valid_ids:
        st.session_state[active_key] = scenarios[0]["id"]
    active_id = st.session_state[active_key]
    renaming = st.session_state.get(renaming_key, False)

    widths: list[int] = []
    specs: list[tuple[str, dict | None]] = []
    for scenario in scenarios:
        is_active = scenario["id"] == active_id
        specs.append(("rename_input" if is_active and renaming else "select", scenario))
        widths.append(4 if is_active else 3)
        specs.append(("close", scenario))
        widths.append(1)
    specs.append(("add", None))
    widths.append(1)

    new_active_id = active_id
    removed_id = None
    # The column layout above was sized from `scenarios`/`active_id` as they
    # were at the top of this run — a click that changes either (switching
    # tabs, adding/closing one, entering rename mode) needs an immediate
    # `st.rerun()` so the strip reflects it now rather than one interaction
    # later (mutating session_state alone doesn't trigger a second rerun on
    # its own). A rename commit doesn't need this: its `on_change` callback
    # already runs — and updates session_state — before this function is
    # even called on the rerun it triggers.
    layout_changed = False

    cols = st.columns(widths, gap="small")
    for col, (kind, scenario) in zip(cols, specs):
        with col:
            if kind == "select":
                is_active = scenario["id"] == active_id
                clicked = st.button(
                    scenario["label"],
                    key=f"tabsel::{platform_name}::{basket_id}::{scenario['id']}",
                    type="primary" if is_active else "secondary",
                    use_container_width=True,
                )
                if clicked:
                    if is_active:
                        st.session_state[renaming_key] = True
                    else:
                        new_active_id = scenario["id"]
                        st.session_state[renaming_key] = False
                    layout_changed = True
            elif kind == "close":
                can_remove = len(scenarios) > 1
                if st.button(
                    "✕",
                    key=f"tabclose::{platform_name}::{basket_id}::{scenario['id']}",
                    type="tertiary",
                    disabled=not can_remove,
                    help="Close this tab" if can_remove else "At least one tab must remain",
                ):
                    removed_id = scenario["id"]
                    layout_changed = True
            elif kind == "rename_input":
                widget_key = f"tabname::{platform_name}::{basket_id}::{scenario['id']}"
                st.text_input(
                    "Tab name",
                    value=scenario["label"],
                    key=widget_key,
                    label_visibility="collapsed",
                    on_change=_commit_rename,
                    args=(scenario, widget_key, renaming_key),
                )
            elif kind == "add":
                if st.button(
                    "＋",
                    key=f"tabadd::{platform_name}::{basket_id}",
                    type="tertiary",
                    help="Add a new weight tab",
                ):
                    new_id = uuid4().hex[:8]
                    scenarios.append({"id": new_id, "label": f"Scenario {len(scenarios) + 1}"})
                    new_active_id = new_id
                    st.session_state[renaming_key] = False
                    layout_changed = True

    if removed_id is not None:
        scenarios[:] = [s for s in scenarios if s["id"] != removed_id]
        if removed_id == new_active_id:
            new_active_id = scenarios[0]["id"]
        st.session_state[renaming_key] = False

    st.session_state[active_key] = new_active_id

    if layout_changed:
        st.rerun()

    return new_active_id


def render_scenario_editor(
    scenario: dict,
    allocation: list[dict],
    real_weights: dict[str, float],
    price_refs: dict[str, str | None],
    display_labels: dict[str, str],
    platform_name: str,
    basket_id: str,
) -> dict[str, float]:
    """Renders one weight-scenario tab: reset button, sliders (dragging one
    proportionally rescales the others to keep the total at 100%, same rule
    for every tab) and the resulting donut chart + table. Returns the
    committed weights for this tab.

    Slider/history state is keyed by `scenario["id"]` (in addition to
    platform/basket), so each tab keeps its own weights fully independent of
    every other tab open on this same basket.
    """
    scenario_id = scenario["id"]

    def _slider_key(asset: str) -> str:
        return f"w::{platform_name}::{basket_id}::{scenario_id}::{asset}"

    history_key = f"prev_w::{platform_name}::{basket_id}::{scenario_id}"
    reset_clicked = st.button(
        "Reset to real weights", key=f"reset::{platform_name}::{basket_id}::{scenario_id}"
    )

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
            # 0 or 2+ diffs: this tab was just created, or nothing changed
            # yet — use pending as-is rather than guessing intent.
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

    df_allocation = pd.DataFrame(
        [
            {"asset": display_labels.get(asset, asset), "weight_pct": w}
            for asset, w in committed.items()
        ]
    )
    fig = px.pie(
        df_allocation,
        names="asset",
        values="weight_pct",
        title="Simulated weight per asset (%)",
        hole=0.55,
    )
    st.plotly_chart(
        theme.make_chart_transparent(theme.apply_chart_theme(fig)),
        use_container_width=True,
        key=f"{theme.PIE_KEY_PREFIX}::{platform_name}::{basket_id}::{scenario_id}",
    )
    st.dataframe(
        df_allocation.rename(columns={"asset": "Asset", "weight_pct": "Simulated weight (%)"}),
        use_container_width=True,
        hide_index=True,
        key=f"table::{platform_name}::{basket_id}::{scenario_id}",
    )

    return committed


def chart_mode_axis_label(chart_mode: str) -> str:
    """Y-axis label for the active chart mode, shared by every chart that
    uses `to_display_series`."""
    if chart_mode == "Indexed value":
        return "Indexed value (base 100)"
    if chart_mode == "Growth of $10k":
        return "Growth of $10,000"
    return "Accumulated return (%)"


# --- UI ----------------------------------------------------------------


st.set_page_config(page_title="Rebalancing Simulator", layout="wide")
theme.inject_css()

with st.sidebar:
    st.header("Control Panel")
    platform_name = st.selectbox("Platform", list(PLATFORMS.keys()))
    adapter = PLATFORMS[platform_name]

    basket_options = build_basket_options(platform_name)
    example_label = st.selectbox(
        "Basket / strategy",
        list(basket_options.keys()) + ["Other (paste manually)"],
    )
    if example_label == "Other (paste manually)":
        placeholder = "strategyId" if platform_name == "Glider" else "<chain>:<address>"
        basket_id = st.text_input("basket_id", placeholder=placeholder).strip()
    else:
        basket_id = basket_options[example_label]

    st.divider()
    st.caption("Compare strategies")
    comparison_options = {
        f"{p_name} / {b_label}": (p_name, b_id)
        for p_name in PLATFORMS
        for b_label, b_id in build_basket_options(p_name).items()
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

# Per-basket TVL, when available, comes from the same discover_baskets()
# rows the sidebar already fetched (cached) — QuantAMM has real per-pool
# TVL there, and Reserve has real per-basket market cap there too (see
# each adapter's discover_baskets docstring). Glider additionally shows
# wallet count, which isn't part of the shared discovery shape and needs
# its own (also cached) discover_strategies call.
basket_rows, basket_rows_error = safe_call(cached_discover_baskets, platform_name)
basket_match = None
if basket_rows:
    basket_match = next(
        (r for r in basket_rows if r["basket_id"].lower() == basket_id.lower()), None
    )
per_basket_tvl = basket_match["tvl_usd"] if basket_match else None

if platform_name == "Glider":
    strategies, strategies_error = safe_call(cached_discover_glider_strategies, "curated")
    portfolio_count = None
    if strategies:
        strategy_match = next((s for s in strategies if s["strategy_id"] == basket_id), None)
        portfolio_count = strategy_match["portfolio_count"] if strategy_match else None

    col1, col2 = st.columns(2)
    col1.metric("TVL (USD)", f"${per_basket_tvl:,.2f}" if per_basket_tvl is not None else "—")
    col2.metric("Nº of wallets", portfolio_count if portfolio_count is not None else "—")
    if per_basket_tvl is None and portfolio_count is None:
        st.caption(
            "TVL/wallet count unavailable: "
            + (basket_rows_error or strategies_error or "this strategy isn't in the curated discovery collection.")
        )
else:
    if per_basket_tvl is not None:
        st.metric("TVL (USD)", f"${per_basket_tvl:,.2f}")
    else:
        tvl, tvl_error = safe_call(adapter.get_tvl_usd_defillama)
        if tvl is not None:
            st.caption(
                f"Per-basket TVL unavailable — protocol-wide TVL (DefiLlama, cross-check): ${tvl:,.0f}"
            )
        else:
            st.caption("TVL unavailable" + (f": {tvl_error}" if tvl_error else "."))

# --- allocation editor ------------------------------------------------------

allocation, allocation_error = safe_call(cached_get_current_allocation, platform_name, basket_id)

real_weights: dict[str, float] = {}
price_refs: dict[str, str | None] = {}
display_labels: dict[str, str] = {}
scenarios: list[dict] = []
scenario_edited_weights: dict[str, dict[str, float]] = {}

st.subheader("Allocation")
if allocation_error:
    st.error(allocation_error)
elif not allocation:
    st.info("No allocation returned by the data source.")
else:
    price_refs = {row["asset"]: row.get("price_ref") for row in allocation}
    display_labels = {row["asset"]: (row.get("display_asset") or row["asset"]) for row in allocation}
    real_weights = {row["asset"]: round(row["weight_pct"], 1) for row in allocation}

    with st.expander("Methodology"):
        st.caption(
            "Adjust each asset's concentration — the sliders start at the real "
            "weights. Moving one asset proportionally rescales the others so "
            "the total always stays at 100%. Each tab is an independent weight "
            "scenario for this same basket — click a tab to switch to it, "
            "click the tab you're already on to rename it, ✕ to close it, "
            "and ＋ to open a new one. Every tab's simulated performance "
            "curve is overlaid on the chart below to compare them, even "
            "while it isn't the one showing."
        )

    scenarios = get_scenarios(platform_name, basket_id)
    active_id = render_tab_bar(platform_name, basket_id, scenarios)
    active_scenario = next(s for s in scenarios if s["id"] == active_id)

    render_scenario_editor(
        active_scenario,
        allocation,
        real_weights,
        price_refs,
        display_labels,
        platform_name,
        basket_id,
    )

    for scenario in scenarios:
        hist_key = f"prev_w::{platform_name}::{basket_id}::{scenario['id']}"
        scenario_edited_weights[scenario["id"]] = st.session_state.get(hist_key, real_weights)

# --- performance curve: real vs. simulated with adjusted weights -----------

perf_title_col, perf_control_col = st.columns([3, 2])
with perf_title_col:
    st.subheader("Performance")
with perf_control_col:
    if hasattr(st, "segmented_control"):
        chart_mode = st.segmented_control(
            "Chart mode",
            CHART_MODES,
            default="Indexed value",
            label_visibility="collapsed",
        )
    else:
        chart_mode = st.radio(
            "Chart mode",
            CHART_MODES,
            horizontal=True,
            label_visibility="collapsed",
        )
chart_mode = chart_mode or "Indexed value"

with st.expander("How to read this chart"):
    st.caption(
        f"**{SERIES_REAL}** comes straight from the platform's own performance "
        "source (see adapter) — it reflects whatever rebalancing actually "
        f"happened on-chain/in the strategy. **{SERIES_HODL}** and every "
        "**Adjusted buy-and-hold (…)** line are computed here by recombining "
        "each asset's individual historical price (via DefiLlama) — never "
        "taken from the platform — so an asset with no available price "
        "history is excluded from all of them, listed as such, never "
        "invented. HODL always uses the basket's real current weights, "
        "static, regardless of any tab's sliders. Each Adjusted line reflects "
        "one weight-scenario tab's sliders — open more tabs (the ＋ above) "
        "to compare several what-if allocations on this same chart. With a "
        "tab's sliders at their real/reset values, its Adjusted "
        "line and HODL use the same weights and should track closely — small "
        "differences can still appear from excluded assets or data timing."
    )

performance, performance_error = safe_call(cached_get_performance, platform_name, basket_id)

perf_frames = []

if performance_error:
    st.error(performance_error)
elif performance and performance.get("points"):
    df_real = pd.DataFrame(performance["points"])
    df_real["series"] = SERIES_REAL
    perf_frames.append(df_real)
else:
    st.info("No real performance data for this basket.")

if real_weights:
    hodl_assets = [
        {"asset": asset, "weight_pct": weight, "price_ref": price_refs.get(asset)}
        for asset, weight in real_weights.items()
    ]
    hodl_points, _hodl_excluded = simulate_weighted_performance(hodl_assets)
    if hodl_points:
        df_hodl = pd.DataFrame(hodl_points)
        df_hodl["series"] = SERIES_HODL
        perf_frames.append(df_hodl)

if scenario_edited_weights:
    any_scenario_simulated = False
    all_excluded: set[str] = set()
    for scenario in scenarios:
        weights = scenario_edited_weights.get(scenario["id"])
        if not weights:
            continue
        weighted_assets = [
            {"asset": asset, "weight_pct": weight, "price_ref": price_refs.get(asset)}
            for asset, weight in weights.items()
        ]
        sim_points, excluded = simulate_weighted_performance(weighted_assets)
        all_excluded.update(excluded)
        if sim_points:
            any_scenario_simulated = True
            df_sim = pd.DataFrame(sim_points)
            df_sim["series"] = adjusted_series_label(scenario["label"])
            perf_frames.append(df_sim)

    if not any_scenario_simulated:
        st.caption(
            "Could not simulate performance with the adjusted weights — "
            "none of this allocation's assets has historical price "
            "available in the source used (DefiLlama)."
        )
    elif all_excluded:
        st.caption(
            "Excluded from the simulation for lack of historical price: "
            + ", ".join(display_labels.get(a, a) for a in sorted(all_excluded))
        )

if perf_frames:
    df_perf_all = to_display_series(pd.concat(perf_frames, ignore_index=True), chart_mode)
    y_label = chart_mode_axis_label(chart_mode)
    fig_perf = px.line(
        df_perf_all,
        x="date",
        y="display_value",
        color="series",
        title=f"Performance — real vs. HODL vs. adjusted ({chart_mode.lower()})",
        labels={"date": "Date", "display_value": y_label},
    )
    st.plotly_chart(theme.apply_chart_theme(fig_perf), use_container_width=True)

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
        y_label = chart_mode_axis_label(chart_mode)
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

theme.render_footer()
